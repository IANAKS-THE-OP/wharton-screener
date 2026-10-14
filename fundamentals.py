"""
Fundamental metrics - SEC EDGAR as the main source, yfinance as fallback
and for market data (price, market cap, beta, enterprise value).
===========================================================================
Computes the 15 stock metrics the team uses for fundamental screening.
Every metric records WHICH SOURCE it came from (an SEC XBRL tag name, or
"yfinance") so a human can always trace a number back to where it came
from - never a silent black box.

If SEC data is missing for something (common for foreign private issuers
that file 20-F under IFRS instead of 10-K/GAAP), we fall back to
yfinance's own value for that one metric and mark the source accordingly.
Where BOTH sources produce a number for the same metric, we cross-check
them and flag anything that differs by more than 10% - a useful sanity
check, not an error in itself (different accounting treatments can
legitimately disagree a little).
"""

from datetime import date

import yfinance as yf

import sec_edgar as sec

METRIC_NAMES = [
    "pe", "peg", "ev_ebitda", "fcf_yield", "pb", "roic", "operating_margin", "roe",
    "revenue_cagr_3yr", "eps_cagr_3yr", "net_debt_ebitda", "interest_coverage",
    "current_ratio", "beta", "ocf_to_ni",
]

DEBT_TAG_PAIRS = [
    ("LongTermDebt", "DebtCurrent"),
    ("LongTermDebtNoncurrent", "LongTermDebtCurrent"),
]

TAG_CANDIDATES = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"],
    "ebit": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss"],
    "eps_diluted": ["EarningsPerShareDiluted"],
    "interest_expense": ["InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt"],
    "tax_expense": ["IncomeTaxExpenseBenefit"],
    "pretax_income": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
    "current_assets": ["AssetsCurrent"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "da": ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization"],
}


# ========================================================================
# STEP 1: pull every raw line item from SEC, with its source tag
# ========================================================================

def fetch_sec_raw(ticker, config_path="config.yaml"):
    cik = sec.get_cik(ticker, config_path)
    if cik is None:
        return None

    submissions = sec.fetch_submissions(cik, config_path)
    companyfacts = sec.fetch_companyfacts(cik, config_path)
    industry = sec.industry_label(submissions)
    latest_10k = sec.latest_10k_info(submissions)

    raw = {"ticker": ticker, "cik": cik, "industry": industry, "latest_10k": latest_10k}

    # Revenue drives which fiscal-year-end dates everything else aligns to.
    revenue_series, revenue_tag = sec.best_annual_series(companyfacts, TAG_CANDIDATES["revenue"])
    raw["revenue"] = {"series": revenue_series, "tag": revenue_tag}
    end_dates = [e["end"] for e in revenue_series]

    for field in ["ebit", "net_income", "tax_expense", "pretax_income", "ocf", "capex", "da"]:
        series, tag = sec.best_annual_series(companyfacts, TAG_CANDIDATES[field])
        raw[field] = {"series": series, "tag": tag}

    eps_series, eps_tag = sec.best_annual_series(companyfacts, TAG_CANDIDATES["eps_diluted"], unit="USD/shares")
    raw["eps_diluted"] = {"series": eps_series, "tag": eps_tag}

    interest_series, interest_tag = sec.best_annual_series(companyfacts, TAG_CANDIDATES["interest_expense"])
    raw["interest_expense"] = {"series": interest_series, "tag": interest_tag}

    # Instant (balance-sheet) items, matched to the revenue series' FY-end dates.
    for field in ["cash", "current_assets", "current_liabilities", "equity"]:
        values, tag = sec.instant_series_at_dates(companyfacts, TAG_CANDIDATES[field], end_dates)
        raw[field] = {"values": values, "tag": tag}

    debt_values, debt_tag = {}, None
    for tag_a, tag_b in DEBT_TAG_PAIRS:
        series_a, _ = sec.instant_series_at_dates(companyfacts, [tag_a], end_dates)
        series_b, _ = sec.instant_series_at_dates(companyfacts, [tag_b], end_dates)
        combined = {}
        for d in end_dates:
            va, vb = series_a.get(d), series_b.get(d)
            if va is not None or vb is not None:
                combined[d] = (va or 0) + (vb or 0)
        if len(combined) > len(debt_values):
            debt_values, debt_tag = combined, f"{tag_a}+{tag_b}"
    raw["debt"] = {"values": debt_values, "tag": debt_tag}

    return raw


