"""
Tags every equity ETF with ONE of the 145 stock industries (the same list the Stocks page uses).
================================================================================================
Morningstar has no industry level, so we derive it from what the fund actually holds:

  1. Take the fund's top-10 holdings (yfinance funds_data).
  2. Map each holding ticker to its stock industry (results/industry_members.csv).
  3. If ONE industry holds >= 60% of the top-10 weight -> the fund is an "industry ETF" for it
     (e.g. SMH / SOXX -> Semiconductors, KRE -> Banks—Regional).
  4. Otherwise the fund is diversified and goes to a broad entry instead:
       sector funds (XLK, XLV...)    -> "Whole sector: <stock sector>"
       size/style funds (SPY, VTV)   -> "Broad US market (diversified)"
       country / regional / global   -> "International / global"

Caches the top-10 holdings in etf_raw.db (table etf_tophold) so a re-run only fetches new funds.
Run:  ./venv/bin/python3 etf_industry.py        (about 5-10 minutes the first time)
"""
import json
import logging
import os
import re
import sqlite3
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import yfinance as yf

import etf_data as ed

log = logging.getLogger(__name__)

MAP_PATH = "results/etf_industry_map.csv"
MEMBERS_PATH = "results/industry_members.csv"
DIRECTORY_PATH = "results/industry_directory.csv"
MIN_SHARE = 0.60   # one industry must hold at least this share of the top-10 weight when the fund NAME agrees
PURE_SHARE = 0.80  # ...or this share when the name says nothing (holdings alone)

BROAD = "Broad US market (diversified)"
INTL = "International / global"
OTHER = "Thematic / other (no single industry)"
WHOLE = "Whole sector: "

# Morningstar sector category -> stock sector (yfinance names), used only for diversified sector funds
CATEGORY_TO_SECTOR = {
    "Technology": "Technology", "Health": "Healthcare", "Financial": "Financial Services", "Equity Energy": "Energy",
    "Industrials": "Industrials", "Consumer Cyclical": "Consumer Cyclical", "Consumer Defensive": "Consumer Defensive",
    "Communications": "Communication Services", "Energy Limited Partnership": "Energy", "Utilities": "Utilities", "Real Estate": "Real Estate",
    "Global Real Estate": "Real Estate", "Natural Resources": "Basic Materials", "Equity Precious Metals": "Basic Materials",
    "Infrastructure": "Industrials",
}


# Sector funds whose holdings are mostly foreign/unlisted (miners, uranium...) have no mappable top-10,
# so for SECTOR-category funds only, a clear keyword in the fund name picks the industry instead.
NAME_TO_INDUSTRY = [
    (r"semiconductor|\bchips?\b", "Semiconductors"), (r"cyber", "Software—Infrastructure"),
    (r"software|cloud|saas", "Software—Infrastructure"), (r"biotech|genom", "Biotechnology"),
    (r"pharma", "Drug Manufacturers—General"), (r"medical device|med.?tech", "Medical Devices"),
    (r"regional bank|community bank", "Banks—Regional"), (r"property (&|and) casualty", "Insurance—Property & Casualty"), (r"\bbanks?\b", "Banks—Diversified"), (r"insur", "Insurance—Diversified"),
    (r"capital market|broker", "Capital Markets"), (r"asset manage", "Asset Management"),
    (r"oil services|oilfield|energy equipment|oil (&|and) gas (equipment|services)", "Oil & Gas Equipment & Services"),
    (r"refiner", "Oil & Gas Refining & Marketing"), (r"exploration|e&p|natural gas", "Oil & Gas E&P"), (r"midstream|\bmlp|pipeline", "Oil & Gas Midstream"), (r"uranium|nuclear", "Uranium"),
    (r"solar", "Solar"), (r"\bgold\b", "Gold"), (r"\bsilver\b", "Silver"), (r"copper", "Copper"), (r"steel", "Steel"),
    (r"coal", "Thermal Coal"), (r"aerospace|defense|defence|warfare", "Aerospace & Defense"), (r"airline|\bjets\b", "Airlines"),
    (r"homebuild|home construction", "Residential Construction"), (r"mortgage reit|mortgage real", "REIT—Mortgage"),
    (r"\bshipping\b", "Marine Shipping"), (r"sports betting|igaming|gambling", "Gambling"),
    (r"residential reit", "REIT—Residential"), (r"\bwater\b", "Utilities—Regulated Water"), (r"metals? (&|and) mining|miners", "Other Industrial Metals & Mining"),
]
SELECT_SECTOR = re.compile(r"select sector|sector spdr", re.I)  # the SPDR sector funds hold the WHOLE sector
# Funds with no Morningstar category are only kept when they prove to be an industry equity ETF; these names are never equity ETFs
UNCAT_BAD_NAME = re.compile(r"bond|treasur|income|premium|covered|buffer|floor|bitcoin|\bbtc\b|ether|crypto|currency|volatil|vix|"
                            r"bullion|trust|municipal|\bmuni|credit|loan|yield|target|maturity|dividend", re.I)


