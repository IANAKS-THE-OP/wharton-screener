"""
Equity-ETF scoring engine (pure functions - no Streamlit, no network).
======================================================================
Same framework as the stock engine (scoring2.py, which it imports helpers
from and does not modify): percentile Composite within a peer group (the
Morningstar category), comfort-range Fit in real units, a blend, red flags,
tie-breakers and low-data handling. Ten metrics, weights total 100.
"""

import math
import logging
import os
from datetime import datetime

import numpy as np
import pandas as pd

import laura
from scoring2 import composite_score, downside_deviation, max_drawdown, metric_fit, percentile_scores  # noqa: F401

log = logging.getLogger(__name__)

METRICS = [
    {"key": "expense_ratio", "label": "Expense ratio", "units": "%", "direction": "lower", "weight": 18, "group": "Cost"},
    {"key": "downside_dev", "label": "Downside deviation", "units": "%", "direction": "lower", "weight": 12, "group": "Risk"},
    {"key": "max_drawdown", "label": "Max drawdown", "units": "%", "direction": "lower", "weight": 18, "group": "Risk"},
    {"key": "n_holdings", "label": "Number of holdings", "units": "", "direction": "higher", "weight": 0, "group": "Diversification"},
    {"key": "top10_weight", "label": "Top-10 weight", "units": "%", "direction": "lower", "weight": 12, "group": "Diversification"},
    {"key": "aum", "label": "AUM", "units": "$M", "direction": "higher", "weight": 12, "group": "Size & liquidity"},
    {"key": "dollar_volume", "label": "Avg daily dollar volume", "units": "$M", "direction": "higher", "weight": 5, "group": "Size & liquidity"},
    {"key": "holdings_quality", "label": "Holdings quality", "units": "/100", "direction": "higher", "weight": 8, "group": "Holdings"},
    {"key": "dividend_yield", "label": "Dividend yield (TTM)", "units": "%", "direction": "higher", "weight": 5, "group": "Return"},
    {"key": "long_run_return", "label": "Long-run return (annualized)", "units": "%", "direction": "higher", "weight": 10, "group": "Return"},
]
METRIC_BY_KEY = {m["key"]: m for m in METRICS}
KEYS = [m["key"] for m in METRICS]
DEFAULT_WEIGHTS = {m["key"]: m["weight"] for m in METRICS}
GROUPS = list(dict.fromkeys(m["group"] for m in METRICS))
DEFAULT_SETTINGS = {"tbill": 0.03, "window": 10}
PREP_VERSION = 2  # bump when prepare_funds' output changes, so cached preps in a session are rebuilt
MIN_POOL = 10
MIN_HQ_COVERAGE = 0.15
HISTORY_TOLERANCE = 0.25  # years of slack when deciding "has the full window"
HOLDINGS_OVERRIDE = "results/etf_holdings_override.csv"  # optional: ticker,n_holdings

OK, EXCL = "ok", "excluded"


def _num(x):
    return x is not None and isinstance(x, (int, float)) and math.isfinite(x)


def holdings_quality(holdings):
    """holdings: list of (weight, composite or None). Returns (quality or None, coverage).
    Renormalized over the holdings that have a score."""
    scored = [(w, c) for w, c in holdings if c is not None]
    wsum = sum(w for w, _ in scored)
    if not scored or wsum <= 0:
        return None, 0.0
    return sum(w * c for w, c in scored) / wsum, wsum


def history_plan(years_avail, window):
    """Returns (use_years or None, flag_text or None). Part D."""
    if years_avail < 5 - 0.1:  # < 5 years (0.1 yr slack for listing-date rounding): exclude the three history metrics
        return None, "short history"
    if years_avail >= window - HISTORY_TOLERANCE:
        return float(window), (f"shorter history ({years_avail:.1f} yrs)" if years_avail < 10 - HISTORY_TOLERANCE else None)
    return float(years_avail), f"shorter history ({years_avail:.1f} yrs)"


def _holdings_override():
    if os.path.exists(HOLDINGS_OVERRIDE):
        try:
            d = pd.read_csv(HOLDINGS_OVERRIDE)
            return dict(zip(d["ticker"], d["n_holdings"]))
        except (OSError, KeyError, ValueError, pd.errors.ParserError) as e:
            log.warning("ignoring unreadable %s: %s", HOLDINGS_OVERRIDE, e)
            return {}
    return {}


