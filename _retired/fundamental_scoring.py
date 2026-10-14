"""
Comfort-range + quality scoring for the 15-metric fundamental screener.
===========================================================================
Replaces pass/fail filtering with "closeness" scoring: every metric gets
a comfort RANGE (not a single cutoff), and a stock is scored on how close
its value sits to - or inside - that range, rather than being thrown out
for missing one cutoff by a hair.

Two scores per stock, blended into one:
  FIT     - how well the stock's numbers sit inside our chosen comfort
            ranges (this is OUR preference, fully adjustable).
  QUALITY - how the stock ranks against its industry peers, independent
            of our personal targets (a pure percentile rank).
  OVERALL - fit_weight x Fit + (1 - fit_weight) x Quality.

The scoring formula is the same for every metric, direction-aware:
  spread   = industry 75th percentile - 25th percentile for that metric
             (falls back to the whole-market spread if 0 or missing)
  distance = how far the value sits OUTSIDE the comfort range, counting
             only the side that's actually penalized for that metric's
             direction (0 if inside, or if only on the "never penalized"
             side)
  score    = 100 x max(0, 1 - (distance / spread) / 2)

So: inside the comfort range = 100. Exactly one spread outside = 50.
Two or more spreads outside = 0. Everything else is partial credit.
"""

import pandas as pd
import yaml


def percentile_rank(series, higher_is_better):
    """0-100 percentile within the group. NaNs are left as NaN (pandas
    .rank() skips them) so a missing metric never looks like a score of 0."""
    return series.rank(pct=True, ascending=higher_is_better) * 100


def _missing(value):
    """True for both None and NaN - a value loaded back from SQLite (a
    NULL) comes back as float('nan'), not None, so checking `is None`
    alone silently lets NaN leak into arithmetic and poison every score
    downstream. Use this everywhere a metric might be absent."""
    return value is None or (isinstance(value, float) and pd.isna(value))


# Purely a UI grouping - lets the app show 3 digestible clusters instead
# of a flat wall of 15 sliders. Doesn't affect scoring at all.
METRIC_GROUPS = {
    "Valuation": ["pe", "peg", "ev_ebitda", "pb", "fcf_yield"],
    "Profitability & Growth": ["roic", "operating_margin", "roe", "revenue_cagr_3yr", "eps_cagr_3yr"],
    "Risk & Leverage": ["net_debt_ebitda", "interest_coverage", "current_ratio", "beta", "ocf_to_ni"],
}


def load_metric_config(config_path="config.yaml"):
    with open(config_path) as f:
        cfg = yaml.safe_load(f)
    return cfg["fundamental_metrics"]


# ========================================================================
# Industry benchmarks (median, P25, P75, spread) - outlier-clipped
# ========================================================================

def clip_outliers(series, lo_pct=0.05, hi_pct=0.95):
    """Clips to the 5th-95th percentile before computing benchmarks, so a
    couple of extreme outliers don't distort the median/spread for
    everyone else in the group."""
    series = series.dropna()
    if series.empty:
        return series
    lo, hi = series.quantile(lo_pct), series.quantile(hi_pct)
    return series.clip(lo, hi)


def compute_industry_benchmarks(df, metric_cols, industry_col="industry"):
    """Returns {industry_or_'ALL': {metric: {median, p25, p75, spread, n}}}.
    'ALL' is the whole-market benchmark, used as a fallback when an
    industry's own spread is 0 or missing (e.g. too few peers)."""
    benchmarks = {}

    def _bench_for(group):
        out = {}
        for metric in metric_cols:
            if metric not in group.columns:
                continue
            clipped = clip_outliers(group[metric])
            if clipped.empty:
                continue
            p25, median, p75 = clipped.quantile(0.25), clipped.quantile(0.50), clipped.quantile(0.75)
            out[metric] = {"median": float(median), "p25": float(p25), "p75": float(p75),
                            "spread": float(p75 - p25), "n": int(len(clipped))}
        return out

    for industry, group in df.groupby(industry_col):
        benchmarks[industry] = _bench_for(group)
    benchmarks["ALL"] = _bench_for(df)
    return benchmarks


def get_benchmark(benchmarks, industry, metric):
    """The industry's own benchmark, with its spread backfilled from the
    whole-market ('ALL') benchmark if 0/missing - never divides by zero."""
    industry_bm = benchmarks.get(industry, {}).get(metric)
    all_bm = benchmarks.get("ALL", {}).get(metric)
    if industry_bm is None:
        return all_bm
    if not industry_bm.get("spread"):
        if all_bm and all_bm.get("spread"):
            return {**industry_bm, "spread": all_bm["spread"]}
    return industry_bm


