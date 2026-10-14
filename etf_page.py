"""
Equity-ETF page (etf_scoring.py engine). Called from app.py for the "ETFs" asset type.
"""

import hashlib
import logging
from datetime import datetime

import pandas as pd
import streamlit as st

import etf_data as ed
import etf_holdings as eh
import etf_scoring as es
import laura
import range_ui as ru

log = logging.getLogger(__name__)
PREFIX = "etf"  # this page's own session-key prefix (the Stocks page uses "stk")
FLAG_ICONS = {"closure_risk": "⚰️", "shorter_history": "🕒", "short_history": "🕒", "concentrated": "🎯",
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


def render(directory_fn):
    import etf_industry as ei
    imap = ei.load_map()
    if imap is None:
        st.error("ETF industry map not built yet. Run `./venv/bin/python3 etf_industry.py` (about a minute), then reload.")
        st.stop()
    try:
        sector_of = dict(zip(*pd.read_csv(ei.DIRECTORY_PATH)[["industry", "sector"]].values.T))
    except (OSError, KeyError, ValueError, pd.errors.ParserError) as e:
        log.warning("could not read %s: %s", ei.DIRECTORY_PATH, e)
        st.warning(f"Couldn't read the industry directory ({ei.DIRECTORY_PATH}: {e}). Industry/sector labels may be incomplete; "
                   "rebuild it with `./venv/bin/python3 build_industry_directory.py`.")
        sector_of = {}
    counts = imap["group"].value_counts().to_dict()
    cached_t = {t for t in ed.load_all_tickers()}
    # same list as the Stocks page (all 145 industries, with the ETF count) + a few broad entries at the end
    import industry_universe as iu
    industries = [i for i in iu.VALID_INDUSTRIES]
    broad = [g for g in counts if g not in sector_of]
    wholes = sorted(g for g in broad if g.startswith(ei.WHOLE))
    tail = wholes + [g for g in (ei.OTHER, ei.BROAD, ei.INTL) if g in counts]
    options = industries + tail

    def _lbl(g):
        n = counts.get(g, 0)
        return f"{g}  ({n} ETF{'s' if n != 1 else ''})" if n else f"{g}  (no ETFs)"

    ru.seed({"etf2_only_with": True})
    only_with = st.sidebar.checkbox("Hide industries with no ETFs", key="etf2_only_with")
    shown = [g for g in options if counts.get(g, 0) > 0] if only_with else options
    group = st.sidebar.selectbox(
        f"Industry ({len([g for g in industries if counts.get(g, 0)])} of {len(industries)} have ETFs - click and type to search)",
        shown, key="etf2_industry", format_func=_lbl,
        help="The same 145 industries as the Stocks page. Sector funds are placed by a clear industry word in their name (checked against their top-10 holdings) or when 80%+ of the top 10 is in one industry; "
             "other funds need 60%+ of the top 10 in the industry AND a matching name. Diversified funds are under the entries at the end of the list.")
    st.sidebar.caption("Funds that hold many industries are listed under *Whole sector*, *Broad US market* or *International*.")
    with st.sidebar.expander("ETF scoring inputs", expanded=False):
        tbill = st.number_input("T-bill rate (downside-deviation MAR, %)", 0.0, 15.0, 3.0, 0.25, key="etf2_tbill") / 100
        window = st.radio("History window (years)", [5, 10], index=1, horizontal=True, key="etf2_window")
    settings = {"tbill": tbill, "window": window}
    st.sidebar.caption("Equity ETFs only. Bond, commodity, leveraged/inverse, volatility, currency and crypto funds are never shown.")

    st.title(f"ETFs: {group}")

    # ------------------------------------------------------------------ load / pull
    members = imap[imap["group"] == group]
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
    # peers = the funds tagged to this industry/group (the raw's category is overwritten with the group name)
    raws = [{**have[t], "category": group} for t in tickers if t in have]
    if missing:
        c2.info(f"{len(missing)} of {len(tickers)} funds here aren't downloaded yet. Click **Pull live ETF data**.")
    else:
        c2.caption(f"**{len(tickers)} ETFs in this group, all downloaded.** Tagged by: "
                   + ", ".join(f"{n} by {how.split(' of ')[0] if '%' in how else how}" for how, n in
                               members["basis"].str.replace(r"^\d+% of top-10 weight$", "holdings", regex=True).value_counts().items()))

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

    tab_res, tab_tune, tab_dir, tab_how = st.tabs(["Ranked results", "Tune scoring", "Directory", "How scoring works"])
    with tab_dir:
        directory_fn()
    if not raws:
        with tab_how:
            _how_tab(None, None)
        return

    # prep depends on data + settings only (never on weights or ranges); hash EVERY fund's fetch date, not just the first few
    data_sig = hashlib.sha1(repr(sorted((r["ticker"], r["fetched"]) for r in raws)).encode()).hexdigest()
    sig = (es.PREP_VERSION, group, data_sig, len(raws), tbill, window, len(hq_scores), len(hq_skips))
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
        ru.buttons((t2, t3), PREFIX, group, bench_cat, wdef)
        ru.seed_laura(PREFIX, group, bench_cat, linfo)  # Laura ranges are the default (only where nothing is set yet)
        total = sum(st.session_state[k] for k in wdef)  # number of holdings is always 0
        (st.success if abs(total - 100) < 1e-9 else st.warning)(
            f"Weights total **{total:g}**" + ("" if abs(total - 100) < 1e-9 else " (not 100 - scores still calculate; the formula normalizes)")
            + " · defaults = Laura Gao's standards (cost and drawdown first)")
        st.caption("Weights affect both Composite and Fit. Comfort ranges affect only Fit. Ranges are in each metric's own units (AUM and dollar volume in $ millions). "
                   "**Laura standards** (default) tighten the bad-side bound to a stricter percentile of THIS group: cost, risk, concentration "
                   "and size at the group median, holdings quality at the 40th percentile, the rest at the 25th-75th.")
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
                        ranges_by_cat.setdefault(group, {})[key] = ru.range_widget(PREFIX, group, key, b, fallback=(linfo.get(key) or {}).get("range"))
                        if laura_hard and key in hl:
                            lo_, hi_ = hl[key]
                            st.checkbox(f"Must be inside: Laura hard limit {'≤ ' + fmt(hi_, m['units']) if hi_ is not None else '≥ ' + fmt(lo_, m['units'])}",
                                        value=True, disabled=True, key=f"etf2hl|{group}|{key}")
                            hard[key] = True
                        else:
                            hard[key] = st.checkbox("Must be inside range", key=f"etf2h|{group}|{key}")
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
        rows = [{
            "rank": i, "ticker": r["ticker"], "fund": r["name"], "category": r["category"], "overall": r["overall"], "fit": r["fit"],
            "composite": r["composite"], "inside range": f"{r['n_inside']}/{r['n_total']}",
            "flags": _icons(r["flags"]) + (" ❓low data" if r["low_data"] else ""), "coverage": r["coverage"] * 100,
            "flag details": " | ".join(f["text"] for f in r["flags"]),
        } for i, r in enumerate(display, 1)]
        bar = lambda: st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f")
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", column_config={
            "overall": bar(), "fit": bar(), "composite": bar(), "coverage": st.column_config.NumberColumn("data coverage %", format="%.0f"),
            "flags": st.column_config.TextColumn(help="Key below the table. Full text in 'flag details'."),
        })
        _flag_key(FLAG_KEY)
        st.download_button("Download full export (CSV)", es.export_rows(final, settings, group).to_csv(index=False),
                           file_name=f"etf_score_{group}_{datetime.now():%Y%m%d_%H%M}.csv".replace(" ", "_").replace("★", ""), mime="text/csv")

        st.subheader("Breakdown")
        pick = st.selectbox("Fund", [r["ticker"] for r in display], key=f"etf2_pick|{group}",
                            format_func=lambda t: f"{t} - {next(r['name'] for r in display if r['ticker'] == t)}")
        chosen = next(r for r in display if r["ticker"] == pick)
        _breakdown(chosen, prep["bench"][chosen["category"]])

    with tab_how:
        _how_tab(chosen, final)


