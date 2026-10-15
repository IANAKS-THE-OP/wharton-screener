"""
Equity-ETF page (etf_scoring.py engine). Called from app.py for the "ETFs" asset type.
"""

import hashlib
import logging
from datetime import datetime

import pandas as pd
import streamlit as st

import compare
import compare_ui
import etf_data as ed
import etf_groups as eg
import etf_holdings as eh
import etf_scoring as es
import laura
import range_ui as ru
import snapshots

log = logging.getLogger(__name__)
PREFIX = "etf"  # this page's own session-key prefix (the Stocks page uses "stk")
FLAG_ICONS = {"closure_risk": "⚰️", "too_new": "🆕", "stale_prices": "🕒", "shorter_history": "🕒", "short_history": "🕒", "concentrated": "🎯",
              "low_lookthrough": "🔍", "hq_unavailable": "🔍", "expense_missing": "❔",
              "category_fallback": "📏"}


def fmt(v, units):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    if units == "%":
        return f"{v:.3f}%" if abs(v) < 1 else f"{v:.2f}%"
    if units == "$M":
        return f"${v:,.0f}M"
    if units == "/100":
        return f"{v:.1f}"
    return f"{v:,.0f}"


def _icons(flags):
    return "".join(dict.fromkeys(FLAG_ICONS.get(f["code"].split(":")[0], "⚑") for f in flags))


