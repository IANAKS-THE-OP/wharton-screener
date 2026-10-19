"""
14-metric stock scoring engine (pure functions - no Streamlit, no network).
============================================================================
Implements the spec in METHODOLOGY.md:

  Part B  compute_metrics()   - 14 metric values + forced-score / exclusion rules
  Part C  red flags           - Altman, Piotroski, ROIC<WACC, payout, data flags
  Part D  prepare_industry()  - peer pools, sector fallback, percentile scores
  Part E  finalize()          - one-sided Fit against each metric's Laura limit
  Part F  wacc                - display + flag only
  Part G  finalize()          - Overall = Composite - k x (100 - Fit), hard limits, ranking
  Part I  run_self_tests()

Two stages so the UI stays fast:
  prepare_industry(...)  depends on data + (T-bill, risk-free, ERP) settings
  finalize(...)          depends on weights / limits / penalty strength / hard limits
"""

import math
from datetime import datetime

import numpy as np
import pandas as pd

import laura

# ========================================================================
# Metric table (Part B). Values are held in DISPLAY units: % , x, pp/year.
# ========================================================================

# Default weights = Laura Gao's standards (moderate risk, no big drops, steady growth, nothing heavy); total 100.
# "category" = one of the six CATEGORIES. Role-based totals: stocks are the growth driver (LAURA_CATEGORY_TOTALS below);
# the ETF model (etf_scoring.py) is the stability core, with its own LAURA_CATEGORY_TOTALS.
METRICS = [
    {"key": "roic", "label": "ROIC", "units": "%", "direction": "higher", "weight": 8, "category": "Quality"},
    {"key": "gross_profitability", "label": "Gross profitability", "units": "%", "direction": "higher", "weight": 5, "category": "Quality"},
    {"key": "delta_roic", "label": "Δ ROIC", "units": "pp/yr", "direction": "higher", "weight": 7, "category": "Growth"},
    {"key": "delta_op_margin", "label": "Δ Operating margin", "units": "pp/yr", "direction": "higher", "weight": 5, "category": "Growth"},
    {"key": "earnings_yield", "label": "Earnings yield", "units": "%", "direction": "higher", "weight": 6, "category": "Cost/valuation"},
    {"key": "fcf_yield", "label": "FCF yield", "units": "%", "direction": "higher", "weight": 6, "category": "Cost/valuation"},
    {"key": "lynch", "label": "Lynch ratio", "units": "x", "direction": "higher", "weight": 6, "category": "Cost/valuation"},
    {"key": "revenue_growth", "label": "Revenue growth", "units": "%", "direction": "higher", "weight": 8, "category": "Growth"},
    {"key": "net_debt_ebitda", "label": "Net debt / EBITDA", "units": "x", "direction": "lower", "weight": 8, "category": "Safety"},
    {"key": "interest_coverage", "label": "Interest coverage", "units": "x", "direction": "higher", "weight": 7, "category": "Safety"},
    {"key": "sloan", "label": "Sloan accrual ratio", "units": "%", "direction": "lower", "weight": 7, "category": "Quality"},
    {"key": "downside_dev", "label": "Downside deviation", "units": "%", "direction": "lower", "weight": 10, "category": "Risk"},
    {"key": "max_drawdown", "label": "Max drawdown", "units": "%", "direction": "lower", "weight": 12, "category": "Risk"},
    {"key": "shareholder_yield", "label": "Shareholder yield", "units": "%", "direction": "higher", "weight": 5, "category": "Income"},
]
METRIC_BY_KEY = {m["key"]: m for m in METRICS}
KEYS = [m["key"] for m in METRICS]
DEFAULT_WEIGHTS = {m["key"]: m["weight"] for m in METRICS}
# Laura's six categories. Same categories in both models, different totals by role (each model sums to 100).
CATEGORIES = ["Risk", "Cost/valuation", "Quality", "Growth", "Safety", "Income"]
LAURA_CATEGORY_TOTALS = {"Risk": 22, "Cost/valuation": 18, "Quality": 20, "Growth": 20, "Safety": 15, "Income": 5}  # growth driver
CATEGORY_LABEL = {c: ("Cost" if c == "Cost/valuation" else c) for c in CATEGORIES}  # what the UI and docs show
CATEGORY_QUESTION = {"Risk": "How much can it drop?", "Cost/valuation": "Are we overpaying?",
                     "Quality": "Is what we own good and well-built?", "Growth": "Is it growing?",
                     "Safety": "Could it fail?", "Income": "What does it pay Laura?"}  # Laura's question per category
ROLE_NOTE = ("Stocks are the growth driver (more growth, less risk weight); ETFs are the stability core (more risk and "
             "diversification weight); bonds guarantee the payments.")


def category_totals(metrics, weights=None):
    """{category: summed weight} in CATEGORIES order. weights defaults to each metric's default weight."""
    out = {c: 0.0 for c in CATEGORIES}
    for m in metrics:
        out[m["category"]] += m["weight"] if weights is None else weights.get(m["key"], m["weight"])
    return out


def category_text(totals=None, sep=" · "):
    """'Risk 22 · Cost 18 · ...' from a model's category totals (default: the stock model's), for test names."""
    totals = totals or LAURA_CATEGORY_TOTALS
    return sep.join(f"{CATEGORY_LABEL[c]} {totals[c]:g}" for c in CATEGORIES)

WINDOWS = (3, 5, 10)  # history-window choices (years) for downside deviation and max drawdown
DEFAULT_SETTINGS = {"tbill": 0.03, "rf": 0.0425, "erp": 0.05, "window": 5}
DEFAULT_PENALTY = 0.4  # k in Overall = max(0, Composite - k x (100 - Fit)); 0 = pure Composite
SENSITIVITY_KS = (0.2, 0.4, 0.6)  # penalty strengths compared under the ranked results (both pages)
PREP_VERSION = 3  # bump when prepare_industry's Composite inputs change. NOTE: part of etf_holdings.model_hash(), so a bump wipes holding_scores.db
WINDOW_SLACK_YEARS = 7 / 365.25  # listing-date / weekend rounding: up to 7 days short of the window still counts as full
HIST_KEYS = ("downside_dev", "max_drawdown")
FOREIGN_FORMS = ("20-F", "40-F")
MIN_POOL = 10
FINANCIAL_WORDS = ["bank", "insurance", "credit services", "capital markets", "mortgage finance"]

OK, ZERO, HUNDRED, EXCL = "ok", "zero", "hundred", "excluded"


def _num(x):
    return x is not None and isinstance(x, (int, float)) and math.isfinite(x)


# ========================================================================
# Percentiles and fit (Parts D and E) - the arithmetic core
# ========================================================================

def percentile_scores(values, higher_is_better=True):
    """values: list of numbers. Rank 1 = lowest, ties share the average rank.
    p = (rank-1)/(n-1)*100 (n=1 -> 50). Lower-is-better metrics flip to 100-p."""
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [50.0]
    ranks = pd.Series(values, dtype=float).rank(method="average").tolist()
    p = [(r - 1) / (n - 1) * 100 for r in ranks]
    return p if higher_is_better else [100 - x for x in p]


def metric_fit(value, limit, spread, direction):
    """Part E6, one-sided. Only the bad side of Laura's limit counts:
      lower-is-better  (limit = ceiling): distance = max(0, value - limit)
      higher-is-better (limit = floor):   distance = max(0, limit - value)
      Fit = 100 x max(0, 1 - (distance / spread) / 2), spread = peer p75 - p25
    So: on the good side -> 100; one spread past -> 50; two spreads past -> 0.
    Returns (fit, distance)."""
    if not (_num(value) and _num(limit) and _num(spread)) or spread <= 0:
        return None, None
    dist = max(0.0, (value - limit) if direction == "lower" else (limit - value))
    return 100.0 * max(0.0, 1.0 - (dist / spread) / 2.0), dist


def overall_score(composite, fit, k=DEFAULT_PENALTY):
    """Part G1: Overall = max(0, Composite - k x (100 - Fit)). No Fit -> Composite; no Composite -> None."""
    if composite is None:
        return None
    if fit is None:
        return composite
    return max(0.0, composite - k * (100.0 - fit))


def penalty_breakdown(rows, weights, den, k, lost):
    """'Why not higher': [(key, points)] for every metric that misses its limit (Fit < 100), most costly first.
    points = k x w x (100 - Fit) / sum(w); scaled down so they add up to the points actually lost when Overall hits 0."""
    raw = [(key, k * weights.get(key, 0) * (100 - r["fit"]) / den) for key, r in rows.items()
           if r["fit"] is not None and r["fit"] < 100 - 1e-9 and r.get("pct") is not None and den]
    tot = sum(x for _, x in raw)
    scale = (lost / tot) if (tot > 0 and lost is not None and lost < tot - 1e-12) else 1.0
    return sorted(((key, x * scale) for key, x in raw if x * scale > 1e-12), key=lambda x: -x[1])


def _short(v):
    return "—" if v is None else f"{v:.3g}" if abs(v) < 100 else f"{v:,.0f}"


