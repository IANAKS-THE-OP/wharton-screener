"""
Backtest: does the live industry pull really return EVERY stock in an industry?
================================================================================
Run:  ./venv/bin/python3 backtest_industries.py

Four independent checks (results saved to results/):

 1. INVENTORY   - pull all 145 industries, record the count yfinance REPORTS
                  vs the count we actually COLLECTED (catches silent
                  pagination truncation) plus duplicates.
 2. TOTALS      - compare the sum across industries to yfinance's own
                  unfiltered US total (catches stocks with no industry).
 3. RECALL      - take the ~500 S&P 500 stocks we cached earlier (their
                  industry came from a DIFFERENT yfinance endpoint) and check
                  each shows up in the live pull for its industry.
 4. EXTERNAL    - compare our tradeable set to NASDAQ Trader's official
                  listing files (the exchange's own list of every listed
                  security), and list what we are missing.

Outputs: results/industry_inventory.csv, results/industry_members.csv,
         results/backtest_missing_vs_exchange.csv, results/backtest_report.txt
"""

import io
import random
import sqlite3
import time
import urllib.request

import pandas as pd
import yfinance as yf

import industry_universe as iu

OUT = "results"
lines = []


def log(msg=""):
    print(msg, flush=True)
    lines.append(msg)


def norm(s):
    """Normalise an industry name so yfinance's two formats compare equal:
    .info uses 'Utilities - Regulated Electric'; the screener uses an em dash."""
    return (s or "").replace(" - ", "—").replace(",", "").replace("  ", " ").strip().lower()


# ---------------------------------------------------------------- 1. inventory
log("=== 1. INVENTORY: pulling every industry ===")
inventory, members = [], []
for i, ind in enumerate(iu.VALID_INDUSTRIES, 1):
    for attempt in range(3):
        try:
            quotes, reported = iu.pull_raw_industry_quotes(ind)
            break
        except Exception as e:  # transient network error -> retry
            time.sleep(2 * (attempt + 1))
            quotes, reported = None, None
    if quotes is None:
        log(f"  FAILED {ind}")
        inventory.append({"industry": ind, "reported": None, "collected": None, "unique": None,
                          "kept_tradeable": None, "status": "FAILED"})
        continue
    symbols = [q.get("symbol") for q in quotes]
    kept, _, n_kept = iu.filter_tradeable(quotes)
    inventory.append({
        "industry": ind, "reported": reported, "collected": len(quotes), "unique": len(set(symbols)),
        "kept_tradeable": n_kept,
        "status": "OK" if reported == len(quotes) == len(set(symbols)) else "MISMATCH",
    })
    for k in kept:
        members.append({"industry": ind, **k})
    if i % 20 == 0:
        log(f"  ...{i}/{len(iu.VALID_INDUSTRIES)} industries")
    time.sleep(0.2)

inv = pd.DataFrame(inventory)
mem = pd.DataFrame(members)
inv.to_csv(f"{OUT}/industry_inventory.csv", index=False)
mem.to_csv(f"{OUT}/industry_members.csv", index=False)
ok = inv[inv.status == "OK"]
log(f"  industries OK (reported == collected == unique): {len(ok)}/{len(inv)}")
bad = inv[inv.status != "OK"]
if len(bad):
    log("  PROBLEM industries:\n" + bad.to_string(index=False))
log(f"  total reported across industries : {int(inv.reported.sum()):,}")
log(f"  total collected                  : {int(inv.collected.sum()):,}")
log(f"  total tradeable (>=$5, NYSE/Nasdaq/NYSE American, not preferred): {int(inv.kept_tradeable.sum()):,}")
multi = mem.ticker.value_counts()
log(f"  distinct tradeable tickers: {mem.ticker.nunique():,} (tickers in >1 industry: {(multi > 1).sum()})")

# ---------------------------------------------------------------- 2. totals
log("\n=== 2. TOTALS: sum of industries vs yfinance's unfiltered US total ===")
q_all = yf.EquityQuery("eq", ["region", "us"])
r = yf.screen(q_all, offset=0, size=1)
unfiltered = r.get("total")
log(f"  yfinance unfiltered US equity total: {unfiltered:,}")
log(f"  sum across the 145 industries      : {int(inv.reported.sum()):,}")
log(f"  not in any industry                : {unfiltered - int(inv.reported.sum()):,} "
    f"({(unfiltered - inv.reported.sum()) / unfiltered:.1%})")

# ---------------------------------------------------------------- 3. recall
log("\n=== 3. RECALL: S&P 500 stocks (industry from a different endpoint) ===")
conn = sqlite3.connect("market_cache.db")
sp = pd.read_sql("select ticker, industry from assets where asset_type='stock' and industry is not null", conn)
live = {}  # normalized industry -> set of ALL collected tickers (before tradeable filter)
raw_by_ind = {}
for ind in iu.VALID_INDUSTRIES:
    pass  # filled lazily below to avoid re-pulling: reuse members + a second light pull for exact matching