def render():
    universe = eg.load_universe()
    if universe is None:
        st.error("ETF universe not built yet. Run `./venv/bin/python3 etf_groups.py` (seconds, no download), then reload.")
        st.stop()
    overrides = eg.load_overrides()
    mem = eg.membership(universe, overrides)
    counts = mem["group"].value_counts().to_dict()
    cached_t = ed.load_all_tickers()
    got = mem[mem["ticker"].isin(cached_t)]["group"].value_counts().to_dict()
    themes = [g for g in eg.THEME_GROUPS if counts.get(g)]
    cats = [g for g in counts if g not in eg.THEME_GROUPS]
    options = eg.morningstar_order(cats, counts, ed.category_group) + themes

    def _lbl(g):
        n, d = counts.get(g, 0), got.get(g, 0)
        return f"{g}  ({n} ETF{'s' if n != 1 else ''}" + ("" if d == n else f", {d} downloaded") + ")"

    sel_key = "etf2_mscat"
    if st.session_state.get(sel_key) not in options:
        st.session_state[sel_key] = "Large Blend" if "Large Blend" in options else next((g for g in options if got.get(g)), options[0])
    group = st.sidebar.selectbox(f"Morningstar category or theme ({len(cats)} categories + {len(themes)} themes - click and type to search)",
                                 options, key=sel_key, format_func=_lbl,
                                 help="Peer group = the funds in that Morningstar category (yfinance), or in a theme found in fund names "
                                      "(ESG, low volatility, dividend / income, quality, equal weight). A fund is in its category AND every theme it matches.")
    ns = eg.ns(group)
    st.sidebar.caption("Themes (ESG, low volatility, dividend, quality, equal weight) are detected from fund names.")
    _lookup(universe, cached_t, mem)
    with st.sidebar.expander("ETF scoring inputs", expanded=False):
        tbill = st.number_input("T-bill rate (downside-deviation MAR, %)", 0.0, 15.0, 3.0, 0.25, key="etf2_tbill") / 100
        window = st.radio("History window (years)", [5, 10], index=0, horizontal=True, key="etf2_window",
                          help="Downside deviation, max drawdown and long-run return are measured for EVERY fund over the same window "
                               "ending on the same date. A fund with less history is 'too new for this window': those three metrics are "
                               "excluded and it is listed after the fully scored funds. 5 years (default) lets most funds be compared.")
    settings = {"tbill": tbill, "window": window}
    st.sidebar.caption("Equity ETFs only. Bond, commodity, leveraged/inverse, VIX/volatility products, currency and crypto funds are never shown "
                       "(low-volatility EQUITY funds are kept).")

    st.title(f"ETFs: {group}" + ("" if group in eg.THEME_GROUPS else "  ·  Morningstar"))

    # ------------------------------------------------------------------ load / pull
    members = mem[mem["group"] == group]
    tickers = list(members["ticker"])
    have = ed.load_funds(tickers)
    missing = [t for t in tickers if t not in have]
    c1, c2 = st.columns([1, 3])
    if len(tickers) > 150 and missing:
        c2.warning(f"{len(tickers)} funds in this group; {len(missing)} still to download (roughly {max(1, len(missing) // 60)} min).")
    def _pull():
        slot = st.empty()
        bar = slot.progress(0, text=f"Downloading {len(tickers) if not missing else len(missing)} funds...")
        try:
            ed.fetch_funds(tickers if not missing else missing, progress_callback=lambda f, m: bar.progress(min(float(f), 1.0), text=m))
        except Exception as e:  # yfinance network errors (e.g. curl SSL errors) must not crash the page
            log.exception("ETF pull failed for %s", group)
            st.session_state["etf2_err"] = (group, f"{type(e).__name__}: {e}")
        else:
            st.session_state.pop("etf2_err", None)
        slot.empty()
        st.rerun()

    if c1.button("Pull live ETF data" if missing else "Refresh ETF data", type="primary", key="etf2_pull"):
        _pull()
    err = st.session_state.get("etf2_err")
    if err and err[0] == group:
        st.error(f"Download failed (network / data source error): {err[1][:300]}. Funds already downloaded are still used.")
        if st.button("Retry", key="etf2_retry"):
            _pull()
    # peers = the funds in this category/theme (the raw's category is overwritten with the group name)
    raws = eg.group_raws(tickers, have, group)
    if missing:
        c2.info(f"{len(missing)} of {len(tickers)} funds here aren't downloaded yet. Click **Pull live ETF data**.")
    else:
        c2.caption(f"**{len(tickers)} ETFs in this group, all downloaded.** Grouped by: "
                   + ", ".join(f"{n} by {how}" for how, n in members["basis"].value_counts().items()))

    # --------------------------------------------------------------- holdings scoring
    if eh.invalidate_if_stale():
        st.info("The stock model's weights/settings changed, so the cached stock scores behind **holdings quality** were cleared. "
                "Click **Score holdings quality** to rebuild them from the cached stock data (no download needed for industries already pulled).")
    hq_scores, hq_skips = eh.load_scores()
    syms = list(dict.fromkeys(h["symbol"] for r in raws for h in sorted(r["holdings"], key=lambda h: -h["weight"])[:10]))
    todo = [s for s in syms if s not in hq_scores and s not in hq_skips and not eh.is_foreign_symbol(s)]
    if raws:
        h1, h2 = st.columns([1, 3])
        label = "Score holdings quality" if todo else "Holdings scored ✓"
        if h1.button(label, key="etf2_hq", disabled=not todo, help="Scores each top-10 holding with the stock model (first run pulls whole industries)."):
            slot = st.empty()
            bar = slot.progress(0, text="Scoring holdings with the stock model...")
            try:
                eh.score_holdings(syms, progress=lambda f, m: bar.progress(min(float(f), 1.0), text=m))
            except Exception as e:  # network errors while pulling a holding's industry
                log.exception("holdings scoring failed")
                st.session_state["etf2_hq_err"] = f"{type(e).__name__}: {e}"
            else:
                st.session_state.pop("etf2_hq_err", None)
            slot.empty()
            st.rerun()
        if st.session_state.get("etf2_hq_err"):
            st.error(f"Scoring holdings failed part-way ({st.session_state['etf2_hq_err'][:300]}). Scores saved so far are kept; "
                     "click **Score holdings quality** again to retry.")
        scored_n = sum(1 for s in syms if s in hq_scores)
        h2.caption(f"Top-10 holdings across these funds: **{len(syms)} unique** · scored **{scored_n}** · skipped **{sum(1 for s in syms if s in hq_skips or eh.is_foreign_symbol(s))}** "
                   f"(foreign / outside the tradeable stock universe) · pending **{len(todo)}**. Holdings quality is excluded for a fund until its holdings are scored.")

    tab_res, tab_tune, tab_cmp, tab_dir, tab_how = st.tabs(["Ranked results", "Tune scoring", "Compare groups", "Directory", "How scoring works"])
    with tab_dir:
        _morningstar_directory(mem, cached_t)
    with tab_cmp:  # drawn BEFORE the Tune widgets so its Laura button can reset the weights in this run
        _compare_tab(mem, got, counts, settings, hq_scores, hq_skips)
    if not raws:
        with tab_how:
            _how_tab(None, None)
        return

    # prep depends on data + settings only (never on weights or ranges); hash EVERY fund's fetch date, not just the first few
    data_sig = hashlib.sha1(repr(sorted((r["ticker"], r["fetched"]) for r in raws)).encode()).hexdigest()
    sig = (es.PREP_VERSION, ns, data_sig, len(raws), tbill, window, len(hq_scores), len(hq_skips))
    if st.session_state.get("etf2_prep_key") != sig:
        st.session_state["etf2_prep"] = es.prepare_funds(raws, settings, hq_scores, hq_skips, all_raws=ed.load_all)
        st.session_state["etf2_prep_key"] = sig
    prep = st.session_state["etf2_prep"]

    # --------------------------------------------------------------------------- Tune
    weights, hard, ranges_by_cat = {}, {}, {}
    with tab_tune:
        wdef = {f"etf2w|{m['key']}": float(m["weight"]) for m in es.METRICS if m["key"] != "n_holdings"}
        ru.seed({"etf2_blend": 50, **wdef})  # Laura weights are the default
        t1, t2, t3 = st.columns([2, 1, 1])
        blend = t1.slider("Blend: Overall = b × Fit + (1 − b) × Composite", 0, 100, step=5, key="etf2_blend") / 100
        bench_cat = prep["bench"][group]
        linfo = laura.laura_info(bench_cat, "etf", es.METRICS)
        ru.buttons((t2, t3), PREFIX, ns, bench_cat, wdef)
        ru.seed_laura(PREFIX, ns, bench_cat, linfo)  # Laura ranges are the default (only where nothing is set yet)
        total = sum(st.session_state[k] for k in wdef)  # number of holdings is always 0
        (st.success if abs(total - 100) < 1e-9 else st.warning)(
            f"Weights total **{total:g}**" + ("" if abs(total - 100) < 1e-9 else " (not 100 - scores still calculate; the formula normalizes)")
            + " · defaults = Laura Gao's standards (cost and drawdown first)")
        st.caption("Weights affect both Composite and Fit. Comfort ranges affect only Fit. Ranges are in each metric's own units (AUM and dollar volume in $ millions). "
                   "The two slider handles are the **good bound** (Fit 100 at or beyond it) and the **bad bound** (Fit 80); Fit falls linearly between "
                   "them and keeps falling past the bad bound (80 − 50 × distance ÷ spread). **Laura standards** (default): good bound = this group's "
                   "best decile (10th percentile for lower-is-better, 90th for higher-is-better); bad bound = the group median for cost, risk, "
                   "concentration and size, the 40th percentile for holdings quality, the 25th for dividend yield and long-run return.")
        hl = laura.LAURA_HARD_LIMITS
        laura_hard = st.checkbox("Laura hard limits: expense ≤ 0.20%, max drawdown ≤ 30%, AUM ≥ $1B, top-10 weight ≤ 40%",
                                 key="etf2_laura_hard", help="Off by default. When on, those four metrics become 'Must be inside' limits with "
                                 "these absolute values (funds outside are removed from the list; never an empty page).")

        for gname in es.GROUPS:
            with st.expander(gname, expanded=False):
                for m in [x for x in es.METRICS if x["group"] == gname]:
                    key = m["key"]
                    st.markdown(f"**{m['label']}** ({m['units'] or 'count'}) · {m['direction']} is better")
                    b = bench_cat[key]
                    if b["q"]:
                        q = b["q"]
                        st.caption(f"Group 25th / median / 75th: {fmt(q['p25'], m['units'])} / {fmt(q['median'], m['units'])} / "
                                   f"{fmt(q['p75'], m['units'])} · {b['pool_n']} peers"
                                   + ("" if b["level"] == "category" else f" · ⚠ all-equity-ETF fallback pool ({b['category_n']} in this group)"))
                        if key != "n_holdings":
                            ru.laura_caption(linfo.get(key), m["direction"], lambda v, u=m["units"]: fmt(v, u))
                        ranges_by_cat.setdefault(group, {})[key] = ru.range_widget(PREFIX, ns, key, b, fallback=(linfo.get(key) or {}).get("range"))
                        if laura_hard and key in hl:
                            lo_, hi_ = hl[key]
                            st.checkbox(f"Must be inside: Laura hard limit {'≤ ' + fmt(hi_, m['units']) if hi_ is not None else '≥ ' + fmt(lo_, m['units'])}",
                                        value=True, disabled=True, key=f"etf2hl|{ns}|{key}")
                            hard[key] = True
                        else:
                            hard[key] = st.checkbox("Must be inside range", key=f"etf2h|{ns}|{key}")
                    else:
                        st.caption("No valid values in this group - metric excluded for everyone.")
                    if key == "n_holdings":
                        weights[key] = 0.0
                        st.caption("**Always excluded** (weight 0): yfinance doesn't provide a holdings count.")
                    else:
                        weights[key] = st.slider("Weight", 0.0, 30.0, step=0.5, key=f"etf2w|{key}")
                    st.divider()
        for m in es.METRICS:
            weights.setdefault(m["key"], st.session_state.get(f"etf2w|{m['key']}", 0.0 if m["key"] == "n_holdings" else m["weight"]))

    final = es.finalize(prep, weights=weights, ranges_by_cat=ranges_by_cat, blend=blend, hard_limits=hard,
                        hard_ranges={k: v for k, v in laura.LAURA_HARD_LIMITS.items() if laura_hard})
    results = final["results"]

    # ------------------------------------------------------------------------ Results
    with tab_res:
        display = results
        if any(hard.values()):
            passing = [r for r in results if not r["hard_limit_failed"]]
            if not passing:
                bad = sorted({es.METRIC_BY_KEY[k]["label"] for r in results for k in r["hard_limit_failures"]})
                st.warning(f"Your hard limit(s) on **{', '.join(bad)}** would remove all {len(results)} funds - showing the full ranked list anyway.")
            else:
                if len(results) - len(passing):
                    st.info(f"{len(results) - len(passing)} removed by hard limits · {len(passing)} remain.")
                display = es.rank([dict(r) for r in passing])
        data_date = compare.data_date(raws)
        st.caption(f"**Data as of {data_date}** (fund downloads, cached) · weights and ranges from Tune scoring · blend {blend:.0%}"
                   + (" · Must-be-inside / Laura hard limits on" if any(hard.values()) else ""))
        is_theme = group in eg.THEME_GROUPS
        why_in = dict(zip(members["ticker"], members["matched_by"])) if is_theme else {}
        rows = [{
            "rank": r["rank"], "tier": r.get("tier_label"), "ticker": r["ticker"], "fund": r["name"], "category": r["category"],
            **({"theme matched by": why_in.get(r["ticker"], "")} if is_theme else {}),
            "overall": r["overall"], "fit": r["fit"],
            "composite": r["composite"], "inside range": f"{r['n_inside']}/{r['n_total']}",
            "why not higher": r.get("why_not_higher") or "",
            "flags": _icons(r["flags"]) + (" ❓low data" if r["low_data"] else "") + (" 🆕too new" if r.get("too_new") else ""),
            "why low data / too new": r["low_data_reason"] or r.get("too_new_reason") or "",
            "coverage": r["coverage"] * 100,
            "flag details": " | ".join(f["text"] for f in r["flags"]),
        } for r in display]
        bar = lambda: st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f")
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", column_config={
            "overall": bar(), "fit": bar(), "composite": bar(), "coverage": st.column_config.NumberColumn("data coverage %", format="%.0f"),
            "flags": st.column_config.TextColumn(help="Key below the table. Full text in 'flag details'."),
            "tier": st.column_config.TextColumn(help="Funds within 1.0 Overall point of the tier's first fund share a tier: treat them as tied."),
            "why not higher": st.column_config.TextColumn(help="Metrics outside their comfort range (beyond the bad bound) and the Overall points "
                                                               "each costs: blend × weight × (100 − Fit) ÷ total weight."),
        })
        n_new = sum(bool(r.get("too_new")) for r in display)
        fits = [r["fit"] for r in display if r["fit"] is not None]
        st.caption(f"**{len({r.get('tier') for r in display})} tiers** for {len(display)} funds · Fit ≥ 97: **{sum(f >= 97 for f in fits)}** of {len(fits)} · "
                   f"{n_new} too new for the {window}-yr window (listed after the fully scored funds)")
        _flag_key(FLAG_KEY)
        _history_diagnostic(raws, settings, hq_scores, hq_skips, weights, blend, group)
        export = es.export_rows(final, settings, group)
        if is_theme:
            export.insert(6, "theme_matched_by", export["ticker"].map(why_in))
        d1, d2 = st.columns(2)
        d1.download_button("Download full export (CSV)", export.to_csv(index=False),
                           file_name=f"etf_score_{group}_{datetime.now():%Y%m%d_%H%M}.csv".replace(" ", "_").replace("★", ""), mime="text/csv")
        shown = {r["ticker"] for r in display}
        snapshots.button(f"etf|{ns}", "etf", f"Morningstar {group}", lambda: export[export["ticker"].isin(shown)],
                         lambda: {"data_date": data_date, "grouping": "Morningstar category + themes", "group": group, "settings": settings, "blend": blend,
                                  "weights": weights, "ranges": {k: list(v) for k, v in ranges_by_cat.get(group, {}).items()},
                                  "range_source": "Tune scoring (Laura standards unless hand-tuned)",
                                  "hard_limits": [k for k, v in hard.items() if v], "laura_hard_limits": bool(laura_hard),
                                  "theme_overrides": overrides.to_dict("records") if is_theme else None, "rows_shown": len(display)}, col=d2)
        if is_theme:
            _overrides_box(universe, overrides, group)

        st.subheader("Breakdown")
        pick = st.selectbox("Fund", [r["ticker"] for r in display], key=f"etf2_pick|{ns}",
                            format_func=lambda t: f"{t} - {next(r['name'] for r in display if r['ticker'] == t)}")
        chosen = next(r for r in display if r["ticker"] == pick)
        _breakdown(chosen, prep["bench"][chosen["category"]])

    with tab_how:
        _how_tab(chosen, final)