def why_text(why, rows, labels):
    """'Max drawdown −2.1 (31.5 vs limit ≤ 24.0); ...' or 'meets every Laura limit'."""
    def one(key, lost):
        r = rows[key]
        side = "≤" if r["direction"] == "lower" else "≥"
        val = "forced 0" if r["status"] == ZERO else _short(r["value"])
        return f"{labels[key]} −{lost:.1f} ({val} vs limit {side} {_short(r['limit'])})"
    return "; ".join(one(k, lost) for k, lost in why) or "meets every Laura limit"


def downside_deviation(monthly_returns, tbill):
    """Part B12: sqrt(mean(min(0, r - MAR)^2)) * sqrt(12), in percent."""
    mar = (1 + tbill) ** (1 / 12) - 1
    sq = [min(0.0, r - mar) ** 2 for r in monthly_returns]
    return math.sqrt(sum(sq) / len(sq)) * math.sqrt(12) * 100


def max_drawdown(prices):
    """Part B13: max over days of (1 - price / highest price so far), as a fraction."""
    peak, worst = -math.inf, 0.0
    for p in prices:
        peak = max(peak, p)
        worst = max(worst, 1 - p / peak)
    return worst


def composite_score(scores, weights):
    """Part D5. scores: {key: score or None (excluded)}. Weights of excluded
    metrics are dropped (the denominator shrinks)."""
    num = den = 0.0
    for k, s in scores.items():
        if s is None:
            continue
        w = weights.get(k, 0)
        num += w * s
        den += w
    return num / den if den > 0 else None


# ========================================================================
# Raw-data helpers
# ========================================================================

def is_financial(raw):
    sic = raw.get("sic")
    if sic is not None and 6000 <= sic <= 6399:
        return True
    names = f"{raw.get('industry') or ''} {raw.get('yf_industry') or ''}".lower()
    return any(w in names for w in FINANCIAL_WORDS)


def _skip_altman(raw):
    sic = raw.get("sic")
    names = f"{raw.get('industry') or ''} {raw.get('yf_industry') or ''}".lower()
    return is_financial(raw) or sic == 6798 or "reit" in names or (sic is not None and 4900 <= sic <= 4999)


class _Co:
    """Convenience wrapper over one raw dict: ordered fiscal years + getters."""

    def __init__(self, raw):
        self.raw = raw
        self.ends = sorted(raw.get("annual", {}))
        self.n = len(self.ends)
        self.A = raw.get("annual", {})

    def v(self, i, field):
        """Annual value at fiscal-year index i (negative allowed), or None if absent."""
        if i < -self.n or i >= self.n:
            return None
        val = self.A[self.ends[i]].get(field)
        return val if _num(val) else None

    def avg(self, i, field):
        """(beginning + end)/2 when both exist, else end of year. Returns (value, used_average)."""
        end = self.v(i, field)
        if end is None:
            return None, False
        idx = i if i >= 0 else self.n + i
        if idx - 1 >= 0:
            beg = self.v(idx - 1, field)
            if beg is not None:
                return (beg + end) / 2, True
        return end, False

    def tax_rate(self, i):
        tax, pre = self.v(i, "tax_expense"), self.v(i, "pretax_income")
        if tax is None or pre is None or pre <= 0:
            return 0.21
        return min(max(tax / pre, 0.0), 0.35)

    def debt(self, i):
        """Total debt. If the company reports a balance sheet but no debt tag at
        all, it's treated as 0 (returns (0, True) so callers can note it)."""
        d = self.v(i, "total_debt")
        if d is not None:
            return d, False
        if self.v(i, "equity") is not None and self.v(i, "total_assets") is not None:
            return 0.0, True
        return None, False

    def invested_capital(self, i):
        debt, _ = self.debt(i)
        eq, cash = self.v(i, "equity"), self.v(i, "cash")
        if debt is None or eq is None or cash is None:
            return None
        return debt + eq - cash

    def roic(self, i):
        """Returns (roic_fraction, avg_ic, note) or (None, None, reason)."""
        idx = i if i >= 0 else self.n + i
        ebit = self.v(idx, "ebit")
        if ebit is None:
            return None, None, "EBIT missing"
        ic_end = self.invested_capital(idx)
        if ic_end is None:
            return None, None, "balance sheet items missing"
        ic_beg = self.invested_capital(idx - 1) if idx >= 1 else None
        ic = (ic_beg + ic_end) / 2 if ic_beg is not None else ic_end
        note = "average invested capital" if ic_beg is not None else "end-of-year invested capital (no prior year)"
        if ic <= 0:
            return None, ic, "negative invested capital"
        return ebit * (1 - self.tax_rate(idx)) / ic, ic, note


def _px_window(px, window):
    """-> (years of prices available, monthly returns, max drawdown over the last `window` yrs, legacy cache?).
    Legacy cache (before 10-yr histories): 36 monthly returns and a 3-yr 'mdd' only."""
    rets = px.get("monthly_returns") or []
    if "years" not in px:  # legacy raw: at most ~3 years were downloaded
        yrs = min((px.get("mdd_months") or 0) / 12, (px.get("months") or 0) / 12)
        return yrs, rets, (px.get("mdd") if window == 3 else None), True
    return px.get("years") or 0.0, rets, px.get(f"mdd_{window}"), False


def is_foreign_filer(raw):
    """Foreign private issuers (annual report on 20-F / 40-F, no 10-K data): out of scope for the stock model."""
    return not raw.get("annual") and raw.get("annual_form") in FOREIGN_FORMS


def _cagr(vals):
    """vals oldest->newest, needs >=2 positive endpoints. Returns (cagr_fraction, years) or (None, reason)."""
    if len(vals) < 2:
        return None, "insufficient history"
    start, end, yrs = vals[0], vals[-1], len(vals) - 1
    if start <= 0 or end <= 0:
        return None, "NM (non-positive start/end)"
    return (end / start) ** (1 / yrs) - 1, yrs


def _avg_changes(series):
    """series: list of (end, value or None) oldest->newest. Average of year-over-year
    changes using only CONSECUTIVE valid years. Returns (avg, n_changes)."""
    ch = [series[i][1] - series[i - 1][1] for i in range(1, len(series))
          if series[i][1] is not None and series[i - 1][1] is not None]
    return (sum(ch) / len(ch), len(ch)) if ch else (None, 0)


# ========================================================================
# Part B: compute the 14 metrics for one company
# ========================================================================

