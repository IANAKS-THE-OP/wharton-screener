"""
Holdings-quality lookups for the ETF scorer.
============================================
Each of a fund's top-10 holdings gets the STOCK model's Composite score
(default weights/settings, scored against the holding's own industry).

  1. Holding already in the stock cache  -> its industry is known.
  2. Otherwise look up its yfinance industry, map it to the screener's
     industry list, pull + score that whole industry with the existing stock
     pipeline (stock_data + scoring2), and cache EVERY company's score from it.
  3. Foreign / unlisted holdings (tickers like ASML.AS, 005930.KQ), foreign filers (20-F / 40-F, e.g. TSM:
     out of scope for the stock model), industries outside the screener list, and stocks that fail the
     tradeability filters are skipped (recorded with a reason, never guessed).

Scores are cached in holding_scores.db so re-runs are instant. The cache is VERSIONED by a hash of
the stock model's default weights + settings (model_hash()): if those change, the cached Composites
are stale, so invalidate_if_stale() clears them and they are rebuilt from the cached stock data
(rebuild_from_cache() does that offline for every industry already scored, keeping the cache committed for the website).
"""

import hashlib
import json
import logging
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import yfinance as yf

import industry_universe as iu
import scoring2 as sc
import stock_data as sd

log = logging.getLogger(__name__)

DB_PATH = "holding_scores.db"
FOREIGN_FILER_SKIP = "foreign filer (20-F / 40-F, no 10-K): out of scope for the stock model"