def _lookup(universe, cached_t, mem):
    """Sidebar lookup: a fund's Morningstar category and its themes (with the keyword that matched, after the team overrides)."""
    with st.sidebar.expander("Find a fund", expanded=False):
        q = st.text_input("Ticker or name", key="etf2_lookup", placeholder="e.g. ESGV, USMV, min vol").strip()
        if not q:
            return
        hit = universe[(universe["ticker"].str.upper() == q.upper())]
        if hit.empty:
            hit = universe[universe["name"].str.contains(q, case=False, regex=False, na=False)].head(12)
        if hit.empty:
            st.caption("Not in the equity-ETF universe (bond, leveraged/inverse, commodity, currency, VIX/volatility and crypto "
                       "funds are excluded, as are funds under $5 and single-stock funds).")
            return
        themes = mem[mem["group"].isin(eg.THEME_GROUPS)]
        for t, n, cat in zip(hit["ticker"], hit["name"], hit["morningstar_category"]):
            mine = themes[themes["ticker"] == t]
            th = ", ".join(f"{x.replace(eg.THEME_PREFIX, '')} (matched “{kw}”)" for x, kw in zip(mine["group"], mine["matched_by"])) or "none"
            st.markdown(f"**{t}** · {n}  \nMorningstar: *{cat}* · themes: *{th}* · "
                        + ("downloaded" if t in cached_t else "not downloaded yet"))