def compute_metrics(raw, settings=None, industry_median_beta=None):
    settings = {**DEFAULT_SETTINGS, **(settings or {})}
    co = _Co(raw)
    market = raw.get("market", {})
    px = raw.get("px", {})
    fin = is_financial(raw)
    out = {k: {"value": None, "status": EXCL, "note": ""} for k in KEYS}
    flags = []
    derived = {}

    def setm(key, value, status=OK, note=""):
        if value is not None and not _num(value):
            value, status, note = None, EXCL, note or "non-finite result"
        out[key] = {"value": value, "status": status, "note": note}

    def flag(code, text):
        flags.append({"code": code, "text": text})

    n = co.n
    last = n - 1
    mcap, price = market.get("market_cap"), market.get("price")
    mcap = mcap if _num(mcap) else None

    for e in raw.get("errors", []):
        flag("data_note", e)
    if n == 0:
        for k in KEYS:
            out[k]["note"] = "no SEC 10-K data"
    # ---- common inputs for the latest fiscal year ------------------------
    ebit = co.v(last, "ebit") if n else None
    da = co.v(last, "da") if n else None
    ebitda = ebit + da if (ebit is not None and da is not None) else None
    debt, debt_assumed = co.debt(last) if n else (None, False)
    cash = co.v(last, "cash") if n else None
    equity = co.v(last, "equity") if n else None
    revenue = co.v(last, "revenue") if n else None
    interest_present = n and "interest_expense" in co.A[co.ends[last]] and _num(co.A[co.ends[last]]["interest_expense"])
    interest = abs(co.v(last, "interest_expense")) if interest_present else None
    ev = None
    if mcap is not None and debt is not None and cash is not None:
        ev = mcap + debt - cash + (co.v(last, "preferred") or 0) + (co.v(last, "minority_interest") or 0)
    derived.update({"fiscal_year_end": co.ends[last] if n else None, "ebit": ebit, "da": da, "ebitda": ebitda,
                    "total_debt": debt, "cash": cash, "equity": equity, "revenue": revenue, "interest_expense": interest,
                    "market_cap": mcap, "enterprise_value": ev, "price": price,
                    "tax_rate": co.tax_rate(last) if n else None})
    if debt_assumed:
        flag("debt_assumed_zero", "No debt tag in the 10-K - total debt assumed 0")

    # 1. ROIC ------------------------------------------------------------
    if fin:
        setm("roic", None, EXCL, "bank/insurer - not applicable")
    elif n:
        r, ic, note = co.roic(last)
        derived["invested_capital_avg"] = ic
        if r is None:
            setm("roic", None, EXCL, note)
            if note == "negative invested capital":
                flag("neg_ic", "Negative invested capital - ROIC excluded")
        else:
            setm("roic", r * 100, OK, f"EBIT×(1−{co.tax_rate(last):.1%}) ÷ {note}")
            derived["roic_value"] = r

    # 2. Gross profitability ---------------------------------------------
    if fin:
        setm("gross_profitability", None, EXCL, "bank/insurer - not applicable")
    elif n:
        gp = co.v(last, "gross_profit")
        src = "GrossProfit tag"
        if gp is None and revenue is not None and co.v(last, "cogs") is not None:
            gp, src = revenue - co.v(last, "cogs"), "revenue − cost of revenue"
        ta, used = co.avg(last, "total_assets")
        derived["gross_profit"] = gp
        if gp is None or ta is None or ta <= 0:
            setm("gross_profitability", None, EXCL, "no gross profit / cost-of-revenue data")
        else:
            setm("gross_profitability", gp / ta * 100, OK, f"{src} ÷ {'average' if used else 'end-of-year'} total assets")

    # 3. Earnings yield ----------------------------------------------------
    if fin:
        setm("earnings_yield", None, EXCL, "bank/insurer - not applicable")
    elif ev is None or ebit is None:
        setm("earnings_yield", None, EXCL, "EV or EBIT missing")
    elif ev <= 0:
        setm("earnings_yield", None, EXCL, "EV ≤ 0")
        flag("ev_le0", "Enterprise value ≤ 0 - earnings yield excluded")
    elif ebit <= 0:
        setm("earnings_yield", ebit / ev * 100, ZERO, "EBIT ≤ 0 → scored 0")
    else:
        setm("earnings_yield", ebit / ev * 100, OK, "EBIT ÷ EV")

    # 4. FCF yield ---------------------------------------------------------
    ocf, capex = (co.v(last, "ocf"), co.v(last, "capex")) if n else (None, None)
    derived.update({"ocf": ocf, "capex": capex})
    if ocf is None or capex is None or mcap is None:
        setm("fcf_yield", None, EXCL, "OCF, capex or market cap missing")
    else:
        derived["fcf"] = ocf - abs(capex)
        setm("fcf_yield", (ocf - abs(capex)) / mcap * 100, OK, "(OCF − capex) ÷ market cap")

    # 5. Lynch ratio -------------------------------------------------------
    eps = co.v(last, "eps_diluted") if n else None
    derived["eps"] = eps
    if eps is None or not _num(price):
        setm("lynch", None, EXCL, "diluted EPS or price missing")
    elif eps <= 0:
        setm("lynch", None, ZERO, "EPS ≤ 0 (P/E not meaningful) → scored 0")
    else:
        pe = price / eps
        derived["pe"] = pe
        eps_hist = [co.v(i, "eps_diluted") for i in range(max(0, n - 4), n)]
        if any(v is None for v in eps_hist) or len(eps_hist) < 3:
            # use the longest all-present trailing run
            run = []
            for v in reversed(eps_hist):
                if v is None:
                    break
                run.insert(0, v)
            eps_hist = run
        g, yrs = _cagr(eps_hist) if len(eps_hist) >= 3 else (None, "fewer than 2 years of EPS growth")
        if g is None:
            setm("lynch", None, EXCL, f"growth NM ({yrs})")
        else:
            dy = market.get("dividend_yield")
            dy_note = ""
            if not _num(dy):
                paid = co.v(last, "dividends_paid")
                dy, dy_note = (abs(paid) / mcap, "; dividend yield from SEC dividends ÷ market cap") if (paid and mcap) else (0.0, "; no dividend data → 0")
            derived.update({"eps_growth_pct": g * 100, "div_yield_pct": dy * 100})
            lbl = f"{yrs}-yr EPS CAGR"
            setm("lynch", (g * 100 + dy * 100) / pe, OK, f"({lbl} {g:.1%} + dividend yield {dy:.1%}) ÷ P/E {pe:.1f}{dy_note}")

    # 6. Revenue growth ----------------------------------------------------
    revs = [co.v(i, "revenue") for i in range(max(0, n - 4), n)]
    run = []
    for v in reversed(revs):
        if v is None:
            break
        run.insert(0, v)
    if len(run) < 2:
        setm("revenue_growth", None, EXCL, "fewer than 2 years of revenue")
    else:
        g, yrs = _cagr(run)
        if g is None:
            setm("revenue_growth", None, EXCL, f"growth NM ({yrs})")
        else:
            setm("revenue_growth", g * 100, OK, f"{yrs}-yr revenue CAGR" + ("" if yrs == 3 else " (fewer than 3 years available)"))

    # 7. Net debt / EBITDA -------------------------------------------------
    if ebitda is None or debt is None or cash is None:
        setm("net_debt_ebitda", None, EXCL, "EBIT, D&A, debt or cash missing")
    elif ebitda <= 0:
        setm("net_debt_ebitda", (debt - cash) / ebitda, ZERO, "EBITDA ≤ 0 → scored 0")
    else:
        setm("net_debt_ebitda", (debt - cash) / ebitda, OK, "(total debt − cash) ÷ EBITDA" + (" [net cash]" if debt < cash else ""))

    # 8. Interest coverage -------------------------------------------------
    if ebit is None and not (interest_present and interest == 0):
        setm("interest_coverage", None, EXCL, "EBIT missing")
    elif interest_present and interest == 0:
        setm("interest_coverage", None, HUNDRED, "interest expense = 0 → scored 100")
    elif not interest_present:
        if debt == 0:
            setm("interest_coverage", None, HUNDRED, "no interest and no debt → scored 100")
        elif debt is not None and debt > 0:
            setm("interest_coverage", None, EXCL, "interest data missing")
            flag("interest_missing", "Interest expense missing but debt > 0 - coverage excluded")
        else:
            setm("interest_coverage", None, EXCL, "interest and debt data missing")
    elif ebit <= 0:
        setm("interest_coverage", ebit / interest, ZERO, "EBIT ≤ 0 → scored 0")
    else:
        proxy = str(raw.get("tags", {}).get("interest_expense", "")).startswith("InterestPaid")
        setm("interest_coverage", ebit / interest, OK,
             "EBIT ÷ interest expense" + (" [proxy: cash interest paid - no interest-expense tag]" if proxy else ""))

    # 9. Δ ROIC (percentage points per year) --------------------------------
    roic_series = []
    for i in range(max(0, n - 4), n):
        r, _, _ = co.roic(i)
        roic_series.append((co.ends[i], None if (r is None or fin) else r * 100))
    d, k = _avg_changes(roic_series)
    if fin or d is None:
        setm("delta_roic", None, EXCL, "bank/insurer - ROIC not applicable" if fin else "no two consecutive valid ROIC years")
    else:
        setm("delta_roic", d, OK, f"average of {k} year-over-year ROIC change(s)" + ("" if k == 3 else " (fewer than 3 available)"))

    # 10. Δ operating margin -------------------------------------------------
    om_series = []
    for i in range(max(0, n - 4), n):
        e, r = co.v(i, "ebit"), co.v(i, "revenue")
        om_series.append((co.ends[i], e / r * 100 if (e is not None and r) else None))
    d, k = _avg_changes(om_series)
    if d is None:
        setm("delta_op_margin", None, EXCL, "no two consecutive years of operating margin")
    else:
        setm("delta_op_margin", d, OK, f"average of {k} year-over-year margin change(s)" + ("" if k == 3 else " (fewer than 3 available)"))

    # 11. Sloan accrual ratio -------------------------------------------------
    ni = co.v(last, "net_income") if n else None
    ta_avg, used = co.avg(last, "total_assets") if n else (None, False)
    derived.update({"net_income": ni, "total_assets_avg": ta_avg})
    if ni is None or ocf is None or ta_avg is None or ta_avg <= 0:
        setm("sloan", None, EXCL, "net income, OCF or total assets missing")
    else:
        setm("sloan", (ni - ocf) / ta_avg * 100, OK, f"(net income − OCF) ÷ {'average' if used else 'end-of-year'} total assets")

    # 12-13. Downside deviation + max drawdown over the COMMON history window ----------------
    # Every company is measured over the same `window` years ending on the same date (prepare_industry checks the
    # end date). Less history than the window -> "too new for this window": both metrics excluded, never a shorter window.
    window = settings["window"]
    years, rets, mdd, legacy = _px_window(px, window)
    too_new = years + WINDOW_SLACK_YEARS < window
    if too_new:
        why = (f"only {years:.1f} yrs of prices cached (refresh the industry for {window} yrs)" if legacy
               else f"too new for this window ({years:.1f} yrs < {window})")
        for k in HIST_KEYS:
            setm(k, None, EXCL, why)
        flag("too_new", f"Too new for the {window}-yr window ({years:.1f} yrs of prices) - downside deviation and max drawdown "
                        "excluded; listed after fully scored companies" if not legacy else f"Price history: {why}")
    else:
        n_m = min(len(rets), window * 12) if len(rets) >= window * 12 - 1 else 0  # one month of slack (listing date)
        if n_m:
            setm("downside_dev", downside_deviation(rets[-n_m:], settings["tbill"]), OK,
                 f"{n_m} monthly returns ({window}-yr window), MAR from {settings['tbill']:.2%} T-bill")
        else:
            setm("downside_dev", None, EXCL, "too few monthly returns")
        if mdd is None:
            setm("max_drawdown", None, EXCL, "price history missing")
        else:
            setm("max_drawdown", mdd * 100, OK, f"worst peak-to-trough, last {window} years of daily adjusted closes")
    derived["history"] = {"years_available": years, "window": window, "too_new": too_new,
                          "start": px.get("first_date"), "end": px.get("last_date")}

    # 14. Shareholder yield --------------------------------------------------------
    parts = []
    for i in range(max(0, n - 3), n):
        if co.v(i, "ocf") is None:  # no cash-flow statement for that year
            continue
        div = abs(co.v(i, "dividends_paid") or 0)
        rep = abs(co.v(i, "repurchases") or 0)
        iss = abs(co.v(i, "stock_issued") or 0)
        parts.append(div + rep - iss)
    if not parts or mcap is None:
        setm("shareholder_yield", None, EXCL, "cash-flow statement or market cap missing")
    else:
        setm("shareholder_yield", sum(parts) / len(parts) / mcap * 100, OK,
             f"{len(parts)}-yr avg (dividends + buybacks − issuance) ÷ market cap"
             + ("" if len(parts) == 3 else " (fewer than 3 years)")
             + "; missing components counted as 0")

    # ---- Part C: red flags -----------------------------------------------------------
    rf = _red_flags(co, raw, fin, derived, mcap)
    flags.extend(rf["flags"])
    wacc = _wacc(co, raw, fin, derived, settings, industry_median_beta, mcap, out["roic"])
    if wacc.get("below"):
        flags.append({"code": "roic_below_wacc", "text": f"ROIC {out['roic']['value']:.1f}% is below WACC {wacc['wacc']*100:.1f}%"})
    return {"metrics": out, "flags": flags, "derived": derived, "wacc": wacc,
            "altman": rf["altman"], "piotroski": rf["piotroski"], "is_financial": fin}


