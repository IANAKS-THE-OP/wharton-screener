"""
Raw stock data layer for the 14-metric scoring system (scoring2.py).
=====================================================================
Pulls, per company, the RAW inputs the spec needs and caches them as JSON:

  * SEC EDGAR 10-K annual facts for up to the last 6 fiscal years (income
    statement, cash flow, balance sheet). Tag fallbacks as in fundamentals.py.
  * yfinance market data (price, market cap, beta, sector) and 3 years of
    split/dividend-adjusted daily prices -> monthly returns + max drawdown.

Nothing is scored here. Scoring settings (T-bill rate, weights, ranges...)
change often, so metrics are recomputed from this cache on every run.
"""

import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta

import pandas as pd
import yfinance as yf

import industry_universe as iu
import sec_edgar as sec

DB_PATH = "stock_raw.db"
STALE_DAYS = 487  # 16 months: older 10-K data is never scored
MAX_FY = 6  # fiscal years of history kept (4 are needed for deltas + 1 prior balance sheet)

# ---- SEC tag candidates (first-listed is preferred; best_annual_series picks
# whichever candidate has the most recent data) --------------------------------
DURATION_TAGS = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet", "SalesRevenueGoodsNet",
                "SalesRevenueServicesNet", "RegulatedAndUnregulatedOperatingRevenue", "ElectricUtilityRevenue",
                "RevenuesNetOfInterestExpense"],
    "cogs": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"],
    "gross_profit": ["GrossProfit"],
    "ebit": ["OperatingIncomeLoss"],
    "da": ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
           "DepreciationAmortizationAndAccretionNet", "Depreciation"],
    "interest_expense": ["InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt",
                         "InterestAndDebtExpense", "InterestPaid", "InterestPaidNet"],  # last two = cash proxy, noted in the metric
    "tax_expense": ["IncomeTaxExpenseBenefit"],
    "pretax_income": [
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    ],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
              "PaymentsForCapitalImprovements"],
    "dividends_paid": ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"],
    "repurchases": ["PaymentsForRepurchaseOfCommonStock", "PaymentsForRepurchaseOfEquity"],
    "stock_issued": ["ProceedsFromIssuanceOfCommonStock"],
}
EPS_TAGS = ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"]
SHARES_TAGS = ["WeightedAverageNumberOfDilutedSharesOutstanding"]