# a fund NAMED after a whole sector is a whole-sector fund even when its top-10 happens to look like one industry
SECTOR_NAME = [(r"communication services", "Communication Services"), (r"information technology", "Technology"),
               (r"health ?care", "Healthcare"), (r"\bfinancials?\b", "Financial Services"), (r"consumer staples", "Consumer Defensive"),
               (r"consumer discretionary", "Consumer Cyclical"), (r"\butilities\b", "Utilities"), (r"\bmaterials\b", "Basic Materials"),
               (r"\benergy\b(?! infrastructure)", "Energy"), (r"real estate", "Real Estate"), (r"\bindustrials?\b", "Industrials")]
GENERIC_ENERGY = re.compile(r"oil (&|and) gas|integrated oil", re.I)  # "oil & gas" alone is the whole energy sector, not E&P


def name_sector(name):
    n = re.sub(r"goldman sachs", "", (name or "").lower())
    for pat, sec in SECTOR_NAME:
        if re.search(pat, n):
            return sec
    return None


def name_industry(name, valid):
    n = re.sub(r"goldman sachs", "", (name or "").lower())
    if re.search(r"integrated oil", n):
        return None
    for pat, ind in NAME_TO_INDUSTRY:
        if re.search(pat, n) and ind in valid:
            return ind
    return None