# ========================================================================
# Part C helpers: Altman, Piotroski, payout
# ========================================================================

def _red_flags(co, raw, fin, derived, mcap):
    flags = []
    n, last = co.n, co.n - 1
    altman = {"applicable": False, "z": None, "model": None, "flag": False, "note": "n/a"}
    piotroski = {"f": None, "tests": {}, "computable": 0, "flag": False, "note": "n/a"}
    if n == 0:
        return {"flags": flags, "altman": altman, "piotroski": piotroski}

    # ---- Altman ----
    if _skip_altman(raw):
        altman["note"] = "not applicable (bank/insurer, REIT or utility)"
    else:
        ta = co.v(last, "total_assets")
        ca, cl = co.v(last, "current_assets"), co.v(last, "current_liabilities")
        re_, ebit, eq = co.v(last, "retained_earnings"), derived.get("ebit"), derived.get("equity")
        tl = co.v(last, "total_liabilities")
        if tl is None and ta is not None and eq is not None:
            tl = ta - eq - (co.v(last, "minority_interest") or 0)
        sic = raw.get("sic")
        manufacturer = sic is not None and 2000 <= sic <= 3999
        need = [ta, ca, cl, re_, ebit, tl]
        if any(v is None for v in need) or not ta or not tl or tl <= 0:
            altman["note"] = "missing inputs"
        elif manufacturer:
            sales = derived.get("revenue")
            if sales is None or mcap is None:
                altman["note"] = "missing sales or market cap"
            else:
                wc = ca - cl
                z = 1.2 * wc / ta + 1.4 * re_ / ta + 3.3 * ebit / ta + 0.6 * mcap / tl + 1.0 * sales / ta
                altman.update(applicable=True, z=z, model="Z (manufacturer)", flag=z < 1.81, note="distress zone < 1.81")
        else:
            if eq is None:
                altman["note"] = "missing equity"
            else:
                wc = ca - cl
                z = 6.56 * wc / ta + 3.26 * re_ / ta + 6.72 * ebit / ta + 1.05 * eq / tl
                altman.update(applicable=True, z=z, model="Z'' (non-manufacturer)", flag=z < 1.1, note="distress zone < 1.1")
    if altman["flag"]:
        flags.append({"code": "altman", "text": f"Altman {altman['model']} = {altman['z']:.2f} (distress zone)"})

    # ---- Piotroski F-score (this year vs last) ----
    if n >= 2:
        t, p = last, last - 1
        def ratio(a, b):
            return a / b if (a is not None and b not in (None, 0)) else None
        ni_t, ni_p = co.v(t, "net_income"), co.v(p, "net_income")
        ta_t, _ = co.avg(t, "total_assets")
        ta_p, _ = co.avg(p, "total_assets")
        ocf_t = co.v(t, "ocf")
        roa_t, roa_p = ratio(ni_t, ta_t), ratio(ni_p, ta_p)
        lev_t = ratio((co.debt(t)[0]), ta_t)
        lev_p = ratio((co.debt(p)[0]), ta_p)
        cr_t = ratio(co.v(t, "current_assets"), co.v(t, "current_liabilities"))
        cr_p = ratio(co.v(p, "current_assets"), co.v(p, "current_liabilities"))
        sh_t, sh_p = co.v(t, "diluted_shares"), co.v(p, "diluted_shares")
        gm_t = ratio(co.v(t, "gross_profit") if co.v(t, "gross_profit") is not None else (
            co.v(t, "revenue") - co.v(t, "cogs") if co.v(t, "revenue") is not None and co.v(t, "cogs") is not None else None), co.v(t, "revenue"))
        gm_p = ratio(co.v(p, "gross_profit") if co.v(p, "gross_profit") is not None else (
            co.v(p, "revenue") - co.v(p, "cogs") if co.v(p, "revenue") is not None and co.v(p, "cogs") is not None else None), co.v(p, "revenue"))
        at_t, at_p = ratio(co.v(t, "revenue"), ta_t), ratio(co.v(p, "revenue"), ta_p)
        tests = {
            "ROA > 0": None if roa_t is None else roa_t > 0,
            "Operating cash flow > 0": None if ocf_t is None else ocf_t > 0,
            "ROA improved": None if roa_t is None or roa_p is None else roa_t > roa_p,
            "OCF > net income": None if ocf_t is None or ni_t is None else ocf_t > ni_t,
            "Leverage fell": None if lev_t is None or lev_p is None else lev_t < lev_p,
            "Current ratio improved": None if cr_t is None or cr_p is None else cr_t > cr_p,
            "No share dilution": None if sh_t is None or sh_p is None else sh_t <= sh_p,
            "Gross margin improved": None if gm_t is None or gm_p is None else gm_t > gm_p,
            "Asset turnover improved": None if at_t is None or at_p is None else at_t > at_p,
        }
        comp = [v for v in tests.values() if v is not None]
        piotroski.update(tests=tests, computable=len(comp), note=f"{len(comp)} of 9 tests computable")
        if len(comp) >= 7 and not fin:
            f = sum(1 for v in comp if v)
            piotroski.update(f=f, flag=f <= 3)
            if f <= 3:
                flags.append({"code": "piotroski", "text": f"Piotroski F-score {f} (weak fundamentals, ≤ 3)"})

    # ---- Unsafe payout: avg(dividends + buybacks) > avg FCF over 3 years ----
    pay, fcf = [], []
    for i in range(max(0, n - 3), n):
        ocf, cap = co.v(i, "ocf"), co.v(i, "capex")
        if ocf is None or cap is None:
            continue
        pay.append(abs(co.v(i, "dividends_paid") or 0) + abs(co.v(i, "repurchases") or 0))
        fcf.append(ocf - abs(cap))
    if pay and sum(pay) / len(pay) > sum(fcf) / len(fcf) and sum(pay) > 0:
        derived["avg_payout"], derived["avg_fcf"] = sum(pay) / len(pay), sum(fcf) / len(fcf)
        flags.append({"code": "unsafe_payout", "text": f"3-yr avg payouts ${sum(pay)/len(pay)/1e6:,.0f}M exceed avg FCF ${sum(fcf)/len(fcf)/1e6:,.0f}M"})
    return {"flags": flags, "altman": altman, "piotroski": piotroski}


# ========================================================================
# Part F: WACC (display + flag only)
# ========================================================================

def _wacc(co, raw, fin, derived, settings, industry_median_beta, mcap, roic_metric):
    res = {"wacc": None, "note": "NM", "below": False, "spread": None}
    if fin:
        res["note"] = "NM (bank/insurer)"
        return res
    market = raw.get("market", {})
    debt = derived.get("total_debt")
    if mcap is None or debt is None:
        res["note"] = "market cap or debt missing"
        return res
    beta, beta_note = market.get("beta"), "yfinance beta"
    if not _num(beta):
        if industry_median_beta is None:
            res["note"] = "beta missing and no industry median"
            return res
        beta, beta_note = industry_median_beta, "industry median beta (company beta missing)"
    ke = settings["rf"] + beta * settings["erp"]
    tax = derived.get("tax_rate") or 0.21
    kd_note = ""
    if debt > 0:
        d_prev = co.debt(co.n - 2)[0] if co.n >= 2 else None
        avg_debt = (debt + d_prev) / 2 if d_prev is not None else debt
        interest = derived.get("interest_expense")
        if interest is None or avg_debt <= 0:
            kd, kd_note = settings["rf"] + 0.02, "interest missing → assumed risk-free + 2%"
        else:
            kd = interest / avg_debt
    else:
        kd = 0.0
    v = mcap + debt
    w = mcap / v * ke + debt / v * kd * (1 - tax)
    res.update(wacc=w, cost_equity=ke, cost_debt=kd, beta=beta, beta_note=beta_note, debt_weight=debt / v,
               note="; ".join(x for x in [beta_note, kd_note] if x))
    if roic_metric["status"] == OK and roic_metric["value"] is not None:
        res["spread"] = roic_metric["value"] - w * 100
        res["below"] = res["spread"] < 0
    return res


