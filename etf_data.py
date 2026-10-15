"""
Raw EQUITY-ETF data layer for the 10-metric ETF scoring system (etf_scoring.py).
==============================================================================
Pulls every US-listed ETF in a Morningstar category (re-using etf_universe's
live category pull), drops non-equity / leveraged / tiny funds BEFORE any
expensive call, then for each remaining fund fetches:

  * up to 10 years of split/dividend-adjusted daily prices + dividends
    (batched) -> monthly returns, max drawdowns and annualized returns for
    the 5- and 10-year windows, and a trailing-12-month dividend yield
  * yfinance info (expense-ratio fallback) and funds_data top-10 holdings

Raw results are cached as JSON in etf_raw.db. Scoring settings (T-bill rate,
history window, weights...) are applied later, so changing them never
re-downloads anything.
"""

import json
import logging
import os
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime

import pandas as pd
import yfinance as yf

import etf_universe as eu

log = logging.getLogger(__name__)

DB_PATH = "etf_raw.db"
MIN_PRICE = 5.0

# ------------------------------------------------------------------ eligibility
_BOND_WORDS = ["bond", "government", "treasury", r"\bmuni", "corporate", "credit", "inflation-protected",
               "ultrashort", "bank loan", "high yield", "securitized", "multisector"]
_COMMODITY_WORDS = ["commodities", "precious metals", "gold", "oil"]  # MLP funds ("Energy Limited Partnership") are equities and stay
_ALT_WORDS = ["volatility", "currency", "alternative", "derivative income", "digital assets", "bitcoin", "crypto", "ether"]
# Not named in the spec but not pure equity either (decision): mixed, option-income, long/short, preferred.
_OTHER_NON_EQUITY = ["uncategorized", "defined outcome", "target maturity", "equity hedged", "multi-asset", "systematic trend",
                     "event driven", "money market", "multistrategy", "macro trading", "relative value", "allocation", "target-date", "convertibles", "preferred stock", "option writing", "long-short",
                     "market neutral", "managed futures", "bear market", "tactical", "multicurrency"]
_NAME_BAD = re.compile(r"(\b(2x|3x|ultra\w*|inverse|short\w*|bear)\b|[-\s][123]x\b)", re.IGNORECASE)


# The only screener-quote fields the app reads; build_etf_universe.py stores just these (keeps etf_raw.db small).
QUOTE_FIELDS = ("symbol", "longName", "shortName", "regularMarketPrice", "netAssets", "netExpenseRatio",
                "averageDailyVolume3Month", "dividendYield", "quoteType", "exchange", "currency", "firstTradeDateMilliseconds")


def slim_quote(q):
    return {k: q[k] for k in QUOTE_FIELDS if k in q}


def category_exclusion_reason(category):
    """None if the category is an equity ETF category we keep; otherwise a reason."""
    c = (category or "").lower()
    if c.startswith("trading"):
        return "trading / leveraged / inverse"
    if "equity precious metals" in c:  # miner STOCKS stay (spec)
        return None
    if any(re.search(w, c) for w in _BOND_WORDS):
        return "bond / fixed income"
    if any(re.search(rf"\b{re.escape(w)}\b", c) for w in _COMMODITY_WORDS):
        return "commodity"
    if any(w in c for w in _ALT_WORDS):
        return "volatility / currency / alternative / crypto"
    if any(w in c for w in _OTHER_NON_EQUITY) or c == "other":
        return "not a pure equity category"
    return None


_NAME_DERIV = re.compile(r"covered call|option income|options income|option-income|premium income|buffer|yieldmax|stocksplus|stocks plus|"
                         r"\bfund of option|defined outcome|\bfloor\b|autocall|income builder|managed futures|futures strategy|"
                         r"weekly ?pay|\bphysical\b|bullion|\bspac\b|efficient .* plus|adr ?hedged|\bvolt\b", re.IGNORECASE)