# ========================================================================
# Metrics for one fund
# ========================================================================

def compute_fund(raw, settings=None, hq_scores=None, hq_skips=None, overrides=None):
    """hq_scores: {symbol: {"composite":..., "industry":...}}; hq_skips: {symbol: reason}."""
    settings = {**DEFAULT_SETTINGS, **(settings or {})}
    hq_scores, hq_skips = hq_scores or {}, hq_skips or {}
    out = {k: {"value": None, "status": EXCL, "note": ""} for k in KEYS}
    flags = []

    def setm(key, value, note="", status=OK):
        if value is not None and not _num(value):
            value, status, note = None, EXCL, note or "non-finite"
        out[key] = {"value": value, "status": status if value is not None else EXCL, "note": note}

    def excl(key, note):
        out[key] = {"value": None, "status": EXCL, "note": note}

    def flag(code, text):
        flags.append({"code": code, "text": text})

    px = raw.get("px") or {}
    years = px.get("years") or 0.0

    # 1 expense ratio
    er = raw.get("expense_ratio")
    if _num(er):
        setm("expense_ratio", er, f"{raw.get('expense_source')} (percent)")
    else:
        excl("expense_ratio", "expense ratio missing")
        flag("expense_missing", "Expense ratio missing")

    # 2, 3, 10 history-window metrics
    use_years, hflag = history_plan(years, settings["window"])
    window = settings["window"]
    if use_years is None:
        for k in ("downside_dev", "max_drawdown", "long_run_return"):
            excl(k, f"short history ({years:.1f} yrs < 5)")
        flag("short_history", f"Short history ({years:.1f} yrs) - risk and return metrics excluded")
    else:
        if hflag:
            flag("shorter_history", hflag[0].upper() + hflag[1:] + f" - using {min(use_years, years):.1f} yrs")
        rets = px.get("monthly_returns") or []
        n_m = min(len(rets), int(round(min(use_years, years) * 12)))
        if n_m >= 12:
            setm("downside_dev", downside_deviation(rets[-n_m:], settings["tbill"]),
                 f"{n_m} monthly returns, MAR from {settings['tbill']:.2%} T-bill")
        else:
            excl("downside_dev", "too few monthly returns")
        mdd = px.get("mdd_5") if window == 5 else px.get("mdd_10")
        if mdd is not None:
            setm("max_drawdown", mdd * 100, f"worst peak-to-trough over {min(use_years, years):.1f} yrs of daily adjusted closes")
        else:
            excl("max_drawdown", "price history missing")
        cagr = px.get("cagr_5") if window == 5 else px.get("cagr_10")
        if cagr is not None:
            setm("long_run_return", cagr * 100, f"annualized total return over {min(use_years, years):.1f} yrs")
        else:
            excl("long_run_return", "price history missing")

    # 4 number of holdings (not provided by yfinance: optional override file)
    nh = raw.get("n_holdings")
    if nh is None and overrides:
        nh = overrides.get(raw["ticker"])
    if _num(nh):
        setm("n_holdings", float(nh), "from holdings override file" if raw.get("n_holdings") is None else "funds_data")
    else:
        excl("n_holdings", "not available from yfinance (supply results/etf_holdings_override.csv)")

    # 5 top-10 weight
    hold = raw.get("holdings") or []
    if hold:
        top10 = sum(h["weight"] for h in sorted(hold, key=lambda h: -h["weight"])[:10]) * 100
        setm("top10_weight", top10, "sum of the 10 largest holdings")
        if top10 > 50:
            flag("concentrated", f"Concentrated: top-10 holdings = {top10:.0f}% of the fund")
    else:
        excl("top10_weight", "holdings data missing")

    # 6, 7 size and liquidity
    aum = raw.get("aum")
    if _num(aum):
        setm("aum", aum / 1e6, "total net assets")
        if aum < 100e6:
            flag("closure_risk", f"Closure risk: AUM ${aum / 1e6:,.0f}M < $100M")
    else:
        excl("aum", "AUM missing")
    dv = raw.get("dollar_volume")
    if _num(dv):
        setm("dollar_volume", dv / 1e6, "3-month avg daily volume × price")
    else:
        excl("dollar_volume", "volume or price missing")

    # 8 holdings quality
    rows = []
    for h in sorted(hold, key=lambda h: -h["weight"])[:10]:
        s = h["symbol"]
        sc_ = hq_scores.get(s)
        if sc_ is not None:
            comp, note, ind = sc_["composite"], "", sc_.get("industry")
        else:
            comp, ind = None, None
            note = hq_skips.get(s) or "not scored yet"
        rows.append({"symbol": s, "name": h["name"], "weight": h["weight"], "industry": ind, "composite": comp, "note": note})
    hq, coverage = holdings_quality([(r["weight"], r["composite"]) for r in rows])
    if not rows:
        excl("holdings_quality", "no holdings data")
        flag("hq_unavailable", "Holdings quality unavailable (no holdings data)")
    elif coverage < MIN_HQ_COVERAGE:
        excl("holdings_quality", f"coverage {coverage:.0%} < 15%")
        flag("hq_unavailable", f"Holdings quality unavailable (scored holdings cover only {coverage:.0%} of the fund)")
    else:
        setm("holdings_quality", hq, f"weighted stock Composite of scored top-10 holdings; covers {coverage:.0%} of the fund")
        if coverage < 0.25:
            flag("low_lookthrough", f"Low look-through coverage ({coverage:.0%})")

    # 9 dividend yield (a genuine 0% is valid)
    dy = px.get("ttm_dividend_yield")
    if _num(dy):
        setm("dividend_yield", dy * 100, "trailing-12-month dividends ÷ price")
    else:
        excl("dividend_yield", "dividend history unavailable")

    return {"metrics": out, "flags": flags, "holdings": rows, "coverage": coverage,
            "history": {"years_available": years, "window": window, "used": None if use_years is None else min(use_years, years)}}