# ========================================================================
# STEP 2: market data from yfinance (price, market cap, beta, EV, PEG)
# ========================================================================

def fetch_yfinance_market_data(ticker):
    t = yf.Ticker(ticker)
    info = t.info
    return {
        "price": info.get("currentPrice") or info.get("regularMarketPrice"),
        "market_cap": info.get("marketCap"),
        "beta": info.get("beta"),
        "enterprise_value": info.get("enterpriseValue"),
        "forward_peg": info.get("pegRatio"),
        "trailing_pe": info.get("trailingPE"),
        "price_to_book": info.get("priceToBook"),
    }


# ========================================================================
# Small helpers
# ========================================================================

def _latest(series_or_values, n=1):
    """series_or_values: either a list of {end,val} dicts (duration series)
    or a {end_date: val} dict (instant values). Returns the most recent
    n values, oldest first."""
    if isinstance(series_or_values, dict):
        items = sorted(series_or_values.items())
        return [v for _, v in items[-n:]]
    return [e["val"] for e in series_or_values[-n:]]


def _cagr(series, years_wanted=3):
    """CAGR over `years_wanted` years, using up to years_wanted+1 of the
    most recent annual values (4 values -> 3-year CAGR). Uses fewer years
    (and labels it) if that many aren't available. Returns (cagr, label)."""
    vals = [e["val"] for e in series[-(years_wanted + 1):]]
    if len(vals) < 2:
        return None, "insufficient data"
    n_years = len(vals) - 1
    first, last = vals[0], vals[-1]
    if first <= 0 or last <= 0:
        return None, "NM (negative base or end value)"
    cagr = (last / first) ** (1 / n_years) - 1
    label = f"{n_years}-yr CAGR" if n_years != years_wanted else f"{years_wanted}-yr CAGR"
    return cagr, label


# ========================================================================
# STEP 3: compute the 15 metrics
# ========================================================================