INSTANT_TAGS = {
    "cash": ["CashAndCashEquivalentsAtCarryingValue",
             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "total_assets": ["Assets"],
    "total_liabilities": ["Liabilities"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "current_assets": ["AssetsCurrent"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "retained_earnings": ["RetainedEarningsAccumulatedDeficit"],
    "preferred": ["PreferredStockValue", "PreferredStockValueOutstanding"],
    "minority_interest": ["MinorityInterest"],
}


def _near_fy(end, fy_ends, tol_days=10):
    """Maps a period end date onto our canonical fiscal-year-end list."""
    d = date.fromisoformat(end)
    best, best_gap = None, None
    for f in fy_ends:
        gap = abs((date.fromisoformat(f) - d).days)
        if gap <= tol_days and (best_gap is None or gap < best_gap):
            best, best_gap = f, gap
    return best


# ========================================================================
# SEC annual facts
# ========================================================================

def fetch_sec_annual(ticker, config_path="config.yaml"):
    """Returns (info, annual, tags) or None if the company has no CIK."""
    cik = sec.get_cik(ticker, config_path)
    if cik is None:
        return None
    submissions = sec.fetch_submissions(cik, config_path)
    facts = sec.fetch_companyfacts(cik, config_path)

    # Fiscal-year ends: from the first of revenue / net income / OCF with data.
    # The series with the MOST RECENT data defines the fiscal years (a stale
    # revenue tag must never pin us to old years - found by testing: HRL, NEE).
    fy_ends, best_last = [], ""
    for key in ("revenue", "net_income", "ocf", "ebit"):
        series, _ = sec.best_annual_series(facts, DURATION_TAGS[key])
        if series and series[-1]["end"] > best_last:
            fy_ends, best_last = [e["end"] for e in series][-MAX_FY:], series[-1]["end"]

    annual = {e: {} for e in fy_ends}
    tags = {}

    def put_duration(field, series, tag):
        tags[field] = tag
        for e in series:
            m = _near_fy(e["end"], fy_ends)
            if m is not None:
                annual[m][field] = e["val"]

    for field, cands in DURATION_TAGS.items():
        series, tag = sec.best_annual_series(facts, cands)
        if series:
            put_duration(field, series, tag)
    series, tag = sec.best_annual_series(facts, EPS_TAGS, unit="USD/shares")
    if series:
        put_duration("eps_diluted", series, tag)
    series, tag = sec.best_annual_series(facts, SHARES_TAGS, unit="shares")
    if series:
        put_duration("diluted_shares", series, tag)

    # Balance sheet items are matched to the FY-end dates (plus the year before
    # the first one is NOT available - callers fall back to end-of-year values).
    for field, cands in INSTANT_TAGS.items():
        values, tag = sec.instant_series_at_dates(facts, cands, fy_ends)
        if values:
            tags[field] = tag
            for d, v in values.items():
                annual[d][field] = v

    # ---- total debt = long-term + current debt, assembled per fiscal year ----
    def inst(tag):
        vals, _ = sec.instant_series_at_dates(facts, [tag], fy_ends)
        return vals

    comb = inst("DebtLongtermAndShorttermCombinedAmount")
    lt_nc = [inst("LongTermDebtNoncurrent"), inst("LongTermDebtAndCapitalLeaseObligations")]
    lt_tot = inst("LongTermDebt")
    cur_debt = inst("DebtCurrent")
    cur_ltd = [inst("LongTermDebtCurrent"), inst("LongTermDebtAndCapitalLeaseObligationsCurrent")]
    short = [inst("ShortTermBorrowings"), inst("CommercialPaper")]
    first = lambda dicts, d: next((x[d] for x in dicts if d in x), None)
    recipes = {}
    for d in fy_ends:
        total, how = None, None
        if d in comb:
            total, how = comb[d], "DebtLongtermAndShorttermCombinedAmount"
        else:
            lt, lt_how = first(lt_nc, d), "LongTermDebtNoncurrent"
            if lt is None and d in lt_tot:  # LongTermDebt INCLUDES the current portion
                cur_part = first(cur_ltd, d)
                if cur_part is not None:
                    lt, lt_how = lt_tot[d] - cur_part, "LongTermDebt−current portion"
                else:
                    total, how = lt_tot[d] + sum(x.get(d, 0) for x in short), "LongTermDebt+ShortTermBorrowings/CP"
            if total is None and lt is not None:
                if d in cur_debt:
                    total, how = lt + cur_debt[d], f"{lt_how}+DebtCurrent"
                else:
                    total, how = lt + (first(cur_ltd, d) or 0) + sum(x.get(d, 0) for x in short), f"{lt_how}+current portion+short-term borrowings"
        if total is not None:
            annual[d]["total_debt"] = total
            recipes[how] = recipes.get(how, 0) + 1
    if recipes:
        tags["total_debt"] = max(recipes, key=recipes.get)

    info = {
        "cik": cik, "sic": int(submissions["sic"]) if submissions.get("sic") else None,
        "sic_desc": submissions.get("sicDescription"), "latest_10k": sec.latest_10k_info(submissions),
    }
    return info, annual, tags


# ========================================================================
# yfinance market data + price history
# ========================================================================

def fetch_market(ticker):
    t = yf.Ticker(ticker)
    info = t.info
    dy = info.get("dividendYield")
    div_yield = dy / 100 if dy is not None else info.get("trailingAnnualDividendYield")
    market = {
        "price": info.get("currentPrice") or info.get("regularMarketPrice"),
        "market_cap": info.get("marketCap"),
        "beta": info.get("beta"),
        "enterprise_value_yf": info.get("enterpriseValue"),
        "dividend_yield": div_yield,
        "name": info.get("longName") or info.get("shortName"),
        "sector": info.get("sector"),
        "yf_industry": info.get("industry"),
    }

    # Adjusted (dividends + splits) closes. ~37 months back so we can form 36
    # monthly returns; the drawdown only uses the last 3 years of dailies.
    start = date.today() - timedelta(days=1130)
    hist = t.history(start=start.isoformat(), auto_adjust=True)
    px = {"monthly_returns": [], "monthly_dates": [], "mdd": None, "months": 0, "first_date": None, "last_date": None}
    if not hist.empty:
        closes = hist["Close"].dropna()
        closes.index = closes.index.tz_localize(None) if closes.index.tz is not None else closes.index
        px["first_date"], px["last_date"] = str(closes.index[0].date()), str(closes.index[-1].date())
        px["months"] = (closes.index[-1] - closes.index[0]).days / 30.4375
        month_end = closes.resample("ME").last().dropna()
        rets = month_end.pct_change().dropna().iloc[-36:]
        px["monthly_returns"] = [float(x) for x in rets.values]
        px["monthly_dates"] = [str(d.date()) for d in rets.index]
        cutoff = closes.index[-1] - pd.Timedelta(days=1096)
        daily = closes[closes.index >= cutoff]
        px["mdd"] = float((1 - daily / daily.cummax()).max())
        px["mdd_months"] = (daily.index[-1] - daily.index[0]).days / 30.4375
    return market, px


def fetch_company(ticker_info, config_path="config.yaml"):
    """ticker_info: dict from industry_universe (ticker, name, price, ...)."""
    ticker = ticker_info["ticker"]
    errors = []
    market, px = fetch_market(ticker)
    try:
        sec_result = fetch_sec_annual(ticker, config_path)
    except Exception as e:  # keep going: market-only metrics still work
        sec_result, _ = None, errors.append(f"SEC fetch failed: {e}")
    if sec_result is None:
        info, annual, tags = {"cik": None, "sic": None, "sic_desc": None, "latest_10k": None}, {}, {}
        errors.append("No SEC 10-K data (no CIK, or files 20-F/IFRS) - fundamentals excluded")
    else:
        info, annual, tags = sec_result
    fy_ends = sorted(annual)
    if not fy_ends and sec_result is not None and not errors:
        errors.append("No 10-K annual data in SEC XBRL (foreign filer on 20-F/40-F, or newly listed) - fundamentals excluded")
    if fy_ends and (date.today() - date.fromisoformat(fy_ends[-1])).days > STALE_DAYS:
        errors.append(f"Stale SEC data: latest fiscal year ended {fy_ends[-1]} (> {STALE_DAYS // 30} months ago) - "
                      "fundamentals excluded (company may have stopped filing, or its XBRL tags changed)")
        annual, fy_ends = {}, []
    return {
        "ticker": ticker, "name": market.get("name") or ticker_info.get("name"),
        "sector": market.get("sector"), "yf_industry": market.get("yf_industry"),
        "sic": info["sic"], "sic_desc": info["sic_desc"], "cik": info["cik"],
        "latest_10k": info["latest_10k"], "fy_ends": fy_ends, "fye": fy_ends[-1] if fy_ends else None,
        "annual": annual, "tags": tags, "market": market, "px": px,
        "errors": errors, "fetched": datetime.now().isoformat(timespec="seconds"),
    }


# ========================================================================
# Cache (one JSON blob per (ticker, industry))
# ========================================================================

def _conn(db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS raw_stock (ticker TEXT, industry TEXT, fetched_date TEXT, "
                 "sector TEXT, json TEXT, PRIMARY KEY (ticker, industry))")
    return conn


def save_industry(industry, raws, db_path=DB_PATH):
    conn = _conn(db_path)
    conn.execute("DELETE FROM raw_stock WHERE industry = ?", (industry,))
    for r in raws:
        r = {**r, "industry": industry}
        conn.execute("INSERT OR REPLACE INTO raw_stock VALUES (?,?,?,?,?)",
                     (r["ticker"], industry, str(date.today()), r.get("sector"), json.dumps(r)))
    conn.commit()
    conn.close()


def load_industry(industry, db_path=DB_PATH):
    """Returns (list_of_raw_dicts, fetched_date) or None."""
    if not os.path.exists(db_path):
        return None
    conn = _conn(db_path)
    rows = conn.execute("SELECT json, fetched_date FROM raw_stock WHERE industry = ?", (industry,)).fetchall()
    conn.close()
    if not rows:
        return None
    return [json.loads(r[0]) for r in rows], rows[0][1]


def load_all_cached(db_path=DB_PATH):
    """Every cached company across all industries (for sector / all-stock fallback pools)."""
    if not os.path.exists(db_path):
        return []
    conn = _conn(db_path)
    rows = conn.execute("SELECT json FROM raw_stock").fetchall()
    conn.close()
    return [json.loads(r[0]) for r in rows]


def cached_industries(db_path=DB_PATH):
    if not os.path.exists(db_path):
        return set()
    conn = _conn(db_path)
    rows = conn.execute("SELECT DISTINCT industry FROM raw_stock").fetchall()
    conn.close()
    return {r[0] for r in rows}


def cached_counts(db_path=DB_PATH):
    """{industry: (number of companies, fetched_date)} for every cached industry (one cheap query)."""
    if not os.path.exists(db_path):
        return {}
    conn = _conn(db_path)
    rows = conn.execute("SELECT industry, COUNT(*), MAX(fetched_date) FROM raw_stock GROUP BY industry").fetchall()
    conn.close()
    return {i: (n, d) for i, n, d in rows}


def get_or_refresh_industry(industry, force_refresh=False, min_price=5.0, max_workers=8,
                            progress_callback=None, config_path="config.yaml"):
    """Cache hit -> instant. Otherwise pulls the live tradeable universe and
    fetches every company in parallel (SEC rate limit is enforced inside
    sec_edgar). One company failing never blocks the rest."""
    if not force_refresh:
        hit = load_industry(industry)
        if hit is not None:
            return {"raws": hit[0], "fetched_date": hit[1], "from_cache": True, "found": None,
                    "kept": len(hit[0]), "failures": {}}

    universe, found, kept = iu.pull_industry_universe(industry, min_price=min_price)
    sec.load_ticker_cik_map(config_path)  # warm shared cache single-threaded
    raws, failures = [], {}
    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(fetch_company, info, config_path): info["ticker"] for info in universe}
        for f in as_completed(futs):
            done += 1
            t = futs[f]
            try:
                raws.append(f.result())
            except Exception as e:
                failures[t] = str(e)
            if progress_callback:
                progress_callback(done, len(universe), t)
    if universe and not raws:
        raise RuntimeError(f"every company failed to download (first error: {next(iter(failures.values()), '?')[:160]}); "
                           "the cached data was left unchanged")
    # a company that failed this time keeps its previously cached data instead of silently disappearing
    old = {r["ticker"]: r for r in (load_industry(industry) or ([], None))[0]}
    kept_old = [old[t] for t in failures if t in old]
    raws = raws + kept_old
    save_industry(industry, raws)
    return {"raws": raws, "fetched_date": str(date.today()), "from_cache": False, "found": found,
            "kept": kept, "failures": failures}
