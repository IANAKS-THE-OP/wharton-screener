"""
Live ETF universe: every US-listed ETF in a Morningstar category.
==================================================================
Same idea as industry_universe.py for stocks - NOT a hand-picked list.

1. yfinance's ETFQuery (screener) pages through EVERY ETF in a category
   listed on a US exchange. The screener quote already carries expense
   ratio, assets, volume, dividend yield and price.
2. Price history for the whole category is downloaded in batches to
   compute 3-yr annualized return, volatility and max drawdown (same
   definitions as screener.compute_price_history_metrics).
3. Bond-category funds also get % investment-grade (funds_data lookup).
4. Everything is cached in etf_live.db so re-opening a category is instant.
"""

import json
import os
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import numpy as np
import pandas as pd
import yfinance as yf

DB_PATH = "etf_live.db"
PAGE_SIZE = 250
US_EXCHANGES = ["NYQ", "NMS", "PCX", "BTS", "ASE", "NGM", "NCM"]
BOND_KEYWORDS = ["bond", "government", "treasury", "gilt", "muni", "inflation-protected", "convertible"]

COLUMNS = ["ticker", "category", "name", "price", "total_assets", "avg_volume", "expense_ratio",
           "dividend_yield", "return_3yr", "volatility", "max_drawdown", "pct_investment_grade", "fetched_date"]


def _etf_query(category):
    E = yf.ETFQuery
    return E("and", [E("eq", ["categoryname", category]), E("is-in", ["exchange"] + US_EXCHANGES)])


MEMBERSHIP_DB = "etf_raw.db"  # written by build_etf_universe.py (table etf_membership)


def valid_filter_categories():
    """Category names yfinance's ETF screener FILTER accepts (an old list: newer Morningstar
    names such as 'Industrials', 'Global Large-Stock Blend' are rejected)."""
    return sorted(yf.ETFQuery("gt", ["intradayprice", 1]).valid_values["categoryname"])


def _membership_quotes(category):
    """Quotes for a category from the full-universe table, or None if it isn't built."""
    if not os.path.exists(MEMBERSHIP_DB):
        return None
    conn = sqlite3.connect(MEMBERSHIP_DB)
    try:
        rows = conn.execute("SELECT json FROM etf_membership WHERE category = ?", (category,)).fetchall()
    except sqlite3.OperationalError:
        rows = []
    conn.close()
    return [json.loads(r[0]) for r in rows] or None


def all_categories():
    """EVERY category that has US-listed ETFs: from the full-universe table
    (build_etf_universe.py) when it exists, else the screener's filter list."""
    if os.path.exists(MEMBERSHIP_DB):
        conn = sqlite3.connect(MEMBERSHIP_DB)
        try:
            cats = [r[0] for r in conn.execute("SELECT DISTINCT category FROM etf_membership")]
        except sqlite3.OperationalError:
            cats = []
        conn.close()
        if cats:
            return sorted(c for c in cats if c and c != "Uncategorized")
    return valid_filter_categories()


def is_bond_category(category):
    low = (category or "").lower()
    # whole-word match: plain substring made "Communications" look like a muni ("commUNIcations")
    return any(re.search(rf"\b{re.escape(k)}", low) for k in BOND_KEYWORDS)


def is_high_yield_category(category):
    low = (category or "").lower()
    return "high yield" in low or "bank loan" in low or "emerging markets bond" in low


def count_category(category):
    return yf.screen(_etf_query(category), size=1).get("total", 0)


def pull_category_quotes(category):
    """Every ETF in the category. Uses the full-universe table when it has the category
    (works for ALL category names); otherwise pages through the screener filter."""
    known = _membership_quotes(category)
    if known is not None:
        return known, len(known)
    quotes, offset, total = [], 0, None
    while total is None or offset < total:
        res = yf.screen(_etf_query(category), offset=offset, size=PAGE_SIZE)
        total = res.get("total", 0)
        page = res.get("quotes", [])
        if not page:
            break
        quotes.extend(page)
        offset += len(page)
        if len(page) < PAGE_SIZE:
            break
    return quotes, total