def compute_metrics(ticker, config_path="config.yaml"):
    """Returns {ticker, industry, latest_10k, metrics: {name: {value, source, note}}, flags: [...]}."""
    raw = fetch_sec_raw(ticker, config_path)
    market = fetch_yfinance_market_data(ticker)
    flags = []

    if raw is None:
        return {"ticker": ticker, "industry": None, "latest_10k": None, "metrics": {}, "flags": ["No CIK found on SEC EDGAR"]}

    metrics = {}

    def set_metric(name, value, source, note=""):
        metrics[name] = {"value": value, "source": source, "note": note}

    def cross_check(name, sec_value, yf_value, yf_label):
        """If both sources agree on a metric, flag a >10% disagreement."""
        if sec_value is None or yf_value is None or sec_value == 0:
            return
        diff_pct = abs(sec_value - yf_value) / abs(sec_value)
        if diff_pct > 0.10:
            flags.append(
                f"{name}: SEC-derived value ({sec_value:.3g}) vs {yf_label} ({yf_value:.3g}) "
                f"differ by {diff_pct*100:.0f}% (>10%)"
            )

    revenue_series = raw["revenue"]["series"]
    ebit_series = raw["ebit"]["series"]
    ni_series = raw["net_income"]["series"]
    eps_series = raw["eps_diluted"]["series"]
    da_series = raw["da"]["series"]
    ocf_series = raw["ocf"]["series"]
    capex_series = raw["capex"]["series"]
    interest_series = raw["interest_expense"]["series"]
    tax_series = raw["tax_expense"]["series"]
    pretax_series = raw["pretax_income"]["series"]

    latest_revenue = _latest(revenue_series)[-1] if revenue_series else None
    latest_ebit = _latest(ebit_series)[-1] if ebit_series else None
    latest_ni = _latest(ni_series)[-1] if ni_series else None
    latest_eps = _latest(eps_series)[-1] if eps_series else None
    latest_da = _latest(da_series)[-1] if da_series else None
    latest_ocf = _latest(ocf_series)[-1] if ocf_series else None
    latest_capex = _latest(capex_series)[-1] if capex_series else None
    latest_interest = _latest(interest_series)[-1] if interest_series else None
    latest_tax = _latest(tax_series)[-1] if tax_series else None
    latest_pretax = _latest(pretax_series)[-1] if pretax_series else None

    cash_vals = raw["cash"]["values"]
    equity_vals = raw["equity"]["values"]
    debt_vals = raw["debt"]["values"]
    curr_assets_vals = raw["current_assets"]["values"]
    curr_liab_vals = raw["current_liabilities"]["values"]

    latest_cash = _latest(cash_vals)[-1] if cash_vals else None
    latest_equity = _latest(equity_vals)[-1] if equity_vals else None
    latest_debt = _latest(debt_vals)[-1] if debt_vals else None
    latest_curr_assets = _latest(curr_assets_vals)[-1] if curr_assets_vals else None
    latest_curr_liab = _latest(curr_liab_vals)[-1] if curr_liab_vals else None

    price = market["price"]
    market_cap = market["market_cap"]
    ev = market["enterprise_value"]

    # --- EBITDA (not one of the 15, but feeds EV/EBITDA and net debt/EBITDA) ---
    ebitda = (latest_ebit + latest_da) if (latest_ebit is not None and latest_da is not None) else None

    # --- 1. P/E ---
    earnings_negative = latest_eps is not None and latest_eps <= 0
    if earnings_negative:
        set_metric("pe", None, raw["eps_diluted"]["tag"], "NM (negative earnings)")
    elif price and latest_eps:
        pe = price / latest_eps
        set_metric("pe", pe, f"price (yfinance) / diluted EPS ({raw['eps_diluted']['tag']})")
        cross_check("P/E", pe, market["trailing_pe"], "yfinance trailingPE")
    else:
        set_metric("pe", None, None, "missing price or EPS")

    # --- 2. PEG ---
    eps_cagr, eps_cagr_label = _cagr(eps_series)
    if earnings_negative:
        set_metric("peg", None, None, "NM (negative earnings)")
    elif market["forward_peg"] is not None:
        set_metric("peg", market["forward_peg"], "yfinance forward PEG")
    elif metrics["pe"]["value"] is not None and eps_cagr and eps_cagr > 0:
        peg = metrics["pe"]["value"] / (eps_cagr * 100)
        set_metric("peg", peg, f"historical PEG: P/E / EPS {eps_cagr_label}", "historical PEG (no forward PEG available)")
    else:
        set_metric("peg", None, None, "missing P/E or positive EPS growth")

    # --- 3. EV/EBITDA ---
    if ebitda is not None and ebitda <= 0:
        set_metric("ev_ebitda", None, None, "NM (negative/zero EBITDA)")
    elif ev and ebitda:
        set_metric("ev_ebitda", ev / ebitda, f"yfinance EV / (EBIT[{raw['ebit']['tag']}] + D&A[{raw['da']['tag']}])")
    else:
        set_metric("ev_ebitda", None, None, "missing EV or EBITDA")

    # --- 4. FCF yield ---
    if latest_ocf is not None and latest_capex is not None and market_cap:
        fcf = latest_ocf - abs(latest_capex)
        set_metric("fcf_yield", fcf / market_cap, f"(OCF[{raw['ocf']['tag']}] - capex[{raw['capex']['tag']}]) / market cap")
    else:
        set_metric("fcf_yield", None, None, "missing OCF, capex, or market cap")

    # --- 5. P/B ---
    if market_cap and latest_equity:
        pb = market_cap / latest_equity
        set_metric("pb", pb, f"market cap / equity[{raw['equity']['tag']}]")
        cross_check("P/B", pb, market["price_to_book"], "yfinance priceToBook")
    else:
        set_metric("pb", None, None, "missing market cap or equity")

    # --- 6. ROIC ---
    common_dates = sorted(set(equity_vals.keys()) & set(debt_vals.keys()) & set(cash_vals.keys()))
    if latest_tax is not None and latest_pretax and latest_ebit is not None and common_dates:
        tax_rate = latest_tax / latest_pretax
        recent_dates = common_dates[-2:]
        ic_values = [debt_vals[d] + equity_vals[d] - cash_vals[d] for d in recent_dates]
        invested_capital = sum(ic_values) / len(ic_values)
        if invested_capital > 0:
            roic = (latest_ebit * (1 - tax_rate)) / invested_capital
            note = "avg of last 2 years' invested capital" if len(ic_values) == 2 else "latest year only"
            set_metric("roic", roic, f"EBIT x (1-tax rate) / (debt+equity-cash)", note)
        else:
            set_metric("roic", None, None, "invested capital <= 0")
    else:
        set_metric("roic", None, None, "missing tax, pretax income, EBIT, or balance sheet data")

    # --- 7. Operating margin ---
    if latest_ebit is not None and latest_revenue:
        set_metric("operating_margin", latest_ebit / latest_revenue, f"EBIT[{raw['ebit']['tag']}] / revenue[{raw['revenue']['tag']}]")
    else:
        set_metric("operating_margin", None, None, "missing EBIT or revenue")

    # --- 8. ROE ---
    if latest_ni is not None and len(_latest(equity_vals, 2)) == 2:
        avg_equity = sum(_latest(equity_vals, 2)) / 2
        if avg_equity > 0:
            set_metric("roe", latest_ni / avg_equity, f"net income[{raw['net_income']['tag']}] / average equity[{raw['equity']['tag']}]")
        else:
            set_metric("roe", None, None, "average equity <= 0")
    else:
        set_metric("roe", None, None, "missing net income or 2 years of equity")

    # --- 9. Revenue 3yr CAGR ---
    rev_cagr, rev_cagr_label = _cagr(revenue_series)
    set_metric("revenue_cagr_3yr", rev_cagr, f"revenue[{raw['revenue']['tag']}] {rev_cagr_label}" if rev_cagr is not None else None,
               rev_cagr_label if rev_cagr is None else "")

    # --- 10. EPS 3yr CAGR ---
    set_metric("eps_cagr_3yr", eps_cagr, f"diluted EPS[{raw['eps_diluted']['tag']}] {eps_cagr_label}" if eps_cagr is not None else None,
               eps_cagr_label if eps_cagr is None else "")

    # --- 11. Net debt/EBITDA ---
    if latest_debt is not None and latest_cash is not None and ebitda:
        if ebitda <= 0:
            set_metric("net_debt_ebitda", None, None, "NM (negative/zero EBITDA)")
        else:
            set_metric("net_debt_ebitda", (latest_debt - latest_cash) / ebitda, f"(debt[{raw['debt']['tag']}] - cash) / EBITDA")
    else:
        set_metric("net_debt_ebitda", None, None, "missing debt, cash, or EBITDA")

    # --- 12. Interest coverage ---
    if latest_ebit is not None and latest_interest:
        set_metric("interest_coverage", latest_ebit / latest_interest, f"EBIT / interest expense[{raw['interest_expense']['tag']}]")
    else:
        set_metric("interest_coverage", None, None, "missing EBIT or interest expense")

    # --- 13. Current ratio ---
    if latest_curr_assets is not None and latest_curr_liab:
        set_metric("current_ratio", latest_curr_assets / latest_curr_liab, "current assets / current liabilities")
    else:
        set_metric("current_ratio", None, None, "missing current assets or liabilities")

    # --- 14. Beta ---
    if market["beta"] is not None:
        set_metric("beta", market["beta"], "yfinance beta")
    else:
        set_metric("beta", None, None, "yfinance beta missing")

    # --- 15. OCF/NI ---
    if latest_ocf is not None and latest_ni:
        set_metric("ocf_to_ni", latest_ocf / latest_ni, f"OCF[{raw['ocf']['tag']}] / net income[{raw['net_income']['tag']}]")
    else:
        set_metric("ocf_to_ni", None, None, "missing OCF or net income")

    return {
        "ticker": ticker, "industry": raw["industry"], "latest_10k": raw["latest_10k"],
        "metrics": metrics, "flags": flags, "raw": raw,
    }
