"""
Laura-limit widgets and the two buttons, shared by the Stocks and ETF pages.
============================================================================
Every metric has ONE "Laura limit": a ceiling for lower-is-better metrics, a floor for higher-is-better ones.
Session-state pattern (ONE rule for every limit box, weight slider and penalty slider):
  * No widget gets a value= argument. Its value lives in st.session_state (seeded with the default
    the first time) and is only ever written BEFORE the widget is created in a run (seeding, or a
    button followed by st.rerun()). The limit slider is rebuilt from the typed box each run.
  * app.py re-saves these keys at the top of every run (PERSIST_PREFIXES): Streamlit otherwise
    deletes a widget's value whenever the widget isn't drawn (e.g. while the other page is open).
  * Limit keys use the page's OWN prefix ("stk" or "etf") + peer group + metric.
The per-page prefix matters: ETF groups can share names with stock industries, so without it
"Semiconductors / max drawdown" on one page would overwrite the other page's limit.
"""

import math

import streamlit as st

import laura


def keys(prefix, ns, key):
    return f"{prefix}lim|{ns}|{key}", f"{prefix}ext|{ns}|{key}"


def _set(prefix, ns, key, b, lim):
    k_lim, k_ext = keys(prefix, ns, key)
    q = b["q"]
    st.session_state[k_lim] = float(lim)
    # slider track = the group's 5th-95th percentile, widened so it always contains the limit
    st.session_state[k_ext] = (min(float(q["p5"]), float(lim)), max(float(q["p95"]), float(lim)))


def seed(defaults):
    """Give each widget key its default value once (call before the widgets are created)."""
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def seed_laura(prefix, ns, bench, info):
    """Laura limits are the default: set them for every metric the user hasn't set yet (call before the widgets)."""
    for key, x in info.items():
        if x["limit"] is not None and keys(prefix, ns, key)[0] not in st.session_state:
            _set(prefix, ns, key, bench[key], x["limit"])


def clear(prefix, ns):
    starts = (f"{prefix}lim|", f"{prefix}ext|")
    for k in [k for k in st.session_state if str(k).startswith(starts) and str(k).split("|")[1] == ns]:
        del st.session_state[k]


def buttons(cols, prefix, ns, bench, metrics, weight_defaults, penalty_key, penalty_default):
    """Exactly two buttons, same on both pages. Must be drawn BEFORE the limit widgets.
    'Laura standards' -> Laura limits (re-seeded after the rerun) + Laura weights + penalty strength back to default.
    'Industry average' -> every limit = the group's p75 (lower-is-better) / p25 (higher-is-better); weights unchanged."""
    c1, c2 = cols
    if c1.button("Laura standards", key=f"{prefix}_btn_laura|{ns}", use_container_width=True,
                 help="Default. Limits: Laura's per-industry percentiles (risk metrics at the industry median, consistency "
                      f"metrics at the 40th percentile, the rest at the 25th / 75th). Weights: Laura's. Penalty strength: {penalty_default}."):
        clear(prefix, ns)  # limits are re-seeded with Laura's on the rerun
        for k, v in weight_defaults.items():
            st.session_state[k] = v  # Laura weights
        st.session_state[penalty_key] = penalty_default
        st.rerun()
    if c2.button("Industry average", key=f"{prefix}_btn_avg|{ns}", use_container_width=True,
                 help="Every limit becomes the peer group's worse quartile: the 75th percentile for lower-is-better metrics, "
                      "the 25th for higher-is-better. Weights and penalty strength unchanged."):
        for key, lim in laura.default_limits(bench, metrics).items():
            _set(prefix, ns, key, bench[key], lim)
        st.rerun()


def limit_widget(prefix, ns, key, b, direction, fallback=None):
    """One-handle slider + typed limit, in the metric's units. The typed number is the source of truth; the slider is
    rebuilt from it each run. fallback = limit to seed if nothing is set (Laura's)."""
    k_lim, k_ext = keys(prefix, ns, key)
    if k_lim not in st.session_state:
        _set(prefix, ns, key, b, fallback if fallback is not None else laura.default_limits({key: b}, [{"key": key, "direction": direction}])[key])
    lim = float(st.session_state[k_lim])
    ext = st.session_state.get(k_ext, (b["q"]["p5"], b["q"]["p95"]))
    tmin, tmax = min(ext[0], lim), max(ext[1], lim)
    if tmax - tmin < 1e-9:
        tmax = tmin + 1.0
    st.session_state[k_ext] = (tmin, tmax)
    step = (tmax - tmin) / 200
    label = "Laura limit (ceiling: at or below scores 100)" if direction == "lower" else "Laura limit (floor: at or above scores 100)"
    new = st.slider(label, float(tmin), float(tmax), lim, step=step,
                    key=f"{prefix}sl|{ns}|{key}|{round(tmin, 6)}|{round(tmax, 6)}|{round(lim, 6)}", format="%.4g")
    if round(new, 9) != round(lim, 9):
        st.session_state[k_lim] = float(new)
        st.rerun()
    st.number_input("limit", key=k_lim, step=float(step), format="%.4f", label_visibility="collapsed")
    return float(st.session_state[k_lim])


