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
# SEC per-share values and share counts are NOT split-adjusted: each is on the share basis of the 10-K it came from
# (later 10-Ks restate the years they report). We keep each value's filing date + the yfinance split history, and
# scoring2 converts them to today's share basis (scoring2._Co.basis_factor).
SHARE_BASIS_FIELDS = ("eps_diluted", "diluted_shares")
SPLITS_START = "2015-01-01"  # older splits predate every fiscal year we keep (MAX_FY)

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
    filed = {e: {} for e in fy_ends}  # per-share / share-count values: filing date = the share basis they are on

    def put_duration(field, series, tag):
        tags[field] = tag
        for e in series:
            m = _near_fy(e["end"], fy_ends)
            if m is not None:
                annual[m][field] = e["val"]
                if field in SHARE_BASIS_FIELDS:
                    filed[m][field] = e["filed"]

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
        "filed": {e: f for e, f in filed.items() if f},
        "cik": cik, "sic": int(submissions["sic"]) if submissions.get("sic") else None,
        "sic_desc": submissions.get("sicDescription"), "latest_10k": sec.latest_10k_info(submissions),
        "annual_form": annual_form(submissions),
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

    try:
        market_splits = _split_list(t.splits)
    except Exception:  # keep going: scoring treats a missing split history as "no splits"
        market_splits = []
    # Adjusted (dividends + splits) closes, 10 years back: the history window (3 / 5 / 10 yrs) is chosen on the page
    start = date.today() - timedelta(days=3665)
    hist = t.history(start=start.isoformat(), auto_adjust=True)
    px = price_block(hist["Close"] if not hist.empty else pd.Series(dtype=float))
    market["_splits"] = market_splits
    return market, px


def _split_list(series):
    """yfinance split Series (date -> ratio, 0 = none) -> [["YYYY-MM-DD", ratio], ...] since SPLITS_START, oldest first."""
    if series is None or len(series) == 0:
        return []
    out = []
    for d, r in series.items():
        d = str(pd.Timestamp(d).date())
        if d >= SPLITS_START and r and float(r) > 0 and float(r) != 1.0:
            out.append([d, float(r)])
    return sorted(out)


def _clean_ratio(r):
    """A ratio a board would declare: n-for-m (or its reverse) with m <= 4, e.g. 10, 3/2, 5/4, 1/20, 2/5."""
    from fractions import Fraction
    return any(abs(float(Fraction(x).limit_denominator(4)) - x) < 1e-6 * max(1.0, x) for x in (r, 1 / r))


def verify_splits(splits, facts, tags):
    """Keep only the yfinance 'splits' that really changed the share count. yfinance also books spin-offs as fractional
    splits (GE 1.281 GE HealthCare, CMCSA 1.067 Versant) to adjust old prices; the share count didn't change, so they
    must not touch EPS. Test: SEC restates every period in filings after a real split, so a period (10-K or 10-Q
    comparative) reported both before and after the split date shows the diluted share count (or EPS, inverted) change
    by the split ratio. Only filing pairs with no other split between them count; the tightest pair wins.
    -> (kept [[date, ratio]], dropped [[date, ratio, reason]])."""
    def by_period(tag, unit):
        out = {}
        for e in ((facts or {}).get("facts", {}).get("us-gaap", {}).get(tag, {}).get("units", {}).get(unit, [])):
            if e.get("start") and e.get("end") and e.get("filed") and e.get("val"):
                out.setdefault((e["start"], e["end"]), []).append((e["filed"], e["val"]))
        return out
    series = [(by_period(t, u), inv) for t, u, inv in
              ((tags.get("diluted_shares") or SHARES_TAGS[0], "shares", False), (tags.get("eps_diluted") or EPS_TAGS[0], "USD/shares", True))]
    dates = [d for d, _ in splits]
    kept, dropped = [], []
    for d, r in splits:
        obs = None
        for per, inv in series:
            best = None
            for vals in per.values():
                x = sorted(vals)
                b = next(((f, v) for f, v in reversed(x) if f < d), None)  # last value filed before the split
                a = next(((f, v) for f, v in x if f > d), None)            # first value filed after it
                if not (b and a) or b[1] * a[1] <= 0 or any(b[0] < o < a[0] for o in dates if o != d):
                    continue
                span = (date.fromisoformat(a[0]) - date.fromisoformat(b[0])).days
                if best is None or span < best[0]:
                    best = (span, (b[1] / a[1]) if inv else (a[1] / b[1]))
            if best is not None:
                obs = best[1]
                break
        if obs is not None and abs(obs / r - 1) < 0.1:
            kept.append([d, r])
        elif obs is not None:
            dropped.append([d, r, f"SEC restatement ratio {obs:.3f}, not {r:g}: not a share split (spin-off adjustment?)"])
        elif _clean_ratio(r):
            kept.append([d, r])  # no filing on both sides yet (e.g. a split after the latest 10-Q): trust a clean ratio
        else:
            dropped.append([d, r, f"unverified fractional ratio {r:g} (yfinance spin-off adjustment?)"])
    return kept, dropped