# ========================================================================
# Part D: prepare an industry (pools + percentile scores) - weight-independent
# ========================================================================

def _quantiles(vals):
    a = np.array(vals, dtype=float)
    return {"p5": float(np.percentile(a, 5)), "p10": float(np.percentile(a, 10)), "p25": float(np.percentile(a, 25)),
            "median": float(np.median(a)), "p75": float(np.percentile(a, 75)), "p90": float(np.percentile(a, 90)),
            "p95": float(np.percentile(a, 95)), "n": len(a)}


def prepare_industry(raws, settings=None, all_raws=None):
    """raws: list of raw dicts for the selected industry.
    all_raws: callable returning every cached company's raw dict (for sector /
    all-stock fallback pools); may be None.
    Foreign filers (20-F / 40-F, no 10-K) are out of scope: they are left out of the peer pools and returned
    separately in "foreign" (never ranked).
    Returns {"companies": {ticker: {...}}, "bench": {key: {...}}, "foreign": [...]}."""
    settings = {**DEFAULT_SETTINGS, **(settings or {})}
    foreign = [{"ticker": r["ticker"], "name": r.get("name"), "annual_form": r.get("annual_form"),
                "reason": f"Not scored: foreign filer (files {r.get('annual_form')}, not 10-K) - out of scope for the stock model"}
               for r in raws if is_foreign_filer(r)]
    raws = [r for r in raws if not is_foreign_filer(r)]
    betas = [r["market"]["beta"] for r in raws if _num(r.get("market", {}).get("beta"))]
    med_beta = float(np.median(betas)) if betas else None
    comps = {}
    for r in raws:
        comps[r["ticker"]] = {"raw": r, **compute_metrics(r, settings, med_beta)}

    sectors = [r.get("sector") for r in raws if r.get("sector")]
    sector = max(set(sectors), key=sectors.count) if sectors else None
    memo = {}

    def other_valid(key, level):
        """Valid values from cached companies outside the industry, for fallback pools."""
        if all_raws is None:
            return {}
        if "others" not in memo:
            memo["others"] = [(r, compute_metrics(r, settings, None)) for r in all_raws()
                              if r["ticker"] not in comps and not is_foreign_filer(r)]
        res = {}
        for r, m in memo["others"]:
            if level == "sector" and (sector is None or r.get("sector") != sector):
                continue
            mv = m["metrics"][key]
            if mv["status"] == OK and _num(mv["value"]):
                res[r["ticker"]] = mv["value"]
        return res

    # common END date: prices stopping more than 7 days before the newest company's are not on the same window
    ends = [c["derived"]["history"]["end"] for c in comps.values() if c["derived"]["history"]["end"]]
    newest = max(ends) if ends else None
    for c in comps.values():
        e = c["derived"]["history"]["end"]
        if e and newest and (pd.Timestamp(newest) - pd.Timestamp(e)).days > 7 and not c["derived"]["history"]["too_new"]:
            for k in HIST_KEYS:
                c["metrics"][k] = {"value": None, "status": EXCL, "note": f"stale prices (end {e}, industry ends {newest})"}
            c["flags"].append({"code": "stale_prices", "text": f"Prices end {e}, industry ends {newest} - history metrics excluded"})

    bench, pct = {}, {k: {} for k in KEYS}
    for key in KEYS:
        meta = METRIC_BY_KEY[key]
        own = {t: c["metrics"][key]["value"] for t, c in comps.items()
               if c["metrics"][key]["status"] == OK and _num(c["metrics"][key]["value"])}
        level, pool = "industry", dict(own)
        if len(pool) < MIN_POOL:
            sec_pool = {**other_valid(key, "sector"), **own}
            if len(sec_pool) >= MIN_POOL:
                level, pool = "sector", sec_pool
            else:
                all_pool = {**other_valid(key, "all"), **own}
                level, pool = "all", all_pool
        tick = list(pool)
        scores = percentile_scores([pool[t] for t in tick], meta["direction"] == "higher")
        for t, s in zip(tick, scores):
            pct[key][t] = s
        q = _quantiles(list(pool.values())) if pool else None
        spread, spread_level = None, level
        if q:
            spread = q["p75"] - q["p25"]
            if not spread:  # zero -> sector spread, then all-stocks spread
                for lv in ("sector", "all"):
                    alt = {**other_valid(key, lv), **own}
                    if len(alt) >= 2:
                        qa = _quantiles(list(alt.values()))
                        if qa["p75"] - qa["p25"] > 0:
                            spread, spread_level = qa["p75"] - qa["p25"], lv
                            break
        bench[key] = {"level": level, "pool_n": len(pool), "industry_n": len(own), "q": q,
                      "vals": sorted(pool.values()),  # the pool itself, so Laura can take any percentile of it
                      "spread": spread, "spread_level": spread_level}
    for t, c in comps.items():
        for key in KEYS:
            if bench[key]["level"] != "industry" and c["metrics"][key]["status"] == OK:
                c["flags"].append({"code": f"sector_fallback:{key}",
                                   "text": f"{bench[key]['level']} fallback pool for {METRIC_BY_KEY[key]['label']} (< {MIN_POOL} in industry)"})
        for key in KEYS:
            c["metrics"][key]["pct"] = pct[key].get(t) if c["metrics"][key]["status"] == OK else None
    return {"companies": comps, "bench": bench, "settings": settings, "sector": sector, "foreign": foreign}


# ========================================================================
# Parts E + G: weights, Laura limits, penalty strength, hard limits -> final ranked list
# ========================================================================

def default_limits(bench):
    """'Industry average' button: every limit = industry p75 (lower-is-better) / p25 (higher-is-better)."""
    return laura.default_limits(bench, METRICS)


def laura_limits(bench):
    """Laura Gao's standards (laura.LAURA_TIERS): one per-industry percentile limit per metric - the DEFAULT."""
    return laura.laura_limits(bench, "stock", METRICS)


def finalize(prep, weights=None, limits=None, penalty=DEFAULT_PENALTY, hard_limits=None, low_data_threshold=0.30):
    """limits: {metric: Laura limit} overriding the defaults (ceiling for lower-is-better, floor for higher-is-better).
    penalty: k in Overall = max(0, Composite - k x (100 - Fit)). Limits never change Composite."""
    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    laura_lim = laura_limits(prep["bench"])
    limits = {**laura_lim, **(limits or {})}  # Laura is the default
    hard_limits = hard_limits or {}
    total_w = sum(weights.values())
    results = []
    for t, c in prep["companies"].items():
        pct_scores, fit_scores, rows = {}, {}, {}
        excluded_w = hist_excl_w = 0.0
        for key in KEYS:
            m = c["metrics"][key]
            meta, w = METRIC_BY_KEY[key], weights.get(key, 0)
            b = prep["bench"][key]
            lim = limits.get(key)
            dist = None
            if m["status"] == EXCL:
                p = f = None
                excluded_w += w
                if key in HIST_KEYS:
                    hist_excl_w += w
            elif m["status"] == ZERO:
                p = f = 0.0
            elif m["status"] == HUNDRED:
                p = f = 100.0
            else:
                p = m.get("pct")
                f, dist = metric_fit(m["value"], lim, b["spread"], meta["direction"])
            pct_scores[key], fit_scores[key] = p, f
            rows[key] = {**m, "pct": p, "fit": f, "distance": dist, "weight": w, "limit": lim, "laura_limit": laura_lim.get(key),
                         "direction": meta["direction"], "units": meta["units"], "label": meta["label"]}
        # a metric that couldn't be percentile-ranked or fitted (None score, not forced) is excluded too
        comp_scores = {k: pct_scores[k] for k in KEYS}
        fit_for_avg = {k: fit_scores[k] if pct_scores[k] is not None else None for k in KEYS}
        for k in KEYS:
            if rows[k]["status"] == OK and (pct_scores[k] is None or fit_scores[k] is None):
                comp_scores[k] = fit_for_avg[k] = None
                rows[k]["status_effective"] = EXCL
                rows[k]["pct"] = rows[k]["fit"] = None
                excluded_w += weights.get(k, 0)
                if k in HIST_KEYS:
                    hist_excl_w += weights.get(k, 0)
        composite = composite_score(comp_scores, weights)
        fit = composite_score(fit_for_avg, weights)
        den = sum(weights.get(k, 0) for k in KEYS if comp_scores[k] is not None)
        for k in KEYS:
            rows[k]["contribution"] = (weights.get(k, 0) * comp_scores[k] / den) if (comp_scores[k] is not None and den) else None
        overall = overall_score(composite, fit, penalty)
        lost = (composite - overall) if (composite is not None and overall is not None) else None
        why = penalty_breakdown(rows, weights, den, penalty, lost)
        hist = c["derived"].get("history") or {}
        too_new = bool(hist.get("too_new"))
        excl_share = excluded_w / total_w if total_w else 0
        other_share = (excluded_w - hist_excl_w) / total_w if total_w else 0
        low_data = (other_share if too_new else excl_share) > low_data_threshold
        missing = [(METRIC_BY_KEY[k]["label"], rows[k]["note"] or "excluded") for k in KEYS
                   if weights.get(k, 0) > 0 and (rows[k]["status"] == EXCL or rows[k].get("status_effective") == EXCL)]
        not_scored = not_scored_reason(c["raw"])
        fails = []
        for k, on in hard_limits.items():
            if not on:
                continue
            r = rows[k]
            if r["status"] in (ZERO, EXCL) or r.get("status_effective") == EXCL:  # forced-worst or unknown can't be shown to meet the limit
                fails.append(k)
            elif r["status"] == OK and (r["distance"] or 0) > 0:
                fails.append(k)
        results.append({
            "ticker": t, "name": c["raw"].get("name"), "fye": c["derived"].get("fiscal_year_end"),
            "sector": c["raw"].get("sector"), "metrics": rows, "composite": composite, "fit": fit, "overall": overall,
            "penalty": lost, "why_not": why, "why_not_higher": why_text(why, rows, {k: METRIC_BY_KEY[k]["label"] for k in KEYS}),
            "n_inside": sum(1 for k in KEYS if rows[k]["status"] == HUNDRED or (rows[k]["distance"] == 0 and fit_for_avg[k] is not None)),
            "n_total": len(KEYS),
            "excluded_weight_share": excl_share, "coverage": 1 - excl_share,
            "low_data": low_data, "too_new": too_new,
            "too_new_reason": (f"Too new for this window: {hist['years_available']:.1f} yrs of prices < {hist['window']}-yr window "
                               "(downside deviation, max drawdown not scored)") if too_new else None,
            "history": hist, "flags": c["flags"], "wacc": c["wacc"],
            "missing": missing, "not_scored": not_scored, "annual_form": c["raw"].get("annual_form"),
            "low_data_reason": low_data_reason(excl_share, missing, not_scored) if (low_data or not_scored) else None,
            "altman": c["altman"], "piotroski": c["piotroski"], "derived": c["derived"],
            "hard_limit_failed": bool(fails), "hard_limit_failures": fails,
        })
    return {"results": rank(results), "weights": weights, "limits": limits, "penalty": penalty, "total_weight": total_w,
            "foreign": prep.get("foreign", [])}


