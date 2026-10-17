"""
Laura Gao's standards: the DEFAULT "Laura limit" of every metric on the Stocks and ETF pages.
==============================================================================================
Laura is a moderate-risk client: no big drops, consistent growth, nothing heavy.
Each metric has ONE limit, on its bad side: a CEILING for lower-is-better metrics, a FLOOR for
higher-is-better ones. The limit is a PERCENTILE of that peer group's own pool (the same pool, including
the < 10-peer fallback pool, that the percentile scores use). Values are never multiplied by a percentage,
so negative metrics (Sloan, delta ROIC, net-cash net debt/EBITDA) work the same way as positive ones.

  risk tier         limit = industry p50 (median)
  consistency tier  limit = industry p40
  moderate metrics  limit = industry p75 (lower-better) / p25 (higher-better)

Fit is one-sided (scoring2.metric_fit): 100 on the good side of the limit; past it,
Fit = 100 x max(0, 1 - (distance / spread) / 2), spread = industry p75 - p25 (moving the limit never changes it).
"Industry average" = p75 (lower-better) / p25 (higher-better) for every metric.
Edit LAURA_TIERS to change a tier or a percentile; both pages read it.
Pure functions (no Streamlit), imported by scoring2.py, etf_scoring.py and the pages.
"""

import numpy as np

# metric -> (tier, percentile of the peer pool used as the limit).
# Metrics not listed are "moderate": limit at the industry p75 (lower-better) / p25 (higher-better).
LAURA_TIERS = {
    "stock": {
        # risk tier: limit at the industry median
        "downside_dev": ("risk", 50), "max_drawdown": ("risk", 50), "net_debt_ebitda": ("risk", 50),
        "sloan": ("risk", 50), "interest_coverage": ("risk", 50),
        # consistency tier: limit at the 40th percentile
        "roic": ("consistency", 40), "gross_profitability": ("consistency", 40),
        "delta_roic": ("consistency", 40), "delta_op_margin": ("consistency", 40),
        # moderate (p25 floor): revenue_growth, fcf_yield, earnings_yield, lynch, shareholder_yield
    },
    "etf": {
        "expense_ratio": ("risk", 50), "downside_dev": ("risk", 50), "max_drawdown": ("risk", 50),
        "top10_weight": ("risk", 50), "aum": ("risk", 50), "dollar_volume": ("risk", 50),
        "holdings_quality": ("consistency", 40),
        # moderate (p25 floor): dividend_yield, long_run_return. n_holdings is always excluded.
    },
}

# Laura's old ABSOLUTE ETF numbers. Not defaults: only used by the optional
# "Laura hard limits" checkbox (off by default), as (low, high) with None = unbounded.
LAURA_HARD_LIMITS = {
    "expense_ratio": (None, 0.20),    # expense ratio <= 0.20%
    "max_drawdown": (None, 30.0),     # max drawdown <= 30%
    "aum": (1000.0, None),            # AUM >= $1B ($ millions)
    "top10_weight": (None, 40.0),     # top-10 weight <= 40%
}

AVG_PCT = {"lower": 75, "higher": 25}  # "Industry average": the worse quartile is the limit

_PCT_NAME = {50: "industry median", 25: "industry 25th percentile", 75: "industry 75th percentile",
             10: "industry 10th percentile", 90: "industry 90th percentile"}


def pct_name(p):
    return _PCT_NAME.get(p, f"industry {p}th percentile")


def _pool_percentile(b, p):
    vals = b.get("vals") if b else None
    if vals:
        return float(np.percentile(np.asarray(vals, dtype=float), p))
    q = (b or {}).get("q") or {}
    return {50: q.get("median"), 25: q.get("p25"), 75: q.get("p75"), 5: q.get("p5"), 95: q.get("p95"),
            10: q.get("p10"), 90: q.get("p90")}.get(p)


def laura_metric(b, direction, tier):
    """-> {"limit": float or None, "tier", "pct", "side" ("ceiling" / "floor"), "note"}.
    b = one metric's benchmark entry (needs b["q"], ideally b["vals"] = the pool's values)."""
    side = "ceiling" if direction == "lower" else "floor"
    if not b or not b.get("q"):
        return {"limit": None, "tier": None, "pct": None, "side": side, "note": "No valid values in this peer group."}
    name, p = tier if tier is not None else ("moderate", AVG_PCT[direction])
    lim = _pool_percentile(b, p)
    if lim is None or not np.isfinite(lim):
        return {"limit": None, "tier": name, "pct": p, "side": side, "note": "No valid values in this peer group."}
    sym = "≤" if direction == "lower" else "≥"
    return {"limit": float(lim), "tier": name, "pct": p, "side": side,
            "note": f"Laura ({name}): {side} {sym} {lim:.4g} ({pct_name(p)})"}


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


def laura_limits(bench, asset, metrics):
    """{key: Laura limit} for every metric with data (the default)."""
    return {k: v["limit"] for k, v in laura_info(bench, asset, metrics).items() if v["limit"] is not None}


def default_limits(bench, metrics):
    """'Industry average' button: limit = industry p75 (lower-better) / p25 (higher-better) for every metric with data."""
    out = {}
    for m in metrics:
        b = bench.get(m["key"])
        if b and b.get("q"):
            v = _pool_percentile(b, AVG_PCT[m["direction"]])
            if v is not None and np.isfinite(v):
                out[m["key"]] = float(v)
    return out