def _overrides_box(universe, overrides, group):
    """Lists the team's theme overrides (results/theme_overrides.csv) and what each did."""
    rep = eg.override_report(universe, overrides)
    with st.expander(f"Theme overrides ({len(rep)} in {eg.OVERRIDES_PATH})", expanded=bool(len(rep))):
        st.caption("Themes come from fund names. The team can correct them in `results/theme_overrides.csv` with columns "
                   "`ticker,theme,action` (action = add or remove; theme = e.g. `ESG / sustainable`). Overrides are applied after "
                   "the name matching. On the hosted site, edit the file locally and push it (cloud edits don't persist).")
        if rep.empty:
            st.caption("No overrides yet.")
        else:
            st.dataframe(rep.assign(**{"this theme": rep["theme"] == group}), hide_index=True, width="stretch")


def _morningstar_directory(mem, cached_t):
    st.caption("Every Morningstar category (and theme) with its equity ETFs. Categories come from yfinance; themes from fund names.")
    rows = []
    for g, x in mem.groupby("group"):
        rows.append({"category / theme": g, "type": "theme" if g in eg.THEME_GROUPS else ed.category_group(g), "etfs": len(x),
                     "downloaded": int(x["ticker"].isin(cached_t).sum()), "example funds": ", ".join(x["ticker"].head(8))})
    t = pd.DataFrame(rows).sort_values(["type", "etfs"], ascending=[True, False])
    st.dataframe(t, hide_index=True, width="stretch")
    st.download_button("Download every ETF with its category and themes (CSV)", mem.to_csv(index=False),
                       file_name="etf_morningstar_map.csv", mime="text/csv", key="etf2_ms_dl")