def _conn():
    conn = sqlite3.connect(ed.DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS etf_tophold (symbol TEXT PRIMARY KEY, json TEXT)")
    return conn


def eligible_funds():
    """[(symbol, category, name, quote)] for every equity ETF that passes the category/name/price rules."""
    conn = sqlite3.connect(ed.DB_PATH)
    rows = conn.execute("SELECT symbol, category, json FROM etf_membership").fetchall()
    conn.close()
    out = []
    for sym, cat, js in rows:
        q = json.loads(js)
        name = q.get("longName") or q.get("shortName")
        if cat == "Uncategorized":
            if UNCAT_BAD_NAME.search(name or ""):
                continue
        elif ed.category_exclusion_reason(cat):
            continue
        if ed.fund_exclusion_reason(name, q.get("regularMarketPrice")):
            continue
        out.append((sym, cat, name, q))
    return out


def _holdings_for(sym):
    for _ in range(2):
        try:
            th = yf.Ticker(sym).funds_data.top_holdings
            if th is None or not len(th):
                return sym, []
            return sym, [{"symbol": str(s), "weight": float(r["Holding Percent"])} for s, r in th.iterrows()
                         if pd.notna(r["Holding Percent"])]
        except Exception as e:
            log.warning("top holdings lookup failed for %s: %s", sym, e)
    return sym, None  # failed - retried next run


def fetch_missing_holdings(symbols, progress=print):
    conn = _conn()
    have = {r[0] for r in conn.execute("SELECT symbol FROM etf_tophold")}
    # funds already fully pulled carry their holdings in etf_raw - reuse them for free
    for (js,) in conn.execute("SELECT json FROM etf_raw").fetchall():
        r = json.loads(js)
        if r["ticker"] not in have and r.get("holdings"):
            conn.execute("INSERT OR REPLACE INTO etf_tophold VALUES (?,?)",
                         (r["ticker"], json.dumps([{"symbol": h["symbol"], "weight": h["weight"]} for h in r["holdings"]])))
            have.add(r["ticker"])
    conn.commit()
    todo = [s for s in symbols if s not in have]
    progress(f"{len(todo)} funds need a holdings lookup")
    with ThreadPoolExecutor(max_workers=8) as ex:
        for n, (sym, h) in enumerate(ex.map(_holdings_for, todo), 1):
            if h is not None:
                conn.execute("INSERT OR REPLACE INTO etf_tophold VALUES (?,?)", (sym, json.dumps(h)))
            if n % 100 == 0:
                conn.commit()
                progress(f"  holdings {n}/{len(todo)}")
    conn.commit()
    conn.close()


def classify(holdings, category, industry_of, sector_of_industry, name=None):
    """-> (tag, how). Pure function (tested). tag None = not a diversified equity industry/sector fund -> left out.
    0. A fund that is (almost) one stock (top holding >= 50% of the whole fund) is a single-stock fund -> left out.
    1. 'Select Sector SPDR' funds, and funds NAMED after a whole sector (Communication Services...), are whole-sector funds.
    2. Sector-category and uncategorized funds: a clear industry word in the NAME decides, but only if the top-10
       holdings do not contradict it; uncategorized funds also need real stock holdings (GLD-style trusts are out).
       No name clue -> holdings must be PURE (one industry >= 80% of the top-10 weight).
    3. Size/style and international funds: the NAME must agree with the holdings (>= 60% in one industry) - holdings alone
       never tag them (a dividend fund whose top 10 happen to be utilities stays broad).
    4. Everything else is diversified: whole sector / broad US market / international."""
    grp = ed.category_group(category)
    sector_fund = grp == ed.GROUP_SECTOR
    uncat = category == "Uncategorized"
    n_ind = name_industry(name, sector_of_industry)
    n_sec = name_sector(name)
    total = sum(h["weight"] for h in holdings)
    if holdings and max(h["weight"] for h in holdings) >= 0.5:  # weights are fractions of the whole fund
        return None, "single-stock fund"
    if sector_fund and SELECT_SECTOR.search(name or ""):
        sec = CATEGORY_TO_SECTOR.get(category)
        return (WHOLE + sec, "Select Sector fund (whole sector)") if sec else (OTHER, "Select Sector fund")
    top, share, mapped, in_name = None, 0.0, 0.0, 0.0
    if total > 0:
        by_ind = defaultdict(float)
        for h in holdings:
            ind = industry_of.get(h["symbol"])
            if ind:
                by_ind[ind] += h["weight"]
        if by_ind:
            top, w = max(by_ind.items(), key=lambda kv: kv[1])
            share, mapped = w / total, sum(by_ind.values()) / total
            in_name = by_ind.get(n_ind, 0.0) / total if n_ind else 0.0
    if uncat and not holdings:
        return None, "uncategorized with no stock holdings (trust / note)"
    if (sector_fund or uncat) and not n_ind and n_sec:
        return WHOLE + n_sec, "named after a whole sector"
    if (sector_fund or uncat) and not n_ind and GENERIC_ENERGY.search(name or ""):
        return WHOLE + "Energy", "generic oil & gas fund (whole energy sector)"
    if (sector_fund or uncat) and n_ind and not (mapped >= 0.5 and in_name == 0):
        return n_ind, "fund name"
    if top and (sector_fund or uncat) and share >= PURE_SHARE:
        return top, f"{share:.0%} of top-10 weight"
    if top and share >= MIN_SHARE and n_ind == top:
        return top, f"{share:.0%} of top-10 weight + name"
    if uncat:
        return None, "uncategorized and not clearly an industry fund"
    if sector_fund:
        sec = CATEGORY_TO_SECTOR.get(category)
        return (WHOLE + sec, "diversified sector fund") if sec else (OTHER, "no dominant industry")
    return (INTL, "country / regional / global fund") if grp == ed.GROUP_INTL else (BROAD, "diversified size/style fund")


def build_map(progress=print):
    funds = eligible_funds()
    fetch_missing_holdings([f[0] for f in funds], progress)
    members = pd.read_csv(MEMBERS_PATH)
    industry_of = dict(zip(members["ticker"], members["industry"]))
    sector_of = dict(zip(*pd.read_csv(DIRECTORY_PATH)[["industry", "sector"]].values.T))
    conn = _conn()
    hold = {s: json.loads(j) for s, j in conn.execute("SELECT symbol, json FROM etf_tophold")}
    conn.close()
    rows = []
    for sym, cat, name, q in funds:
        tag, how = classify(hold.get(sym, []), cat, industry_of, sector_of, name)
        if tag is None:  # uncategorized and not provably an industry equity fund -> not in the universe
            continue
        rows.append({"ticker": sym, "name": name, "morningstar_category": cat, "group": tag,
                     "kind": "industry" if tag in sector_of else "broad", "basis": how,
                     "has_holdings": bool(hold.get(sym))})
    df = pd.DataFrame(rows)
    df.to_csv(MAP_PATH, index=False)
    progress(f"tagged {len(df)} funds: {int((df.kind == 'industry').sum())} industry ETFs in "
             f"{df[df.kind == 'industry'].group.nunique()} industries, {int((df.kind == 'broad').sum())} broad/diversified")
    return df


def load_map():
    return pd.read_csv(MAP_PATH) if os.path.exists(MAP_PATH) else None


if __name__ == "__main__":
    build_map(lambda m: print(m, flush=True))