def _history_metrics(closes):
    closes = closes.dropna()
    out = {"return_3yr": np.nan, "volatility": np.nan, "max_drawdown": np.nan}
    if len(closes) < 252:  # need about a year of trading days to trust it
        return out
    daily = closes.pct_change().dropna()
    out["volatility"] = daily.std() * (252 ** 0.5)
    years = (closes.index[-1] - closes.index[0]).days / 365.25
    if years > 0:
        out["return_3yr"] = (closes.iloc[-1] / closes.iloc[0]) ** (1 / years) - 1
    out["max_drawdown"] = ((closes - closes.cummax()) / closes.cummax()).min()
    return out


def fetch_history_metrics(tickers, chunk=80):
    """{ticker: {return_3yr, volatility, max_drawdown}} using batched downloads."""
    result = {}
    for i in range(0, len(tickers), chunk):
        part = tickers[i:i + chunk]
        try:
            data = yf.download(part, period="3y", auto_adjust=True, progress=False, threads=True, group_by="column")
            close = data["Close"] if "Close" in data else pd.DataFrame()
            if isinstance(close, pd.Series):
                close = close.to_frame(part[0])
        except Exception:
            close = pd.DataFrame()
        for t in part:
            result[t] = _history_metrics(close[t]) if t in close.columns else _history_metrics(pd.Series(dtype=float))
    return result


def _investment_grade(ticker):
    try:
        ratings = yf.Ticker(ticker).funds_data.bond_ratings
        if ratings:
            return sum(ratings.get(k, 0) or 0 for k in ["aaa", "aa", "a", "bbb"])
    except Exception:
        pass
    return np.nan


def fetch_category(category, progress_callback=None, max_workers=8):
    """Live pull of one category -> DataFrame (and cached). Returns (df, found)."""
    quotes, total = pull_category_quotes(category)
    if progress_callback:
        progress_callback(0.1, f"{len(quotes)} ETFs found - downloading 3 years of price history...")
    rows = []
    for q in quotes:
        er, dy = q.get("netExpenseRatio"), q.get("dividendYield")
        rows.append({
            "ticker": q["symbol"], "category": category, "name": q.get("shortName") or q.get("longName"),
            "price": q.get("regularMarketPrice"), "total_assets": q.get("netAssets"),
            "avg_volume": q.get("averageDailyVolume3Month"),
            "expense_ratio": er / 100 if er is not None else np.nan,  # yfinance reports percent
            "dividend_yield": dy / 100 if dy is not None else np.nan,
            "pct_investment_grade": np.nan,
        })
    df = pd.DataFrame(rows, columns=COLUMNS[:-1])
    if df.empty:
        return df, total

    hist = fetch_history_metrics(df["ticker"].tolist())
    for m in ("return_3yr", "volatility", "max_drawdown"):
        df[m] = df["ticker"].map(lambda t: hist[t][m])

    if is_bond_category(category):
        done = 0
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {ex.submit(_investment_grade, t): t for t in df["ticker"]}
            for f in as_completed(futs):
                df.loc[df["ticker"] == futs[f], "pct_investment_grade"] = f.result()
                done += 1
                if progress_callback:
                    progress_callback(0.3 + 0.7 * done / len(futs), f"credit quality {done} of {len(futs)}...")

    df["fetched_date"] = str(date.today())
    save_to_cache(category, df)
    return df[COLUMNS], total


# ------------------------------------------------------------------ cache
def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(f"CREATE TABLE IF NOT EXISTS etf_live ({', '.join(c + ' TEXT' if c in ('ticker','category','name','fetched_date') else c + ' REAL' for c in COLUMNS)}, PRIMARY KEY (ticker, category))")
    return conn


def save_to_cache(category, df):
    conn = _conn()
    conn.execute("DELETE FROM etf_live WHERE category = ?", (category,))
    df[COLUMNS].to_sql("etf_live", conn, if_exists="append", index=False)
    conn.commit()
    conn.close()


def load_from_cache(category):
    if not os.path.exists(DB_PATH):
        return None
    conn = _conn()
    df = pd.read_sql("SELECT * FROM etf_live WHERE category = ?", conn, params=(category,))
    conn.close()
    if df.empty:
        return None
    return df, df["fetched_date"].iloc[0]


def cached_categories():
    if not os.path.exists(DB_PATH):
        return set()
    conn = _conn()
    rows = conn.execute("SELECT DISTINCT category FROM etf_live").fetchall()
    conn.close()
    return {r[0] for r in rows}
