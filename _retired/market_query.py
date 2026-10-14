"""
ETF / Bond ETF data helpers + fuzzy search.
============================================
ETF data comes from market_cache.db (built by fetch_universe.py) - these
helpers never call yfinance live, so they're fast enough to run on every
click. Stocks do NOT use this module's cache; they are pulled live per
industry (industry_universe.py + industry_fundamentals.py).

ETFs are scored with the SAME comfort-range Fit + Quality engine as stocks
(fundamental_scoring.py) - only the metric list and directions differ.
"""

import difflib
import sqlite3

import pandas as pd

DB_PATH = "market_cache.db"

METRIC_LABELS = {
    "expense_ratio": "Expense ratio",
    "total_assets": "Assets under management ($)",
    "avg_volume": "Avg volume (shares/day)",
    "dividend_yield": "Dividend yield",
    "return_3yr": "3-yr annualized return",
    "volatility": "3-yr volatility",
    "max_drawdown": "Max drawdown (3-yr)",
    "pct_investment_grade": "% investment-grade",
}

# Direction vocabulary matches fundamental_scoring.py: "higher" = bigger is
# better (being above the comfort range is never penalized), "lower" =
# smaller is better, "sweet_spot" = penalized on both sides.
ETF_METRIC_DIRECTIONS = {
    "expense_ratio": "lower", "total_assets": "higher", "return_3yr": "higher",
    "volatility": "lower", "max_drawdown": "higher", "dividend_yield": "higher",
}
# Bond ETFs back the operating reserve, so safety metrics (volatility,
# drawdown, credit quality) sit alongside yield and cost.
BOND_ETF_METRIC_DIRECTIONS = {
    "dividend_yield": "higher", "expense_ratio": "lower", "pct_investment_grade": "higher",
    "volatility": "lower", "max_drawdown": "higher", "total_assets": "higher",
}
ETF_METRIC_GROUPS = {
    "Cost & size": ["expense_ratio", "total_assets"],
    "Return & income": ["return_3yr", "dividend_yield"],
    "Risk": ["volatility", "max_drawdown"],
}
BOND_ETF_METRIC_GROUPS = {
    "Cost & size": ["expense_ratio", "total_assets"],
    "Income & credit quality": ["dividend_yield", "pct_investment_grade"],
    "Risk": ["volatility", "max_drawdown"],
}

# Not available from yfinance at all - flagged in the UI instead of hidden.
UNAVAILABLE_METRICS = {
    "ETF": "Number of holdings isn't reliably available from yfinance.",
    "Bond ETF": "Fund duration isn't available from yfinance - check the issuer's fact sheet by hand.",
}

BOND_CATEGORY_KEYWORDS = ["bond", "government", "treasury", "gilt"]


def etf_metric_config(asset_type_label):
    """A metric_config shaped like config.yaml's `fundamental_metrics`, so
    fundamental_scoring.py can score ETFs without any changes."""
    directions = BOND_ETF_METRIC_DIRECTIONS if asset_type_label == "Bond ETF" else ETF_METRIC_DIRECTIONS
    return {"direction": dict(directions), "sweet_spot_default_range": {}, "low_data_threshold": 0.30}


def is_bond_category(category_label):
    if not category_label:
        return False
    lower = category_label.lower()
    return any(kw in lower for kw in BOND_CATEGORY_KEYWORDS)


# ========================================================================
# Fuzzy / partial-word search (stdlib difflib - no new dependency)
# ========================================================================

def search_options(query, options, max_suggestions=5, cutoff=0.4):
    """Case-insensitive substring search first (typing "semi" matches
    "Semiconductors"); if nothing matches, falls back to difflib fuzzy
    matching for small typos. Returns (matches, is_fuzzy)."""
    options = list(options)
    if not query or not query.strip():
        return options, False
    q = query.strip().lower()
    substring_matches = [o for o in options if q in o.lower()]
    if substring_matches:
        return substring_matches, False
    fuzzy = difflib.get_close_matches(query.strip(), options, n=max_suggestions, cutoff=cutoff)
    return fuzzy, True


# ========================================================================
# ETF cache queries
# ========================================================================

def get_connection(db_path=DB_PATH):
    return sqlite3.connect(db_path)


def load_etfs(bond_only=False, db_path=DB_PATH):
    """All cached ETFs (or only bond/government-category ones) as a DataFrame."""
    conn = get_connection(db_path)
    df = pd.read_sql("SELECT * FROM assets WHERE asset_type = 'etf'", conn)
    conn.close()
    df["category"] = df["group_label"].fillna("Unknown")
    if bond_only:
        df = df[df["category"].apply(is_bond_category)]
    return df.reset_index(drop=True)