# ========================================================================
# Per-metric scoring
# ========================================================================

def metric_fit_score(value, comfort_range, direction, spread):
    """Returns (score, distance) or (None, None) if it can't be scored."""
    if _missing(value) or comfort_range is None or not spread or spread <= 0:
        return None, None

    lo, hi = comfort_range
    if direction == "higher":
        distance = max(0.0, lo - value)   # below range is penalized; above never is
    elif direction == "lower":
        distance = max(0.0, value - hi)   # above range is penalized; below never is
    else:  # sweet_spot: penalized on both sides
        distance = max(0.0, lo - value, value - hi)

    score = 100 * max(0.0, 1 - (distance / spread) / 2)
    return score, distance


def metric_quality_score(value, peer_values, direction, comfort_range=None):
    """Percentile rank (0-100) within the peer group, flipped for
    lower-is-better metrics. Sweet-spot metrics rank by closeness to the
    middle of the comfort range instead."""
    if _missing(value):
        return None
    peers = pd.Series(peer_values).dropna()
    if peers.empty:
        return None

    if direction == "sweet_spot" and comfort_range:
        mid = sum(comfort_range) / 2
        closeness = -abs(value - mid)
        peer_closeness = -(peers - mid).abs()
        combined = pd.concat([peer_closeness, pd.Series([closeness])], ignore_index=True)
        ranked = percentile_rank(combined, higher_is_better=True)
        return float(ranked.iloc[-1])

    higher_is_better = direction == "higher"
    combined = pd.concat([peers, pd.Series([value])], ignore_index=True)
    ranked = percentile_rank(combined, higher_is_better=higher_is_better)
    return float(ranked.iloc[-1])


def plain_english_note(metric_label, value, comfort_range, direction, distance, score):
    if _missing(value):
        return "No data available for this metric."
    lo, hi = comfort_range
    if distance == 0:
        if direction == "higher" and value >= hi:
            return f"{metric_label} {value:.3g} is above our {lo:.3g}-{hi:.3g} range - not penalized (higher is better)."
        if direction == "lower" and value <= lo:
            return f"{metric_label} {value:.3g} is below our {lo:.3g}-{hi:.3g} range - not penalized (lower is better)."
        return f"{metric_label} {value:.3g} is inside our {lo:.3g}-{hi:.3g} comfort range."
    side = "above" if value > hi else "below"
    severity = "well " if score < 50 else "a bit "
    return f"{metric_label} {value:.3g} is {severity}{side} our {lo:.3g}-{hi:.3g} range (score {score:.0f}/100)."


# ========================================================================
# Scoring one stock against its peer universe
# ========================================================================

def default_comfort_range(metric, direction, benchmark, metric_config):
    """Industry P25-P75 for ordinary metrics; the configured sweet-spot
    default range for current_ratio/ocf_to_ni/beta."""
    if benchmark:
        return (benchmark["p25"], benchmark["p75"])
    if direction == "sweet_spot":  # no industry data at all -> configured fallback
        rng = metric_config.get("sweet_spot_default_range", {}).get(metric)
        if rng:
            return tuple(rng)
    return None