def laura_caption(info, direction, fmt_fn):
    """One line under each metric: what Laura's limit is and where it comes from."""
    if not info or info["limit"] is None:
        return
    side = "≤" if direction == "lower" else "≥"
    st.caption(f"**Laura ({info['tier']})**: {info['side']} {side} {fmt_fn(info['limit'])} ({laura.pct_name(info['pct'])}). "
               "Good side of the limit → Fit 100; past it Fit falls 50 points per spread (0 at two spreads).")


def category_table(metrics, current, page):
    """Laura's six category totals for both models side by side (role-based: they differ on purpose), plus this page's
    current weights per category. metrics: this page's METRICS; current: {metric key: weight now}; page: "Stocks" or "ETFs"."""
    import pandas as pd

    import etf_scoring as es
    import scoring2 as sc

    stk, etf, now = sc.category_totals(sc.METRICS), sc.category_totals(es.METRICS), sc.category_totals(metrics, current)
    cols = ("Stocks: growth driver", "ETFs: stability core", f"{page} now")
    df = pd.DataFrame({"Category": sc.CATEGORIES, cols[0]: [stk[c] for c in sc.CATEGORIES], cols[1]: [etf[c] for c in sc.CATEGORIES],
                       cols[2]: [now[c] for c in sc.CATEGORIES],
                       "Metrics (this page)": [", ".join(m["label"] for m in metrics if m["category"] == c and m["weight"]) for c in sc.CATEGORIES]})
    df.loc[len(df)] = ["Total", sum(stk.values()), sum(etf.values()), sum(now.values()), ""]
    st.dataframe(df, hide_index=True, width="stretch", column_config={c: st.column_config.NumberColumn(format="%g") for c in cols})
    st.caption(sc.ROLE_NOTE + " The *now* column changes as you move the weight sliders.")


# ------------------------------------------------------------------ "Laura standards (default)" badge
def is_laura(prefix, ns, info, weight_defaults, penalty_key, penalty_default):
    """True when this page's weights, penalty strength and this group's limits are exactly Laura standards
    (what the Laura standards button restores). Unset keys count as Laura: they are seeded with her values."""
    same = lambda a, b: math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-12)
    ss = st.session_state
    return (all(same(ss.get(k, v), v) for k, v in weight_defaults.items())
            and same(ss.get(penalty_key, penalty_default), penalty_default)
            and all(same(ss.get(keys(prefix, ns, k)[0], x["limit"]), x["limit"]) for k, x in info.items() if x["limit"] is not None))


def reset_laura(prefix, groups, weight_defaults, penalty_key, penalty_default, cmp_key=None):
    """on_click callback (runs before the next run, so it may change widget values): Laura weights + penalty strength,
    and the limits of `groups` (page namespaces) cleared so they are re-seeded with Laura's; Compare back to Laura limits."""
    for g in groups:
        clear(prefix, g)
    for k, v in weight_defaults.items():
        st.session_state[k] = v
    st.session_state[penalty_key] = penalty_default
    if cmp_key:
        st.session_state[f"{cmp_key}_rmode"] = "laura"


def laura_badge(default, key, reset_args):
    """'Laura standards (default)', or 'Custom settings' + a Reset to Laura standards button. reset_args -> reset_laura."""
    if default:
        st.badge("Laura standards (default)", icon=":material/check_circle:", color="green")
        return
    c1, c2 = st.columns([1, 3], vertical_alignment="center")
    c1.badge("Custom settings", icon=":material/tune:", color="orange")
    c2.button("Reset to Laura standards", key=key, on_click=reset_laura, args=reset_args,
              help="Laura's weights, penalty strength k = 0.4 and Laura limits (this group).")


# ------------------------------------------------------------------ penalty strength sensitivity
def sensitivity_table(sens, k_now, k_default, word):
    """Under the ranked table: top 10 at each k (scoring2.penalty_sensitivity) and how many names change."""
    import pandas as pd
    st.markdown(f"**Penalty strength sensitivity**: top 10 {word}s at k = " + " / ".join(f"{k:g}" for k in sens["top"])
                + " (same weights and limits; Overall in brackets)")
    lbl = lambda k: f"k = {k:g}" + (" (default)" if math.isclose(k, k_default) else "") + (" ← now" if math.isclose(k, k_now) else "")
    df = pd.DataFrame(sens["rows"]).rename(columns={k: lbl(k) for k in sens["top"]})
    st.dataframe(df, hide_index=True, width="stretch")
    st.caption(" · ".join(f"k {a:g} vs {b:g}: **{n}** of the top 10 change" for a, b, n in sens["changes"])
               + f". Each Fit point lost costs k Overall points (k = {k_default:g} by default).")