def _compare_tab(mem, got, counts, settings, hq_scores, hq_skips):
    """'Compare groups' for ETFs: any number of Morningstar categories and themes."""
    options = [g for g in sorted(got) if got.get(g)]
    weights = {m["key"]: (0.0 if m["key"] == "n_holdings" else float(st.session_state.get(f"etf2w|{m['key']}", m["weight"]))) for m in es.METRICS}
    blend = st.session_state.get("etf2_blend", 50) / 100
    cache = st.session_state.setdefault("_cmpcache_etf", {})
    if len(cache) > 80:
        cache.clear()

    def run(sel, cmode, rmode):
        tick = {g: list(mem.loc[mem["group"] == g, "ticker"]) for g in sel}
        have = ed.load_funds(sorted({t for ts in tick.values() for t in ts}))
        by = {g: eg.group_raws(ts, have, g) for g, ts in tick.items()}
        stamps = {g: hashlib.sha1(repr(sorted((r["ticker"], r["fetched"]) for r in raws)).encode()).hexdigest() for g, raws in by.items()}
        return compare.etf_compare(by, settings, weights, blend, hq_scores, hq_skips, cmode, rmode,
                                   all_raws=ed.load_all, cache=cache, stamps=stamps)

    compare_ui.render(key="etfcmp_|morningstar", asset="etf", metrics=[m for m in es.METRICS if m["key"] != "n_holdings"],
                      options=options, counts={g: f"{got.get(g, 0)} of {counts.get(g, 0)} downloaded" for g in options}, run=run,
                      weight_defaults={f"etf2w|{m['key']}": float(m["weight"]) for m in es.METRICS if m["key"] != "n_holdings"},
                      fmt=fmt, icons=_icons, word="group", name_word="fund",
                      page_prefix=PREFIX, ns_fn=eg.ns,
                      extra_note="Groups = Morningstar categories and themes (ESG, low volatility, dividend, quality, equal weight).", hard_prefix="etf2h|",
                      global_hard="Laura hard limits (expense ≤ 0.20%, max drawdown ≤ 30%, AUM ≥ $1B, top-10 ≤ 40%)" if st.session_state.get("etf2_laura_hard") else None,
                      meta_extra={"settings": settings, "blend": blend, "grouping": "Morningstar category + themes"})