def score_stock(ticker, metrics_row, industry, peer_df, benchmarks, metric_config,
                 comfort_ranges=None, weights=None, fit_weight=0.5, hard_limits=None):
    """
    metrics_row: {metric: value} for this one stock.
    peer_df: DataFrame of all stocks in the same industry (for Quality's
             percentile ranking) - includes this stock.
    comfort_ranges: {metric: (lo, hi)} overrides; defaults to industry P25-P75
                     (or the sweet-spot default) when not provided.
    weights: {metric: weight}; defaults to equal weight across all 15.
    hard_limits: {metric: True/False} - only True entries can disqualify a stock.
    """
    directions = metric_config["direction"]
    metric_names = list(directions.keys())
    comfort_ranges = comfort_ranges or {}
    weights = weights or {m: 1.0 for m in metric_names}
    hard_limits = hard_limits or {}

    breakdown = {}
    fit_scores, quality_scores = {}, {}
    hard_limit_failed = False
    hard_limit_failures = []
    n_missing = 0

    for metric in metric_names:
        direction = directions[metric]
        value = metrics_row.get(metric)
        benchmark = get_benchmark(benchmarks, industry, metric)
        comfort_range = comfort_ranges.get(metric) or default_comfort_range(metric, direction, benchmark, metric_config)
        spread = benchmark["spread"] if benchmark else None

        if _missing(value):
            n_missing += 1
            breakdown[metric] = {
                "value": None, "industry_median": benchmark["median"] if benchmark else None,
                "target": comfort_range, "fit": None, "quality": None, "weight": weights.get(metric, 0),
                "note": "Missing - left out and other metrics re-weighted.",
            }
            continue

        fit, distance = metric_fit_score(value, comfort_range, direction, spread)
        peer_values = peer_df[metric] if metric in peer_df.columns else pd.Series(dtype=float)
        quality = metric_quality_score(value, peer_values.drop(peer_values.index[peer_values.index == ticker], errors="ignore"),
                                        direction, comfort_range)

        if hard_limits.get(metric) and comfort_range is not None:
            lo, hi = comfort_range
            if value < lo or value > hi:
                hard_limit_failed = True
                hard_limit_failures.append(metric)

        note = plain_english_note(metric, value, comfort_range, direction, distance or 0, fit if fit is not None else 0)

        fit_scores[metric] = fit
        quality_scores[metric] = quality
        breakdown[metric] = {
            "value": value, "industry_median": benchmark["median"] if benchmark else None,
            "target": comfort_range, "distance_outside": distance, "fit": fit, "quality": quality,
            "weight": weights.get(metric, 0), "note": note,
        }

    def weighted_avg(score_dict):
        available = {m: s for m, s in score_dict.items() if not _missing(s)}
        if not available:
            return None
        total_weight = sum(weights.get(m, 0) for m in available)
        if total_weight == 0:
            return None
        return sum(weights.get(m, 0) * s for m, s in available.items()) / total_weight

    fit = weighted_avg(fit_scores)
    quality = weighted_avg(quality_scores)
    overall = None
    if fit is not None and quality is not None:
        overall = fit_weight * fit + (1 - fit_weight) * quality
    elif fit is not None:
        overall = fit
    elif quality is not None:
        overall = quality

    targets_met = sum(1 for m, s in fit_scores.items() if s == 100)
    low_data = n_missing > metric_config.get("low_data_threshold", 0.30) * len(metric_names)

    return {
        "ticker": ticker, "industry": industry, "fit": fit, "quality": quality, "overall": overall,
        "targets_met": targets_met, "n_metrics": len(metric_names), "n_missing": n_missing,
        "low_data": low_data, "hard_limit_failed": hard_limit_failed,
        "hard_limit_failures": hard_limit_failures, "breakdown": breakdown,
    }


def score_universe(metrics_by_ticker, industry_by_ticker, metric_config,
                    comfort_ranges=None, weights=None, fit_weight=0.5, hard_limits=None):
    """metrics_by_ticker: {ticker: {metric: value}}. industry_by_ticker:
    {ticker: industry}. Returns (results_list, benchmarks).

    IMPORTANT: this NEVER removes a stock for failing a hard limit - every
    stock you pass in comes back out, scored, with `hard_limit_failed` and
    `hard_limit_failures` set so the CALLER can decide what to do (e.g.
    filter for display, but fall back to showing everyone rather than an
    empty page if a hard limit would zero out the whole list). Sorted
    low_data-last, then by Overall score descending."""
    metric_names = list(metric_config["direction"].keys())
    rows = []
    for ticker, metrics_row in metrics_by_ticker.items():
        row = {"ticker": ticker, "industry": industry_by_ticker.get(ticker, "Unknown")}
        row.update({m: metrics_row.get(m) for m in metric_names})
        rows.append(row)
    df = pd.DataFrame(rows).set_index("ticker")

    benchmarks = compute_industry_benchmarks(df, metric_names, industry_col="industry")

    results = []
    for ticker, metrics_row in metrics_by_ticker.items():
        industry = industry_by_ticker.get(ticker, "Unknown")
        peer_df = df[df["industry"] == industry]
        result = score_stock(
            ticker, metrics_row, industry, peer_df, benchmarks, metric_config,
            comfort_ranges=comfort_ranges, weights=weights, fit_weight=fit_weight, hard_limits=hard_limits,
        )
        results.append(result)

    results.sort(key=lambda r: (r["low_data"], -(r["overall"] if r["overall"] is not None else -1)))
    return results, benchmarks


# ========================================================================
# Self-tests
# ========================================================================