# ========================================================================
# Peer pools and percentile scores (Part E)
# ========================================================================

def _q(vals):
    a = np.array(vals, dtype=float)
    return {"p5": float(np.percentile(a, 5)), "p25": float(np.percentile(a, 25)), "median": float(np.median(a)),
            "p75": float(np.percentile(a, 75)), "p95": float(np.percentile(a, 95)), "n": len(a)}


def prepare_funds(raws, settings=None, hq_scores=None, hq_skips=None, all_raws=None):
    """raws: funds to score (any mix of categories). Percentiles are computed within
    each fund's OWN category; a metric with < 10 valid values in the category falls
    back to ALL scored equity ETFs (all_raws() + raws). Weight-independent."""
    settings = {**DEFAULT_SETTINGS, **(settings or {})}
    overrides = _holdings_override()
    funds = {r["ticker"]: {"raw": r, **compute_fund(r, settings, hq_scores, hq_skips, overrides)} for r in raws}

    universe = dict(funds)
    if all_raws is not None:
        for r in all_raws():
            if r["ticker"] not in universe:
                universe[r["ticker"]] = {"raw": r, **compute_fund(r, settings, hq_scores, hq_skips, overrides)}

    def valid(f, k):
        m = f["metrics"][k]
        return m["value"] if (m["status"] == OK and _num(m["value"])) else None

    all_vals = {k: {t: v for t, f in universe.items() if (v := valid(f, k)) is not None} for k in KEYS}
    all_pct = {}
    for k in KEYS:
        tk = list(all_vals[k])
        all_pct[k] = dict(zip(tk, percentile_scores([all_vals[k][t] for t in tk], METRIC_BY_KEY[k]["direction"] == "higher")))
    all_q = {k: (_q(list(all_vals[k].values())) if all_vals[k] else None) for k in KEYS}

    by_cat = {}
    for t, f in funds.items():
        by_cat.setdefault(f["raw"]["category"], []).append(t)
    bench = {}
    for cat, tickers in by_cat.items():
        bench[cat] = {}
        for k in KEYS:
            meta = METRIC_BY_KEY[k]
            own = {t: valid(funds[t], k) for t in tickers if valid(funds[t], k) is not None}
            # peers from the same category that are cached but not in `raws` (combined views add them in `funds`)
            level, pool = "category", own
            if len(own) < MIN_POOL:
                level, pool = "all", all_vals[k]
            if level == "category":
                tk = list(pool)
                pct = dict(zip(tk, percentile_scores([pool[t] for t in tk], meta["direction"] == "higher")))
            else:
                pct = all_pct[k]
            q = _q(list(pool.values())) if pool else None
            spread = (q["p75"] - q["p25"]) if q else None
            if q and not spread and all_q[k]:
                spread = all_q[k]["p75"] - all_q[k]["p25"]
            bench[cat][k] = {"level": level, "pool_n": len(pool), "category_n": len(own), "q": q, "spread": spread,
                             "vals": sorted(pool.values())}  # the pool itself, so Laura can take any percentile of it
            for t in tickers:
                f = funds[t]
                f["metrics"][k]["pct"] = pct.get(t) if f["metrics"][k]["status"] == OK else None
                if level == "all" and f["metrics"][k]["status"] == OK:
                    f["flags"].append({"code": f"category_fallback:{k}",
                                       "text": f"Category fallback: {meta['label']} ({len(own)} valid in category < {MIN_POOL})"})
    return {"funds": funds, "bench": bench, "settings": settings}


