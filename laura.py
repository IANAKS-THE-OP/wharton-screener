"""
Laura Gao's standards: the DEFAULT comfort ranges for the Stocks and ETF pages.
===============================================================================
Laura is a moderate-risk client: no big drops, consistent growth, nothing heavy.
Her ranges are RELATIVE to each industry (each ETF group): the "bad side" bound of a
metric is moved to a stricter PERCENTILE of that peer group's own pool (the same pool,
including the < 10-peer fallback pool, that the percentile scores use). Values are never
multiplied by a percentage, so negative metrics (Sloan, delta ROIC, net-cash net debt/EBITDA)
work the same way as positive ones.

  lower-is-better  -> range = (industry p25, industry pXX)   only "value > high" is penalized
  higher-is-better -> range = (industry pXX, industry p75)   only "value < low" is penalized

The SPREAD used by the Fit formula stays industry p75 - p25; tightening a bound never
changes it. Edit LAURA_TIERS to change a tier or a percentile; both pages read it.
Pure functions (no Streamlit), imported by scoring2.py, etf_scoring.py and the pages.
"""

import numpy as np

# metric -> (tier, percentile of the peer pool used for the bad-side bound).
# Metrics not listed are "moderate": industry p25-p75, unchanged.
LAURA_TIERS = {
    "stock": {
        # risk tier: bad-side bound at the industry median
        "downside_dev": ("risk", 50), "max_drawdown": ("risk", 50), "net_debt_ebitda": ("risk", 50),
        "sloan": ("risk", 50), "interest_coverage": ("risk", 50),
        # consistency tier: bad-side bound at the 40th percentile
        "roic": ("consistency", 40), "gross_profitability": ("consistency", 40),
        "delta_roic": ("consistency", 40), "delta_op_margin": ("consistency", 40),
        # moderate (p25-p75): revenue_growth, fcf_yield, earnings_yield, lynch, shareholder_yield
    },
    "etf": {
        "expense_ratio": ("risk", 50), "downside_dev": ("risk", 50), "max_drawdown": ("risk", 50),
        "top10_weight": ("risk", 50), "aum": ("risk", 50), "dollar_volume": ("risk", 50),
        "holdings_quality": ("consistency", 40),
        # moderate (p25-p75): dividend_yield, long_run_return. n_holdings is always excluded.
    },
}

# Laura's old ABSOLUTE ETF numbers. No longer ranges: only used by the optional
# "Laura hard limits" checkbox (off by default), as (low, high) with None = unbounded.
LAURA_HARD_LIMITS = {
    "expense_ratio": (None, 0.20),    # expense ratio <= 0.20%
    "max_drawdown": (None, 30.0),     # max drawdown <= 30%
    "aum": (1000.0, None),            # AUM >= $1B ($ millions)
    "top10_weight": (None, 40.0),     # top-10 weight <= 40%
}

_EPS = 1e-12
_PCT_NAME = {50: "industry median", 25: "industry 25th percentile", 75: "industry 75th percentile"}


def pct_name(p):
    return _PCT_NAME.get(p, f"industry {p}th percentile")


def industry_default(b):
    """Industry 25th-75th percentile. Never zero-width: if p25 == p75 use p5-p95, and if the
    whole pool is one value, a small band around it (the Fit spread is handled by the engine)."""
    q = b.get("q") if b else None
    if not q:
        return None
    lo, hi = q["p25"], q["p75"]
    if hi - lo > _EPS:
        return lo, hi
    if q["p95"] - q["p5"] > _EPS:
        return q["p5"], q["p95"]
    d = max(abs(lo) * 0.05, 0.01)
    return lo - d, hi + d


def _pool_percentile(b, p):
    vals = b.get("vals")
    if vals:
        return float(np.percentile(np.asarray(vals, dtype=float), p))
    q = b.get("q") or {}
    return {50: q.get("median"), 25: q.get("p25"), 75: q.get("p75"), 5: q.get("p5"), 95: q.get("p95")}.get(p)


def laura_metric(b, direction, tier):
    """-> {"range": (lo, hi) or None, "tier", "pct", "bound", "fallback": bool, "note"}.
    b = one metric's benchmark entry (needs b["q"], ideally b["vals"] = the pool's values)."""
    default = industry_default(b)
    if default is None:
        return {"range": None, "tier": None, "pct": None, "bound": None, "fallback": False,
                "note": "No valid values in this peer group."}
    if tier is None:
        return {"range": default, "tier": "moderate", "pct": None, "bound": None, "fallback": False,
                "note": "Laura (moderate): industry 25th-75th percentile, unchanged."}
    name, p = tier
    bound = _pool_percentile(b, p)
    q = b["q"]
    if direction == "lower":
        lo, hi = q["p25"], bound
        ok = bound is not None and np.isfinite(bound) and hi - lo > _EPS
    else:
        lo, hi = bound, q["p75"]
        ok = bound is not None and np.isfinite(bound) and hi - lo > _EPS
    if not ok:
        return {"range": default, "tier": name, "pct": p, "bound": bound, "fallback": True,
                "note": f"Laura ({name}): the {pct_name(p)} equals the other bound here, so the industry default "
                        f"(25th-75th) is used for this metric."}
    side = "<=" if direction == "lower" else ">="
    return {"range": (float(lo), float(hi)), "tier": name, "pct": p, "bound": float(bound), "fallback": False,
            "note": f"Laura ({name}): {side} {bound:.4g} ({pct_name(p)})"}


def laura_info(bench, asset, metrics):
    """bench: {metric_key: benchmark entry} for ONE peer group. metrics: the engine's METRICS list.
    -> {key: laura_metric(...)} for every metric that has data."""
    tiers = LAURA_TIERS[asset]
    out = {}
    for m in metrics:
        b = bench.get(m["key"])
        if not b or not b.get("q"):
            continue
        out[m["key"]] = laura_metric(b, m["direction"], tiers.get(m["key"]))
    return out


def laura_ranges(bench, asset, metrics):
    return {k: v["range"] for k, v in laura_info(bench, asset, metrics).items() if v["range"]}


def default_ranges(bench):
    """Industry 25th-75th for every metric with data ('Industry average' button)."""
    return {k: r for k, b in bench.items() if (r := industry_default(b))}
