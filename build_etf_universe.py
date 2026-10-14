"""
Builds the COMPLETE US-listed ETF universe with each fund's real Morningstar category.
======================================================================================
Yahoo's category filter only accepts OLD category names, so ~2,200 ETFs (sector funds like
XLI/XLY/XLP/XLC, global funds like VT/ACWI, thematic funds...) can't be reached by category.
This script fixes that:

  1. pull EVERY ETF on a US exchange (paged, exchange filter only)
  2. label the ones in the 103 filterable categories (cheap)
  3. look up the category of every remaining ETF from yfinance (info['category'])
  4. save symbol -> category (+ the screener quote) in etf_raw.db (table etf_membership)
  5. write results/etf_category_directory.csv and results/etf_categories_all.csv

Run:  ./venv/bin/python3 build_etf_universe.py        (about 10-20 minutes)
"""
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import yfinance as yf

import etf_data as ed
import etf_universe as eu

E = yf.ETFQuery
t0 = time.time()

# 1. every US-listed ETF
all_q, offset, total = {}, 0, None
while total is None or offset < total:
    res = yf.screen(E("is-in", ["exchange"] + eu.US_EXCHANGES), offset=offset, size=250)
    total = res.get("total", 0)
    page = res.get("quotes", [])
    if not page:
        break
    for q in page:
        all_q[q["symbol"]] = q
    offset += len(page)
    print(f"all ETFs: {len(all_q)}/{total}", flush=True)
    if len(page) < 250:
        break

# 2. label via the filterable categories
cat_of = {}
for i, cat in enumerate(eu.valid_filter_categories(), 1):
    try:
        quotes, _ = eu.pull_category_quotes(cat)
    except Exception as e:
        print("category pull failed", cat, e)
        continue
    for q in quotes:
        cat_of[q["symbol"]] = cat
        all_q.setdefault(q["symbol"], q)
    print(f"[{i}/103] {cat}: {len(quotes)}", flush=True)

# 3. everyone else: ask yfinance
todo = [s for s in all_q if s not in cat_of]
print(f"{len(todo)} ETFs need a category lookup", flush=True)


def lookup(sym):
    for _ in range(2):
        try:
            return sym, yf.Ticker(sym).info.get("category")
        except Exception:
            time.sleep(1)
    return sym, None


with ThreadPoolExecutor(max_workers=8) as ex:
    for n, (sym, cat) in enumerate(ex.map(lookup, todo), 1):
        cat_of[sym] = cat or "Uncategorized"
        if n % 200 == 0:
            print(f"  looked up {n}/{len(todo)}", flush=True)

# 4. save membership
conn = sqlite3.connect(ed.DB_PATH)
conn.execute("CREATE TABLE IF NOT EXISTS etf_membership (symbol TEXT PRIMARY KEY, category TEXT, json TEXT)")
conn.execute("DELETE FROM etf_membership")
for s, q in all_q.items():
    conn.execute("INSERT INTO etf_membership VALUES (?,?,?)", (s, cat_of.get(s, "Uncategorized"), json.dumps(q)))
conn.commit()
conn.close()

# 5. directories (also re-runnable offline: python3 -c "import etf_data; etf_data.write_directories()")
d = ed.write_directories()
print(f"DONE in {time.time() - t0:.0f}s: {len(all_q)} ETFs, {len(counts)} categories, "
      f"{int(d[d.status.str.startswith('KEPT')].etfs.sum())} equity ETFs in {int(d.status.str.startswith('KEPT').sum())} kept categories", flush=True)