# ========================================================================
# Parts F + G
# ========================================================================

def default_ranges(bench_cat):
    """Group 25th-75th percentile (never zero-width) - the 'Industry average' button."""
    return laura.default_ranges(bench_cat)


def laura_ranges(bench_cat):
    """Laura Gao's standards (laura.LAURA_TIERS), per group - the DEFAULT ranges."""
    return laura.laura_ranges(bench_cat, "etf", METRICS)


def finalize(prep, weights=None, ranges_by_cat=None, blend=0.5, hard_limits=None, low_data_threshold=0.30, hard_ranges=None):
    """hard_ranges: {metric: (low or None, high or None)} used for that metric's hard-limit check INSTEAD of its
    comfort range (the optional 'Laura hard limits': expense <= 0.20% etc.). Ranges never change Composite."""
    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    hard_ranges = hard_ranges or {}
    ranges_by_cat = ranges_by_cat or {}
    hard_limits = hard_limits or {}
    total_w = sum(weights.values())
    results = []
    for t, f in prep["funds"].items():
        cat = f["raw"]["category"]
        bench = prep["bench"][cat]
        rng = {**default_ranges(bench), **laura_ranges(bench), **ranges_by_cat.get(cat, {})}  # each fund: its OWN group's Laura ranges
        rows, pct_scores, fit_scores, excluded_w = {}, {}, {}, 0.0
        for k in KEYS:
            m, meta, w, b = f["metrics"][k], METRIC_BY_KEY[k], weights.get(k, 0), bench[k]
            lo, hi = rng.get(k, (None, None))
            dist = fit = p = None
            if m["status"] == OK:
                p = m.get("pct")
                fit, dist = metric_fit(m["value"], lo, hi, b["spread"], meta["direction"])
            if p is None or fit is None:
                p = fit = None
                excluded_w += w
            pct_scores[k], fit_scores[k] = p, fit
            rows[k] = {**m, "pct": p, "fit": fit, "distance": dist, "weight": w, "range": (lo, hi),
                       "direction": meta["direction"], "units": meta["units"], "label": meta["label"]}
        composite = composite_score(pct_scores, weights)
        fit_total = composite_score(fit_scores, weights)
        den = sum(weights.get(k, 0) for k in KEYS if pct_scores[k] is not None)
        for k in KEYS:
            rows[k]["contribution"] = weights.get(k, 0) * pct_scores[k] / den if (pct_scores[k] is not None and den) else None
        if composite is None or fit_total is None:
            overall = composite if fit_total is None else fit_total
        else:
            overall = blend * fit_total + (1 - blend) * composite
        share = excluded_w / total_w if total_w else 0
        fails = []
        for k, on in hard_limits.items():
            if on:
                r = rows[k]
                lo, hi = hard_ranges.get(k, r["range"])
                v = r["value"]
                if v is None or r["status"] != OK or (lo is None and hi is None and k not in hard_ranges) \
                        or (lo is not None and v < lo) or (hi is not None and v > hi):
                    fails.append(k)
        results.append({
            "ticker": t, "name": f["raw"].get("name"), "category": cat, "metrics": rows, "composite": composite,
            "fit": fit_total, "overall": overall, "n_inside": sum(1 for k in KEYS if fit_scores[k] == 100),
            "n_total": len(KEYS), "excluded_weight_share": share, "coverage": 1 - share, "low_data": share > low_data_threshold,
            "flags": f["flags"], "holdings": f["holdings"], "hq_coverage": f["coverage"], "history": f["history"],
            "hard_limit_failed": bool(fails), "hard_limit_failures": fails,
        })
    return {"results": rank(results), "weights": weights, "ranges_by_cat": ranges_by_cat, "blend": blend, "total_weight": total_w}