def self_tests(verbose=True):
    results = []

    def check(name, actual, expected, tol=0.01):
        passed = actual is not None and abs(actual - expected) <= tol
        results.append({"name": name, "passed": passed, "detail": f"actual={actual}, expected={expected}"})

    # 1. Value inside the range -> 100
    score, _ = metric_fit_score(value=15, comfort_range=(10, 20), direction="higher", spread=10)
    check("Value inside the comfort range -> 100", score, 100)

    # 2. Lower-is-better metric below the range -> 100 (never penalized on that side)
    score, _ = metric_fit_score(value=8, comfort_range=(10, 20), direction="lower", spread=10)
    check("Lower-is-better metric below the range -> 100", score, 100)

    # 3. Exactly 1 spread outside -> 50
    score, _ = metric_fit_score(value=0, comfort_range=(10, 20), direction="higher", spread=10)
    check("Exactly 1 spread outside the range -> 50", score, 50)

    # 4. 2+ spreads outside -> 0 (and stays at 0, doesn't go negative, further out)
    score, _ = metric_fit_score(value=-10, comfort_range=(10, 20), direction="higher", spread=10)
    check("Exactly 2 spreads outside -> 0", score, 0)
    score_further, _ = metric_fit_score(value=-30, comfort_range=(10, 20), direction="higher", spread=10)
    check("3+ spreads outside -> still 0 (floored, not negative)", score_further, 0)

    # Backward-compatible check from the original spec: ROE 12%, target 15%,
    # spread 10% -> fit 85 (a TARGET is just a zero-width comfort range).
    score, _ = metric_fit_score(value=0.12, comfort_range=(0.15, 0.15), direction="higher", spread=0.10)
    check("ROE 12% vs target 15%, spread 10% -> fit 85 (TARGET mode)", score, 85)

    # 5. A stock missing 3/15 metrics but strong elsewhere should outrank
    # a stock with all 15 metrics sitting exactly at the industry median.
    metric_config = load_metric_config()
    metric_names = list(metric_config["direction"].keys())

    # Build a synthetic peer universe: 20 stocks with normal-ish spread
    # per metric, all in one industry, so medians/spreads are well-defined.
    import random
    random.seed(42)
    metrics_by_ticker = {}
    for i in range(20):
        metrics_by_ticker[f"PEER{i}"] = {m: 1.0 + random.uniform(-0.3, 0.3) for m in metric_names}

    # Stock A: missing 3 metrics, the other 12 are strong IN THEIR OWN
    # DIRECTION (high for higher-is-better, low for lower-is-better) - so
    # fit=100 and quality is high on every metric it has. Sweet-spot
    # metrics are left out (their fixed config range doesn't interact
    # with the peer group the same way, which isn't what this test is
    # checking) along with 2 others, totaling 3 missing.
    directions = metric_config["direction"]
    favorable = {}
    for m in metric_names:
        if directions[m] == "higher":
            favorable[m] = 2.0   # clearly above every peer (peers are ~0.7-1.3)
        elif directions[m] == "lower":
            favorable[m] = 0.3   # clearly below every peer
    dropped = set(metric_names) - set(favorable)  # the 3 sweet-spot metrics
    metrics_by_ticker["STOCK_A_STRONG_BUT_MISSING"] = favorable

    # Stock B: has all 15 metrics, every one exactly at the peer median (~1.0).
    metrics_by_ticker["STOCK_B_AVERAGE_COMPLETE"] = {m: 1.0 for m in metric_names}

    industry_by_ticker = {t: "TestIndustry" for t in metrics_by_ticker}
    results_list, _ = score_universe(metrics_by_ticker, industry_by_ticker, metric_config, fit_weight=0.5)
    by_ticker = {r["ticker"]: r for r in results_list}
    a_overall = by_ticker["STOCK_A_STRONG_BUT_MISSING"]["overall"]
    b_overall = by_ticker["STOCK_B_AVERAGE_COMPLETE"]["overall"]
    passed = a_overall is not None and b_overall is not None and a_overall > b_overall
    results.append({
        "name": "Stock missing 3/15 but strong elsewhere outranks a complete-but-average stock",
        "passed": passed,
        "detail": f"strong-but-missing overall={a_overall:.1f}, average-complete overall={b_overall:.1f}",
    })

    if verbose:
        print("=== FUNDAMENTAL SCORING SELF-TESTS ===")
        for r in results:
            status = "PASS" if r["passed"] else "FAIL"
            print(f"  [{status}] {r['name']}: {r['detail']}")
        all_passed = all(r["passed"] for r in results)
        print(f"  Overall: {'ALL PASSED' if all_passed else 'SOME FAILED'}")

    return results
