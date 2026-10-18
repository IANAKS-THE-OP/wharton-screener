"""
Stock page for the 14-metric scoring system (scoring2.py).
Called from app.py when the asset type is "Stocks".
"""

import logging
from datetime import datetime

import pandas as pd
import streamlit as st

import compare
import compare_ui
import laura
import range_ui as ru
import scoring2 as sc
import snapshots
import stock_data as sd

log = logging.getLogger(__name__)
PREFIX = "stk"  # this page's own session-key prefix (the ETF page uses "etf")

FLAG_ICONS = {"altman": "🔻", "piotroski": "📉", "roic_below_wacc": "💸", "unsafe_payout": "🚨",
              "neg_ic": "➖", "ev_le0": "➖", "interest_missing": "❔", "short_history": "🕒", "too_new": "🆕", "stale_prices": "🕒",
              "debt_assumed_zero": "ℹ️",
              "data_note": "📝", "sector_fallback": "📏"}


@st.cache_data(ttl=86400)
def _ten_year_yield():
    """(10-year Treasury yield from yfinance ^TNX, error text or None). Falls back to 4.25% with the reason."""
    try:
        import yfinance as yf
        v = float(yf.Ticker("^TNX").history(period="5d")["Close"].dropna().iloc[-1])
        v = v / 100 if v < 25 else v / 1000
        if 0.005 < v < 0.20:
            return v, None
        return 0.0425, f"implausible value {v}"
    except Exception as e:  # network / SSL / empty history
        log.warning("10-year yield lookup failed: %s", e)
        return 0.0425, f"{type(e).__name__}: {e}"


def fmt(v, units):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    if units == "%":
        return f"{v:.2f}%"
    if units == "x":
        return f"{v:.2f}x"
    return f"{v:+.2f} pp/yr"


def _icons(flags):
    seen = []
    for f in flags:
        code = f["code"].split(":")[0]
        seen.append(FLAG_ICONS.get(code, "⚑"))
    return "".join(dict.fromkeys(seen))


def _settings_sidebar():
    with st.sidebar.expander("Scoring inputs", expanded=False):
        tbill = st.number_input("T-bill rate (downside-deviation MAR, %)", 0.0, 15.0, 3.0, 0.25, key="s2_tbill")
        y10, y10_err = _ten_year_yield()
        rf = st.number_input("Risk-free rate (WACC, %)", 0.0, 15.0, round(y10 * 100, 2), 0.05, key="s2_rf",
                             help="Defaults to the live 10-year Treasury yield.")
        if y10_err:
            st.warning(f"Live 10-year yield unavailable ({y10_err[:80]}); using 4.25%.")
        erp = st.number_input("Equity risk premium (WACC, %)", 0.0, 15.0, 5.0, 0.25, key="s2_erp")
    return {"tbill": tbill / 100, "rf": rf / 100, "erp": erp / 100, "window": int(st.session_state.get("hist_window", 5))}


