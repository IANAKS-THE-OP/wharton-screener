"""
Wharton Global High School Investment Competition - Asset Screener
====================================================================
WHAT THIS SCRIPT DOES (in plain English):

1. Reads your list of candidate tickers from candidates.csv.
2. Reads all the rules and weights from config.yaml.
3. Downloads live data for each ticker with yfinance (free Yahoo Finance data).
4. Applies "hard filters" - pass/fail rules. A ticker that fails is removed
   and we record WHY, but it still shows up in the output table for review.
5. Scores everything that passed: each metric becomes a 0-100 percentile
   compared only to OTHER TICKERS IN THE SAME SECTOR (so a bond fund isn't
   compared to an emerging-markets stock fund). Metrics where "lower is
   better" (like expense ratio) are flipped before scoring.
6. Combines the percentiles into one weighted final score per bucket.
7. Saves a dated CSV to results/ and prints a readable summary.
8. Checks a simple "sell rule": if a HELD ticker has fallen outside the
   top N for several runs in a row, it gets flagged for human review.

This tool only RANKS candidates. It does not predict future returns.
A human (you and your team) makes the final buy/sell decision.
"""

import glob
import os
from datetime import date

import numpy as np
import pandas as pd
import yaml
import yfinance as yf

# ----------------------------------------------------------------------
# This list collects every (ticker, field) pair we could NOT get data
# for. We never silently treat "missing" as zero - we log it and move on,
# so a human can see exactly what data gaps exist before trusting a score.
# ----------------------------------------------------------------------
MISSING_LOG = []


def log_missing(ticker, field):
    MISSING_LOG.append((ticker, field))


# ========================================================================
# STEP 1 & 2: Load the config file and the candidates file
# ========================================================================

def load_config(path="config.yaml"):
    with open(path, "r") as f:
        return yaml.safe_load(f)


def load_candidates(path="candidates.csv"):
    df = pd.read_csv(path)
    df["ticker"] = df["ticker"].str.strip().str.upper()
    df["bucket"] = df["bucket"].str.strip().str.lower()
    df["held"] = df["held"].astype(str).str.strip().str.lower()
    return df


# ========================================================================
# STEP 3: Pull data with yfinance, field by field, logging anything missing
# ========================================================================

def safe_get(info, keys, ticker, label):
    """Try each possible field name in `keys` (yfinance isn't always
    consistent about naming) and return the first one that has a real
    value. If none of them do, log it as missing and return NaN -
    NEVER return 0, because 0 would look like a real (and terrible) score."""
    for key in keys:
        value = info.get(key)
        if value is not None:
            return value
    log_missing(ticker, label)
    return np.nan


def compute_price_history_metrics(t, ticker):
    """Calculates 3-year annualized return, 3-year annualized volatility,
    and max drawdown directly from daily price history. We compute these
    ourselves instead of trusting yfinance's summary fields, because the
    summary fields are missing for many ETFs."""
    result = {"return_3yr": np.nan, "volatility": np.nan, "max_drawdown": np.nan}

    try:
        hist = t.history(period="3y")
    except Exception:
        hist = pd.DataFrame()

    # Need roughly a year of trading days at minimum to trust the numbers.
    if hist.empty or len(hist) < 252:
        log_missing(ticker, "price_history (not enough history)")
        return result

    closes = hist["Close"].dropna()
    daily_returns = closes.pct_change().dropna()

    # Annualize daily volatility: multiply by sqrt(252 trading days/year).
    result["volatility"] = daily_returns.std() * (252 ** 0.5)

    total_return = closes.iloc[-1] / closes.iloc[0] - 1
    years = (closes.index[-1] - closes.index[0]).days / 365.25
    if years > 0:
        result["return_3yr"] = (1 + total_return) ** (1 / years) - 1

    # Max drawdown: the worst drop from a prior peak, as a negative number.
    running_max = closes.cummax()
    drawdown = (closes - running_max) / running_max
    result["max_drawdown"] = drawdown.min()

    return result


