"""
Comfort-range widgets and the two range buttons, shared by the Stocks and ETF pages.
====================================================================================
Session-state pattern (ONE rule for every range box, weight slider and blend slider):
  * No widget gets a value= argument. Its value lives in st.session_state (seeded with the default
    the first time) and is only ever written BEFORE the widget is created in a run (seeding, or a
    button followed by st.rerun()). The two-handle range slider is rebuilt from the boxes each run.
  * app.py re-saves these keys at the top of every run (PERSIST_PREFIXES): Streamlit otherwise
    deletes a widget's value whenever the widget isn't drawn (e.g. while the other page is open).
  * Range keys use the page's OWN prefix ("stk" or "etf") + peer group + metric.
The per-page prefix matters: ETF groups use the same industry names as the stocks, so without it
"Semiconductors / max drawdown" on one page would overwrite the other page's range.
"""

import streamlit as st

import laura


def keys(prefix, ns, key):
    return f"{prefix}lo|{ns}|{key}", f"{prefix}hi|{ns}|{key}", f"{prefix}ext|{ns}|{key}"


def _set(prefix, ns, key, b, lo, hi):
    k_lo, k_hi, k_ext = keys(prefix, ns, key)
    q = b["q"]
    st.session_state[k_lo], st.session_state[k_hi] = float(lo), float(hi)
    # slider track = the group's 5th-95th percentile, widened so it always contains the range
    st.session_state[k_ext] = (min(float(q["p5"]), float(lo), float(hi)), max(float(q["p95"]), float(lo), float(hi)))


def seed(defaults):
    """Give each widget key its default value once (call before the widgets are created)."""
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def seed_laura(prefix, ns, bench, info):
    """Laura ranges are the default: set them for every metric the user hasn't set yet (call before the widgets)."""
    for key, x in info.items():
        if x["range"] and keys(prefix, ns, key)[0] not in st.session_state:
            _set(prefix, ns, key, bench[key], *x["range"])


def clear(prefix, ns):
    starts = (f"{prefix}lo|", f"{prefix}hi|", f"{prefix}ext|")
    for k in [k for k in st.session_state if str(k).startswith(starts) and str(k).split("|")[1] == ns]:
        del st.session_state[k]


def buttons(cols, prefix, ns, bench, weight_defaults):
    """Exactly two buttons, same on both pages. Must be drawn BEFORE the range widgets.
    'Laura standards' -> Laura ranges (re-seeded after the rerun) + Laura weights.
    'Industry average' -> every range = the group's 25th-75th percentile; weights unchanged."""
    c1, c2 = cols
    if c1.button("Laura standards", key=f"{prefix}_btn_laura|{ns}", use_container_width=True,
                 help="Default. Ranges: Laura's stricter per-industry percentiles (risk metrics at the industry median, "
                      "consistency metrics at the 40th percentile). Weights: Laura's weights."):
        clear(prefix, ns)  # ranges are re-seeded with Laura's on the rerun
        for k, v in weight_defaults.items():
            st.session_state[k] = v  # Laura weights
        st.rerun()
    if c2.button("Industry average", key=f"{prefix}_btn_avg|{ns}", use_container_width=True,
                 help="Every range becomes the peer group's 25th-75th percentile (its middle half). Weights unchanged."):
        for key, r in laura.default_ranges(bench).items():
            _set(prefix, ns, key, bench[key], *r)
        st.rerun()


def range_widget(prefix, ns, key, b, fallback=None):
    """Two-handle slider + typed low/high, in the metric's units. The typed numbers are the source
    of truth; the slider is rebuilt from them each run. fallback = range to seed if nothing is set."""
    k_lo, k_hi, k_ext = keys(prefix, ns, key)
    if k_lo not in st.session_state:
        _set(prefix, ns, key, b, *(fallback or laura.industry_default(b)))
    lo, hi = st.session_state[k_lo], st.session_state[k_hi]
    ext = st.session_state.get(k_ext, (b["q"]["p5"], b["q"]["p95"]))
    tmin, tmax = min(ext[0], lo, hi), max(ext[1], lo, hi)
    if tmax - tmin < 1e-9:
        tmax = tmin + 1.0
    st.session_state[k_ext] = (tmin, tmax)
    step = (tmax - tmin) / 200
    new = st.slider("range", float(tmin), float(tmax), (float(min(lo, hi)), float(max(lo, hi))), step=step,
                    label_visibility="collapsed", key=f"{prefix}sl|{ns}|{key}|{round(tmin, 6)}|{round(tmax, 6)}|{round(lo, 6)}|{round(hi, 6)}",
                    format="%.4g")
    if (round(new[0], 9), round(new[1], 9)) != (round(float(min(lo, hi)), 9), round(float(max(lo, hi)), 9)):
        st.session_state[k_lo], st.session_state[k_hi] = float(new[0]), float(new[1])
        st.rerun()
    c1, c2 = st.columns(2)
    c1.number_input("low", key=k_lo, step=float(step), format="%.4f")
    c2.number_input("high", key=k_hi, step=float(step), format="%.4f")
    return min(st.session_state[k_lo], st.session_state[k_hi]), max(st.session_state[k_lo], st.session_state[k_hi])


def laura_caption(info, direction, fmt_fn):
    """One line under each metric: what Laura's bound is and where it comes from."""
    if not info:
        return
    if info["tier"] == "moderate":
        st.caption("Laura (moderate): industry 25th–75th percentile.")
    elif info["fallback"]:
        st.caption(f"⚠ {info['note']}")
    else:
        side = "≤" if direction == "lower" else "≥"
        st.caption(f"**Laura ({info['tier']}): {side} {fmt_fn(info['bound'])}** ({laura.pct_name(info['pct'])}).")