def not_scored_reason(raw):
    """Companies with NO SEC annual (10-K) data get no rank at all, with the reason, instead of a silent 'last place'.
    raw['annual_form'] comes from the SEC submissions (stock_data.annual_form). Foreign filers (20-F / 40-F) never
    get here: prepare_industry takes them out of scope."""
    if raw.get("annual"):
        return None
    form = raw.get("annual_form")
    if form in FOREIGN_FORMS:
        return f"Not scored: files {form}, not 10-K"
    if form is None and raw.get("cik"):
        return "Not scored: no annual report (10-K) in SEC data yet (new listing / shell company?)"
    if not raw.get("cik"):
        return "Not scored: not found in SEC EDGAR (no CIK)"
    return "Not scored: no usable 10-K data in SEC XBRL"


def low_data_reason(share, missing, not_scored=None):
    if not_scored:
        return not_scored
    return f"{share:.0%} of the weight is excluded (> 30%). Missing: " + "; ".join(f"{lbl} ({why})" for lbl, why in missing)


def rank(results):
    """Part G3: fully scored first, then 'too new for this window', then low data, then not scored (no 10-K data,
    UNRANKED: rank None). Within each: Overall desc; then Composite desc, fewer flags, less excluded weight, ticker A-Z."""
    def key(r):
        return (bool(r.get("not_scored")), r["low_data"], bool(r.get("too_new")), -(r["overall"] if r["overall"] is not None else -1),
                -(r["composite"] if r["composite"] is not None else -1),
                len(r["flags"]), r["excluded_weight_share"], r["ticker"])
    out = sorted(results, key=key)
    i = 0
    for r in out:
        if r.get("not_scored"):
            r["rank"] = None
        else:
            i += 1
            r["rank"] = i
    return out


def export_rows(final, settings, industry):
    """Part H4: one row per company with every raw value, score, Fit, Laura limit, flag and setting."""
    stamp = datetime.now().isoformat(timespec="seconds")
    rows = []
    for r in final["results"]:
        h = r.get("history") or {}
        row = {"timestamp": stamp, "industry": industry, "rank": r["rank"], "ticker": r["ticker"], "name": r["name"],
               "fiscal_year_end": r["fye"], "overall": r["overall"], "fit": r["fit"], "composite": r["composite"],
               "laura_penalty": r["penalty"], "why_not_higher": r["why_not_higher"],
               "metrics_meeting_limit": f"{r['n_inside']}/{r['n_total']}", "data_coverage": r["coverage"],
               "too_new_for_window": r.get("too_new"), "low_data": r["low_data"], "not_scored": r["not_scored"],
               "low_data_reason": r["low_data_reason"] or r.get("too_new_reason"),
               "annual_form": r["annual_form"], "red_flags": " | ".join(f["text"] for f in r["flags"]),
               "history_years_available": h.get("years_available"), "history_window": h.get("window"),
               "history_start": h.get("start"), "history_end": h.get("end"),
               "wacc": r["wacc"].get("wacc"), "roic_minus_wacc_pp": r["wacc"].get("spread"),
               "altman_z": r["altman"].get("z"), "piotroski_f": r["piotroski"].get("f")}
        for k, v in r["derived"].items():
            if k != "history":
                row[f"input_{k}"] = v
        for k in KEYS:
            m = r["metrics"][k]
            row.update({f"{k}__value": m["value"], f"{k}__status": m["status"], f"{k}__percentile_score": m["pct"],
                        f"{k}__laura_limit": m["laura_limit"], f"{k}__limit_used": m["limit"], f"{k}__distance_past_limit": m["distance"],
                        f"{k}__fit": m["fit"], f"{k}__weight": m["weight"], f"{k}__note": m["note"]})
        row.update({"setting_laura_penalty_k": final["penalty"], "setting_history_window": settings.get("window"),
                    "setting_tbill": settings["tbill"], "setting_rf": settings["rf"], "setting_erp": settings["erp"]})
        rows.append(row)
    return pd.DataFrame(rows)


# ========================================================================
# Part I: self-tests
# ========================================================================

def _synthetic_raw(i, n_fy=5, ebitda_neg=False, no_interest_no_debt=False, loss=False, short_hist=False, years=10.0):
    """Deterministic fake company i with plausible, fully-populated data."""
    rng = np.random.default_rng(i)
    ends = [f"{2021 + j}-12-31" for j in range(n_fy)]
    annual = {}
    scale = 1e9 * (1 + i / 10)
    for j, e in enumerate(ends):
        rev = scale * (1 + 0.05 * j + 0.02 * rng.random())
        ebit = rev * (0.10 + 0.01 * j + 0.05 * rng.random())
        if loss and j == n_fy - 1:
            ebit = -abs(ebit)
        if ebitda_neg and j == n_fy - 1:
            ebit = -rev * 0.2
        a = {"revenue": rev, "cogs": rev * 0.6, "gross_profit": rev * 0.4, "ebit": ebit, "da": rev * 0.03,
             "tax_expense": ebit * 0.2, "pretax_income": ebit, "net_income": ebit * 0.75, "ocf": ebit * 0.9 + rev * 0.03,
             "capex": rev * 0.04, "dividends_paid": rev * 0.01, "repurchases": rev * 0.01, "eps_diluted": (ebit * 0.75) / 1e9 + 0.5,
             "cash": rev * 0.1, "total_assets": rev * 1.2, "total_liabilities": rev * 0.6, "equity": rev * 0.6,
             "current_assets": rev * 0.4, "current_liabilities": rev * 0.2, "retained_earnings": rev * 0.3,
             "total_debt": 0.0 if no_interest_no_debt else rev * 0.25, "diluted_shares": 1e9}
        if not no_interest_no_debt:
            a["interest_expense"] = rev * 0.01
        else:
            a["interest_expense"] = 0.0
        annual[e] = a
    years = 1.5 if short_hist else years
    months = min(120, int(round(years * 12)))
    m3 = float(0.1 + 0.3 * rng.random())
    px = {"monthly_returns": list(rng.normal(0.008, 0.05, months)), "years": years, "months": years * 12,
          "mdd_3": m3, "mdd_5": m3 + 0.05, "mdd_10": m3 + 0.15, "first_date": None, "last_date": "2026-10-14"}
    return {"ticker": f"T{i:02d}", "name": f"Test Co {i}", "sector": "Technology", "industry": "Test Industry",
            "yf_industry": "Test", "sic": 3674, "fy_ends": ends, "annual": annual,
            "market": {"price": 50 + i, "market_cap": scale * 4, "beta": 1.0 + i / 20, "dividend_yield": 0.01},
            "px": px}