def rank(results):
    """G3: low-data last; Overall desc; then Composite desc, fewer red flags, less excluded weight, ticker."""
    def key(r):
        return (r["low_data"], -(r["overall"] if r["overall"] is not None else -1),
                -(r["composite"] if r["composite"] is not None else -1), len(r["flags"]),
                r["excluded_weight_share"], r["ticker"])
    out = sorted(results, key=key)
    for i, r in enumerate(out, 1):
        r["rank"] = i
    return out


def export_rows(final, settings, label):
    stamp = datetime.now().isoformat(timespec="seconds")
    rows = []
    for r in final["results"]:
        row = {"timestamp": stamp, "view": label, "rank": r["rank"], "ticker": r["ticker"], "name": r["name"],
               "category": r["category"], "overall": r["overall"], "fit": r["fit"], "composite": r["composite"],
               "metrics_inside_range": f"{r['n_inside']}/{r['n_total']}", "data_coverage": r["coverage"],
               "low_data": r["low_data"], "red_flags": " | ".join(f["text"] for f in r["flags"]),
               "history_years_available": r["history"]["years_available"], "history_years_used": r["history"]["used"],
               "holdings_coverage": r["hq_coverage"]}
        for k in KEYS:
            m = r["metrics"][k]
            row.update({f"{k}__value": m["value"], f"{k}__status": m["status"], f"{k}__percentile_score": m["pct"],
                        f"{k}__fit": m["fit"], f"{k}__weight": m["weight"], f"{k}__range_low": m["range"][0],
                        f"{k}__range_high": m["range"][1], f"{k}__note": m["note"]})
        row.update({"setting_blend": final["blend"], "setting_tbill": settings["tbill"], "setting_history_window": settings["window"]})
        rows.append(row)
    return pd.DataFrame(rows)


# ========================================================================
# Part I tests
# ========================================================================

def _fake_fund(i, years=10.0, category="Test Cat", holdings=True, aum=5e9):
    rng = np.random.default_rng(100 + i)
    n = min(120, int(years * 12))
    return {"ticker": f"E{i:02d}", "name": f"Test Fund {i}", "category": category, "price": 50.0 + i, "aum": aum * (1 + i / 10),
            "avg_volume": 1e6, "dollar_volume": 5e7 * (1 + i / 10), "expense_ratio": 0.03 + 0.01 * i, "expense_source": "netExpenseRatio",
            "n_holdings": None,
            "holdings": [{"symbol": f"H{j}", "name": f"Holding {j}", "weight": 0.05 - 0.002 * j} for j in range(10)] if holdings else [],
            "px": {"years": years, "monthly_returns": list(rng.normal(0.008, 0.04, n)), "mdd_5": 0.15 + 0.01 * i, "mdd_10": 0.2 + 0.01 * i,
                   "cagr_5": 0.08 + 0.005 * i, "cagr_10": 0.1 + 0.004 * i, "ttm_dividend_yield": 0.01 + 0.002 * i}, "errors": []}


