"""
Full-market data fetcher for the v2 screener.
================================================
WHAT THIS DOES (plain English):

candidates.csv only has the handful of tickers you typed in by hand. This
script instead builds a much bigger pool automatically:

  - All current S&P 500 stocks (a standard, well-known "broad U.S. market"
    list - a true scan of every US stock and ETF (8,000+ tickers) would
    take over an hour and risk getting rate-limited by Yahoo, so S&P 500
    is the practical starting universe. See the note at the bottom of this
    file for how to widen it later.)
  - A curated list of ~150 well-known ETFs spanning bonds, dividend/value,
    low-volatility, broad market, growth, sector, and international funds.

For each ticker it reuses screener.fetch_raw_data() (the exact same
fetch logic the candidates.csv screener uses - same missing-data handling,
same unit-conversion fixes) and saves everything into a local SQLite
database (market_cache.db) instead of re-downloading on every page view.

It's throttled (a short pause between tickers) and resumable (skips
anything already fetched today), so re-running it after an interruption
picks up where it left off instead of starting over.

Run it directly:
    ./venv/bin/python3 fetch_universe.py                 # full run
    ./venv/bin/python3 fetch_universe.py --limit 30       # quick test run
"""

import argparse
import io
import sqlite3
import time
from datetime import date

import pandas as pd
import requests

import screener

DB_PATH = "market_cache.db"

# A browser-style User-Agent is required - Wikipedia blocks plain script requests.
WIKI_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
}
SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

# ------------------------------------------------------------------------
# A curated list of well-known, liquid ETFs across the categories Laura's
# strategy actually cares about (bonds, conservative, growth/sector/intl).
# There's no single free "list every ETF" source the way there is for
# stocks, so this list is hand-picked instead of scraped. yfinance's own
# `category` field (fetched live, not guessed here) is what actually drives
# grouping - this list just needs good category coverage, not exact labels.
# ------------------------------------------------------------------------
CURATED_ETFS = [
    # Treasury / government bonds
    "IEF", "GOVT", "SHY", "SHV", "BIL", "TLT", "VGSH", "VGIT", "VGLT", "SCHO", "SCHR",
    "SPTS", "SPTL", "TIP", "VTIP", "STIP",
    # Aggregate / core bond
    "BND", "AGG", "SCHZ", "BIV", "BLV", "BSV",
    # Corporate investment-grade bonds
    "VCIT", "VCSH", "VCLT", "LQD", "SPIB", "IGIB",
    # High-yield (kept so the hard filter actually has something to exclude)
    "HYG", "JNK", "SHYG", "USHY",
    # Dividend / value / low-volatility / quality (conservative bucket)
    "SCHD", "VIG", "VYM", "DVY", "HDV", "USMV", "SPLV", "QUAL", "SPHQ", "NOBL",
    # Broad market
    "VTI", "ITOT", "SPY", "VOO", "IVV", "SCHB",
    # Large growth / momentum
    "VUG", "SCHG", "MGK", "SPYG", "QQQ", "MTUM", "IWF",
    # Small / mid cap
    "IJR", "VB", "IJH", "VO", "SCHA", "SCHM",
    # International developed
    "VEA", "EFA", "SCHF", "IEFA", "SPDW",
    # Emerging markets
    "VWO", "EEM", "IEMG", "SCHE",
    # Single-country / regional growth
    "EWT", "EWJ", "EWY", "EWZ", "INDA", "MCHI", "FXI",
    # Sector funds
    "XLK", "XLV", "XLF", "XLE", "XLI", "XLY", "XLP", "XLU", "XLRE", "XLB", "XLC",
    "VGT", "VHT", "VFH", "VDE", "VIS", "VCR", "VDC", "VPU", "VNQ", "VAW", "VOX",
    # ESG
    "ESGU", "ESGD", "ESGE", "SUSA",
    # Commodities / gold
    "GLD", "IAU", "SLV", "DBC", "USO",
]