def render(group, directory_fn):
    settings = _settings_sidebar()
    st.caption(
        "14-metric scoring: **Composite** (percentile vs peers) minus a **Laura penalty** for every Laura limit a company misses "
        "(**Overall = Composite − k × (100 − Fit)**). See the *How scoring works* tab."
    )

    hit = sd.load_industry(group)
    c1, c2 = st.columns([1, 3])

    def _pull():
        slot = st.empty()
        bar = slot.progress(0, text="Pulling the live industry universe from yfinance...")

        def _cb(done, total, ticker):
            bar.progress(done / total, text=f"{done} of {total} companies... ({ticker})")

        try:
            res = sd.get_or_refresh_industry(group, force_refresh=True, progress_callback=_cb)
        except Exception as e:  # yfinance / SEC network errors (e.g. curl SSL errors) must not crash the page
            log.exception("industry pull failed for %s", group)
            slot.empty()
            st.session_state["s2_pull_err"] = (group, f"{type(e).__name__}: {e}")
            st.rerun()
        slot.empty()
        st.session_state.pop("s2_pull_err", None)
        st.session_state["s2_msg"] = (group, f"{res['found']} found → {res['kept']} tradeable (≥ $5, major US exchange, no preferred/units/warrants)"
                                      + (f"; {len(res['failures'])} failed and were skipped: {list(res['failures'])}" if res["failures"] else "."))
        st.rerun()

    if c1.button("Pull live industry data" if hit is None else "Refresh industry data", type="primary", key="s2_pull"):
        _pull()
    err = st.session_state.get("s2_pull_err")
    if err and err[0] == group:
        st.error(f"Download failed (network / data source error): {err[1][:300]}. Nothing was changed; any cached data is still used.")
        if st.button("Retry", key="s2_retry"):
            _pull()
    if hit is None:
        c2.info(f"'{group}' isn't cached yet. Click **Pull live industry data** (≈1–3 min for ~50 companies).")
    else:
        c2.caption(f"**{len(hit[0])} companies** · data as of **{hit[1]}**")
    msg = st.session_state.get("s2_msg")
    if msg and msg[0] == group:
        st.success(msg[1])

    tab_res, tab_tune, tab_cmp, tab_dir, tab_how = st.tabs(["Ranked results", "Tune scoring", "Compare industries", "Directory", "How scoring works"])
    with tab_dir:
        directory_fn()
    with tab_cmp:  # drawn BEFORE the Tune widgets so its Laura button can reset the weights in this run
        _compare_tab(settings)
    if hit is None:
        with tab_how:
            _how_tab(None, None)
        return

    raws, fetched = hit
    # prep depends on data + settings (incl. the history window) only; weights and limits are applied later in finalize (never re-pulls)
    memo_key = (sc.PREP_VERSION, group, fetched, settings["tbill"], settings["rf"], settings["erp"], settings["window"])
    if st.session_state.get("s2_prep_key") != memo_key:
        st.session_state["s2_prep"] = sc.prepare_industry(raws, settings, all_raws=sd.load_all_cached)
        st.session_state["s2_prep_key"] = memo_key
    prep = st.session_state["s2_prep"]
    bench = prep["bench"]
    ns = group
    linfo = laura.laura_info(bench, "stock", sc.METRICS)

    # ------------------------------------------------------------------ Tune
    weights, limits, hard = {}, {}, {}
    with tab_tune:
        wdef = {k: v for k, v in laura.session_defaults("stock").items() if k != "s2_penalty"}
        ru.seed(laura.session_defaults("stock"))  # Laura weights + penalty strength are the default
        ru.laura_badge(ru.is_laura(PREFIX, ns, linfo, wdef, "s2_penalty", sc.DEFAULT_PENALTY), f"s2_badge|tune|{ns}",
                       (PREFIX, [ns], wdef, "s2_penalty", sc.DEFAULT_PENALTY))
        t1, t2, t3 = st.columns([2, 1, 1])
        ru.buttons((t2, t3), PREFIX, ns, bench, sc.METRICS, wdef, "s2_penalty", sc.DEFAULT_PENALTY)  # before the slider: it resets it
        penalty = t1.slider("Laura penalty strength k: Overall = Composite − k × (100 − Fit)", 0.0, 1.0, step=0.05, key="s2_penalty",
                            help="0 = pure Composite (peer percentiles). Each Fit point below 100 costs k Overall points.")
        ru.seed_laura(PREFIX, ns, bench, linfo)  # Laura limits are the default (only where nothing is set yet)
        total = sum(st.session_state[k] for k in wdef)
        (st.success if abs(total - 100) < 0.01 else st.warning)(
            f"Weights total **{total:.1f}**" + ("" if abs(total - 100) < 0.01 else " (not 100 - scores still calculate; the formula normalizes)")
            + " · defaults = Laura Gao's standards (same six category totals as the ETF model)")
        ru.category_table(sc.METRICS, {m["key"]: st.session_state[f"s2w|{m['key']}"] for m in sc.METRICS}, "Stocks")
        st.caption("Weights affect Composite and Fit. Laura limits affect only Fit. Each metric has ONE **Laura limit** in its own units: "
                   "a **ceiling** for lower-is-better metrics, a **floor** for higher-is-better. On the good side of the limit Fit = **100**; "
                   "past it **Fit = 100 × max(0, 1 − (distance ÷ spread) ÷ 2)** (one spread past = 50, two = 0; spread = industry 75th − 25th). "
                   "**Laura standards** (default): limit = the industry median for risk metrics, the 40th percentile for consistency metrics, "
                   "the 25th (floor) for the rest. **Industry average**: the 75th (ceiling) / 25th (floor).")

        for gname in sc.GROUPS:
            with st.expander(gname, expanded=False):
                for m in [x for x in sc.METRICS if x["group"] == gname]:
                    key = m["key"]
                    b = bench[key]
                    st.markdown(f"**{m['label']}** ({m['units']}) · {'higher' if m['direction'] == 'higher' else 'lower'} is better")
                    if not b["q"]:
                        st.caption("No valid values in this industry - metric excluded for everyone.")
                        weights[key] = st.session_state[f"s2w|{key}"]
                        continue
                    q = b["q"]
                    st.caption(f"Industry 25th / median / 75th: {fmt(q['p25'], m['units'])} / {fmt(q['median'], m['units'])} / "
                               f"{fmt(q['p75'], m['units'])} · {b['pool_n']} peers"
                               + ("" if b["level"] == "industry" else f" · ⚠ {b['level']} fallback pool ({b['industry_n']} in industry)"))
                    ru.laura_caption(linfo.get(key), m["direction"], lambda v, u=m["units"]: fmt(v, u))
                    limits[key] = ru.limit_widget(PREFIX, ns, key, b, m["direction"], fallback=(linfo.get(key) or {}).get("limit"))
                    cw, ch = st.columns(2)
                    weights[key] = cw.slider("Weight", 0.0, 20.0, step=0.5, key=f"s2w|{key}")
                    hard[key] = ch.checkbox("Must be inside (meet the Laura limit)", key=f"s2h|{ns}|{key}")
                    st.divider()
        for m in sc.METRICS:
            weights.setdefault(m["key"], st.session_state[f"s2w|{m['key']}"])

    final = sc.finalize(prep, weights=weights, limits=limits, penalty=penalty, hard_limits=hard)
    results = final["results"]

    # ------------------------------------------------------------- Results
    with tab_res:
        ru.laura_badge(ru.is_laura(PREFIX, ns, linfo, wdef, "s2_penalty", sc.DEFAULT_PENALTY), f"s2_badge|res|{ns}",
                       (PREFIX, [ns], wdef, "s2_penalty", sc.DEFAULT_PENALTY))
        display = results
        if any(hard.values()):
            passing = [r for r in results if not r["hard_limit_failed"]]
            if not passing:
                bad = sorted({sc.METRIC_BY_KEY[k]["label"] for r in results for k in r["hard_limit_failures"]})
                st.warning(f"Your hard limit(s) on **{', '.join(bad)}** would remove all {len(results)} companies - showing the full ranked list anyway.")
            else:
                if len(results) - len(passing):
                    st.info(f"{len(results) - len(passing)} removed by hard limits · {len(passing)} remain.")
                display = sc.rank([dict(r) for r in passing])
        st.caption(f"**Data as of {fetched}** (SEC 10-K + yfinance, cached) · weights and Laura limits from Tune scoring · "
                   f"penalty strength k = {penalty:.2f} · {settings['window']}-yr history window"
                   + (" · Must-be-inside limits on" if any(hard.values()) else ""))
        rows = [{
            "rank": r["rank"], "ticker": r["ticker"], "company": r["name"], "overall": r["overall"], "composite": r["composite"],
            "fit": r["fit"], "Laura penalty": r["penalty"], "meets limits": f"{r['n_inside']}/{r['n_total']}",
            "why not higher": r.get("why_not_higher") or "",
            "flags": _icons(r["flags"]) + (" ⛔ not scored" if r["not_scored"] else " ❓low data" if r["low_data"] else "")
                     + (" 🆕too new" if r.get("too_new") else ""),
            "why low data / not scored / too new": r["low_data_reason"] or r.get("too_new_reason") or "",
            "coverage": r["coverage"] * 100, "FYE": r["fye"],
            "flag details": " | ".join(f["text"] for f in r["flags"]),
        } for r in display]
        table = pd.DataFrame(rows)
        bar = lambda: st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f")
        st.dataframe(table, hide_index=True, width="stretch", column_config={
            "overall": bar(), "fit": bar(), "composite": bar(), "coverage": st.column_config.NumberColumn("data coverage %", format="%.0f"),
            "flags": st.column_config.TextColumn(help="Key below the table. Full text in 'flag details'."),
            "rank": st.column_config.NumberColumn(help="Blank = not scored (no 10-K data)."),
            "Laura penalty": st.column_config.NumberColumn(format="%.1f", help="Overall points lost for missing Laura limits: Composite − Overall = k × (100 − Fit)."),
            "why not higher": st.column_config.TextColumn(help="Each missed Laura limit and the Overall points it costs: k × weight × (100 − Fit) ÷ total weight."),
        })
        n_new = sum(bool(r.get("too_new")) for r in display)
        if n_new:
            st.caption(f"{n_new} too new for the {settings['window']}-yr window (listed after the fully scored companies).")

        def shown_at(k):  # the ranked table at penalty strength k (same weights, limits and hard-limit filter)
            res = sc.finalize(prep, weights=weights, limits=limits, penalty=k, hard_limits=hard)["results"]
            keep = [r for r in res if not r["hard_limit_failed"]] if any(hard.values()) else res
            return sc.rank([dict(r) for r in keep]) if keep and keep is not res else res
        ru.sensitivity_table(sc.penalty_sensitivity({k: shown_at(k) for k in sc.SENSITIVITY_KS}), penalty, sc.DEFAULT_PENALTY, "company")
        _foreign_list(final["foreign"])
        _flag_key(FLAG_KEY)
        export = sc.export_rows(final, settings, group)
        d1, d2 = st.columns([1, 1])
        d1.download_button("Download full export (CSV)", export.to_csv(index=False),
                           file_name=f"stock_score_{group}_{datetime.now():%Y%m%d_%H%M}.csv".replace(" ", "_"), mime="text/csv")
        shown = {r["ticker"] for r in display}
        snapshots.button(f"stk|{ns}", "stock", group, lambda: export[export["ticker"].isin(shown)],
                         lambda: {"data_date": fetched, "industry": group, "settings": settings, "laura_penalty_k": penalty, "weights": weights,
                                  "limits": dict(final["limits"]),
                                  "limit_source": "Tune scoring (Laura standards unless hand-tuned)",
                                  "hard_limits": [k for k, v in hard.items() if v], "rows_shown": len(display)}, col=d2)

        st.subheader("Breakdown")
        pick = st.selectbox("Company", [r["ticker"] for r in display], key=f"s2_pick|{ns}",
                            format_func=lambda t: f"{t} - {next(r['name'] for r in display if r['ticker'] == t)}")
        chosen = next(r for r in display if r["ticker"] == pick)
        st.session_state["s2_chosen"] = chosen
        _breakdown(chosen, bench, prep["companies"][pick])

    with tab_how:
        _how_tab(chosen, final)


