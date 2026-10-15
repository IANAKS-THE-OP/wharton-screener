"""
"Compare industries" (Stocks) / "Compare groups" (ETFs: Morningstar categories + themes) tab (the engine is compare.py).
=======================================================================================
Session keys (all start with this tab's own key, e.g. "stkcmp_" or "etfcmp_|morningstar"):
  <key>_sel    selected industries/groups     <key>_mode   "own" / "pooled"
  <key>_rmode  "laura" / "avg" (set by the two buttons, not a widget)
  <key>_cell   metric cells show percentile score or Fit        <key>_sort / <key>_desc  table order
Nothing here writes a page's range keys (stklo| / etflo|...), so Compare never changes a page's ranges.
The "Laura standards" button resets the page's WEIGHTS to Laura's, exactly like the button on the page.
Buttons use keys starting with "cmpbtn|" (button state can't be re-saved by app.py's PERSIST_PREFIXES).
"""

import html
import zlib
from datetime import datetime

import pandas as pd
import streamlit as st

import compare
import laura
import snapshots

MODE_LABELS = {"own": "Each vs its own {word}", "pooled": "Pooled (one peer group)"}


def _clean_selection(k, options):
    """Drop stale picks (e.g. a group that has no downloaded funds any more) before the widget is drawn."""
    if k in st.session_state:
        st.session_state[k] = [v for v in st.session_state[k] if v in options]


def _tuned_groups(prefix, ns_fn, bench_by_group, asset, metrics):
    """Groups whose page has hand-tuned ranges that differ from Laura's (Compare doesn't use them)."""
    out = []
    for g, bench in bench_by_group.items():
        if g == compare.POOLED:
            continue
        info = laura.laura_info(bench, asset, metrics)
        for k, x in info.items():
            lo, hi = st.session_state.get(f"{prefix}lo|{ns_fn(g)}|{k}"), st.session_state.get(f"{prefix}hi|{ns_fn(g)}|{k}")
            if lo is not None and x["range"] and (abs(lo - x["range"][0]) > 1e-9 or abs(hi - x["range"][1]) > 1e-9):
                out.append(g)
                break
    return out


def _color(v):
    if v is None:
        return ""
    return f"background:hsla({int(v * 1.2)},70%,45%,0.20);"


def _ticked_hard(hard_prefix, ns_fn, groups, metrics):
    """{group: [metric labels]} whose 'Must be inside range' box is ticked on the page (Compare ignores them)."""
    out = {}
    for g in groups:
        on = [m["label"] for m in metrics if st.session_state.get(f"{hard_prefix}{ns_fn(g)}|{m['key']}")]
        if on:
            out[g] = on
    return out