def run_self_tests():
    from etf_data import category_exclusion_reason, fund_exclusion_reason
    results = []

    def check(name, ok, detail=""):
        results.append({"test": name, "passed": bool(ok), "detail": detail})

    close = lambda a, b, tol=1e-9: len(a) == len(b) and all(abs(x - y) <= tol for x, y in zip(a, b))
    # 1 percentiles
    ok1 = (close(percentile_scores([1, 2, 3, 4, 5], True), [0, 25, 50, 75, 100]) and close(percentile_scores([1, 2, 3, 4, 5], False), [100, 75, 50, 25, 0])
           and close(percentile_scores([1, 2, 2, 3], True), [0, 50, 50, 100]) and percentile_scores([9], True) == [50.0])
    check("1. Percentiles: higher/lower-better, ties, n = 1", ok1)
    # 2 fit
    fh = [metric_fit(v, 10, 20, 10, "higher")[0] for v in (25, 15, 5, 0, -10)]
    fl = [metric_fit(v, 0.05, 0.15, 0.10, "lower")[0] for v in (0.10, 0.20, 0.25, 0.35)]
    check("2. Fit: higher [100,100,75,50,0]; lower-better [100,75,50,0]", close(fh, [100, 100, 75, 50, 0]) and close(fl, [100, 75, 50, 0]), f"{fh} {fl}")
    # 3 holdings quality
    q, cov = holdings_quality([(0.60, 80.0), (0.40, 50.0)])
    q3, cov3 = holdings_quality([(0.5, 80.0), (0.3, 50.0), (0.2, None)])
    check("3. Holdings quality: 60%@80 + 40%@50 = 68; unscored holding renormalized", abs(q - 68) < 1e-9 and abs(q3 - (0.5 * 80 + 0.3 * 50) / 0.8) < 1e-9,
          f"{q:.2f}; renormalized {q3:.2f}, coverage {cov3:.0%}")
    # 4 coverage < 15% -> excluded + flagged
    f = _fake_fund(1)
    f["holdings"] = [{"symbol": "A", "name": "A", "weight": 0.10}, {"symbol": "B", "name": "B", "weight": 0.50}]
    r = compute_fund(f, hq_scores={"A": {"composite": 70.0, "industry": "X"}}, hq_skips={"B": "foreign"})
    check("4. Coverage 10% < 15% → holdings quality excluded and flagged",
          r["metrics"]["holdings_quality"]["status"] == EXCL and any(x["code"] == "hq_unavailable" for x in r["flags"]), f"coverage {r['coverage']:.0%}")
    # 5 max drawdown
    check("5. Max drawdown [100,120,90,130,104] = 25%", abs(max_drawdown([100, 120, 90, 130, 104]) - 0.25) < 1e-12)
    # 6 downside deviation hand check: 58 x +1%, 2 x -5%, MAR 0 -> sqrt(2*0.05^2/60)*sqrt(12) = sqrt(0.001) = 3.16228%
    dd = downside_deviation([0.01] * 58 + [-0.05] * 2, 0.0)
    check("6. Downside deviation 60 months hand-computed 3.16228%", abs(dd - math.sqrt(0.001) * 100) < 0.01, f"{dd:.5f}% (hand: sqrt(0.001) = 3.16228%)")
    # 7 history windows
    r7 = compute_fund(_fake_fund(2, years=7.0), {"window": 10})
    r4 = compute_fund(_fake_fund(3, years=4.0), {"window": 10})
    ok7 = (r7["metrics"]["max_drawdown"]["status"] == OK and abs(r7["history"]["used"] - 7.0) < 1e-9 and any(x["code"] == "shorter_history" for x in r7["flags"])
           and all(r4["metrics"][k]["status"] == EXCL for k in ("downside_dev", "max_drawdown", "long_run_return"))
           and any(x["code"] == "short_history" for x in r4["flags"]))
    check("7. 7 yrs / 10-yr window → uses 7 yrs + flag; 4 yrs → 3 history metrics excluded + flag", ok7,
          f"used={r7['history']['used']}, 4-yr statuses={[r4['metrics'][k]['status'] for k in ('downside_dev', 'max_drawdown', 'long_run_return')]}")
    # 8 exclusion rescaling
    scores = {k: 10.0 + 5 * i for i, k in enumerate(KEYS)}
    scores["holdings_quality"] = None
    w = DEFAULT_WEIGHTS
    exp = sum(w[k] * scores[k] for k in KEYS if k != "holdings_quality") / (sum(w.values()) - w["holdings_quality"])
    check("8. Missing metric → Σ(other weight × score) ÷ Σ(other weights)", abs(composite_score(scores, w) - exp) < 1e-9, f"{composite_score(scores, w):.4f} vs {exp:.4f}")
    # 9 removal
    bad_cats = ["Trading--Leveraged Equity", "Trading--Inverse Equity", "Intermediate Government", "High Yield Bond", "Muni National Long", "Commodities Broad Basket",
                "Multicurrency", "Digital Assets", "Alternative Volatility", "Corporate Bond", "Bank Loan",
                "Defined Outcome", "Target Maturity", "Equity Hedged", "Uncategorized", "Money Market-Taxable"]
    bad_names = ["ProShares UltraPro QQQ", "Direxion Daily S&P 500 Bull 3X", "ProShares Short QQQ", "Inverse Fund -1x", "Bear 2x ETF", "Fund 3x Leveraged"]
    ok9 = (all(category_exclusion_reason(c) for c in bad_cats) and all(fund_exclusion_reason(n, 50.0) for n in bad_names)
           and category_exclusion_reason("Equity Precious Metals") is None and category_exclusion_reason("Communications") is None and category_exclusion_reason("Energy Limited Partnership") is None and category_exclusion_reason("Large Blend") is None
           and fund_exclusion_reason("Plain Value ETF", 3.0) is not None)
    check("9. Leveraged/inverse, bond, commodity, volatility, currency, crypto removed; Equity Precious Metals kept", ok9)
    # 10 / 11 range vs composite, tie-breakers
    funds = [_fake_fund(i) for i in range(12)]
    prep = prepare_funds(funds)
    base = finalize(prep)
    moved = finalize(prep, ranges_by_cat={"Test Cat": {"expense_ratio": (50.0, 60.0), "dividend_yield": (9.0, 9.5)}})
    b = {x["ticker"]: x for x in base["results"]}
    m = {x["ticker"]: x for x in moved["results"]}
    check("10. Changing a comfort range changes Fit only; Composite unchanged",
          all(abs(b[t]["composite"] - m[t]["composite"]) < 1e-12 for t in b) and any(abs(b[t]["fit"] - m[t]["fit"]) > 1e-6 for t in b))
    a = {"ticker": "B", "overall": 70, "composite": 60, "flags": [1], "excluded_weight_share": 0.1, "low_data": False}
    bb = {"ticker": "A", "overall": 70, "composite": 65, "flags": [1, 2], "excluded_weight_share": 0.1, "low_data": False}
    c = {"ticker": "C", "overall": 70, "composite": 65, "flags": [1], "excluded_weight_share": 0.1, "low_data": False}
    ld = {"ticker": "LD", "overall": 99, "composite": 99, "flags": [], "excluded_weight_share": 0.5, "low_data": True}
    order = [x["ticker"] for x in rank([a, bb, c, ld])]
    check("11. Tie-breakers (Composite, then flags) and low-data always last", order == ["C", "A", "B", "LD"], str(order))
    # 12 industry tagging (etf_industry.classify): 80% pure / 60% + name, name wins for sector funds, Select Sector = whole sector
    import etf_industry as ei
    ind_of = {"NVDA": "Semiconductors", "AMD": "Semiconductors", "AAPL": "Consumer Electronics", "MSFT": "Software—Infrastructure"}
    sec_of = {"Semiconductors": "Technology", "Gold": "Basic Materials", "Uranium": "Basic Materials"}
    pure = [{"symbol": "NVDA", "weight": 0.24}, {"symbol": "AMD", "weight": 0.10}, {"symbol": "AAPL", "weight": 0.06}]      # 85% semis (weights = share of the whole fund)
    mid = [{"symbol": "NVDA", "weight": 0.20}, {"symbol": "AMD", "weight": 0.08}, {"symbol": "AAPL", "weight": 0.12}]         # 70% semis
    mixed = [{"symbol": "NVDA", "weight": 0.12}, {"symbol": "AAPL", "weight": 0.16}, {"symbol": "MSFT", "weight": 0.12}]
    f = ei.classify
    t = [f(pure, "Large Growth", ind_of, sec_of, "Some Growth ETF")[0] == ei.BROAD,                             # size/style fund: holdings alone never tag it
         f(mid, "Large Growth", ind_of, sec_of, "Some Growth ETF")[0] == ei.BROAD,                            # 70% and the name is silent -> diversified
         f(mid, "Large Growth", ind_of, sec_of, "Semiconductor Leaders ETF")[0] == "Semiconductors",          # 70% + name agrees
         f(mid, "Technology", ind_of, sec_of, "Technology Select Sector SPDR ETF")[0] == ei.WHOLE + "Technology",  # Select Sector = whole sector
         f(mid, "Technology", ind_of, sec_of, "Tech Leaders ETF")[0] == ei.WHOLE + "Technology",              # 70%, sector fund, no name cue
         f(mixed, "Japan Stock", ind_of, sec_of, "Japan ETF")[0] == ei.INTL,
         f([], "Equity Precious Metals", ind_of, sec_of, "VanEck Gold Miners ETF")[0] == "Gold",              # sector fund: name decides
         f([], "Large Blend", ind_of, sec_of, "Gold Miners Growth ETF")[0] == ei.BROAD,                       # name alone never decides for broad funds
         f([], "Uncategorized", ind_of, sec_of, "Plain Fund")[0] is None,                                      # uncategorized and not provable -> dropped
         f([], "Uncategorized", ind_of, sec_of, "SPDR Gold Shares")[0] is None,                               # physical trust: no stock holdings -> out
         f([{"symbol": "NVDA", "weight": 1.0}], "Technology", ind_of, sec_of, "Nvidia ADRhedged")[0] is None,  # single-stock fund -> out
         f(pure, "Large Value", ind_of, sec_of, "Dividend Volatility Wtd ETF")[0] == ei.BROAD,                 # holdings alone never tag a size/style fund
         f(pure, "Technology", ind_of, sec_of, "Goldman Sachs Tech ETF")[0] == "Semiconductors",              # 'Goldman' is not 'gold'
         f(mid, "Uncategorized", ind_of, sec_of, "Global X Communication Services ETF")[0] == ei.WHOLE + "Communication Services"]
    check("12. Industry tag rules (80% pure, 60% + name, Select Sector, name for sector funds, uncategorized/trusts/single-stock dropped)", all(t), str(t))
    # 13-14 Laura standards per group + optional absolute hard limits
    two = [_fake_fund(i) for i in range(12)] + [_fake_fund(i + 30, category="Other Cat") for i in range(12)]
    for x in two[12:]:
        x["px"]["mdd_10"] += 0.15  # the second group draws down more
    p2 = prepare_funds(two)
    la, lb = laura_ranges(p2["bench"]["Test Cat"]), laura_ranges(p2["bench"]["Other Cat"])
    ma, mb = (float(np.median(p2["bench"][c]["max_drawdown"]["vals"])) for c in ("Test Cat", "Other Cat"))
    f2 = finalize(p2)["results"]
    own = all(r["metrics"]["max_drawdown"]["range"] == laura_ranges(p2["bench"][r["category"]])["max_drawdown"] for r in f2)
    check("13. Laura max-drawdown upper bound = each group's median, differs by group; each fund uses its own group's Laura range",
          abs(la["max_drawdown"][1] - ma) < 1e-9 and abs(lb["max_drawdown"][1] - mb) < 1e-9 and abs(ma - mb) > 1e-6 and own
          and abs(la["aum"][0] - p2["bench"]["Test Cat"]["aum"]["q"]["median"]) < 1e-9,  # higher-better risk metric: low = median
          f"Test Cat {la['max_drawdown'][1]:.2f} vs Other Cat {lb['max_drawdown'][1]:.2f}; own-group ranges={own}")
    hr = finalize(prep, hard_limits={"expense_ratio": True}, hard_ranges={"expense_ratio": (None, 0.08)})["results"]
    fails = sorted(r["ticker"] for r in hr if r["hard_limit_failed"])
    exp_f = sorted(f["ticker"] for f in funds if f["expense_ratio"] > 0.08 + 1e-12)
    same_c = all(abs({x["ticker"]: x for x in hr}[t]["composite"] - b[t]["composite"]) < 1e-12 for t in b)
    check("14. Laura hard limit (expense <= 0.08% here) uses the absolute cap, not the comfort range; Composite unchanged",
          fails == exp_f and len(fails) > 0 and same_c, f"failed {fails}")
    return results