def fetch_raw_data(ticker):
    """Fetches every raw field we might need for ANY bucket. Buckets just
    pick and choose which of these fields they actually score on."""
    try:
        t = yf.Ticker(ticker)
        info = t.info
    except Exception:
        info = {}
        log_missing(ticker, "info (lookup failed - check ticker spelling / internet)")
        t = yf.Ticker(ticker)  # keep an object around so history() can still try

    data = {"quote_type": info.get("quoteType", "UNKNOWN")}

    data["price"] = safe_get(info, ["currentPrice", "regularMarketPrice"], ticker, "price")
    data["market_cap"] = safe_get(info, ["marketCap"], ticker, "market_cap")
    data["total_assets"] = safe_get(info, ["totalAssets"], ticker, "total_assets")
    data["avg_volume"] = safe_get(info, ["averageVolume"], ticker, "avg_volume")
    data["roe"] = safe_get(info, ["returnOnEquity"], ticker, "return_on_equity")

    # Stocks are classified by sector/industry; ETFs are classified by category
    # instead (yfinance never returns sector/industry for an ETF, or category
    # for a stock). We keep both so the full-market screener can group each
    # asset type by whichever classification actually applies to it.
    data["sector"] = info.get("sector")
    data["industry"] = info.get("industry")
    data["category"] = info.get("category")

    # --- Fields yfinance reports as PERCENT numbers, not fractions ---
    # e.g. an expense ratio of 0.15 means 0.15%, i.e. a fraction of 0.0015.
    # We confirmed this against real fund expense ratios before trusting it.
    expense_raw = safe_get(info, ["netExpenseRatio", "annualReportExpenseRatio"], ticker, "expense_ratio")
    data["expense_ratio"] = expense_raw / 100 if pd.notna(expense_raw) else np.nan

    dividend_raw = safe_get(info, ["dividendYield"], ticker, "dividend_yield")
    data["dividend_yield"] = dividend_raw / 100 if pd.notna(dividend_raw) else np.nan

    debt_raw = safe_get(info, ["debtToEquity"], ticker, "debt_to_equity")
    data["debt_to_equity"] = debt_raw / 100 if pd.notna(debt_raw) else np.nan

    # --- Fields only used by the full-market screener (v2) ---
    data["pe_ratio"] = safe_get(info, ["trailingPE", "forwardPE"], ticker, "pe_ratio")

    # Stocks and ETFs report beta under different field names.
    if data["quote_type"] == "ETF":
        data["beta"] = safe_get(info, ["beta3Year"], ticker, "beta")
    else:
        data["beta"] = safe_get(info, ["beta"], ticker, "beta")

    data["revenue_growth"] = safe_get(info, ["revenueGrowth"], ticker, "revenue_growth")
    data["eps_growth"] = safe_get(info, ["earningsGrowth"], ticker, "eps_growth")

    free_cashflow = safe_get(info, ["freeCashflow"], ticker, "free_cashflow")
    if pd.notna(free_cashflow) and pd.notna(data["market_cap"]) and data["market_cap"] > 0:
        data["fcf_yield"] = free_cashflow / data["market_cap"]
    else:
        data["fcf_yield"] = np.nan
        log_missing(ticker, "fcf_yield (needs both free cash flow and market cap)")

    # Credit quality (ETFs only): % of a bond fund's holdings rated at least
    # investment-grade (AAA/AA/A/BBB) or backed by the US government. This is
    # a separate network call beyond .info, so we only make it for ETFs and
    # don't let a failure here break the rest of the fetch.
    data["pct_investment_grade"] = np.nan
    if data["quote_type"] == "ETF":
        try:
            ratings = t.funds_data.bond_ratings
            if ratings:
                # NOTE: these rating buckets sum to ~1.0 on their own (verified
                # directly) - "us_government" is a separate, overlapping stat
                # (e.g. an AA-rated government holding counts in both), so it
                # must NOT also be added here or investment-grade % exceeds 100%.
                investment_grade_keys = ["aaa", "aa", "a", "bbb"]
                data["pct_investment_grade"] = sum(ratings.get(k, 0) or 0 for k in investment_grade_keys)
            else:
                log_missing(ticker, "pct_investment_grade (not a bond fund, or no rating data)")
        except Exception:
            log_missing(ticker, "pct_investment_grade (funds_data lookup failed)")

    # Price-history-based metrics (return, volatility, drawdown)
    data.update(compute_price_history_metrics(t, ticker))

    return data