def fetch_splits(tickers, chunk=100):
    """{ticker: [[date, ratio], ...]} from batched yfinance downloads (actions=True). A ticker whose download failed
    is left out (callers keep what they had), so 'no splits' ([]) is never confused with 'unknown'."""
    import yfinance.shared as yfs
    out = {}
    for i in range(0, len(tickers), chunk):
        part = tickers[i:i + chunk]
        try:
            data = yf.download(part, start=SPLITS_START, auto_adjust=True, actions=True, progress=False,
                               threads=True, group_by="column")
        except Exception:
            continue
        errs = dict(getattr(yfs, "_ERRORS", {}) or {})
        for t in part:
            if t in errs:
                continue
            try:
                close = data["Close"] if len(part) == 1 else data["Close"][t]
                sp = (data["Stock Splits"] if len(part) == 1 else data["Stock Splits"][t]) if "Stock Splits" in data else None
            except (KeyError, IndexError, TypeError):
                continue
            if isinstance(close, pd.DataFrame):
                close, sp = close.iloc[:, 0], (sp.iloc[:, 0] if isinstance(sp, pd.DataFrame) else sp)
            if close.dropna().empty:
                continue
            out[t] = _split_list(sp.dropna() if sp is not None else None)
    return out


def backfill_share_basis(db_path=DB_PATH, cache_dir="sec_cache", splits=None):
    """Add the split history ("splits") and each per-share value's filing date ("filed") to every cached company
    without a full refresh. Filing dates come from the local SEC companyfacts cache, matched on the tag, fiscal year
    and the cached VALUE (a value that no longer matches is left without a date: scoring then uses the fiscal year end).
    splits: {ticker: list} (default: fetch_splits for every cached ticker). Returns a summary dict."""
    conn = _conn(db_path)
    rows = sorted(conn.execute("SELECT ticker, industry, json FROM raw_stock").fetchall())  # a ticker's rows adjacent
    tickers = sorted({t for t, _, _ in rows})
    splits = fetch_splits(tickers) if splits is None else splits
    facts_cache, n_dated, n_vals, n_split = {}, 0, 0, 0
    for t, ind, js in rows:
        r = json.loads(js)
        yf_splits = splits.get(t, r.get("splits_yf", r.get("splits")))
        filed = {}
        cik = r.get("cik")
        facts = None
        if cik:
            if cik not in facts_cache:
                p = os.path.join(cache_dir, f"companyfacts_{str(cik).zfill(10)}.json")
                facts_cache.clear()  # one company at a time (files are large)
                facts_cache[cik] = json.load(open(p)) if os.path.exists(p) else None
            facts = facts_cache[cik]
        if yf_splits is not None:
            r["splits_yf"] = yf_splits
            r["splits"], r["splits_dropped"] = verify_splits(yf_splits, facts, r.get("tags") or {})
        n_split += bool(r.get("splits"))
        if cik and r.get("annual"):
            ends = sorted(r["annual"])
            for field, unit in (("eps_diluted", "USD/shares"), ("diluted_shares", "shares")):
                tag = (r.get("tags") or {}).get(field)
                series = sec.annual_duration_series(facts, tag, unit) if (facts and tag) else []
                for e in series:
                    m = _near_fy(e["end"], ends)
                    if m is None or field not in r["annual"][m]:
                        continue
                    if abs(e["val"] - r["annual"][m][field]) <= 1e-9 * max(1.0, abs(e["val"])):
                        filed.setdefault(m, {})[field] = e["filed"]
            n_vals += sum(field in a for a in r["annual"].values() for field in SHARE_BASIS_FIELDS)
            n_dated += sum(len(v) for v in filed.values())
        r["filed"] = filed
        conn.execute("UPDATE raw_stock SET json = ? WHERE ticker = ? AND industry = ?", (json.dumps(r), t, ind))
    conn.commit()
    conn.close()
    return {"rows": len(rows), "tickers": len(tickers), "splits_fetched": len(splits), "rows_with_splits": n_split,
            "share_values": n_vals, "dated": n_dated}