members_by_ind = mem.groupby("industry").ticker.apply(set).to_dict()
norm_to_ind = {norm(i): i for i in iu.VALID_INDUSTRIES}
hit = miss = unmapped = 0
missing_rows, wrong_industry = [], []
all_kept = set(mem.ticker)
for _, row in sp.iterrows():
    live_ind = norm_to_ind.get(norm(row.industry))
    if live_ind is None:
        unmapped += 1
        continue
    if row.ticker in members_by_ind.get(live_ind, set()):
        hit += 1
    else:
        miss += 1
        where = mem[mem.ticker == row.ticker].industry.tolist()
        (wrong_industry if where else missing_rows).append(
            {"ticker": row.ticker, "cache_industry": row.industry, "live_industries": ";".join(where)})
log(f"  S&P 500 stocks tested: {hit + miss} (could not map industry name: {unmapped})")
log(f"  found in the live pull for their industry: {hit} ({hit / max(hit + miss, 1):.1%})")
log(f"  found, but filed under a DIFFERENT industry: {len(wrong_industry)}")
log(f"  not found anywhere in the tradeable set  : {len(missing_rows)}")
for row in (wrong_industry + missing_rows)[:40]:
    log(f"    {row}")

# ---------------------------------------------------------------- 4. external
log("\n=== 4. EXTERNAL: NASDAQ Trader official listings ===")


def fetch(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=60).read().decode()


try:
    nas = pd.read_csv(io.StringIO(fetch("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt")), sep="|")
    oth = pd.read_csv(io.StringIO(fetch("https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt")), sep="|")
    nas = nas[(nas["Test Issue"] == "N") & (nas["ETF"] == "N")].rename(columns={"Symbol": "sym", "Security Name": "name"})
    oth = oth[(oth["Test Issue"] == "N") & (oth["ETF"] == "N") & (oth["Exchange"].isin(["N", "A"]))].rename(
        columns={"ACT Symbol": "sym", "Security Name": "name"})
    ex = pd.concat([nas[["sym", "name"]], oth[["sym", "name"]]]).dropna()
    # keep common stock / ADS only: drop warrants, units, rights, preferreds, notes
    bad_words = r"warrant|unit|right|preferred|depositary share|notes|% |subordinated|trust preferred|fund|etf|acquisition"
    common = ex[~ex.name.str.contains(bad_words, case=False, regex=True)]
    common = common[~common.sym.str.contains(r"[\$\.\^]")]
    common_syms = set(common.sym.str.replace(".", "-", regex=False))
    log(f"  exchange-listed common-stock-like securities: {len(common_syms):,}")
    ours = all_kept
    inter = common_syms & ours
    log(f"  of those, in our tradeable set (any industry) : {len(inter):,} ({len(inter) / len(common_syms):.1%})")
    log(f"  in our set but NOT on the exchange lists      : {len(ours - common_syms):,} (mostly ETFs/trusts/funds that yfinance calls EQUITY, or name-filter edge cases)")
    missing = sorted(common_syms - ours)
    log(f"  exchange-listed but NOT in our set            : {len(missing):,}")
    # Why are they missing? Sample and ask yfinance.
    random.seed(1)
    sample = random.sample(missing, min(80, len(missing)))
    reasons = {"price<5": 0, "no industry assigned": 0, "other region (non-US)": 0, "not found at yfinance": 0, "other": 0}
    detail = []
    for s in sample:
        try:
            info = yf.Ticker(s).info
            price = info.get("regularMarketPrice") or info.get("currentPrice")
            industry = info.get("industry")
            country = info.get("country")
            if price is None and not info.get("longName"):
                why = "not found at yfinance"
            elif price is not None and price < 5:
                why = "price<5"
            elif not industry:
                why = "no industry assigned"
            elif country and country != "United States":
                why = "other region (non-US)"
            else:
                why = "other"
            reasons[why] += 1
            detail.append({"ticker": s, "why": why, "price": price, "industry": industry, "country": country})
        except Exception:
            reasons["not found at yfinance"] += 1
            detail.append({"ticker": s, "why": "not found at yfinance"})
        time.sleep(0.15)
    pd.DataFrame(detail).to_csv(f"{OUT}/backtest_missing_vs_exchange.csv", index=False)
    log(f"  WHY missing (random sample of {len(sample)}): {reasons}")
    others = [d for d in detail if d["why"] == "other"]
    for d in others[:15]:
        log(f"    unexplained: {d}")
except Exception as e:
    log(f"  external check failed: {e!r}")

with open(f"{OUT}/backtest_report.txt", "w") as f:
    f.write("\n".join(lines))
log("\nDONE. See results/backtest_report.txt")
