"""
Industry fundamentals: live pull, parallel fetch, and caching.
===================================================================
WHAT THIS DOES (plain English):

Ties industry_universe.py (find every tradeable stock in an industry)
together with fundamentals.py (the 15-metric SEC+yfinance calculator) to
build a complete, scoreable picture of a whole industry - fetched in
parallel for speed, respecting SEC's rate limit, and cached so picking
the same industry again is instant instead of re-pulling everything.

If a company's fetch fails (missing CIK, malformed filing, network
hiccup), we skip it, record why, and keep going - one bad ticker never
blocks the rest of the industry from being scored.
"""

import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import pandas as pd

import fundamentals as fm
import industry_universe as iu
import sec_edgar as sec

DB_PATH = "industry_fundamentals.db"
METRIC_NAMES = fm.METRIC_NAMES


def init_db(db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    metric_cols = ", ".join(f'"{m}" REAL, "{m}_source" TEXT' for m in METRIC_NAMES)
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS industry_fundamentals (
            ticker TEXT,
            industry TEXT,
            name TEXT,
            price REAL,
            exchange TEXT,
            market_cap REAL,
            sec_industry TEXT,
            latest_10k_date TEXT,
            {metric_cols},
            flags TEXT,
            fetched_date TEXT,
            PRIMARY KEY (ticker, industry)
        )
    """)
    conn.commit()
    return conn


# ========================================================================
# Fetching (one company, then the whole industry in parallel)
# ========================================================================

def fetch_one(ticker_info, config_path="config.yaml"):
    """ticker_info: {'ticker','name','price','exchange','market_cap'} from
    industry_universe. Returns a flat dict ready to cache, or raises if
    the fetch fails outright (caller collects these as failures)."""
    result = fm.compute_metrics(ticker_info["ticker"], config_path)
    row = {
        "ticker": ticker_info["ticker"], "name": ticker_info.get("name"),
        "price": ticker_info.get("price"), "exchange": ticker_info.get("exchange"),
        "market_cap": ticker_info.get("market_cap"),
        "sec_industry": result.get("industry"),
        "latest_10k_date": (result.get("latest_10k") or {}).get("filed_date"),
        "flags": "; ".join(result.get("flags", [])),
    }
    for m in METRIC_NAMES:
        metric = result["metrics"].get(m, {})
        row[m] = metric.get("value")
        row[f"{m}_source"] = metric.get("source")
    return row


def fetch_industry_parallel(tickers_info, max_workers=8, progress_callback=None, config_path="config.yaml"):
    """Returns (rows, failures). failures: {ticker: error message}.
    progress_callback(completed, total, last_ticker) is called after each
    company finishes, in whatever order they complete (not submission
    order) - good for a live "34 of 114..." progress display."""
    # Pre-warm the shared ticker->CIK map once, single-threaded, so the
    # thread pool doesn't race to fetch/write that one shared cache file.
    sec.load_ticker_cik_map(config_path)

    rows, failures = [], {}
    total = len(tickers_info)
    completed = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_ticker = {executor.submit(fetch_one, info, config_path): info["ticker"] for info in tickers_info}
        for future in as_completed(future_to_ticker):
            ticker = future_to_ticker[future]
            completed += 1
            try:
                rows.append(future.result())
            except Exception as e:
                failures[ticker] = str(e)
            if progress_callback:
                progress_callback(completed, total, ticker)

    return rows, failures


# ========================================================================
# Cache
# ========================================================================

def save_to_cache(industry, rows, db_path=DB_PATH):
    conn = init_db(db_path)
    today_str = str(date.today())
    conn.execute("DELETE FROM industry_fundamentals WHERE industry = ?", (industry,))

    col_names = ["ticker", "industry", "name", "price", "exchange", "market_cap", "sec_industry", "latest_10k_date"]
    for m in METRIC_NAMES:
        col_names += [m, f"{m}_source"]
    col_names += ["flags", "fetched_date"]
    quoted_cols = ", ".join(f'"{c}"' for c in col_names)
    placeholders = ", ".join(["?"] * len(col_names))

    for row in rows:
        values = [row.get("ticker"), industry, row.get("name"), row.get("price"), row.get("exchange"),
                   row.get("market_cap"), row.get("sec_industry"), row.get("latest_10k_date")]
        for m in METRIC_NAMES:
            values.append(row.get(m))
            values.append(row.get(f"{m}_source"))
        values.append(row.get("flags"))
        values.append(today_str)
        conn.execute(f"INSERT OR REPLACE INTO industry_fundamentals ({quoted_cols}) VALUES ({placeholders})", values)

    conn.commit()
    conn.close()


def load_from_cache(industry, db_path=DB_PATH):
    if not os.path.exists(db_path):
        return None
    conn = sqlite3.connect(db_path)
    df = pd.read_sql("SELECT * FROM industry_fundamentals WHERE industry = ?", conn, params=(industry,))
    conn.close()
    if df.empty:
        return None
    return df, df["fetched_date"].iloc[0]


def cached_industries(db_path=DB_PATH):
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT DISTINCT industry, MAX(fetched_date) FROM industry_fundamentals GROUP BY industry").fetchall()
    conn.close()
    return [{"industry": r[0], "fetched_date": r[1]} for r in rows]


# ========================================================================
# Orchestration
# ========================================================================

def get_or_refresh_industry(industry, force_refresh=False, min_price=5.0, max_workers=8,
                             progress_callback=None, config_path="config.yaml", db_path=DB_PATH):
    """Cache hit (and not force_refresh) -> instant load. Otherwise pulls
    the live universe, fetches every company's fundamentals in parallel,
    and caches the result."""
    if not force_refresh:
        cached = load_from_cache(industry, db_path)
        if cached is not None:
            df, fetched_date = cached
            return {"df": df, "fetched_date": fetched_date, "from_cache": True,
                    "found": None, "kept": len(df), "failures": {}, "elapsed_seconds": 0.0}

    t0 = time.time()
    kept_list, found, kept_count = iu.pull_industry_universe(industry, min_price=min_price)
    rows, failures = fetch_industry_parallel(
        kept_list, max_workers=max_workers, progress_callback=progress_callback, config_path=config_path
    )
    save_to_cache(industry, rows, db_path)
    df, fetched_date = load_from_cache(industry, db_path)
    elapsed = time.time() - t0

    return {"df": df, "fetched_date": fetched_date, "from_cache": False,
            "found": found, "kept": kept_count, "failures": failures, "elapsed_seconds": elapsed}


def df_to_scoring_inputs(df):
    """Converts the cached DataFrame into the {ticker: {metric: value}}
    and {ticker: industry} shapes fundamental_scoring.score_universe
    expects."""
    metrics_by_ticker, industry_by_ticker = {}, {}
    for _, row in df.iterrows():
        metrics_by_ticker[row["ticker"]] = {m: row[m] for m in METRIC_NAMES}
        # Peer group = the industry the user PICKED (yfinance's screener
        # taxonomy), never SEC's SIC description - SIC splits one industry
        # into dozens of 1-3 company groups (Packaged Foods -> 18 groups),
        # which made every percentile/benchmark meaningless.
        industry_by_ticker[row["ticker"]] = row["industry"]
    return metrics_by_ticker, industry_by_ticker