FLAG_KEY = [("⚰️", "Closure risk", "AUM under $100M"),
            ("🆕", "Too new for this window", "less price history than the window: downside deviation, max drawdown and long-run return excluded; listed after fully scored funds"),
            ("🕒", "Stale prices", "prices end more than 7 days before the rest of the group: history metrics excluded"),
            ("🎯", "Concentrated", "top-10 holdings are more than 50% of the fund"),
            ("🔍", "Low / no look-through", "under 25% of the fund's holdings could be scored for holdings quality"),
            ("❔", "Expense missing", "no expense ratio reported"),
            ("📏", "Group fallback", "fewer than 10 funds in the group for a metric, ranked against all equity ETFs"),
            ("❓", "Low data", "more than 30% of the weight excluded; listed last (the 'why low data' column lists the missing metrics)"),
            ("⚑", "Other", "any other note; read the 'flag details' column")]


def _flag_key(rows):
    """Legend for the red-flag icons, shown under the ranked table. Flags are shown, never scored."""
    st.markdown("**Red-flag key** (shown, never scored)")
    st.markdown("  \n".join(f"{icon} **{name}**: {what}" for icon, name, what in rows))


def _breakdown(r, bench):
    if r["low_data"]:
        st.warning("**Low data** (listed after every fully scored fund): " + r["low_data_reason"])
    if r.get("too_new"):
        st.warning("🆕 **" + r["too_new_reason"] + "**. Listed after every fully scored fund; switch the history window to 5 years "
                   "under *ETF scoring inputs* if it has 5+ years.")
    if None not in (r["overall"], r["fit"], r["composite"]):
        st.markdown(f"**Overall {r['overall']:.1f}** ({r.get('tier_label')}) · Fit {r['fit']:.1f} · Composite {r['composite']:.1f} · "
                    f"{r['n_inside']}/{r['n_total']} inside range · coverage {r['coverage']:.0%} · category **{r['category']}**")
        st.markdown(f"**Why not higher:** {r.get('why_not_higher')}")
    h = r["history"]
    st.caption(f"History available {h['years_available']:.1f} yrs ({h.get('start')} to {h.get('end')}) · window {h['window']} yrs, the same for every fund · used "
               + (f"{h['used']:.1f} yrs" if h["used"] else "none (too new for this window)"))
    hq = r["metrics"]["holdings_quality"]
    if hq.get("weight_factor") is not None and hq["pct"] is not None:
        st.caption(f"🔍 Holdings quality covers only {r['hq_coverage']:.0%} of the fund, so its weight is reduced "
                   f"{hq['weight_nominal']:g} → {hq['weight']:.2f} (weight × coverage, applied under {es.HQ_FULL_WEIGHT_COVERAGE:.0%} coverage).")
    rows = []
    for m in es.METRICS:
        x, b = r["metrics"][m["key"]], bench[m["key"]]
        q = b["q"] or {}
        rows.append({
            "metric": f"{m['label']} ({m['units'] or 'count'})", "value": fmt(x["value"], m["units"]) if x["value"] is not None else "excluded",
            "better": m["direction"], "category 10th": q.get("p10"), "25th": q.get("p25"), "median": q.get("median"), "75th": q.get("p75"),
            "90th": q.get("p90"), "good bound (Fit 100)": x["good"], "bad bound (Fit 80)": x["bad"], "distance past bad bound": x["distance"],
            "fit": x["fit"], "percentile score": x["pct"],
            "weight": x["weight"] if abs(x["weight"] - x["weight_nominal"]) < 1e-9 else f"{x['weight']:.2f} (of {x['weight_nominal']:g}: coverage {r['hq_coverage']:.0%})",
            "contribution": x["contribution"], "note": x["note"],
        })
    st.dataframe(pd.DataFrame(rows).astype({"weight": str}), hide_index=True, width="stretch")

    st.markdown(f"**Top-10 holdings** (look-through coverage {r['hq_coverage']:.0%} of the fund)")
    hold = pd.DataFrame([{"symbol": x["symbol"], "holding": x["name"], "weight %": x["weight"] * 100, "industry": x["industry"],
                          "stock Composite": x["composite"], "status": x["note"] or "scored"} for x in r["holdings"]])
    if hold.empty:
        st.caption("No holdings data from yfinance for this fund.")
    else:
        st.dataframe(hold, hide_index=True, width="stretch", column_config={
            "weight %": st.column_config.NumberColumn(format="%.2f"), "stock Composite": st.column_config.NumberColumn(format="%.1f")})
    st.markdown("**Red flags**")
    if r["flags"] or r["low_data"]:
        for f in r["flags"]:
            st.markdown(f"- {_icons([f])} {f['text']}")
        if r["low_data"]:
            st.markdown(f"- ❓ Low data: {r['excluded_weight_share']:.0%} of the weight is excluded (> 30%)")
    else:
        st.caption("None triggered.")