def _compare_tab(settings):
    """'Compare industries': any number of cached industries, each vs its own industry or pooled (compare.py)."""
    counts = sd.cached_counts()
    options = [i for i in sorted(counts)]
    weights = {m["key"]: float(st.session_state.get(f"s2w|{m['key']}", m["weight"])) for m in sc.METRICS}
    penalty = float(st.session_state.get("s2_penalty", sc.DEFAULT_PENALTY))
    cache = st.session_state.setdefault("_cmpcache_stk", {})
    if len(cache) > 80:
        cache.clear()

    def run(sel, mode, rmode):
        by = {}
        for g in sel:
            hit = sd.load_industry(g)
            if hit:
                by[g] = hit
        return compare.stock_compare(by, settings, weights, penalty, mode, rmode, all_raws=sd.load_all_cached, cache=cache)

    compare_ui.render(key="stkcmp_", asset="stock", metrics=sc.METRICS, options=options,
                      counts={g: f"{n} companies" for g, (n, _) in counts.items()}, run=run,
                      weight_defaults={k: v for k, v in laura.session_defaults("stock").items() if k != "s2_penalty"},
                      fmt=fmt, icons=_icons, word="industry", name_word="company", page_prefix=PREFIX, ns_fn=lambda g: g,
                      hard_prefix="s2h|", meta_extra={"settings": settings, "laura_penalty_k": penalty},
                      penalty_key="s2_penalty", penalty_default=sc.DEFAULT_PENALTY)