def render(*, key, asset, metrics, options, counts, run, weight_defaults, fmt, icons, word="industry",
           name_word="company", page_prefix=None, ns_fn=None, extra_note="", hard_prefix=None, global_hard=None,
           meta_extra=None):
    """options: groups the user can pick (only ones with downloaded data). counts: {group: label text for n}.
    run(selected, mode, range_mode) -> compare.stock_compare / etf_compare result.
    hard_prefix: the page's 'Must be inside' key prefix (e.g. 's2h|'); global_hard: label of a page-wide hard-limit
    switch that is on (e.g. ETF 'Laura hard limits'), or None. meta_extra: settings saved with a snapshot."""
    banner = st.container()  # filled once the result is known; always the first thing on the tab
    st.caption(f"Rank names from several {word}s side by side. Only {word}s with downloaded data are listed. {extra_note}")
    if not options:
        st.info(f"Nothing downloaded yet. Pull at least one {word} first.")
        return
    k_sel, k_mode, k_rm = f"{key}_sel", f"{key}_mode", f"{key}_rmode"
    _clean_selection(k_sel, options)
    if k_mode not in st.session_state:
        st.session_state[k_mode] = "own"
    st.session_state.setdefault(k_rm, "laura")
    sel = st.multiselect(f"{word.capitalize()}s to compare (any number)", options, key=k_sel,
                         format_func=lambda g: f"{g}  ({counts.get(g, '?')})", placeholder=f"Pick two or more {word}s")
    c1, c2, c3 = st.columns([2, 1, 1])
    mode = c1.radio("Peer group", list(MODE_LABELS), key=k_mode, horizontal=True, format_func=lambda m: MODE_LABELS[m].format(word=word),
                    help=f"Each vs its own {word}: every name keeps the percentile scores and Laura ranges it gets on its own "
                         f"{word} page, then all are ranked together by Overall. Pooled: all selected {word}s are treated as ONE "
                         "peer group, so percentiles, 25th/median/75th, spread and Laura ranges are recalculated on the pooled set.")
    if c2.button("Reset ranges and weights everywhere", key=f"cmpbtn|{key}|laura", use_container_width=True):
        st.session_state[k_rm] = "laura"
        for wk, v in weight_defaults.items():
            st.session_state[wk] = v  # the SHARED Tune scoring weights
        if page_prefix:
            import range_ui as ru
            for g in sel:
                ru.clear(page_prefix, ns_fn(g))  # page ranges of the selected groups go back to Laura (re-seeded there)
        st.rerun()
    c2.caption(f"Laura standards. **Also changes Tune scoring**: resets its weights to Laura's and the ranges on the "
               f"selected {word}s' pages to Laura's.")
    if c3.button("Industry average", key=f"cmpbtn|{key}|avg", use_container_width=True):
        st.session_state[k_rm] = "avg"
        st.rerun()
    c3.caption("Ranges only, Compare only: every range = the peer group's 25th–75th. Weights and Tune scoring unchanged.")
    rmode = st.session_state[k_rm]
    if not sel:
        st.info(f"Pick at least one {word} above.")
        return
    with st.spinner("Scoring..."):
        res = run(sel, mode, rmode)
    rows = res["rows"]
    dates = res.get("dates") or {}

    # ---------------------------------------------------------------- banner: exactly what is applied
    rtxt = "Laura standards" if rmode == "laura" else "Industry average (25th–75th)"
    custom_w = any(abs(float(st.session_state.get(k, v)) - v) > 1e-9 for k, v in weight_defaults.items())
    lines = [f"**Ranges:** {rtxt} of " + (f"each name's own {word}" if mode == "own" else f"the pooled set of {len(sel)} {word}s"),
             "**Weights:** shared with Tune scoring (" + ("hand-changed there" if custom_w else "Laura's defaults") + ")",
             "**Hard limits:** not applied", "**Data as of:** " + ", ".join(f"{g} {d}" for g, d in dates.items() if d)]
    ignored = []
    if page_prefix:
        tuned = _tuned_groups(page_prefix, ns_fn, res["bench"], asset, metrics)
        if tuned:
            ignored.append(f"hand-tuned ranges on Tune scoring for **{', '.join(tuned)}**")
    if hard_prefix:
        ticked = _ticked_hard(hard_prefix, ns_fn, sel, metrics)
        if ticked:
            ignored.append("ticked hard limits (" + "; ".join(f"{g}: {', '.join(v)}" for g, v in ticked.items()) + ")")
    if global_hard:
        ignored.append(global_hard)
    with banner:
        st.info("  \n".join(lines))
        if ignored:
            st.warning("**Ignored here:** " + " · ".join(ignored) + ". Scores in Compare can therefore differ from those pages.")

    # ---------------------------------------------------------------- per-group summary
    shown_m = [m for m in metrics if m["weight"] > 0]
    st.markdown(f"**Per-{word} summary**: median and Laura bound of each metric (⚠ = fallback pool, under 10 peers)")
    summ = compare.summary(res["bench"], {g: str(counts.get(g, "")) for g in res["bench"]} | {compare.POOLED: f"{len(rows)} names"}, asset, shown_m)
    srows = []
    for s in summ:
        row = {word: s["group"], "n": s["n"], "data as of": dates.get(s["group"]) or (", ".join(sorted({d for d in dates.values() if d})) if s["group"] == compare.POOLED else "")}
        for m in shown_m:
            x = s[m["key"]]
            if x["median"] is None:
                row[m["label"]] = "—"
                continue
            side = "≤" if m["direction"] == "lower" else "≥"
            if x["bound"] is not None:
                lb = f"{side} {fmt(x['bound'], m['units'])}"
            elif x["range"]:
                lb = f"{fmt(x['range'][0], m['units'])}–{fmt(x['range'][1], m['units'])}"
            else:
                lb = "—"
            fb = "" if x["level"] in ("industry", "category") else " ⚠"
            row[m["label"]] = f"{fmt(x['median'], m['units'])} · {lb}{fb}"
        srows.append(row)
    st.dataframe(pd.DataFrame(srows), hide_index=True, width="stretch")

    # ---------------------------------------------------------------- ranked table
    groups_in = sorted({g for r in rows for g in r["groups"]})
    f1, f2, f3, f4 = st.columns([3, 2, 1, 2])
    filt = f1.multiselect(f"Show only these {word}s", groups_in, key=f"{key}_filt|{zlib.crc32(repr(sel).encode())}|{mode}",
                          placeholder="All")
    sort_cols = ["rank", "ticker", "name", word, "Overall", "Fit", "Composite", "inside", "flags"] + [m["label"] for m in shown_m]
    if f"{key}_sort" not in st.session_state or st.session_state[f"{key}_sort"] not in sort_cols:
        st.session_state[f"{key}_sort"] = "Overall"
    st.session_state.setdefault(f"{key}_desc", True)
    st.session_state.setdefault(f"{key}_cell", "pct")
    sort_by = f2.selectbox("Sort by", sort_cols, key=f"{key}_sort")
    desc = f3.toggle("Descending", key=f"{key}_desc")
    cell = f4.radio("Metric cells", ["pct", "fit"], key=f"{key}_cell", horizontal=True,
                    format_func={"pct": "Percentile score", "fit": "Fit score"}.get,
                    help="Each metric cell is 0-100 (100 = best). Hover a cell for the raw value, comfort range and both scores.")
    view = [r for r in rows if not filt or any(g in filt for g in r["groups"])]

    def sval(r):
        return {"rank": r["rank"], "ticker": r["ticker"], "name": r["name"] or "", word: r["group"], "Overall": r["overall"],
                "Fit": r["fit"], "Composite": r["composite"], "inside": r["n_inside"], "flags": len(r["flags"])} \
            .get(sort_by, next((r["metrics"][m["key"]][cell] for m in shown_m if m["label"] == sort_by), None))
    present = [r for r in view if sval(r) is not None]
    missing = [r for r in view if sval(r) is None]
    view = sorted(present, key=sval, reverse=desc) + missing  # blanks always last

    head = ["rank", f"#in {word}", "ticker", name_word, word, "Overall", "Fit", "Composite", "inside", "flags"] + [m["label"] for m in shown_m]
    th = "".join(f"<th>{html.escape(h)}</th>" for h in head)
    body = []
    for r in view:
        def num(v):
            return f"<td style='{_color(v)}'>{'' if v is None else format(v, '.0f')}</td>"
        dash = lambda v: "—" if v is None else v
        why = r.get("low_data_reason") or ""
        tds = [f"<td>{dash(r['rank'])}</td>", f"<td>{dash(r['group_rank'])}</td>", f"<td><b>{html.escape(r['ticker'])}</b></td>",
               f"<td title='{html.escape(r['name'] or '')}'>{html.escape((r['name'] or '')[:34])}</td>",
               f"<td>{html.escape(r['group'][:40])}</td>",
               num(r["overall"]), num(r["fit"]), num(r["composite"]), f"<td>{r['n_inside']}/{r['n_total']}</td>",
               f"<td title='{html.escape(chr(10).join(x for x in [why] + [f['text'] for f in r['flags']] if x))}'>{icons(r['flags'])}"
               f"{' ⛔ ' + html.escape(r['not_scored']) if r.get('not_scored') else ' ❓' if r['low_data'] else ''}</td>"]
        for m in shown_m:
            x = r["metrics"][m["key"]]
            v = x[cell]
            lo, hi = x["range"]
            raw = fmt(x["value"], m["units"]) if x["value"] is not None else x.get("note") or "excluded"
            tip = (f"{m['label']}: {raw}\nrange {fmt(lo, m['units'])} to {fmt(hi, m['units'])}" if lo is not None else f"{m['label']}: {raw}")
            p_txt = "—" if x["pct"] is None else f"{x['pct']:.0f}"
            f_txt = "—" if x["fit"] is None else f"{x['fit']:.0f}"
            tip += f"\npercentile {p_txt} · fit {f_txt}"
            if x.get("note"):
                tip += f"\n{x['note']}"
            tds.append(f"<td style='{_color(v)}' title='{html.escape(tip)}'>{'—' if v is None else format(v, '.0f')}</td>")
        body.append("<tr>" + "".join(tds) + "</tr>")
    st.html(f"""
<style>
.cmpwrap{{max-height:620px;overflow:auto;border:1px solid rgba(128,128,128,.35);border-radius:6px}}
.cmpwrap table{{border-collapse:collapse;font-size:13px;width:100%}}
.cmpwrap th{{position:sticky;top:0;background:rgba(128,128,128,.25);backdrop-filter:blur(6px);padding:4px 6px;text-align:left;white-space:nowrap;z-index:1}}
.cmpwrap td{{padding:3px 6px;border-top:1px solid rgba(128,128,128,.18);white-space:nowrap;text-align:right}}
.cmpwrap td:nth-child(3),.cmpwrap td:nth-child(4),.cmpwrap td:nth-child(5){{text-align:left}}
</style>
<div class="cmpwrap"><table><thead><tr>{th}</tr></thead><tbody>{''.join(body)}</tbody></table></div>""")
    st.caption(f"{len(view)} of {len(rows)} rows · cells coloured red (0) → green (100) · hover a cell for raw value, range and both scores · "
               "❓ = low data (over 30% of weight excluded, listed after fully scored names; hover the flags cell for the missing metrics) · "
               "⛔ = not scored (no 10-K data, e.g. a 20-F / 40-F foreign filer): no rank")

    exp = []
    for r in rows:
        e = {"rank": r["rank"], f"rank_in_{word}": r["group_rank"], "ticker": r["ticker"], "name": r["name"], word: r["group"],
             "overall": r["overall"], "fit": r["fit"], "composite": r["composite"], "inside_range": f"{r['n_inside']}/{r['n_total']}",
             "low_data": r["low_data"], "not_scored": r.get("not_scored"), "low_data_reason": r.get("low_data_reason"),
             "data_as_of": dates.get(r["groups"][0]), "red_flags": " | ".join(f["text"] for f in r["flags"])}
        for m in metrics:
            x = r["metrics"][m["key"]]
            e.update({f"{m['key']}__value": x["value"], f"{m['key']}__percentile_score": x["pct"], f"{m['key']}__fit": x["fit"],
                      f"{m['key']}__range_low": x["range"][0], f"{m['key']}__range_high": x["range"][1], f"{m['key']}__weight": x["weight"]})
        e.update({"setting_mode": mode, "setting_ranges": rmode, "setting_groups": " | ".join(sel)})
        exp.append(e)
    exp_df = pd.DataFrame(exp)
    d1, d2 = st.columns(2)
    d1.download_button("Download comparison (CSV)", exp_df.to_csv(index=False), mime="text/csv",
                       file_name=f"compare_{asset}_{mode}_{datetime.now():%Y%m%d_%H%M}.csv", key=f"cmpbtn|{key}|csv")
    snapshots.button(f"cmp|{key}", asset, f"compare_{mode}_" + "+".join(sel), lambda: exp_df, lambda: {
        "data_date": dates, "mode": mode, "range_source": rtxt, "groups": sel,
        "weights": {k.split("|", 1)[1]: float(st.session_state.get(k, v)) for k, v in weight_defaults.items()},
        "ranges": {g: {k: list(v) for k, v in ({**laura.default_ranges(b), **(laura.laura_ranges(b, asset, metrics) if rmode == "laura" else {})}).items()}
                   for g, b in res["bench"].items()},
        "hard_limits": "not applied", "ignored": ignored, **(meta_extra or {})}, col=d2)