# ========================================================================
# STEP 4: Hard filters (pass/fail)
# ========================================================================

def check_hard_filter(row, cfg):
    """Returns (passed: bool, reason: str or None).
    If a required field is missing, we FAIL the filter rather than guess -
    a human should look at it, not the screener silently pass it through."""
    stock_f = cfg["filters"]["stocks"]
    etf_f = cfg["filters"]["etfs"]
    bond_f = cfg["filters"]["bond_etfs"]

    def etf_checks():
        if pd.isna(row["expense_ratio"]):
            return False, "missing data for filter: expense_ratio"
        if row["expense_ratio"] > etf_f["max_expense_ratio"]:
            return False, f"expense ratio {row['expense_ratio']*100:.2f}% is above {etf_f['max_expense_ratio']*100:.2f}% limit"

        if pd.isna(row["total_assets"]):
            return False, "missing data for filter: total_assets"
        if row["total_assets"] < etf_f["min_total_assets"]:
            return False, f"total assets ${row['total_assets']:,.0f} below ${etf_f['min_total_assets']:,} limit"

        if pd.isna(row["avg_volume"]):
            return False, "missing data for filter: avg_volume"
        if row["avg_volume"] < etf_f["min_avg_volume"]:
            return False, f"avg volume {row['avg_volume']:,.0f} below {etf_f['min_avg_volume']:,} limit"

        return True, None

    if row["bucket"] == "bonds":
        passed, reason = etf_checks()
        if not passed:
            return passed, reason
        if row["ticker"] in bond_f["high_yield_exclude_list"]:
            return False, "excluded: on high-yield exclude list (not Treasury / investment-grade)"
        return True, None

    if row["quote_type"] == "ETF":
        return etf_checks()

    # Otherwise, treat it as a stock.
    if pd.isna(row["price"]):
        return False, "missing data for filter: price"
    if row["price"] < stock_f["min_price"]:
        return False, f"price ${row['price']:.2f} below ${stock_f['min_price']} minimum"

    if pd.isna(row["market_cap"]):
        return False, "missing data for filter: market_cap"
    if row["market_cap"] < stock_f["min_market_cap"]:
        return False, f"market cap ${row['market_cap']:,.0f} below ${stock_f['min_market_cap']:,} minimum"

    if pd.isna(row["debt_to_equity"]):
        return False, "missing data for filter: debt_to_equity"
    if row["debt_to_equity"] > stock_f["max_debt_to_equity"]:
        return False, f"debt/equity {row['debt_to_equity']:.2f} above {stock_f['max_debt_to_equity']} limit"

    return True, None


# ========================================================================
# STEP 5: Build the raw metric columns each bucket scores on
# ========================================================================

def add_bucket_metrics(df, bucket, target_duration):
    """Adds the raw metric columns a bucket needs, and returns the list of
    (metric_name, column_name, higher_is_better) tuples used for scoring.
    metric_name must match the keys under weights.<bucket> in config.yaml."""

    if bucket == "bonds":
        df["yield_raw"] = df["dividend_yield"]
        df["duration_fit_raw"] = (df["duration_years"] - target_duration).abs()
        df["expense_ratio_raw"] = df["expense_ratio"]
        spec = [
            ("yield", "yield_raw", True),
            ("duration_fit", "duration_fit_raw", False),  # smaller distance = better
            ("expense_ratio", "expense_ratio_raw", False),
        ]

    elif bucket == "conservative":
        df["volatility_raw"] = df["volatility"]
        df["expense_ratio_raw"] = df["expense_ratio"]
        df["return_3yr_raw"] = df["return_3yr"]
        df["dividend_yield_raw"] = df["dividend_yield"]
        spec = [
            ("volatility", "volatility_raw", False),
            ("expense_ratio", "expense_ratio_raw", False),
            ("return_3yr", "return_3yr_raw", True),
            ("dividend_yield", "dividend_yield_raw", True),
        ]

    elif bucket == "growth":
        df["return_3yr_raw"] = df["return_3yr"]
        df["volatility_raw"] = df["volatility"]
        # "Quality": ETFs are judged on low expense ratio, stocks on high ROE.
        # These are different units, so we only ever rank an ETF against other
        # ETFs, and a stock against other stocks (see score_bucket below).
        df["quality_raw"] = np.where(df["quote_type"] == "ETF", df["expense_ratio"], df["roe"])
        df["max_drawdown_raw"] = df["max_drawdown"]  # less negative = better
        spec = [
            ("return_3yr", "return_3yr_raw", True),
            ("volatility", "volatility_raw", False),
            ("quality", "quality_raw", None),  # direction depends on quote_type, handled specially
            ("max_drawdown", "max_drawdown_raw", True),
        ]

    else:
        raise ValueError(f"Unknown bucket: {bucket}")

    return df, spec