FLAG_KEY = [("🔻", "Altman distress", "Altman Z below the distress line (bankruptcy-risk model)"),
            ("📉", "Piotroski F ≤ 3", "weak financial health on Piotroski's 9 checks"),
            ("💸", "ROIC below WACC", "returns on capital below the cost of capital"),
            ("🚨", "Payouts > free cash flow", "3-yr dividends + buybacks exceed 3-yr free cash flow"),
            ("➖", "Negative capital / EV", "negative invested capital or enterprise value ≤ 0"),
            ("❔", "Interest missing", "interest expense not reported although the company has debt"),
            ("🆕", "Too new for this window", "less price history than the history window: downside deviation and max drawdown excluded; listed after fully scored companies"),
            ("🕒", "Stale prices", "prices end more than 7 days before the rest of the industry: history metrics excluded"),
            ("ℹ️", "Debt assumed zero", "no debt reported in the 10-K, treated as zero"),
            ("📝", "Data note", "a data source problem for this company (e.g. no 10-K in SEC XBRL); see flag details"),
            ("📏", "Fallback pool", "fewer than 10 industry peers for a metric, ranked against the sector / all stocks"),
            ("❓", "Low data", "more than 30% of the weight excluded; listed after the fully scored names (the 'why' column lists the missing metrics)"),
            ("⛔", "Not scored", "no 10-K data (new listing or not in SEC EDGAR); not ranked. Foreign filers (20-F / 40-F) are out of scope: see 'Not scored: foreign filer'"),
            ("⚑", "Other", "any other note; read the 'flag details' column")]