def penalty_sensitivity(ranked_by_k, n=10):
    """ranked_by_k: {k: ranked results (already filtered like the page's table)}. Top n by rank at each k.
    -> {"rows": [{"rank": 1, k1: "TICK (Overall)", ...}], "top": {k: [tickers]},
        "changes": [(k_a, k_b, how many of k_a's top n are not in k_b's top n), ...] for each neighbouring pair and first vs last}."""
    ks = sorted(ranked_by_k)
    top = {k: [r for r in ranked_by_k[k] if r.get("rank") is not None][:n] for k in ks}
    rows = [{"rank": i + 1, **{k: (f"{top[k][i]['ticker']} ({top[k][i]['overall']:.1f})" if i < len(top[k]) else "") for k in ks}}
            for i in range(max((len(v) for v in top.values()), default=0))]
    names = {k: [r["ticker"] for r in v] for k, v in top.items()}
    pairs = list(zip(ks, ks[1:])) + ([(ks[0], ks[-1])] if len(ks) > 2 else [])
    return {"rows": rows, "top": names, "changes": [(a, b, len(set(names[a]) - set(names[b]))) for a, b in pairs]}


def run_self_tests():
    results = []

    def check(name, ok, detail=""):
        results.append({"test": name, "passed": bool(ok), "detail": detail})

    close = lambda a, b, tol=1e-9: all(abs(x - y) <= tol for x, y in zip(a, b)) and len(a) == len(b)

    # 1-4 percentiles
    check("1. Percentile, higher-is-better [1..5] → [0,25,50,75,100]",
          close(percentile_scores([1, 2, 3, 4, 5], True), [0, 25, 50, 75, 100]), str(percentile_scores([1, 2, 3, 4, 5], True)))
    check("2. Percentile, lower-is-better [1..5] → [100,75,50,25,0]",
          close(percentile_scores([1, 2, 3, 4, 5], False), [100, 75, 50, 25, 0]), str(percentile_scores([1, 2, 3, 4, 5], False)))
    check("3. Ties [1,2,2,3] → [0,50,50,100]", close(percentile_scores([1, 2, 2, 3], True), [0, 50, 50, 100]),
          str(percentile_scores([1, 2, 2, 3], True)))
    check("4. n = 1 → 50", percentile_scores([7], True) == [50.0], str(percentile_scores([7], True)))

    # 5 forced scores through the real pipeline
    r = compute_metrics(_synthetic_raw(1, ebitda_neg=True))["metrics"]
    check("5a. EBITDA ≤ 0 → net debt/EBITDA status zero", r["net_debt_ebitda"]["status"] == ZERO, r["net_debt_ebitda"]["note"])
    r = compute_metrics(_synthetic_raw(2, no_interest_no_debt=True))["metrics"]
    check("5b. Zero interest + zero debt → interest coverage scored 100", r["interest_coverage"]["status"] == HUNDRED,
          r["interest_coverage"]["note"])
    raw = _synthetic_raw(3)
    for e in raw["annual"]:
        pass
    last = sorted(raw["annual"])[-1]
    raw["annual"][last].update({"eps_diluted": -0.5, "ebit": -1e8})
    r = compute_metrics(raw)["metrics"]
    check("5c. Loss-making → Lynch zero and earnings yield zero",
          r["lynch"]["status"] == ZERO and r["earnings_yield"]["status"] == ZERO, f"{r['lynch']['status']}/{r['earnings_yield']['status']}")

    # 6 exclusion rescaling
    w = dict(DEFAULT_WEIGHTS)
    scores = {k: 10.0 + 6.0 * i for i, k in enumerate(KEYS)}  # varied, so the weighting really matters
    scores["roic"] = None  # ROIC (weight 8) excluded
    expected = sum(w[k] * scores[k] for k in KEYS if k != "roic") / (sum(w.values()) - w["roic"])
    check("6. Missing metric → Σ(other w × score) ÷ Σ(other w)", abs(composite_score(scores, w) - expected) < 1e-9,
          f"{composite_score(scores, w):.4f} vs {expected:.4f}")

    # 7 simple (one-sided) Fit: limit 10, spread 4
    fl = [metric_fit(v, 10, 4, "lower")[0] for v in (2, 10, 14, 18, 30)]
    fh = [metric_fit(v, 10, 4, "higher")[0] for v in (30, 10, 6, 2, -10)]
    check("7a. Simple Fit lower-is-better (ceiling 10, spread 4): good side 2 / at limit 10 → 100; one spread past (14) → 50; two (18) → 0; beyond → 0",
          close(fl, [100, 100, 50, 0, 0]), str(fl))
    check("7b. Simple Fit higher-is-better (floor 10, spread 4): good side 30 / 10 → 100; one spread past (6) → 50; two (2) → 0",
          close(fh, [100, 100, 50, 0, 0]), str(fh))

    # 8 / 9 / 13 / 14 full pipeline on a synthetic industry of 12 companies
    raws = [_synthetic_raw(i) for i in range(12)]
    prep = prepare_industry(raws)
    base = finalize(prep)
    bt = {x["ticker"]: x for x in base["results"]}
    moved = finalize(prep, limits={"roic": 1000.0, "sloan": -500.0})
    mt = {x["ticker"]: x for x in moved["results"]}
    same_comp = all(abs(bt[t]["composite"] - mt[t]["composite"]) < 1e-12 for t in bt)
    diff_fit = any(abs(bt[t]["fit"] - mt[t]["fit"]) > 1e-6 for t in bt)
    check("8. Moving a Laura limit changes Fit but not Composite", same_comp and diff_fit, f"composite same={same_comp}, fit changed={diff_fit}")
    w2 = dict(DEFAULT_WEIGHTS, roic=0, shareholder_yield=20)
    wt = {x["ticker"]: x for x in finalize(prep, weights=w2)["results"]}
    wchg = any(abs(bt[t]["composite"] - wt[t]["composite"]) > 1e-6 for t in bt) and any(abs(bt[t]["fit"] - wt[t]["fit"]) > 1e-6 for t in bt)
    w3 = {k: v * 3 for k, v in DEFAULT_WEIGHTS.items()}  # total 300, not 100
    t3 = {x["ticker"]: x for x in finalize(prep, weights=w3)["results"]}
    scale_inv = all(abs(bt[t]["composite"] - t3[t]["composite"]) < 1e-9 for t in bt)
    check("9. Weight change moves Composite & Fit; normalization works when total ≠ 100", wchg and scale_inv,
          f"changed={wchg}, total=300 gives identical composite={scale_inv}")

    # 10 downside deviation hand-check: 22×(+2%), 2×(−3%), T-bill 0 → MAR 0 → DD = 3.0000%
    dd = downside_deviation([0.02] * 22 + [-0.03] * 2, 0.0)
    check("10. Downside deviation hand-computed 3.0000%", abs(dd - 3.0) < 0.0001, f"{dd:.6f}% (hand: sqrt(2×0.03²/24)×√12 = 3%)")

    # 11 max drawdown
    mdd = max_drawdown([100, 120, 90, 130, 104])
    check("11. Max drawdown [100,120,90,130,104] = 25%", abs(mdd - 0.25) < 1e-12, f"{mdd:.4%}")

    # 12 tie-breakers
    a = {"ticker": "B", "overall": 70, "composite": 60, "flags": [1], "excluded_weight_share": 0.1, "low_data": False}
    b = {"ticker": "A", "overall": 70, "composite": 65, "flags": [1, 2], "excluded_weight_share": 0.1, "low_data": False}
    c = {"ticker": "C", "overall": 70, "composite": 65, "flags": [1], "excluded_weight_share": 0.1, "low_data": False}
    order = [x["ticker"] for x in rank([a, b, c])]
    check("12. Equal Overall → higher Composite, then fewer flags", order == ["C", "A", "B"], str(order))

    # 13 low data always last
    ld = {"ticker": "LD", "overall": 99, "composite": 99, "flags": [], "excluded_weight_share": 0.5, "low_data": True}
    ok_ = {"ticker": "OK", "overall": 10, "composite": 10, "flags": [], "excluded_weight_share": 0.0, "low_data": False}
    check("13. Low-data company ranks below all others", [x["ticker"] for x in rank([ld, ok_])] == ["OK", "LD"])

    # 14 sector fallback
    small = [_synthetic_raw(i) for i in range(5)]
    others = [_synthetic_raw(i) for i in range(20, 32)]
    prep2 = prepare_industry(small, all_raws=lambda: others)
    lv = prep2["bench"]["roic"]["level"]
    has_flag = any(f["code"] == "sector_fallback:roic" for f in prep2["companies"]["T00"]["flags"])
    check("14. Pool < 10 → sector fallback with flag", lv == "sector" and has_flag, f"level={lv}, flag shown={has_flag}")

    # 15-17 Laura standards (laura.py): one per-industry percentile limit, spread untouched
    prep_b = prepare_industry([_synthetic_raw(i) for i in range(40, 52)])
    la, lb = laura_limits(prep["bench"]), laura_limits(prep_b["bench"])
    med_a = float(np.median(prep["bench"]["max_drawdown"]["vals"]))
    med_b = float(np.median(prep_b["bench"]["max_drawdown"]["vals"]))
    check("15. Laura max-drawdown ceiling = that industry's median, and differs between two industries",
          abs(la["max_drawdown"] - med_a) < 1e-9 and abs(lb["max_drawdown"] - med_b) < 1e-9 and abs(med_a - med_b) > 1e-6,
          f"industry A {la['max_drawdown']:.3f} (median {med_a:.3f}) vs industry B {lb['max_drawdown']:.3f} (median {med_b:.3f})")
    bq = lambda k: prep["bench"][k]
    pq = lambda k, p: float(np.percentile(bq(k)["vals"], p))
    avg_l = default_limits(prep["bench"])
    ok16 = (abs(la["interest_coverage"] - bq("interest_coverage")["q"]["median"]) < 1e-9           # higher-better risk: floor = p50
            and abs(la["roic"] - pq("roic", 40)) < 1e-9                                              # consistency: floor = p40
            and abs(la["revenue_growth"] - pq("revenue_growth", 25)) < 1e-9                         # moderate higher-better: p25
            and abs(avg_l["max_drawdown"] - pq("max_drawdown", 75)) < 1e-9 and abs(avg_l["roic"] - pq("roic", 25)) < 1e-9  # Industry average
            and all(abs(bq(k)["spread"] - (bq(k)["q"]["p75"] - bq(k)["q"]["p25"])) < 1e-12 for k in KEYS if bq(k)["q"] and bq(k)["q"]["p75"] > bq(k)["q"]["p25"])
            and all(base["results"][0]["metrics"][k]["limit"] == la[k] for k in la))                 # engine default = Laura
    t_mid = next((t for t, c in prep["companies"].items() if c["metrics"]["max_drawdown"]["status"] == OK
                  and med_a < c["metrics"]["max_drawdown"]["value"] <= bq("max_drawdown")["q"]["p75"]), None)
    avg = {x["ticker"]: x for x in finalize(prep, limits=avg_l)["results"]}
    stricter = t_mid is not None and avg[t_mid]["metrics"]["max_drawdown"]["fit"] == 100 and bt[t_mid]["metrics"]["max_drawdown"]["fit"] < 100
    check("16. Laura limits: risk = p50, consistency = p40, moderate = p25 floor / p75 ceiling; Industry average = p75 / p25; "
          "spread unchanged; Laura is the engine default and is stricter",
          ok16 and stricter, f"tiers/spread/default ok={ok16}, stricter on {t_mid}={stricter}")
    one = laura.laura_metric({"q": {"p5": 2.0, "p25": 2.0, "median": 2.0, "p75": 2.0, "p95": 2.0, "n": 3}, "vals": [2, 2, 2]}, "lower", ("risk", 50))
    check("17. Single-value pool → limit = that value (one number, never a range)", one["limit"] == 2.0 and one["side"] == "ceiling", str(one))
    # 18-19 foreign filer out of scope + low-data reason
    fr = _synthetic_raw(5)
    fr.update({"ticker": "FOR", "annual": {}, "fy_ends": [], "annual_form": "40-F", "cik": "0000000001"})
    nr = _synthetic_raw(6)
    nr["ticker"] = "NOE"
    for e in nr["annual"]:
        nr["annual"][e].pop("ebit")  # a 10-K filer with no operating-income line (like NEM)
    pf = prepare_industry([_synthetic_raw(i) for i in range(10)] + [fr, nr])
    ff = finalize(pf)
    out = ff["results"]
    no = next(x for x in out if x["ticker"] == "NOE")
    check("18. Foreign filer (40-F, no 10-K) → out of scope: not ranked, not in any peer pool, listed in 'foreign' with the reason",
          all(x["ticker"] != "FOR" for x in out) and [f["ticker"] for f in ff["foreign"]] == ["FOR"] and "foreign filer" in ff["foreign"][0]["reason"]
          and "FOR" not in pf["companies"] and pf["bench"]["max_drawdown"]["pool_n"] == 11 and all(x["rank"] is not None for x in out),
          ff["foreign"][0]["reason"] if ff["foreign"] else "no foreign list")
    labels = [m for m, _ in no["missing"]]
    check("19. Low data names the missing metrics and why (EBIT missing → ROIC, earnings yield, interest coverage...)",
          no["low_data"] and "ROIC" in labels and "Interest coverage" in labels and "EBIT missing" in no["low_data_reason"]
          and no["rank"] is not None, (no["low_data_reason"] or "")[:140])
    # 20-21 Overall = Composite - k x (100 - Fit)
    lim_all = {k: (1e9 if METRIC_BY_KEY[k]["direction"] == "lower" else -1e9) for k in KEYS}  # every company on the good side
    f100 = finalize(prep, limits=lim_all, penalty=0.4)["results"]
    ok20 = all(abs(x["fit"] - 100) < 1e-9 and abs(x["overall"] - x["composite"]) < 1e-9 and abs(x["penalty"]) < 1e-9 for x in f100)
    pen = [x for x in base["results"] if x["fit"] < 100]
    ok20b = all(abs(x["overall"] - max(0.0, x["composite"] - 0.4 * (100 - x["fit"]))) < 1e-9 for x in base["results"]) and pen
    check("20. k = 0.4: a company with Fit 100 has Overall = Composite; otherwise Overall = max(0, Composite − 0.4 × (100 − Fit))",
          ok20 and ok20b, f"{len(f100)} at Fit 100; {len(pen)} with Fit < 100")
    k0 = finalize(prep, penalty=0.0)["results"]
    by_comp = sorted(k0, key=lambda x: (-x["composite"], len(x["flags"]), x["excluded_weight_share"], x["ticker"]))
    w0 = next(x for x in base["results"] if x["why_not"])
    check("21. k = 0 → ranking equals the Composite ranking; why-not-higher points add up to the Laura penalty",
          [x["ticker"] for x in k0] == [x["ticker"] for x in by_comp] and all(x["overall"] == x["composite"] for x in k0)
          and abs(sum(v for _, v in w0["why_not"]) - w0["penalty"]) < 1e-9,
          f"{[x['ticker'] for x in k0][:5]}…; {w0['ticker']}: {w0['why_not_higher'][:80]}")
    # 22 common history window 3 / 5 / 10
    four = _synthetic_raw(70, years=4.0)
    s3, s5 = compute_metrics(four, {"window": 3}), compute_metrics(four, {"window": 5})
    runs = {w: finalize(prepare_industry([_synthetic_raw(i) for i in range(12)], {"window": w}))["results"] for w in WINDOWS}
    ok22 = (all(s3["metrics"][k]["status"] == OK for k in HIST_KEYS) and abs(s3["metrics"]["max_drawdown"]["value"] - four["px"]["mdd_3"] * 100) < 1e-9
            and not s3["derived"]["history"]["too_new"] and s5["derived"]["history"]["too_new"]
            and all(s5["metrics"][k]["status"] == EXCL and "too new" in s5["metrics"][k]["note"] for k in HIST_KEYS)
            and all(len(v) == 12 and all(x["overall"] is not None for x in v) for v in runs.values())
            and abs(runs[10][0]["metrics"]["max_drawdown"]["value"] - next(r for r in [_synthetic_raw(i) for i in range(12)]
                    if r["ticker"] == runs[10][0]["ticker"])["px"]["mdd_10"] * 100) < 1e-9 and DEFAULT_SETTINGS["window"] == 5)
    check("22. Window 3 / 5 / 10 all run; a stock with 4 yrs of prices is scored at 3 yrs and 'too new' at 5", ok22,
          f"3-yr statuses {[s3['metrics'][k]['status'] for k in HIST_KEYS]}, 5-yr {[s5['metrics'][k]['status'] for k in HIST_KEYS]}")
    # 23 too-new stock listed after every fully scored one
    pt = finalize(prepare_industry([_synthetic_raw(i) for i in range(12)] + [_synthetic_raw(71, years=2.0)]))["results"]
    pos = [x["ticker"] for x in pt].index("T71")
    check("23. Too-new stock is listed after every fully scored stock (not low data)", pt[pos]["too_new"] and not pt[pos]["low_data"] and pos == len(pt) - 1,
          f"position {pos + 1} of {len(pt)}")
    # 24 Laura default weights: total 100, the stock model's own six category totals (growth driver)
    ct = category_totals(METRICS)
    check(f"24. Stock default weights total 100; every metric has a category; category totals = {category_text(sep=' / ')}",
          sum(DEFAULT_WEIGHTS.values()) == 100 and all(m.get("category") in CATEGORIES for m in METRICS) and ct == LAURA_CATEGORY_TOTALS,
          f"total {sum(DEFAULT_WEIGHTS.values())}; {ct}")
    # 25 penalty sensitivity: top 10 at k = 0.2 / 0.4 / 0.6, counts of names that leave the top 10
    pk = prepare_industry([_synthetic_raw(i) for i in range(14)])
    by_k = {k: finalize(pk, penalty=k)["results"] for k in SENSITIVITY_KS}
    sens = penalty_sensitivity(by_k)
    exp_top = {k: [r["ticker"] for r in v if r["rank"] is not None][:10] for k, v in by_k.items()}
    exp_ch = [(a, b, len(set(exp_top[a]) - set(exp_top[b]))) for a, b in ((0.2, 0.4), (0.4, 0.6), (0.2, 0.6))]
    mid = {r["ticker"]: r for r in by_k[0.4]}
    check("25. Penalty sensitivity: top 10 at k = 0.2 / 0.4 / 0.6 match finalize at each k; change counts = names leaving the top 10",
          sens["top"] == exp_top and sens["changes"] == exp_ch and len(sens["rows"]) == 10
          and all(sens["rows"][i][0.4].startswith(t) and f"({mid[t]['overall']:.1f})" in sens["rows"][i][0.4] for i, t in enumerate(exp_top[0.4])),
          f"changes {sens['changes']}")
    return results