def _conn(db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS holding_scores (ticker TEXT PRIMARY KEY, industry TEXT, composite REAL, "
                 "overall REAL, fit REAL, fetched_date TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS holding_skips (ticker TEXT PRIMARY KEY, reason TEXT, fetched_date TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
    return conn


def model_hash():
    """Everything a cached holding Composite depends on besides the data: stock weights, settings, prep version."""
    blob = json.dumps({"w": sc.DEFAULT_WEIGHTS, "s": sc.DEFAULT_SETTINGS, "v": sc.PREP_VERSION}, sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


def invalidate_if_stale(db_path=DB_PATH):
    """Clear cached Composites (and 'no score' skips) made under a different stock model. Returns True if cleared.
    Foreign / not-in-list skips don't depend on the model and are kept."""
    if not os.path.exists(db_path):
        return False
    conn = _conn(db_path)
    row = conn.execute("SELECT v FROM meta WHERE k = 'model_hash'").fetchone()
    cur = model_hash()
    stale = row is None or row[0] != cur
    had = conn.execute("SELECT COUNT(*) FROM holding_scores").fetchone()[0]
    if stale:
        conn.execute("DELETE FROM holding_scores")
        conn.execute("DELETE FROM holding_skips WHERE reason LIKE '%no score%'")
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('model_hash', ?)", (cur,))
        conn.commit()
    conn.close()
    return stale and had > 0


def _norm(s):
    return (s or "").replace(" - ", "—").replace(",", "").replace("  ", " ").strip().lower()


_VALID = {_norm(i): i for i in iu.VALID_INDUSTRIES}


def load_scores(db_path=DB_PATH):
    """({ticker: {composite, industry}}, {ticker: skip_reason})"""
    if not os.path.exists(db_path):
        return {}, {}
    conn = _conn(db_path)
    scores = {r[0]: {"composite": r[2], "overall": r[3], "fit": r[4], "industry": r[1]}
              for r in conn.execute("SELECT ticker, industry, composite, overall, fit FROM holding_scores")}
    skips = {r[0]: r[1] for r in conn.execute("SELECT ticker, reason FROM holding_skips")}
    conn.close()
    return scores, skips


def is_foreign_symbol(sym):
    return ("." in sym) or ("=" in sym) or (not sym.replace("-", "").isalnum())


def _industry_from_yf(sym):
    try:
        ind = yf.Ticker(sym).info.get("industry")
    except Exception as e:  # network error -> treated as unknown (recorded as a skip, retried after a cache reset)
        log.warning("industry lookup failed for %s: %s", sym, e)
        return None
    return _VALID.get(_norm(ind))


def _score_industry(industry, db_path=DB_PATH, progress=None):
    """Make sure the industry is pulled, score it with default stock settings,
    and cache every company's Composite. Returns the set of tickers scored."""
    hit = sd.load_industry(industry)
    if hit is None:
        res = sd.get_or_refresh_industry(industry, force_refresh=True,
                                         progress_callback=(lambda d, t, tk: progress(f"{industry}: {d}/{t} companies")) if progress else None)
        raws = res["raws"]
    else:
        raws = hit[0]
    prep = sc.prepare_industry(raws, sc.DEFAULT_SETTINGS, all_raws=sd.load_all_cached)
    final = sc.finalize(prep)
    conn = _conn(db_path)
    for r in final["results"]:
        if r["composite"] is not None:
            conn.execute("INSERT OR REPLACE INTO holding_scores VALUES (?,?,?,?,?,?)",
                         (r["ticker"], industry, r["composite"], r["overall"], r["fit"], str(date.today())))
    for f in final["foreign"]:  # out of scope: recorded as a skip, never scored
        conn.execute("DELETE FROM holding_scores WHERE ticker = ?", (f["ticker"],))
        conn.execute("INSERT OR REPLACE INTO holding_skips VALUES (?,?,?)", (f["ticker"], FOREIGN_FILER_SKIP, str(date.today())))
    conn.commit()
    conn.close()
    return {r["ticker"] for r in final["results"] if r["composite"] is not None}


def rebuild_from_cache(db_path=DB_PATH):
    """Offline (no network): re-score every industry that already has cached holding scores with the CURRENT stock model,
    from the cached stock data, and stamp the new model_hash. Tickers that no longer get a Composite become skips.
    Returns {"industries", "scored", "foreign_skipped", "dropped"}."""
    conn = _conn(db_path)
    before = {r[0]: r[1] for r in conn.execute("SELECT ticker, industry FROM holding_scores")}
    conn.close()
    industries = sorted(set(before.values()))
    scored = set()
    for ind in industries:
        if sd.load_industry(ind) is None:
            continue
        scored |= _score_industry(ind, db_path)
    conn = _conn(db_path)
    foreign = {r[0] for r in conn.execute("SELECT ticker FROM holding_skips WHERE reason = ?", (FOREIGN_FILER_SKIP,))}
    dropped = [t for t in before if t not in scored and t not in foreign]
    for t in dropped:
        conn.execute("DELETE FROM holding_scores WHERE ticker = ?", (t,))
        conn.execute("INSERT OR REPLACE INTO holding_skips VALUES (?,?,?)",
                     (t, "not in the tradeable stock universe (price/exchange filters) or no score", str(date.today())))
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('model_hash', ?)", (model_hash(),))
    conn.commit()
    conn.close()
    return {"industries": len(industries), "scored": len(scored), "foreign_skipped": len(foreign & set(before)), "dropped": dropped}


def score_holdings(symbols, progress=None, db_path=DB_PATH):
    """Ensure every US-listed symbol has a cached Composite (or a recorded skip).
    progress(fraction, message). Returns (scores, skips)."""
    scores, skips = load_scores(db_path)
    todo = [s for s in dict.fromkeys(symbols) if s not in scores and s not in skips]
    conn = _conn(db_path)
    for s in list(todo):
        if is_foreign_symbol(s):
            conn.execute("INSERT OR REPLACE INTO holding_skips VALUES (?,?,?)", (s, "foreign / non-US listing", str(date.today())))
            todo.remove(s)
    conn.commit()
    if not todo:
        conn.close()
        return load_scores(db_path)

    # industry per symbol: stock cache first, then yfinance
    known = {r["ticker"]: r["industry"] for r in sd.load_all_cached() if r.get("industry")}
    industry_of = {s: known[s] for s in todo if s in known}
    unknown = [s for s in todo if s not in industry_of]
    if progress:
        progress(0.02, f"looking up industries for {len(unknown)} holdings...")
    with ThreadPoolExecutor(max_workers=8) as ex:
        for s, ind in zip(unknown, ex.map(_industry_from_yf, unknown)):
            if ind is None:
                conn.execute("INSERT OR REPLACE INTO holding_skips VALUES (?,?,?)", (s, "industry not in screener list", str(date.today())))
            else:
                industry_of[s] = ind
    conn.commit()
    conn.close()

    industries = sorted(set(industry_of.values()))
    for i, ind in enumerate(industries):
        if progress:
            progress(0.1 + 0.9 * i / max(len(industries), 1), f"scoring industry {i + 1} of {len(industries)}: {ind}")
        scored = _score_industry(ind, db_path, progress=(lambda m: progress(0.1 + 0.9 * i / len(industries), m)) if progress else None)
        conn = _conn(db_path)
        for s in [s for s, x in industry_of.items() if x == ind and s not in scored]:
            conn.execute("INSERT OR REPLACE INTO holding_skips VALUES (?,?,?)",
                         (s, "not in the tradeable stock universe (price/exchange filters) or no score", str(date.today())))
        conn.commit()
        conn.close()
    if progress:
        progress(1.0, "holdings scored")
    return load_scores(db_path)