def _foreign_list(foreign):
    """Foreign filers (20-F / 40-F, no 10-K) are out of scope: listed here, never ranked."""
    if not foreign:
        return
    with st.expander(f"Not scored: foreign filer ({len(foreign)})", expanded=False):
        st.caption("These companies file a 20-F (foreign private issuer) or 40-F (Canadian issuer) instead of a 10-K. The stock model "
                   "uses SEC 10-K data only, so they are out of scope: not ranked and not part of the peer pools.")
        st.dataframe(pd.DataFrame([{"ticker": f["ticker"], "company": f["name"], "annual report": f["annual_form"], "reason": f["reason"]}
                                   for f in foreign]), hide_index=True, width="stretch")


def _flag_key(rows):
    """Legend for the red-flag icons, shown under the ranked table. Flags are shown, never scored."""
    st.markdown("**Red-flag key** (shown, never scored)")
    st.markdown("  \n".join(f"{icon} **{name}**: {what}" for icon, name, what in rows))


def _breakdown(r, bench, comp):
    if r["not_scored"]:
        st.warning(f"**{r['not_scored']}.** Fundamentals come from SEC 10-K XBRL only, so this company has no ROIC, margins, "
                   "debt or valuation metrics here and is not ranked. Price-based metrics are shown below for reference.")
    elif r["low_data"]:
        st.warning("**Low data** (listed after every fully scored company): " + r["low_data_reason"])
    if r.get("too_new"):
        st.warning("🆕 **" + r["too_new_reason"] + "**. Listed after every fully scored company; pick a shorter *History window* in the sidebar.")
    st.markdown(f"**Overall {r['overall']:.1f}** = Composite {r['composite']:.1f} − Laura penalty {r['penalty']:.1f} (Fit {r['fit']:.1f}) · "
                f"{r['n_inside']}/{r['n_total']} metrics meet their Laura limit · coverage {r['coverage']:.0%} · fiscal year end **{r['fye']}**"
                if None not in (r["overall"], r["fit"], r["composite"]) else "Not enough data to score this company.")
    if r.get("why_not_higher") and None not in (r["overall"], r["composite"]):
        st.markdown(f"**Why not higher:** {r['why_not_higher']}")
    rows = []
    for m in sc.METRICS:
        x = r["metrics"][m["key"]]
        b = bench[m["key"]]
        q = b["q"] or {}
        lim = x["limit"]
        rows.append({
            "metric": f"{m['label']} ({m['units']})", "value": fmt(x["value"], m["units"]) if x["value"] is not None else ("scored 100" if x["status"] == sc.HUNDRED else "excluded"),
            "better": "higher" if m["direction"] == "higher" else "lower",
            "industry 25th": q.get("p25"), "median": q.get("median"), "75th": q.get("p75"),
            "Laura limit": (f"{'≤' if m['direction'] == 'lower' else '≥'} {lim:.4g}") if lim is not None else None,
            "distance past limit": x["distance"], "fit": x["fit"], "percentile score": x["pct"],
            "weight": x["weight"], "contribution to composite": x["contribution"], "note": x["note"],
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    w = r["wacc"]
    roic = r["metrics"]["roic"]
    c1, c2, c3 = st.columns(3)
    c1.metric("ROIC", fmt(roic["value"], "%") if roic["value"] is not None else "—")
    c2.metric("WACC", f"{w['wacc'] * 100:.2f}%" if w.get("wacc") is not None else w.get("note", "NM"))
    c3.metric("ROIC − WACC", f"{w['spread']:+.2f} pp" if w.get("spread") is not None else "—")
    if w.get("wacc") is not None:
        st.caption(f"Cost of equity {w['cost_equity']:.2%} (beta {w['beta']:.2f}: {w['beta_note']}) · cost of debt {w['cost_debt']:.2%} · "
                   f"debt weight {w['debt_weight']:.1%}" + (f" · {w['note']}" if w.get("note") else ""))

    st.markdown("**Red flags**")
    if r["flags"] or r["low_data"]:
        for f in r["flags"]:
            st.markdown(f"- {_icons([f])} {f['text']}")
        if r["low_data"]:
            st.markdown(f"- ❓ Low data: {r['excluded_weight_share']:.0%} of the weight is excluded (> 30%)")
    else:
        st.caption("None triggered.")
    a, p = r["altman"], r["piotroski"]
    altman_txt = f"{a['model']} = {a['z']:.2f}" if a.get("z") is not None else a["note"]
    st.caption(f"Altman: {altman_txt} · "
               f"Piotroski F: {p['f'] if p['f'] is not None else 'NM'} ({p['note']})")
    raw = comp["raw"]
    tags = raw.get("tags", {})
    k10 = raw.get("latest_10k") or {}
    st.caption(f"Sources: SEC EDGAR 10-K (fiscal year ended {r['fye']}"
               + (f", filed {k10.get('filed_date')}" if k10.get("filed_date") else "") + ") + yfinance market data. "
               + "Tags: " + ", ".join(f"{k}={v}" for k, v in list(tags.items())[:12]) + ("…" if len(tags) > 12 else ""))
    if k10.get("edgar_url"):
        st.markdown(f"[Open the 10-K filing index]({k10['edgar_url']})")
    with st.expander("All raw inputs used (latest fiscal year)"):
        st.json({k: v for k, v in r["derived"].items()})


def _how_tab(chosen, final):
    st.markdown("""
### Composite, minus a penalty for Laura's limits
For each of the 14 metrics a company gets two numbers, both 0–100 where **100 is best**:

* **Composite (percentile).** Rank the company among its industry peers that have a valid value for that metric. Sort ascending, rank 1 = lowest, ties share the average rank. **p = (rank − 1) ÷ (n − 1) × 100** (n = 1 → 50). *Higher-is-better* metrics use p; *lower-is-better* use 100 − p.
* **Fit (one Laura limit per metric).** Each metric has ONE limit in its own units: a **ceiling** for lower-is-better metrics, a **floor** for higher-is-better. Only the bad side counts: distance = max(0, value − ceiling) or max(0, floor − value), and **Fit = 100 × max(0, 1 − (distance ÷ spread) ÷ 2)**, spread = industry 75th − 25th value (moving the limit does **not** change the spread). So anything on the good side of the limit scores **100**, one spread past it **50**, two spreads past **0**. **Default = Laura standards, per industry**: risk metrics (downside deviation, max drawdown, net debt/EBITDA, Sloan, interest coverage) at the **industry median**, consistency metrics (ROIC, gross profitability, Δ ROIC, Δ operating margin) at the **40th percentile**, the rest (revenue growth, FCF yield, earnings yield, Lynch, shareholder yield) at the **25th percentile** (floor). *Industry average* sets every limit to the worse quartile (75th for a ceiling, 25th for a floor). *Must be inside* (optional, per metric) removes companies that miss that limit.

Each is a **weighted average** over the metrics (default weights add to 100 and are Laura Gao's standards: moderate risk, no big drops, steady growth). The weights are set by **six categories, the same totals as the ETF model**: **Risk 30** (max drawdown 16, downside deviation 14) · **Cost/valuation 18** (earnings yield 6, FCF yield 6, Lynch 6) · **Quality 20** (ROIC 8, Sloan 7, gross profitability 5) · **Growth 10** (revenue growth 4, Δ ROIC 4, Δ operating margin 2) · **Safety 17** (net debt/EBITDA 9, interest coverage 8) · **Income 5** (shareholder yield 5). Excluded metrics drop out and the denominator shrinks - a company missing a weight-16 metric is divided by 84, not 100.

**Overall = max(0, Composite − k × (100 − Fit))**, k = the *Laura penalty strength* slider (default **0.4**; *Laura standards* resets it; k = 0 → pure Composite). **What k = 0.4 means:** each Fit point lost costs 0.4 Overall points (Fit 90 → −4, Fit 75 → −10); a company that meets every limit keeps its Composite. The table under the ranked results shows the top 10 at k = 0.2 / 0.4 / 0.6 and how many names change. **Laura standards are the default**: the badge at the top of Tune scoring, the ranked table and Compare says *Laura standards (default)* or *Custom settings* with a reset button, and the app resets to Laura standards on first load and whenever the default weights or limits change. The **Laura penalty** column = Composite − Overall; **why not higher** lists each missed limit and the points it costs: k × weight × (100 − Fit) ÷ total weight. Weights change both scores; limits change only Fit.

### History window
**Downside deviation** and **max drawdown** use the sidebar's *History window* (3, 5 or 10 years; default 5) for every company, ending on the same date. A company with less price history than the window is **too new for this window**: both metrics are excluded (never measured over a shorter period) and it is listed after the fully scored companies. Prices that end more than 7 days before the rest of the industry are excluded the same way. 3 years starts in late 2023 and leaves out the 2022 decline, so risk numbers look better than over a full market cycle.

### Forced scores
Some situations are scored 0 or 100 outright instead of ranked: EBITDA ≤ 0 → net debt/EBITDA = 0; EBIT ≤ 0 → earnings yield, interest coverage = 0; EPS ≤ 0 → Lynch = 0; no interest expense and no debt → interest coverage = 100. These companies are *not* part of that metric's ranking pool.

### Pools and fallbacks
Percentiles use only companies with a **valid numeric value**. If an industry has fewer than 10, the company's **sector** is used (then all cached stocks) and a flag is shown. **Foreign filers** (20-F / 40-F, no 10-K) are out of scope: they are left out of every pool and ranking and listed under *Not scored: foreign filer*.

### Final order
Fully scored companies first, then **too new for this window**, then **low data** (> 30% of the weight excluded, not counting the window metrics of a too-new company), then not scored (no 10-K data; no rank). Within each: Overall ↓, then Composite ↓, fewer red flags, less excluded weight, ticker A–Z. Red flags are displayed, never scored.
""")
    if chosen and chosen["overall"] is not None:
        st.markdown(f"### Worked example: {chosen['ticker']} ({chosen['name']})")
        rows = [(m, chosen["metrics"][m["key"]]) for m in sc.METRICS]
        pick = next(((m, x) for m, x in rows if x["status"] == sc.OK and x["pct"] is not None and x["fit"] is not None and x["fit"] < 100), None) \
            or next(((m, x) for m, x in rows if x["status"] == sc.OK and x["pct"] is not None), None)
        if pick:
            m, x = pick
            side = "ceiling ≤" if m["direction"] == "lower" else "floor ≥"
            st.markdown(
                f"**{m['label']}**: value **{fmt(x['value'], m['units'])}**, Laura limit **{side} {x['limit']:.4g}**, distance past the limit **{x['distance']:.4g}** "
                f"→ Fit **{x['fit']:.1f}**. Percentile score among peers: **{x['pct']:.1f}**. Weight **{x['weight']}**.")
        den = sum(x["weight"] for _, x in rows if x["pct"] is not None)
        num = sum(x["weight"] * x["pct"] for _, x in rows if x["pct"] is not None)
        st.markdown(f"**Composite** = Σ(weight × percentile score) ÷ Σ(weights not excluded) = {num:,.1f} ÷ {den} = **{chosen['composite']:.2f}**.")
        st.markdown(f"**Overall** = max(0, Composite {chosen['composite']:.2f} − {final['penalty']:.2f} × (100 − Fit {chosen['fit']:.2f})) = **{chosen['overall']:.2f}** "
                    f"(Laura penalty {chosen['penalty']:.2f}).")
    with st.expander("Self-tests (debug)"):
        tests = sc.run_self_tests() + laura.run_self_tests() + compare.run_self_tests() + snapshots.run_self_tests()
        st.markdown(f"**{sum(t['passed'] for t in tests)} / {len(tests)} passed**")
        st.dataframe(pd.DataFrame([{"result": "PASS" if t["passed"] else "FAIL", "test": t["test"], "detail": t["detail"]} for t in tests]),
                     hide_index=True, width="stretch")