# ========================================================================
# STEP 5 (continued): turn raw metrics into 0-100 percentiles, then a
# single weighted final score
# ========================================================================

def percentile_rank(series, higher_is_better):
    """0-100 percentile within the group. NaNs are left as NaN (pandas
    .rank() skips them automatically) so a missing metric never looks like
    a score of 0."""
    return series.rank(pct=True, ascending=higher_is_better) * 100


def score_bucket(df, bucket, spec, weights):
    passed_mask = df["passed"]
    pct_cols = {}

    for metric, raw_col, higher_is_better in spec:
        pct_col = f"{metric}_pct"
        df[pct_col] = np.nan
        pct_cols[metric] = pct_col

        if not passed_mask.any():
            continue

        if metric == "quality" and bucket == "growth":
            # Rank ETFs against ETFs and stocks against stocks, within sector,
            # since expense ratio and ROE aren't comparable numbers.
            sub = df.loc[passed_mask]
            for (_, qtype), group_idx in sub.groupby(["sector", "quote_type"]).groups.items():
                hib = qtype != "ETF"  # ETF -> lower expense ratio is better; stock -> higher ROE is better
                df.loc[group_idx, pct_col] = percentile_rank(df.loc[group_idx, raw_col], hib)
        else:
            sub = df.loc[passed_mask]
            ranked = sub.groupby("sector")[raw_col].transform(lambda s: percentile_rank(s, higher_is_better))
            df.loc[passed_mask, pct_col] = ranked

    def weighted_score(row):
        available = {m: row[pct_cols[m]] for m in weights if pd.notna(row[pct_cols[m]])}
        if not available:
            return np.nan
        # If a metric is missing for this ticker, drop it and redistribute
        # its weight proportionally across the metrics we DO have - we never
        # treat a missing metric as a zero.
        weight_sum = sum(weights[m] for m in available)
        return round(sum(weights[m] * available[m] for m in available) / weight_sum, 2)

    df["final_score"] = np.nan
    df.loc[passed_mask, "final_score"] = df.loc[passed_mask].apply(weighted_score, axis=1)

    df["rank"] = np.nan
    df.loc[passed_mask, "rank"] = df.loc[passed_mask, "final_score"].rank(ascending=False, method="min")

    return df, pct_cols


# ========================================================================
# STEP 8: Sell-rule check against previously saved runs
# ========================================================================

def sell_rule_check(cfg, candidates, results_dir="results"):
    """Returns a dict describing the outcome instead of only printing, so
    both the command-line script and the app can show the same result."""
    lookback = cfg["sell_rule"]["lookback_runs"]
    threshold = cfg["sell_rule"]["rank_threshold"]

    files = sorted(glob.glob(os.path.join(results_dir, "screen_*.csv")))

    if len(files) < lookback:
        return {
            "status": "not_enough_runs",
            "message": f"Only {len(files)} saved run(s) so far - need {lookback} to evaluate.",
            "lookback": lookback,
            "threshold": threshold,
            "flagged": [],
        }

    recent_runs = [pd.read_csv(f) for f in files[-lookback:]]
    held_tickers = candidates.loc[candidates["held"] == "yes", "ticker"].tolist()

    if not held_tickers:
        return {
            "status": "no_held_tickers",
            "message": "No tickers are currently marked held = yes.",
            "lookback": lookback,
            "threshold": threshold,
            "flagged": [],
        }

    flagged = []
    for ticker in held_tickers:
        outside_every_run = True
        for run_df in recent_runs:
            match = run_df[run_df["ticker"] == ticker]
            rank = match["rank"].iloc[0] if not match.empty else np.nan
            if pd.notna(rank) and rank <= threshold:
                outside_every_run = False
                break
        if outside_every_run:
            flagged.append(ticker)

    return {
        "status": "flagged" if flagged else "ok",
        "message": (
            f"REVIEW NEEDED: {flagged} - held, but outside top {threshold} for the last {lookback} runs."
            if flagged
            else "No held tickers triggered the sell-rule review."
        ),
        "lookback": lookback,
        "threshold": threshold,
        "flagged": flagged,
    }