# VIX / volatility PRODUCTS (futures, premium-selling, ETNs). Deliberately narrow: "Low Volatility", "Min Vol",
# "Managed Volatility" and "Volatility Weighted" EQUITY funds (USMV, SPLV, LGLV...) must NOT match.
VOL_PRODUCT = re.compile(r"\bvix\b|volatility (premium|futures|index fund|etn)|volatility shares|\bvol(atility)? etn|"
                         r"short[- ]term futures|long volatility|short volatility|\bvolt\b", re.IGNORECASE)


def fund_exclusion_reason(name, price):
    if name and VOL_PRODUCT.search(name):
        return "VIX / volatility product (by name)"
    if name and _NAME_BAD.search(name):
        return "leveraged / inverse / short (by name)"
    if name and _NAME_DERIV.search(name):
        return "option-income / buffer / derivative strategy (by name)"
    if price is None or price < MIN_PRICE:
        return "priced under $5"
    return None


def equity_categories():
    return [c for c in eu.all_categories() if category_exclusion_reason(c) is None]


# ------------------------------------------------------------------ sector / broad / international
# Morningstar mixes three different ideas in one "category" list. We split them so the picker can
# show SECTOR funds only (what the team wants) and keep size/style and region as separate groups.
SECTOR_CATEGORIES = ["Technology", "Health", "Financial", "Equity Energy", "Industrials", "Consumer Cyclical",
                     "Consumer Defensive", "Communications", "Utilities", "Real Estate", "Global Real Estate",
                     "Natural Resources", "Equity Precious Metals", "Infrastructure", "Miscellaneous Sector", "Energy Limited Partnership"]
GROUP_SECTOR, GROUP_BROAD, GROUP_INTL = "Sector", "Broad market (size / style)", "International / regional"
_INTL_WORDS = ["foreign", "europe", "japan", "china", "india", "pacific", "asia", "emerging", "global", "world",
               "latin", "focused region", "region"]


def category_group(category):
    """'Sector', 'Broad market (size / style)' or 'International / regional' for a kept equity category."""
    if category in SECTOR_CATEGORIES:
        return GROUP_SECTOR
    c = (category or "").lower()
    if any(w in c for w in _INTL_WORDS):
        return GROUP_INTL
    return GROUP_BROAD


# ------------------------------------------------------------------ price history
def _window_stats(closes, years):
    """closes: Series (DatetimeIndex, adjusted). Stats over the last `years` years
    (or all of it if shorter). Returns (mdd_fraction, cagr_fraction)."""
    cut = closes.index[-1] - pd.Timedelta(days=int(365.25 * years))
    w = closes[closes.index >= cut]
    if len(w) < 30:
        return None, None
    mdd = float((1 - w / w.cummax()).max())
    span = (w.index[-1] - w.index[0]).days / 365.25
    cagr = float((w.iloc[-1] / w.iloc[0]) ** (1 / span) - 1) if span > 0 else None
    return mdd, cagr


def history_block(close, dividends, price):
    px = {"years": 0.0, "monthly_returns": [], "mdd_5": None, "mdd_10": None, "cagr_5": None, "cagr_10": None,
          "first_date": None, "last_date": None, "ttm_dividend_yield": None}
    close = close.dropna()
    if len(close) < 30:
        return px
    close.index = close.index.tz_localize(None) if close.index.tz is not None else close.index
    px["first_date"], px["last_date"] = str(close.index[0].date()), str(close.index[-1].date())
    px["years"] = (close.index[-1] - close.index[0]).days / 365.25
    month_end = close.resample("ME").last().dropna()
    px["monthly_returns"] = [float(x) for x in month_end.pct_change().dropna().iloc[-120:].values]
    px["mdd_5"], px["cagr_5"] = _window_stats(close, 5)
    px["mdd_10"], px["cagr_10"] = _window_stats(close, 10)
    if dividends is not None:
        d = dividends.copy()
        d.index = d.index.tz_localize(None) if d.index.tz is not None else d.index
        ttm = float(d[d.index > close.index[-1] - pd.Timedelta(days=365)].sum())
        last_price = price or float(close.iloc[-1])
        px["ttm_dividend_yield"] = ttm / last_price if last_price else None
    return px


