"""
Price-only refresh of the cached stocks and ETFs (no SEC, no fund details): 10 years of adjusted daily closes,
ending on a FIXED date, so every name's history window (3 / 5 / 10 yrs) ends on the same day.

  ./venv/bin/python3 refresh_prices.py 2026-10-15          (end date is exclusive: last close = 2026-10-14)

Only the "px" block of each cached raw is replaced; fundamentals, holdings and fetched dates are untouched.
A ticker whose download fails keeps its old px (listed at the end).
"""
import json
import sqlite3
import sys

import pandas as pd

import etf_data as ed
import stock_data as sd


def _download(tickers, end):
    failed = {}
    hist = ed.fetch_histories(tickers, chunk=60, failed=failed, end=end)
    retry = [t for t in tickers if t in failed or hist.get(t, (pd.Series(dtype=float), None))[0].dropna().empty]
    if retry:
        failed2 = {}
        hist.update(ed.fetch_histories(retry, chunk=20, failed=failed2, end=end))
    return hist


def refresh(db, table, key_cols, end, block):
    conn = sqlite3.connect(db)
    rows = conn.execute(f"SELECT {', '.join(key_cols)}, json FROM {table}").fetchall()
    tickers = sorted({r[0] for r in rows})
    print(f"{db}: {len(rows)} rows, {len(tickers)} tickers", flush=True)
    hist = _download(tickers, end)
    kept_old = []
    for *keys, js in rows:
        raw = json.loads(js)
        close, div = hist.get(keys[0], (pd.Series(dtype=float), None))
        if close is None or close.dropna().empty:
            kept_old.append(keys[0])
            continue
        raw["px"] = block(raw, close, div)
        where = " AND ".join(f"{c} = ?" for c in key_cols)
        conn.execute(f"UPDATE {table} SET json = ? WHERE {where}", (json.dumps(raw), *keys))
    conn.commit()
    conn.close()
    print(f"{db}: updated {len(rows) - len(kept_old)}, kept old px for {len(set(kept_old))}: {sorted(set(kept_old))[:40]}", flush=True)
    return sorted(set(kept_old))


if __name__ == "__main__":
    end = sys.argv[1]
    refresh(ed.DB_PATH, "etf_raw", ["ticker", "category"], end,
            lambda raw, close, div: ed.history_block(close, div, raw.get("price")))
    refresh(sd.DB_PATH, "raw_stock", ["ticker", "industry"], end,
            lambda raw, close, div: sd.price_block(close))