# ========================================================================
# Putting it all together
# ========================================================================

def run_screen(config_path="config.yaml", candidates_path="candidates.csv", results_dir="results", verbose=True):
    MISSING_LOG.clear()  # start each run with a clean missing-data log

    cfg = load_config(config_path)
    candidates = load_candidates(candidates_path)

    if verbose:
        print(f"Fetching data for {len(candidates)} tickers from Yahoo Finance...")
    rows = []
    for _, c in candidates.iterrows():
        if verbose:
            print(f"  {c['ticker']}...")
        raw = fetch_raw_data(c["ticker"])
        if c["bucket"] == "bonds" and pd.isna(c.get("duration_years")):
            log_missing(c["ticker"], "duration_years (yfinance has no duration field - fill this in manually in candidates.csv from the fund issuer's website)")
        rows.append({**c.to_dict(), **raw})
    df = pd.DataFrame(rows)

    df["passed"], df["filter_reason"] = zip(*df.apply(lambda r: check_hard_filter(r, cfg), axis=1))

    bucket_results = {}
    for bucket in ["bonds", "conservative", "growth"]:
        bdf = df[df["bucket"] == bucket].copy()
        bdf, spec = add_bucket_metrics(bdf, bucket, cfg["target_duration_years"])
        bdf, pct_cols = score_bucket(bdf, bucket, spec, cfg["weights"][bucket])
        bdf = bdf.sort_values(by=["passed", "final_score"], ascending=[False, False])
        bucket_results[bucket] = bdf

    combined = pd.concat(bucket_results.values(), ignore_index=True)
    combined["run_date"] = str(date.today())

    os.makedirs(results_dir, exist_ok=True)
    out_path = os.path.join(results_dir, f"screen_{date.today()}.csv")
    combined.to_csv(out_path, index=False)

    sell_rule_result = sell_rule_check(cfg, candidates, results_dir)
    missing_log_snapshot = list(MISSING_LOG)

    if verbose:
        print_summary(bucket_results, cfg)
        print(f"\nSell-rule check (flag HELD tickers outside top {sell_rule_result['threshold']} "
              f"for {sell_rule_result['lookback']} runs in a row):")
        print(f"  {sell_rule_result['message']}")
        print_missing_log()
        print(f"\nFull results saved to: {out_path}")

    return {
        "combined": combined,
        "bucket_results": bucket_results,
        "sell_rule": sell_rule_result,
        "missing_log": missing_log_snapshot,
        "out_path": out_path,
        "top_n": cfg["top_n_per_bucket"],
    }


def print_summary(bucket_results, cfg):
    top_n = cfg["top_n_per_bucket"]
    for bucket, bdf in bucket_results.items():
        print(f"\n=== {bucket.upper()} ===")
        passed = bdf[bdf["passed"]].sort_values("rank")
        failed = bdf[~bdf["passed"]]

        if passed.empty:
            print("  No candidates passed the hard filters.")
        else:
            print(f"  Top {min(top_n, len(passed))} by score:")
            cols = ["rank", "ticker", "sector", "final_score"]
            print(passed[cols].head(top_n).to_string(index=False))

        if not failed.empty:
            print("  Filtered out:")
            for _, r in failed.iterrows():
                print(f"    {r['ticker']}: {r['filter_reason']}")


def print_missing_log():
    if not MISSING_LOG:
        print("\nNo missing-data warnings.")
        return
    print("\nMissing-data warnings (field not available from yfinance):")
    by_ticker = {}
    for ticker, field in MISSING_LOG:
        by_ticker.setdefault(ticker, []).append(field)
    for ticker, fields in by_ticker.items():
        print(f"  {ticker}: {', '.join(fields)}")


if __name__ == "__main__":
    run_screen()