def fetch_histories(tickers, chunk=60, failed=None):
    """{ticker: history_block-ready (close, dividends)} via batched downloads.
    failed: optional dict that receives {ticker: error text} for every ticker whose download errored
    (network / SSL), so callers never mistake a failed download for a fund with no history."""
    out = {}
    failed = {} if failed is None else failed
    for i in range(0, len(tickers), chunk):
        part = tickers[i:i + chunk]
        try:
            data = yf.download(part, period="10y", auto_adjust=True, actions=True, progress=False,
                               threads=True, group_by="column")
        except Exception as e:  # network / SSL error for the whole chunk
            log.warning("price history download failed for %d funds: %s", len(part), e)
            data = pd.DataFrame()
            for t in part:
                failed[t] = f"price history download failed: {type(e).__name__}: {e}"
        try:  # yfinance usually does NOT raise on network/SSL errors: it records them per ticker and returns NaNs
            import yfinance.shared as yfs
            for t, msg in dict(getattr(yfs, "_ERRORS", {}) or {}).items():
                if t in part and t not in failed:
                    failed[t] = f"price history download failed: {str(msg)[:200]}"
        except ImportError:
            pass
        for t in part:
            try:
                if len(part) == 1:
                    close, div = data["Close"], data["Dividends"] if "Dividends" in data else None
                    if isinstance(close, pd.DataFrame):
                        close = close.iloc[:, 0]
                    if isinstance(div, pd.DataFrame):
                        div = div.iloc[:, 0]
                else:
                    close = data["Close"][t]
                    div = data["Dividends"][t] if "Dividends" in data else None
                out[t] = (close, div)
            except (KeyError, IndexError, TypeError) as e:  # ticker missing from the batch result
                if t not in failed and data.empty:
                    failed[t] = f"price history empty: {e}"
                out[t] = (pd.Series(dtype=float), None)
    return out


# ------------------------------------------------------------------ per-fund info + holdings
def fetch_info_and_holdings(ticker):
    res = {"expense_fallback": None, "holdings": [], "inception": None, "errors": []}
    try:
        t = yf.Ticker(ticker)
        info = t.info
        ar = info.get("annualReportExpenseRatio")
        res["expense_fallback"] = ar * 100 if ar is not None else None  # fraction -> percent
        res["inception"] = info.get("fundInceptionDate")
        try:
            th = t.funds_data.top_holdings
            if th is not None and len(th):
                res["holdings"] = [{"symbol": str(sym), "name": str(row["Name"]), "weight": float(row["Holding Percent"])}
                                   for sym, row in th.iterrows() if pd.notna(row["Holding Percent"])]
        except Exception as e:
            res["errors"].append(f"holdings unavailable: {e}")
    except Exception as e:
        res["errors"].append(f"info failed: {e}")
    return res