PX_WINDOWS = (3, 5, 10)  # history-window choices (years); the same list as etf_data.WINDOWS


def price_block(closes):
    """Daily adjusted closes -> the stock price fields the scorer uses: up to 120 monthly returns, years of history,
    and the max drawdown over the last 3 / 5 / 10 years (all ending on the last close)."""
    px = {"monthly_returns": [], "years": 0.0, "months": 0, "first_date": None, "last_date": None,
          **{f"mdd_{w}": None for w in PX_WINDOWS}}
    closes = closes.dropna()
    if len(closes) < 30:
        return px
    closes.index = closes.index.tz_localize(None) if closes.index.tz is not None else closes.index
    px["first_date"], px["last_date"] = str(closes.index[0].date()), str(closes.index[-1].date())
    days = (closes.index[-1] - closes.index[0]).days
    px["months"], px["years"] = days / 30.4375, days / 365.25
    month_end = closes.resample("ME").last().dropna()
    px["monthly_returns"] = [float(x) for x in month_end.pct_change().dropna().iloc[-120:].values]
    for w in PX_WINDOWS:
        daily = closes[closes.index >= closes.index[-1] - pd.Timedelta(days=int(365.25 * w))]
        px[f"mdd_{w}"] = float((1 - daily / daily.cummax()).max())
    return px


ANNUAL_FORMS = ("10-K", "10-KT", "20-F", "40-F")
FOREIGN_FORMS = ("20-F", "40-F")


def annual_form(submissions):
    """The company's most recent ANNUAL report form from its SEC submissions: '10-K', '20-F' (foreign private issuer),
    '40-F' (Canadian issuer), '10-KT', or None (no annual report yet, e.g. a new listing or SPAC). Amendments count
    as their base form."""
    forms = ((submissions or {}).get("filings") or {}).get("recent", {}).get("form") or []
    return next((f.split("/")[0] for f in forms if f.split("/")[0] in ANNUAL_FORMS), None)


def backfill_annual_forms(db_path=DB_PATH, cache_dir="sec_cache"):
    """One-off, offline: add 'annual_form' to cached companies from the local SEC submissions cache (no network).
    Returns {form: count}."""
    conn = _conn(db_path)
    rows = conn.execute("SELECT ticker, industry, json FROM raw_stock").fetchall()
    counts = {}
    for t, ind, js in rows:
        r = json.loads(js)
        sub = None
        if r.get("cik"):
            p = os.path.join(cache_dir, f"submissions_{str(r['cik']).zfill(10)}.json")
            if os.path.exists(p):
                with open(p) as f:
                    sub = json.load(f)
        r["annual_form"] = annual_form(sub) if sub else None
        counts[r["annual_form"]] = counts.get(r["annual_form"], 0) + 1
        conn.execute("UPDATE raw_stock SET json = ? WHERE ticker = ? AND industry = ?", (json.dumps(r), t, ind))
    conn.commit()
    conn.close()
    return counts


def _verified(splits_yf, info, tags, config_path):
    if not splits_yf:
        return [], []
    try:
        facts = sec.fetch_companyfacts(info["cik"], config_path) if info.get("cik") else None
    except Exception:
        facts = None
    return verify_splits(splits_yf, facts, tags)


def fetch_company(ticker_info, config_path="config.yaml"):
    """ticker_info: dict from industry_universe (ticker, name, price, ...)."""
    ticker = ticker_info["ticker"]
    errors = []
    market, px = fetch_market(ticker)
    splits_yf = market.pop("_splits", [])
    try:
        sec_result = fetch_sec_annual(ticker, config_path)
    except Exception as e:  # keep going: market-only metrics still work
        sec_result, _ = None, errors.append(f"SEC fetch failed: {e}")
    if sec_result is None:
        info, annual, tags = {"cik": None, "sic": None, "sic_desc": None, "latest_10k": None, "annual_form": None}, {}, {}
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
        "latest_10k": info["latest_10k"], "annual_form": info.get("annual_form"), "fy_ends": fy_ends, "fye": fy_ends[-1] if fy_ends else None,
        "annual": annual, "tags": tags, "market": market, "px": px,
        "splits_yf": splits_yf, **dict(zip(("splits", "splits_dropped"), _verified(splits_yf, info, tags, config_path))), "filed": {e: f for e, f in (info.get("filed") or {}).items() if e in annual},
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