FLAG_KEY = [("⚰️", "Closure risk", "AUM under $100M"),
            ("🕒", "Short / shorter history", "under 10 years of prices (under 5: drawdown, downside deviation and return excluded)"),
            ("🎯", "Concentrated", "top-10 holdings are more than 50% of the fund"),
            ("🔍", "Low / no look-through", "under 25% of the fund's holdings could be scored for holdings quality"),
            ("❔", "Expense missing", "no expense ratio reported"),
            ("📏", "Group fallback", "fewer than 10 funds in the group for a metric, ranked against all equity ETFs"),
            ("❓", "Low data", "more than 30% of the weight excluded; listed last"),
            ("⚑", "Other", "any other note; read the 'flag details' column")]


def _flag_key(rows):
    """Legend for the red-flag icons, shown under the ranked table. Flags are shown, never scored."""
    st.markdown("**Red-flag key** (shown, never scored)")
    st.markdown("  \n".join(f"{icon} **{name}**: {what}" for icon, name, what in rows))


def _breakdown(r, bench):
    if None not in (r["overall"], r["fit"], r["composite"]):
        st.markdown(f"**Overall {r['overall']:.1f}** · Fit {r['fit']:.1f} · Composite {r['composite']:.1f} · "
                    f"{r['n_inside']}/{r['n_total']} inside range · coverage {r['coverage']:.0%} · category **{r['category']}**")
    h = r["history"]
    st.caption(f"History available {h['years_available']:.1f} yrs · window {h['window']} yrs · used "
               + (f"{h['used']:.1f} yrs" if h["used"] else "none (short history)"))
    rows = []
    for m in es.METRICS:
        x, b = r["metrics"][m["key"]], bench[m["key"]]
        q = b["q"] or {}
        lo, hi = x["range"]
        rows.append({
            "metric": f"{m['label']} ({m['units'] or 'count'})", "value": fmt(x["value"], m["units"]) if x["value"] is not None else "excluded",
            "better": m["direction"], "category 25th": q.get("p25"), "median": q.get("median"), "75th": q.get("p75"),
            "comfort range": f"{lo:.4g} to {hi:.4g}" if lo is not None else None, "distance outside": x["distance"],
            "fit": x["fit"], "percentile score": x["pct"], "weight": x["weight"], "contribution": x["contribution"], "note": x["note"],
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

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


def _how_tab(chosen, final):
    st.markdown("""
### Same framework as the stocks, ten ETF metrics
Each fund is compared with the other funds in its **industry group** (the same 145 stock industries; its peer group). Two 0–100 scores per metric, where **100 is best**:

* **Composite (percentile).** Rank the fund among peers with a valid value: sort ascending, ties share the average rank, **p = (rank − 1) ÷ (n − 1) × 100** (n = 1 → 50). Higher-is-better metrics score p; lower-is-better (expense ratio, downside deviation, max drawdown, top-10 weight) score 100 − p. If a category has fewer than 10 valid values for a metric, that metric is ranked against **all scored equity ETFs** and flagged.
* **Fit (comfort range).** A range in the metric's units. Only the bad side counts: higher-better → `max(0, low − value)`; lower-better → `max(0, value − high)`. **Fit = 100 × max(0, 1 − (distance ÷ spread) ÷ 2)**, spread = group 75th − 25th (Laura's tighter bound does **not** change the spread). **Default ranges = Laura standards, per group**: the bad-side bound moves to a stricter percentile of *this group's* peer pool - expense ratio, downside deviation, max drawdown, top-10 weight (high = group median), AUM and dollar volume (low = group median), holdings quality (low = 40th percentile); dividend yield and long-run return stay at the 25th–75th. *Industry average* sets every range to the 25th–75th. These are Fit-only; the optional **Laura hard limits** checkbox (off by default) adds absolute cut-offs: expense ≤ 0.20%, max drawdown ≤ 30%, AUM ≥ $1B, top-10 weight ≤ 40%.

Each is a **weighted average** over the metrics the fund has (default weights are Laura Gao's standards, total 100; excluded metrics drop out and the denominator shrinks). **Overall = b × Fit + (1 − b) × Composite.** Weights change both; ranges change only Fit.

### The ten metrics
Expense ratio **18** · max drawdown **18** · AUM **12** · top-10 weight **12** · downside deviation **12** · long-run return **10** · holdings quality **8** · dollar volume **5** · dividend yield **5** · number of holdings **0** (always excluded). Why: Laura is a moderate-risk client who needs no big drops and steady growth, so cost and drawdown come first.

**Holdings quality** = Σ(weight × stock Composite) ÷ Σ(weights of scored holdings) over the fund's top-10 holdings, where each holding is scored by the stock model against its own industry. Foreign/unlisted holdings are skipped. Under 15% coverage it is excluded and flagged.

**History window** (5 or 10 years) drives downside deviation, max drawdown and long-run return. A fund with ≥ 5 but fewer than the window's years uses what it has (flagged); under 5 years those three metrics are excluded.

**Number of holdings** is not provided by yfinance, so it is excluded for every fund unless you supply `results/etf_holdings_override.csv` (columns `ticker,n_holdings`).

### Final order
Funds with > 30% of weight excluded are **low data** and listed last. Otherwise Overall ↓, then Composite ↓, fewer red flags, less excluded weight, ticker A–Z. Red flags are shown, never scored.
""")
    if chosen and chosen["overall"] is not None:
        st.markdown(f"### Worked example: {chosen['ticker']} ({chosen['name']})")
        rows = [(m, chosen["metrics"][m["key"]]) for m in es.METRICS]
        pick = next(((m, x) for m, x in rows if x["pct"] is not None and x["fit"] is not None and x["fit"] < 100), None) \
            or next(((m, x) for m, x in rows if x["pct"] is not None), None)
        if pick:
            m, x = pick
            lo, hi = x["range"]
            st.markdown(f"**{m['label']}**: value **{fmt(x['value'], m['units'])}**, comfort range **{lo:.4g} to {hi:.4g}**, distance outside **{x['distance']:.4g}** "
                        f"→ Fit **{x['fit']:.1f}**. Percentile score among peers: **{x['pct']:.1f}**. Weight **{x['weight']:g}**.")
        den = sum(x["weight"] for _, x in rows if x["pct"] is not None)
        num = sum(x["weight"] * x["pct"] for _, x in rows if x["pct"] is not None)
        st.markdown(f"**Composite** = {num:,.1f} ÷ {den:g} = **{chosen['composite']:.2f}**. "
                    f"**Overall** = {final['blend']:.0%} × Fit {chosen['fit']:.2f} + {1 - final['blend']:.0%} × Composite {chosen['composite']:.2f} = **{chosen['overall']:.2f}**.")
    with st.expander("Self-tests (debug)"):
        tests = es.run_self_tests()
        st.markdown(f"**{sum(t['passed'] for t in tests)} / {len(tests)} passed**")
        st.dataframe(pd.DataFrame([{"result": "PASS" if t["passed"] else "FAIL", "test": t["test"], "detail": t["detail"]} for t in tests]),
                     hide_index=True, width="stretch")