# ------------------------------------------------------------------ cache
def _conn(db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS etf_raw (ticker TEXT, category TEXT, fetched_date TEXT, json TEXT, "
                 "PRIMARY KEY (ticker, category))")
    conn.execute("CREATE TABLE IF NOT EXISTS etf_meta (category TEXT PRIMARY KEY, fetched_date TEXT, json TEXT)")
    return conn


def save_category(category, raws, meta, db_path=DB_PATH):
    conn = _conn(db_path)
    conn.execute("DELETE FROM etf_raw WHERE category = ?", (category,))
    for r in raws:
        conn.execute("INSERT OR REPLACE INTO etf_raw VALUES (?,?,?,?)", (r["ticker"], category, str(date.today()), json.dumps(r)))
    conn.execute("INSERT OR REPLACE INTO etf_meta VALUES (?,?,?)", (category, str(date.today()), json.dumps(meta)))
    conn.commit()
    conn.close()


def load_category(category, db_path=DB_PATH):
    """(raws, meta) or None."""
    if not os.path.exists(db_path):
        return None
    conn = _conn(db_path)
    rows = conn.execute("SELECT json FROM etf_raw WHERE category = ?", (category,)).fetchall()
    meta = conn.execute("SELECT json FROM etf_meta WHERE category = ?", (category,)).fetchone()
    conn.close()
    if meta is None:
        return None
    return [json.loads(r[0]) for r in rows], json.loads(meta[0])


def load_all(db_path=DB_PATH):
    """Every cached (scored-eligible) fund across all pulled categories."""
    if not os.path.exists(db_path):
        return []
    conn = _conn(db_path)
    rows = conn.execute("SELECT json FROM etf_raw").fetchall()
    conn.close()
    return [json.loads(r[0]) for r in rows]


def cached_categories(db_path=DB_PATH):
    if not os.path.exists(db_path):
        return set()
    conn = _conn(db_path)
    rows = conn.execute("SELECT category FROM etf_meta").fetchall()
    conn.close()
    return {r[0] for r in rows}


# ------------------------------------------------------------------ the category pull
def _build_raws(kept, category_of, progress_callback=None, max_workers=8):
    """Download price history, fund info and top holdings for these screener quotes and
    return the raw fund dicts. category_of(quote) gives each fund's Morningstar category."""
    tickers = [q["symbol"] for q in kept]
    hist_failed = {}
    hist = fetch_histories(tickers, failed=hist_failed)
    if progress_callback:
        progress_callback(0.4, "price history done - fund details and holdings...")

    infos, done = {}, 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(fetch_info_and_holdings, t): t for t in tickers}
        for f in as_completed(futs):
            infos[futs[f]] = f.result()
            done += 1
            if progress_callback and done % 10 == 0:
                progress_callback(0.4 + 0.6 * done / len(futs), f"fund details {done} of {len(futs)}...")

    raws = []
    for q in kept:
        t = q["symbol"]
        info = infos.get(t, {"expense_fallback": None, "holdings": [], "errors": []})
        er = q.get("netExpenseRatio")
        er_src = "netExpenseRatio"
        if er is None:
            er, er_src = info["expense_fallback"], "annualReportExpenseRatio"
        close, div = hist.get(t, (pd.Series(dtype=float), None))
        price = q.get("regularMarketPrice")
        avg_vol = q.get("averageDailyVolume3Month")
        raws.append({
            "ticker": t, "name": q.get("longName") or q.get("shortName"), "category": category_of(q), "price": price,
            "aum": q.get("netAssets"), "avg_volume": avg_vol,
            "dollar_volume": avg_vol * price if (avg_vol is not None and price) else None,
            "expense_ratio": er, "expense_source": er_src if er is not None else None,  # percent, e.g. 0.03 = 0.03%
            "holdings": info["holdings"], "n_holdings": None, "errors": info["errors"],
            "px": history_block(close, div, price), "fetched": datetime.now().isoformat(timespec="seconds"),
        })
        if t in hist_failed:
            raws[-1]["errors"] = raws[-1]["errors"] + [hist_failed[t]]
            raws[-1]["fetch_failed"] = True  # network failure: never cache this over good data
    return raws


def fetch_category(category, progress_callback=None, max_workers=8):
    """Live pull of one equity category. Returns (raws, meta). Raises ValueError
    for a non-equity category (it must never be scored)."""
    reason = category_exclusion_reason(category)
    if reason:
        raise ValueError(f"'{category}' is not an equity ETF category ({reason})")
    quotes, total = eu.pull_category_quotes(category)
    removed, kept = {}, []
    for q in quotes:
        name = q.get("longName") or q.get("shortName")
        why = fund_exclusion_reason(name, q.get("regularMarketPrice"))
        if why:
            removed.setdefault(why, []).append(q["symbol"])
        else:
            kept.append(q)
    meta = {"found": len(quotes), "removed": {k: len(v) for k, v in removed.items()}, "removed_tickers": removed,
            "scored": len(kept), "category": category}
    if progress_callback:
        progress_callback(0.05, f"{len(quotes)} found, {len(quotes) - len(kept)} removed, {len(kept)} to fetch - price history...")

    raws = _build_raws(kept, lambda q: category, progress_callback, max_workers)
    save_category(category, raws, meta)
    return raws, meta


