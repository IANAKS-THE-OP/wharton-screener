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


# ------------------------------------------------------------------ Laura standards = the session default
# Bump LAURA_VERSION whenever the default weights, LAURA_TIERS or the default penalty strength change, and paste the
# new laura.fingerprint() into LAURA_FINGERPRINT (the ETF self-tests fail until both are done). Every open session whose
# saved version differs is then reset to Laura standards on its next run (app.py calls ensure_current first thing).
LAURA_VERSION = 3
LAURA_FINGERPRINT = "87fec91d075d"
VERSION_KEY = "laura_version"
# session keys that hold weights, Laura limits and penalty strength on both pages (what "Laura standards" restores)
RESET_PREFIXES = ("s2w|", "etf2w|", "s2_penalty", "etf2_penalty", "stklim|", "stkext|", "etflim|", "etfext|",
                  "stkcmp__rmode", "etfcmp_|morningstar_rmode")  # + Compare's limit mode (Laura / Industry average)


def session_defaults(asset):
    """Laura-standard session values for one page: {"s2w|roic": 7.0, ..., "s2_penalty": 0.4} (stocks) or the etf2 keys.
    The pages seed their weight and penalty widgets from this; limits are seeded per group by range_ui.seed_laura."""
    import etf_scoring
    import scoring2
    if asset == "stock":
        d = {f"s2w|{m['key']}": float(m["weight"]) for m in scoring2.METRICS}
        d["s2_penalty"] = scoring2.DEFAULT_PENALTY
    else:
        d = {f"etf2w|{m['key']}": float(m["weight"]) for m in etf_scoring.METRICS if m["key"] != "n_holdings"}
        d["etf2_penalty"] = etf_scoring.DEFAULT_PENALTY
    return d


def fingerprint():
    """Hash of everything the Laura defaults depend on: both weight sets, LAURA_TIERS and both default penalties."""
    import hashlib
    import json
    blob = json.dumps({"stock": session_defaults("stock"), "etf": session_defaults("etf"), "tiers": LAURA_TIERS},
                      sort_keys=True, default=list)
    return hashlib.sha1(blob.encode()).hexdigest()[:12]


def ensure_current(state, version=LAURA_VERSION):
    """First load, or the defaults changed since this session saved its settings: drop every saved weight, Laura limit
    and penalty strength, so the pages re-seed them with Laura standards this run. Returns True if it reset.
    state: st.session_state (or any dict, for the self-tests)."""
    if state.get(VERSION_KEY) == version:
        return False
    for k in [k for k in list(state.keys()) if str(k).startswith(RESET_PREFIXES)]:
        del state[k]
    state[VERSION_KEY] = version
    return True


def run_self_tests():
    results = []

    def check(name, ok, detail=""):
        results.append({"test": name, "passed": bool(ok), "detail": str(detail)})

    def page_seed(state):  # what the pages do before their widgets: seed only keys that are missing
        for asset in ("stock", "etf"):
            for k, v in session_defaults(asset).items():
                state.setdefault(k, v)

    # 1 first load: empty session -> version stored, then the pages seed Laura weights and k = 0.4
    s = {}
    first = ensure_current(s)
    page_seed(s)
    exp = {**session_defaults("stock"), **session_defaults("etf")}
    check("1. First load: Laura standards (version stored; every weight and both penalty strengths = Laura defaults)",
          first and s[VERSION_KEY] == LAURA_VERSION and all(s[k] == v for k, v in exp.items()) and s["s2_penalty"] == 0.4,
          f"reset={first}, version={s.get(VERSION_KEY)}")
    # 2 same version: custom settings are kept
    s["s2w|roic"], s["etf2_penalty"], s["stklim|Gold|roic"] = 20.0, 0.9, 1.23
    again = ensure_current(s)
    check("2. Same weights-version: custom weights / limits / k are kept", not again and s["s2w|roic"] == 20.0 and s["stklim|Gold|roic"] == 1.23)
    # 3 a session saved under an older version is reset to Laura standards; other keys survive
    s[VERSION_KEY], s["industry_choice"], s["s2h|Gold|roic"] = LAURA_VERSION - 1, "Gold", True
    reset = ensure_current(s)
    gone = not any(str(k).startswith(RESET_PREFIXES) for k in s)
    page_seed(s)
    check("3. Changed weights-version: weights, Laura limits and k reset to Laura standards; other settings kept",
          reset and gone and s["s2w|roic"] == exp["s2w|roic"] and s["etf2_penalty"] == exp["etf2_penalty"]
          and "stklim|Gold|roic" not in s and s["industry_choice"] == "Gold" and s["s2h|Gold|roic"] is True and s[VERSION_KEY] == LAURA_VERSION,
          f"reset={reset}")
    # 4 the version was bumped for the current defaults
    fp = fingerprint()
    check(f"4. LAURA_VERSION matches the current defaults (fingerprint {fp}; if it changed, bump LAURA_VERSION and update LAURA_FINGERPRINT)",
          fp == LAURA_FINGERPRINT, f"code {fp} vs recorded {LAURA_FINGERPRINT}")
    return results