def fetch_sp500_tickers():
    """Returns a DataFrame[ticker, sector, sub_industry] from Wikipedia's
    current S&P 500 constituent table."""
    resp = requests.get(SP500_URL, headers=WIKI_HEADERS, timeout=30)
    resp.raise_for_status()
    table = pd.read_html(io.StringIO(resp.text))[0]
    df = table[["Symbol", "GICS Sector", "GICS Sub-Industry"]].copy()
    df.columns = ["ticker", "gics_sector", "gics_sub_industry"]
    # yfinance uses a hyphen for share classes (BRK-B), Wikipedia uses a dot (BRK.B)
    df["ticker"] = df["ticker"].str.replace(".", "-", regex=False)
    return df


# ========================================================================
# SQLite cache
# ========================================================================

def init_db(db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS assets (
            ticker TEXT PRIMARY KEY,
            asset_type TEXT,
            quote_type TEXT,
            sector TEXT,
            industry TEXT,
            category TEXT,
            group_label TEXT,
            price REAL,
            market_cap REAL,
            total_assets REAL,
            avg_volume REAL,
            roe REAL,
            expense_ratio REAL,
            dividend_yield REAL,
            debt_to_equity REAL,
            return_3yr REAL,
            volatility REAL,
            max_drawdown REAL,
            pe_ratio REAL,
            beta REAL,
            revenue_growth REAL,
            eps_growth REAL,
            fcf_yield REAL,
            pct_investment_grade REAL,
            fetched_date TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS industry_stats (
            asset_type TEXT,
            group_label TEXT,
            metric TEXT,
            p5 REAL,
            p25 REAL,
            median REAL,
            p75 REAL,
            p95 REAL,
            n INTEGER,
            PRIMARY KEY (asset_type, group_label, metric)
        )
        """
    )
    conn.commit()
    return conn


def group_label_for(asset_type, data):
    """Stocks group by industry (falling back to sector if industry is
    missing); ETFs group by category - whichever classification actually
    applies to that asset type."""
    if asset_type == "stock":
        return data.get("industry") or data.get("sector") or "Unknown"
    return data.get("category") or "Unknown"


def already_fetched_today(conn, ticker, today_str):
    row = conn.execute(
        "SELECT 1 FROM assets WHERE ticker = ? AND fetched_date = ?", (ticker, today_str)
    ).fetchone()
    return row is not None


ASSET_COLUMNS = [
    "price", "market_cap", "total_assets", "avg_volume", "roe", "expense_ratio",
    "dividend_yield", "debt_to_equity", "return_3yr", "volatility", "max_drawdown",
    "pe_ratio", "beta", "revenue_growth", "eps_growth", "fcf_yield", "pct_investment_grade",
]


def fetch_and_cache(tickers, asset_type, conn, delay=0.3, limit=None, verbose=True):
    """Throttled, resumable fetch. Skips tickers already cached today so a
    re-run after an interruption just picks up where it left off."""
    today_str = str(date.today())
    tickers = list(tickers)
    if limit:
        tickers = tickers[:limit]

    fetched, skipped, failed = 0, 0, 0
    for i, ticker in enumerate(tickers, start=1):
        if already_fetched_today(conn, ticker, today_str):
            skipped += 1
            continue
        try:
            data = screener.fetch_raw_data(ticker)
            group_label = group_label_for(asset_type, data)
            values = [data.get(c) for c in ASSET_COLUMNS]
            conn.execute(
                f"""
                INSERT OR REPLACE INTO assets
                (ticker, asset_type, quote_type, sector, industry, category, group_label,
                 {", ".join(ASSET_COLUMNS)}, fetched_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, {", ".join(["?"] * len(ASSET_COLUMNS))}, ?)
                """,
                [ticker, asset_type, data.get("quote_type"), data.get("sector"),
                 data.get("industry"), data.get("category"), group_label]
                + values + [today_str],
            )
            conn.commit()
            fetched += 1
        except Exception as e:
            failed += 1
            if verbose:
                print(f"  [{i}/{len(tickers)}] {ticker}: FAILED ({e})")
        if verbose and i % 25 == 0:
            print(f"  [{i}/{len(tickers)}] fetched={fetched} skipped(cached today)={skipped} failed={failed}")
        time.sleep(delay)

    if verbose:
        print(f"Done with {asset_type}s: fetched={fetched}, skipped(cached today)={skipped}, failed={failed}")
    return {"fetched": fetched, "skipped": skipped, "failed": failed}


# ========================================================================
# Industry / category statistics (5th/25th/median/75th/95th percentile).
# Also writes a group_label="ALL" row per asset_type - the market-wide
# fallback used when a specific industry/category has too few tickers
# with data for a metric to trust its own percentiles.
# ========================================================================

def _stat_rows_for_group(asset_type, group_label, group_df):
    rows = []
    for metric in ASSET_COLUMNS:
        series = group_df[metric].dropna()
        if len(series) == 0:
            continue
        rows.append((
            asset_type, group_label, metric,
            float(series.quantile(0.05)), float(series.quantile(0.25)), float(series.quantile(0.50)),
            float(series.quantile(0.75)), float(series.quantile(0.95)), int(len(series)),
        ))
    return rows


def compute_industry_stats(conn):
    df = pd.read_sql("SELECT * FROM assets", conn)
    conn.execute("DELETE FROM industry_stats")

    rows = []
    for (asset_type, group_label), group_df in df.groupby(["asset_type", "group_label"]):
        rows.extend(_stat_rows_for_group(asset_type, group_label, group_df))

    # Market-wide fallback: same stats, grouped by asset_type only.
    for asset_type, type_df in df.groupby("asset_type"):
        rows.extend(_stat_rows_for_group(asset_type, "ALL", type_df))

    conn.executemany(
        "INSERT OR REPLACE INTO industry_stats VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
    )
    conn.commit()
    return len(rows)


# ========================================================================
# CLI
# ========================================================================

def main():
    parser = argparse.ArgumentParser(description="Fetch a broad stock/ETF universe into market_cache.db")
    parser.add_argument("--limit", type=int, default=None, help="Only fetch this many tickers per asset type (for testing)")
    parser.add_argument("--delay", type=float, default=0.3, help="Seconds to pause between tickers")
    args = parser.parse_args()

    conn = init_db()

    print("Fetching S&P 500 ticker list from Wikipedia...")
    sp500 = fetch_sp500_tickers()
    print(f"  Got {len(sp500)} tickers.")

    print(f"\nFetching stock data ({'limit ' + str(args.limit) if args.limit else 'full S&P 500'})...")
    fetch_and_cache(sp500["ticker"], "stock", conn, delay=args.delay, limit=args.limit)

    print(f"\nFetching ETF data ({'limit ' + str(args.limit) if args.limit else f'all {len(CURATED_ETFS)} curated ETFs'})...")
    fetch_and_cache(CURATED_ETFS, "etf", conn, delay=args.delay, limit=args.limit)

    print("\nComputing industry/category statistics...")
    n = compute_industry_stats(conn)
    print(f"  Wrote {n} (asset_type, group, metric) stat rows.")

    conn.close()
    print(f"\nDone. Data cached in {DB_PATH}")


if __name__ == "__main__":
    main()

# ------------------------------------------------------------------------
# To widen this beyond the S&P 500 to (closer to) the full U.S. stock
# market later: swap fetch_sp500_tickers() for NASDAQ's free public symbol
# directory (https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt
# and otherlisted.txt), which lists all ~8,000+ listed tickers (symbols
# only, no sector data - you'd still get sector/industry from yfinance
# per-ticker like this script already does). Fetching fundamentals for all
# of them will take over an hour even with this throttle - run it as a
# background job, not something you wait on in the app.
# ------------------------------------------------------------------------