def _history_diagnostic(raws, settings, hq_scores, hq_skips, weights, blend, group):
    """Old (legacy, 10-yr: funds with 5-10 yrs used whatever they had) vs new (common window) rule on this group."""
    with st.expander("History-window diagnostic (old rule vs common window)", expanded=False):
        if not st.toggle("Run it for this group", key=f"etf2_diag|{group}"):
            st.caption("Re-scores this group under the old history rule to show the bias the common window removes.")
            return
        d = es.history_diagnostic(raws, settings, hq_scores, hq_skips, weights, blend, all_raws=ed.load_all)
        st.markdown(f"Old = previous rule at the old default **10-yr** window (a fund with 5–10 yrs used whatever it had, e.g. 6.5 yrs from "
                    f"April 2020, after the crash). New = every fund over the same **{d['window_new']}-yr** window. Same weights, blend, "
                    f"graded Fit and Laura ranges, so only the history rule differs. *Short history* = under 10 years of prices.")
        st.dataframe(pd.DataFrame([{"": f"top {n}", "short-history funds (old)": d[f"top{n}"]["old_short"],
                                    "short-history funds (new)": d[f"top{n}"]["new_short"],
                                    "measured over a shorter window (old)": d[f"top{n}"]["old_window_shrunk"],
                                    "measured over a shorter window (new)": d[f"top{n}"]["new_window_shrunk"],
                                    "too new (new)": d[f"top{n}"]["new_too_new"]} for n in (10, 30)]), hide_index=True, width="stretch")
        st.markdown(f"**{d['n_moved']} of {d['n']} funds move more than 10 ranks** · {d['n_too_new']} too new for the "
                    f"{d['window_new']}-yr window · Fit ≥ 97 under the new rule: {d['fit97_new']}")
        if d["moved"]:
            st.dataframe(pd.DataFrame(d["moved"], columns=["ticker", "old rank", "new rank"]), hide_index=True, width="stretch")