def write_directories():
    """Rebuild results/etf_categories_all.csv and results/etf_category_directory.csv from the
    membership table (no network). Returns the all-categories DataFrame."""
    conn = sqlite3.connect(DB_PATH)
    cats = pd.read_sql("SELECT category, COUNT(*) AS etfs FROM etf_membership GROUP BY category", conn)
    conn.close()
    rows = []
    for cat, n in zip(cats["category"], cats["etfs"]):
        why = category_exclusion_reason(cat)
        rows.append({"category": cat, "etfs": int(n), "group": category_group(cat) if why is None else "",
                     "status": "KEPT (equity)" if why is None else "removed: " + why,
                     "is_bond": bool(eu.is_bond_category(cat)), "high_yield": bool(eu.is_high_yield_category(cat))})
    d = pd.DataFrame(rows).sort_values(["status", "group", "etfs"], ascending=[True, True, False])
    d.to_csv("results/etf_categories_all.csv", index=False)
    d[["category", "etfs", "group", "is_bond", "high_yield"]].to_csv("results/etf_category_directory.csv", index=False)
    return d


# ------------------------------------------------------------------ fetch / load by ticker (industry view)
def _membership_quotes_for(tickers):
    conn = sqlite3.connect(DB_PATH)
    ph = ",".join("?" * len(tickers))
    rows = conn.execute(f"SELECT symbol, category, json FROM etf_membership WHERE symbol IN ({ph})", list(tickers)).fetchall()
    conn.close()
    return [(sym, cat, json.loads(js)) for sym, cat, js in rows]


def load_funds(tickers, db_path=DB_PATH):
    """{ticker: raw} for every ticker already downloaded (any Morningstar category)."""
    if not os.path.exists(db_path) or not tickers:
        return {}
    conn = _conn(db_path)
    out = {}
    for i in range(0, len(tickers), 500):
        part = list(tickers)[i:i + 500]
        ph = ",".join("?" * len(part))
        for (js,) in conn.execute(f"SELECT json FROM etf_raw WHERE ticker IN ({ph})", part):
            r = json.loads(js)
            out[r["ticker"]] = r
    conn.close()
    return out


def fetch_funds(tickers, progress_callback=None, max_workers=8):
    """Download the funds that are not cached yet (stored under their own Morningstar category)."""
    quotes = _membership_quotes_for(tickers)
    cat_of = {sym: cat for sym, cat, _ in quotes}
    raws = _build_raws([q for _, _, q in quotes], lambda q: cat_of[q["symbol"]], progress_callback, max_workers)
    good = [r for r in raws if not r.get("fetch_failed")]
    bad = [r for r in raws if r.get("fetch_failed")]
    conn = _conn()
    for r in good:  # a failed download is NOT saved: the old cached copy (if any) stays, and the fund stays retryable
        conn.execute("INSERT OR REPLACE INTO etf_raw VALUES (?,?,?,?)", (r["ticker"], r["category"], str(date.today()), json.dumps(r)))
    conn.commit()
    conn.close()
    if bad:
        raise RuntimeError(f"{len(bad)} of {len(raws)} funds failed to download ({bad[0]['errors'][-1][:160]}). "
                           f"The {len(good)} that worked were saved; failed ones keep their previous cached data.")
    return raws


def load_all_tickers(db_path=DB_PATH):
    if not os.path.exists(db_path):
        return set()
    conn = _conn(db_path)
    rows = conn.execute("SELECT DISTINCT ticker FROM etf_raw").fetchall()
    conn.close()
    return {r[0] for r in rows}