def _how_tab(chosen, final):
    st.markdown("""
### Same framework as the stocks, ten ETF metrics
Each fund is compared with the other funds in its **peer group**: its **Morningstar category** (yfinance: Large Blend, Large Value, Technology...) or a **theme** found in fund names (ESG, low volatility, dividend / income, quality, equal weight), whichever the sidebar picker selects. A fund is in its category AND in every theme it matches (USMV: Large Blend + low volatility). There is no industry grouping for ETFs; stock industries are used only inside **holdings quality**. Two 0–100 scores per metric, where **100 is best**:

* **Composite (percentile).** Rank the fund among peers with a valid value: sort ascending, ties share the average rank, **p = (rank − 1) ÷ (n − 1) × 100** (n = 1 → 50). Higher-is-better metrics score p; lower-is-better (expense ratio, downside deviation, max drawdown, top-10 weight) score 100 − p. If a group has fewer than 10 valid values for a metric, that metric is ranked against **all scored equity ETFs** and flagged.
* **Fit (comfort range, graded).** A range from a **good bound** to a **bad bound** in the metric's units (the two slider handles). At or beyond the good bound → **100**; inside the range Fit falls linearly from 100 at the good bound to **80** at the bad bound; beyond the bad bound **Fit = max(0, 80 − 50 × distance ÷ spread)**, spread = group 75th − 25th (moving a bound never changes the spread). So "lowest possible" is rewarded: a fund at the group's best decile beats one just inside the bad bound. **Default ranges = Laura standards, per group**: good bound = the group's **10th percentile** for lower-is-better metrics, **90th** for higher-is-better; bad bound = the group **median** for expense ratio, downside deviation, max drawdown, top-10 weight, AUM and dollar volume, the **40th percentile** for holdings quality, the **25th** for dividend yield and long-run return. *Industry average* sets every range to the 25th–75th (good = the better quartile). These are Fit-only; the optional **Laura hard limits** checkbox (off by default) adds absolute cut-offs: expense ≤ 0.20%, max drawdown ≤ 30%, AUM ≥ $1B, top-10 weight ≤ 40%.

Each is a **weighted average** over the metrics the fund has (default weights are Laura Gao's standards, total 100; excluded metrics drop out and the denominator shrinks). **Overall = b × Fit + (1 − b) × Composite.** Weights change both; ranges change only Fit.

### The ten metrics
Expense ratio **18** · max drawdown **18** · AUM **12** · top-10 weight **12** · downside deviation **12** · long-run return **10** · holdings quality **8** · dollar volume **5** · dividend yield **5** · number of holdings **0** (always excluded). Why: Laura is a moderate-risk client who needs no big drops and steady growth, so cost and drawdown come first.

**Holdings quality** = Σ(weight × stock Composite) ÷ Σ(weights of scored holdings) over the fund's top-10 holdings, where each holding is scored by the stock model against **its own stock industry**. Foreign/unlisted holdings are skipped. Under 15% coverage it is excluded and flagged. Between 15% and 50% coverage its **weight is scaled by coverage** (weight × coverage ÷ 100: USMV at 15% → 8 × 0.15 = 1.2), shown in the Breakdown.

**History window** (5 years by default, or 10) drives downside deviation, max drawdown and long-run return. Every fund is measured over the **same window ending on the same date** (7 days of slack for listing-date rounding); the window is never shortened for a fund. A fund with less history than the window is **too new for this window**: those three metrics are excluded and it is listed after every fully scored fund. A fund whose prices stop more than 7 days before the rest of its group is excluded the same way. The *History-window diagnostic* under the ranked table shows how the old rule (a fund with 5–10 years used whatever it had) changed the top 10 / top 30.

**Number of holdings** is not provided by yfinance, so it is excluded for every fund unless you supply `results/etf_holdings_override.csv` (columns `ticker,n_holdings`).

### Final order, tiers, why not higher
Fully scored funds first, then **too new for this window**, then **low data** (> 30% of the weight excluded, not counting the history metrics of a too-new fund). Within each: Overall ↓, then Composite ↓, fewer red flags, less excluded weight, ticker A–Z. Red flags are shown, never scored.
**Tier**: funds within **1.0 Overall point** of their tier's first fund share a tier ("Tier 3: ranks 10–15"); treat them as tied. **Why not higher** lists each metric beyond its bad bound with the Overall points it costs: b × weight × (100 − Fit) ÷ total weight.
The CSV export has, for every metric, the raw value, percentile score, Laura good / bad bounds, the bounds actually used, Fit and weight (effective and nominal), plus history years used and holdings-quality coverage.
""")
    if chosen and chosen["overall"] is not None:
        st.markdown(f"### Worked example: {chosen['ticker']} ({chosen['name']})")
        rows = [(m, chosen["metrics"][m["key"]]) for m in es.METRICS]
        pick = next(((m, x) for m, x in rows if x["pct"] is not None and x["fit"] is not None and x["fit"] < 100), None) \
            or next(((m, x) for m, x in rows if x["pct"] is not None), None)
        if pick:
            m, x = pick
            st.markdown(f"**{m['label']}**: value **{fmt(x['value'], m['units'])}**, good bound **{x['good']:.4g}** (Fit 100), bad bound **{x['bad']:.4g}** (Fit 80), "
                        f"distance past the bad bound **{x['distance']:.4g}** → Fit **{x['fit']:.1f}**. Percentile score among peers: **{x['pct']:.1f}**. Weight **{x['weight']:g}**.")
        den = sum(x["weight"] for _, x in rows if x["pct"] is not None)
        num = sum(x["weight"] * x["pct"] for _, x in rows if x["pct"] is not None)
        st.markdown(f"**Composite** = {num:,.1f} ÷ {den:g} = **{chosen['composite']:.2f}**. "
                    f"**Overall** = {final['blend']:.0%} × Fit {chosen['fit']:.2f} + {1 - final['blend']:.0%} × Composite {chosen['composite']:.2f} = **{chosen['overall']:.2f}**.")
    with st.expander("Self-tests (debug)"):
        tests = es.run_self_tests() + eg.run_self_tests() + compare.run_self_tests() + snapshots.run_self_tests()
        st.markdown(f"**{sum(t['passed'] for t in tests)} / {len(tests)} passed**")
        st.dataframe(pd.DataFrame([{"result": "PASS" if t["passed"] else "FAIL", "test": t["test"], "detail": t["detail"]} for t in tests]),
                     hide_index=True, width="stretch")
