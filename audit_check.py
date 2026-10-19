"""
Independent math & logic audit of the Wharton screener (read-only). Run:

    ./venv/bin/python3 audit_check.py              # writes results/audit_check.json and prints the tables used in AUDIT.md
    ./venv/bin/python3 audit_check.py --net        # also re-downloads daily prices for the worked examples (yfinance)

Everything below is RE-IMPLEMENTED from the raw databases (stock_raw.db, etf_raw.db, holding_scores.db,
results/etf_universe.csv) and from the written rules in METHODOLOGY.md / laura.py: metric formulas, peer pools,
fallback pools, average-rank percentiles, Laura / Industry-average limits, Fit, Composite, Overall, rank order,
holdings-quality trust and the k-sensitivity table. The app's modules (scoring2, etf_scoring, laura) are imported
ONLY to get the app's own answers for comparison (and the constants we are told to check, e.g. LAURA_TIERS).
Nothing here changes code, weights, limits or data.
"""

import json
import math
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import date

import numpy as np
import pandas as pd

# ----------------------------------------------------------------------------- the written rules (METHODOLOGY.md)
STOCK_W = {"max_drawdown": 12, "downside_dev": 10, "earnings_yield": 6, "fcf_yield": 6, "lynch": 6, "roic": 8, "sloan": 7,
           "gross_profitability": 5, "revenue_growth": 8, "delta_roic": 7, "delta_op_margin": 5, "net_debt_ebitda": 8,
           "interest_coverage": 7, "shareholder_yield": 5}
STOCK_DIR = {"max_drawdown": "lower", "downside_dev": "lower", "sloan": "lower", "net_debt_ebitda": "lower"}
STOCK_CAT = {"max_drawdown": "Risk", "downside_dev": "Risk", "earnings_yield": "Cost", "fcf_yield": "Cost", "lynch": "Cost",
             "roic": "Quality", "sloan": "Quality", "gross_profitability": "Quality", "revenue_growth": "Growth",
             "delta_roic": "Growth", "delta_op_margin": "Growth", "net_debt_ebitda": "Safety", "interest_coverage": "Safety",
             "shareholder_yield": "Income"}
ETF_W = {"max_drawdown": 18, "downside_dev": 12, "expense_ratio": 18, "holdings_quality": 8, "top10_weight": 12, "n_holdings": 0,
         "long_run_return": 10, "aum": 12, "dollar_volume": 5, "dividend_yield": 5}
ETF_DIR = {"max_drawdown": "lower", "downside_dev": "lower", "expense_ratio": "lower", "top10_weight": "lower"}
ETF_CAT = {"max_drawdown": "Risk", "downside_dev": "Risk", "expense_ratio": "Cost", "holdings_quality": "Quality", "top10_weight": "Quality",
           "n_holdings": "Quality", "long_run_return": "Growth", "aum": "Safety", "dollar_volume": "Safety", "dividend_yield": "Income"}
DOC_TOTALS = {"stock": {"Risk": 22, "Cost": 18, "Quality": 20, "Growth": 20, "Safety": 15, "Income": 5},
              "etf": {"Risk": 30, "Cost": 18, "Quality": 20, "Growth": 10, "Safety": 17, "Income": 5}}
K, WINDOW, TBILL, MIN_POOL, SLACK = 0.4, 5, 0.03, 10, 7 / 365.25
FIN_WORDS = ("bank", "insurance", "credit services", "capital markets", "mortgage finance")
STOCK_INDUSTRIES = ["Entertainment", "Semiconductors", "Discount Stores", "Internet Content & Information"]
ETF_CATS = ["Large Blend", "Large Growth"]
EX_STOCKS, EX_ETFS = ["IMAX", "NFLX", "DIS"], ["USMV", "VIG", "VOO"]
TODAY = date(2026, 10, 18)
OK, ZERO, HUNDRED, EXCL = "ok", "zero", "hundred", "excluded"
# What-if switches for section 6 of AUDIT.md (estimated effect of each recommended fix). Defaults = the app's current rules.
SIM = {"moderate": None,        # e.g. {"higher": 40, "lower": 60}: a stricter percentile for the 'moderate' tier
       "drop_too_new": False,   # keep too-new names out of EVERY metric's peer pool, not only the history metrics
       "hq_cov": "fund"}        # 'fund' = coverage as a share of the whole fund (app); 'top10' = share of the top-10 weight


def num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def dirn(asset, k):
    return (STOCK_DIR if asset == "stock" else ETF_DIR).get(k, "higher")


# ----------------------------------------------------------------------------- raw data straight from SQLite
def load_stock_rows():
    c = sqlite3.connect("stock_raw.db")
    rows = c.execute("SELECT ticker, industry, fetched_date, json FROM raw_stock").fetchall()
    c.close()
    out = []
    for t, ind, fd, js in rows:
        r = json.loads(js)
        r["_industry"], r["_fetched_date"] = ind, fd
        out.append(r)
    return out


def load_etfs():
    c = sqlite3.connect("etf_raw.db")
    rows = c.execute("SELECT ticker, fetched_date, json FROM etf_raw").fetchall()
    c.close()
    out = {}
    for t, fd, js in rows:
        r = json.loads(js)
        r["_fetched_date"] = fd
        out[t] = r
    return out


def load_holding_scores():
    c = sqlite3.connect("holding_scores.db")
    s = {t: (ind, comp) for t, ind, comp in c.execute("SELECT ticker, industry, composite FROM holding_scores")}
    c.close()
    return s


# ----------------------------------------------------------------------------- stock metrics, re-implemented
class Co:
    def __init__(self, r):
        self.r, self.A = r, r.get("annual") or {}
        self.ends = sorted(self.A)
        self.n = len(self.ends)

    def v(self, i, f):
        if i < 0 or i >= self.n:
            return None
        x = self.A[self.ends[i]].get(f)
        return x if num(x) else None

    def avg(self, i, f):
        e = self.v(i, f)
        if e is None:
            return None, False
        b = self.v(i - 1, f)
        return ((b + e) / 2, True) if b is not None else (e, False)

    def tax(self, i):
        t, p = self.v(i, "tax_expense"), self.v(i, "pretax_income")
        return 0.21 if (t is None or p is None or p <= 0) else min(max(t / p, 0.0), 0.35)

    def debt(self, i):
        d = self.v(i, "total_debt")
        if d is not None:
            return d
        return 0.0 if (self.v(i, "equity") is not None and self.v(i, "total_assets") is not None) else None

    def ic(self, i):
        d, e, c = self.debt(i), self.v(i, "equity"), self.v(i, "cash")
        return None if None in (d, e, c) else d + e - c

    def roic(self, i):
        ebit, end = self.v(i, "ebit"), self.ic(i)
        if ebit is None or end is None:
            return None
        beg = self.ic(i - 1) if i >= 1 else None
        ic = (beg + end) / 2 if beg is not None else end
        return None if ic <= 0 else ebit * (1 - self.tax(i)) / ic

    # split-adjusted per-share earnings (the rule in METHODOLOGY.md "Split-adjusted Lynch ratio")
    def factor(self, i, field):
        end = self.ends[i]
        filed = ((self.r.get("filed") or {}).get(end) or {}).get(field) or end
        upto = (self.r.get("fetched") or "")[:10] or "9999"
        f = 1.0
        for d, ratio in self.r.get("splits") or []:
            if filed < d <= upto:
                f *= ratio
        return f

    def shares_adj(self, i):
        s = self.v(i, "diluted_shares")
        return None if s is None else s * self.factor(i, "diluted_shares")

    def eps_adj(self, i):
        ni, sh = self.v(i, "net_income"), self.shares_adj(i)
        m = self.r.get("market") or {}
        plausible = True
        if sh and num(m.get("market_cap")) and num(m.get("price")) and m["market_cap"] > 0 and m["price"] > 0:
            plausible = 0.25 <= sh / (m["market_cap"] / m["price"]) <= 4
        if ni is not None and sh and plausible:
            return ni / sh
        e = self.v(i, "eps_diluted")
        return None if e is None else e / self.factor(i, "eps_diluted")


def cagr(vals):
    if len(vals) < 2 or vals[0] <= 0 or vals[-1] <= 0:
        return None
    return (vals[-1] / vals[0]) ** (1 / (len(vals) - 1)) - 1


def trailing_run(vals):
    run = []
    for x in reversed(vals):
        if x is None:
            break
        run.insert(0, x)
    return run


def avg_changes(vals):
    ch = [vals[i] - vals[i - 1] for i in range(1, len(vals)) if vals[i] is not None and vals[i - 1] is not None]
    return sum(ch) / len(ch) if ch else None


def is_foreign(r):
    return not r.get("annual") and r.get("annual_form") in ("20-F", "40-F")


def is_fin(r):
    sic = r.get("sic")
    if sic is not None and 6000 <= sic <= 6399:
        return True
    names = f"{r.get('industry') or r.get('_industry') or ''} {r.get('yf_industry') or ''}".lower()
    return any(w in names for w in FIN_WORDS)


def stock_metrics(r, window=WINDOW):
    """-> {metric: (value, status)} following METHODOLOGY.md section B."""
    co, m, px = Co(r), r.get("market") or {}, r.get("px") or {}
    n, L = co.n, co.n - 1
    fin = is_fin(r)
    mcap = m.get("market_cap") if num(m.get("market_cap")) else None
    price = m.get("price")
    out = {k: (None, EXCL) for k in STOCK_W}
    ebit = co.v(L, "ebit")
    da = co.v(L, "da")
    ebitda = ebit + da if (ebit is not None and da is not None) else None
    debt, cash = (co.debt(L), co.v(L, "cash")) if n else (None, None)
    rev = co.v(L, "revenue")
    # ROIC
    if n and not fin:
        x = co.roic(L)
        if x is not None:
            out["roic"] = (x * 100, OK)
    # gross profitability
    if n and not fin:
        gp = co.v(L, "gross_profit")
        if gp is None and rev is not None and co.v(L, "cogs") is not None:
            gp = rev - co.v(L, "cogs")
        ta, _ = co.avg(L, "total_assets")
        if gp is not None and ta is not None and ta > 0:
            out["gross_profitability"] = (gp / ta * 100, OK)
    # earnings yield
    if not fin and mcap is not None and debt is not None and cash is not None and ebit is not None:
        ev = mcap + debt - cash + (co.v(L, "preferred") or 0) + (co.v(L, "minority_interest") or 0)
        if ev > 0:
            out["earnings_yield"] = (ebit / ev * 100, OK if ebit > 0 else ZERO)
    # FCF yield
    ocf, capex = (co.v(L, "ocf"), co.v(L, "capex")) if n else (None, None)
    if ocf is not None and capex is not None and mcap is not None:
        out["fcf_yield"] = ((ocf - abs(capex)) / mcap * 100, OK)
    # Lynch (split-adjusted)
    eps = co.eps_adj(L) if n else None
    if eps is not None and num(price):
        if eps <= 0:
            out["lynch"] = (None, ZERO)
        else:
            pe = price / eps
            hist = [co.eps_adj(i) for i in range(max(0, n - 4), n)]
            if any(h is None for h in hist) or len(hist) < 3:
                hist = trailing_run(hist)
            g = cagr(hist) if len(hist) >= 3 else None
            if g is not None:
                dy = m.get("dividend_yield")
                if not num(dy):
                    paid = co.v(L, "dividends_paid")
                    dy = abs(paid) / mcap if (paid and mcap) else 0.0
                out["lynch"] = ((g * 100 + dy * 100) / pe, OK)
    # revenue growth
    run = trailing_run([co.v(i, "revenue") for i in range(max(0, n - 4), n)])
    if len(run) >= 2 and cagr(run) is not None:
        out["revenue_growth"] = (cagr(run) * 100, OK)
    # net debt / EBITDA
    if ebitda is not None and debt is not None and cash is not None:
        out["net_debt_ebitda"] = ((debt - cash) / ebitda, OK if ebitda > 0 else ZERO)
    # interest coverage
    a_last = co.A[co.ends[L]] if n else {}
    ip = n and num(a_last.get("interest_expense"))
    interest = abs(a_last["interest_expense"]) if ip else None
    if ip and interest == 0:
        out["interest_coverage"] = (None, HUNDRED)
    elif ebit is None:
        pass
    elif not ip:
        if debt == 0:
            out["interest_coverage"] = (None, HUNDRED)
    else:
        out["interest_coverage"] = (ebit / interest, OK if ebit > 0 else ZERO)
    # delta ROIC / delta operating margin (last 4 fiscal years -> up to 3 consecutive changes)
    idx = range(max(0, n - 4), n)
    if not fin:
        d = avg_changes([(co.roic(i) * 100 if co.roic(i) is not None else None) for i in idx])
        if d is not None:
            out["delta_roic"] = (d, OK)
    d = avg_changes([(co.v(i, "ebit") / co.v(i, "revenue") * 100) if (co.v(i, "ebit") is not None and co.v(i, "revenue")) else None for i in idx])
    if d is not None:
        out["delta_op_margin"] = (d, OK)
    # Sloan
    ni = co.v(L, "net_income")
    ta, _ = co.avg(L, "total_assets") if n else (None, False)
    if ni is not None and ocf is not None and ta is not None and ta > 0:
        out["sloan"] = ((ni - ocf) / ta * 100, OK)
    # downside deviation + max drawdown over the common window
    years = px.get("years") or 0.0
    rets = px.get("monthly_returns") or []
    if years + SLACK >= window:
        if len(rets) >= window * 12 - 1:
            rr = rets[-min(len(rets), window * 12):]
            mar = (1 + TBILL) ** (1 / 12) - 1
            out["downside_dev"] = (math.sqrt(sum(min(0.0, x - mar) ** 2 for x in rr) / len(rr)) * math.sqrt(12) * 100, OK)
        if px.get(f"mdd_{window}") is not None:
            out["max_drawdown"] = (px[f"mdd_{window}"] * 100, OK)
    # shareholder yield
    parts = [abs(co.v(i, "dividends_paid") or 0) + abs(co.v(i, "repurchases") or 0) - abs(co.v(i, "stock_issued") or 0)
             for i in range(max(0, n - 3), n) if co.v(i, "ocf") is not None]
    if parts and mcap is not None:
        out["shareholder_yield"] = (sum(parts) / len(parts) / mcap * 100, OK)
    too_new = years + SLACK < window
    return out, too_new


# ----------------------------------------------------------------------------- shared scoring math, re-implemented
def avg_rank_pct(vals, higher):
    """Average ranks (1 = lowest), p = (rank - 1) / (n - 1) x 100; flipped for lower-is-better."""
    n = len(vals)
    if n == 0:
        return []
    if n == 1:
        return [50.0]
    order = sorted(range(n), key=lambda i: vals[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        for t in range(i, j + 1):
            ranks[order[t]] = (i + j) / 2 + 1
        i = j + 1
    p = [(r - 1) / (n - 1) * 100 for r in ranks]
    return p if higher else [100 - x for x in p]


def pctl(vals, p):
    """Linear-interpolation percentile (the 'linear' rule: position = p/100 x (n - 1))."""
    s = sorted(vals)
    pos = p / 100 * (len(s) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def fit_of(value, limit, spread, d):
    if not (num(value) and num(limit) and num(spread)) or spread <= 0:
        return None, None
    dist = max(0.0, value - limit) if d == "lower" else max(0.0, limit - value)
    return 100 * max(0.0, 1 - (dist / spread) / 2), dist


LAURA_PCT = {  # from laura.py LAURA_TIERS (risk 50, consistency 40); not listed = Industry-average percentile
    "stock": {"downside_dev": 50, "max_drawdown": 50, "net_debt_ebitda": 50, "sloan": 50, "interest_coverage": 50,
              "roic": 40, "gross_profitability": 40, "delta_roic": 40, "delta_op_margin": 40},
    "etf": {"expense_ratio": 50, "downside_dev": 50, "max_drawdown": 50, "top10_weight": 50, "aum": 50, "dollar_volume": 50,
            "holdings_quality": 40}}


def avg_pct(d):
    return 75 if d == "lower" else 25


def laura_pct(asset, k):
    if k not in LAURA_PCT[asset] and SIM["moderate"]:
        return SIM["moderate"][dirn(asset, k)]
    return LAURA_PCT[asset].get(k, avg_pct(dirn(asset, k)))


def score_group(asset, members, metrics, fallback_vals, weights, k=K, trust=None, sector_vals=None, all_pct=None, too_new=None):
    """members: tickers in the peer group; metrics: {ticker: {metric: (value, status)}};
    fallback_vals: {metric: {ticker: value}} for the 'all' pool; sector_vals: same for the stock sector pool.
    -> ({ticker: {...scores...}}, bench)."""
    W = weights
    bench = {}
    pct = {t: {} for t in members}
    for m in W:
        d = dirn(asset, m)
        own = {t: metrics[t][m][0] for t in members if metrics[t][m][1] == OK and num(metrics[t][m][0])
               and not (SIM["drop_too_new"] and too_new and too_new.get(t))}
        level, pool = ("industry" if asset == "stock" else "category"), dict(own)
        if len(pool) < MIN_POOL:
            if asset == "stock" and sector_vals is not None and len({**sector_vals[m], **own}) >= MIN_POOL:
                level, pool = "sector", {**sector_vals[m], **own}
            else:
                level, pool = "all", {**fallback_vals[m], **own}
        if level == "all" and asset == "etf" and all_pct is not None:
            for t in members:
                if t in own:
                    pct[t][m] = all_pct[m][t]
        else:
            tk = list(pool)
            for t, s in zip(tk, avg_rank_pct([pool[t] for t in tk], d == "higher")):
                if t in pct:
                    pct[t][m] = s
        vals = list(pool.values())
        if vals:
            p25, p50, p75 = pctl(vals, 25), pctl(vals, 50), pctl(vals, 75)
            spread = p75 - p25
            if not spread:
                for alt in ([sector_vals[m]] if (asset == "stock" and sector_vals is not None) else []) + [fallback_vals[m]]:
                    a = list({**alt, **own}.values())
                    if len(a) >= 2 and pctl(a, 75) - pctl(a, 25) > 0:
                        spread = pctl(a, 75) - pctl(a, 25)
                        break
            bench[m] = {"level": level, "n": len(vals), "own_n": len(own), "p25": p25, "median": p50, "p75": p75, "spread": spread,
                        "laura": pctl(vals, laura_pct(asset, m)), "industry_avg": pctl(vals, avg_pct(d)), "own": own}
        else:
            bench[m] = None
    res = {}
    for t in members:
        rows, num_c = {}, 0.0
        for m in W:
            v, st = metrics[t][m]
            b = bench[m]
            p = f = None
            if st == ZERO and asset == "stock":
                p = f = 0.0
            elif st == HUNDRED and asset == "stock":
                p = f = 100.0
            elif st == OK and b is not None:
                p = pct[t].get(m)
                f, _ = fit_of(v, b["laura"], b["spread"], dirn(asset, m))
                tr = (trust or {}).get(t) if m == "holdings_quality" else None
                if tr is not None and p is not None and f is not None:
                    p = tr * p + (1 - tr) * 50
                    f = tr * f + (1 - tr) * 100
            if p is None or f is None:
                p = f = None
            rows[m] = (p, f)
        den = sum(W[m] for m in W if rows[m][0] is not None)
        comp = sum(W[m] * rows[m][0] for m in W if rows[m][0] is not None) / den if den else None
        fit = sum(W[m] * rows[m][1] for m in W if rows[m][0] is not None) / den if den else None
        overall = None if comp is None else (comp if fit is None else max(0.0, comp - k * (100 - fit)))
        res[t] = {"rows": rows, "composite": comp, "fit": fit, "overall": overall, "excl_w": sum(W.values()) - den}
    return res, bench


def rank_order(res, too_new, low_data, not_scored, nflags):
    def key(t):
        r = res[t]
        return (not_scored.get(t, False), low_data[t], too_new[t], -(r["overall"] if r["overall"] is not None else -1),
                -(r["composite"] if r["composite"] is not None else -1), nflags.get(t, 0), r["excl_w"], t)
    order = sorted(res, key=key)
    out, i = {}, 0
    for t in order:
        if not_scored.get(t):
            out[t] = None
        else:
            i += 1
            out[t] = i
    return out


# ----------------------------------------------------------------------------- stock groups end to end
class StockWorld:
    def __init__(self):
        self.rows = load_stock_rows()
        self.by_ind = defaultdict(list)
        for r in self.rows:
            self.by_ind[r["_industry"]].append(r)
        self._m = {}
        uniq = {}
        for r in self.rows:
            uniq.setdefault(r["ticker"], r)
        self.uniq = uniq

    def metrics(self, r):
        key = (r["ticker"], r["_industry"])
        if key not in self._m:
            self._m[key] = stock_metrics(r)
        return self._m[key]

    def group(self, industry, k=K):
        raws = [r for r in self.by_ind[industry] if not is_foreign(r)]
        foreign = [r["ticker"] for r in self.by_ind[industry] if is_foreign(r)]
        mets, too_new = {}, {}
        for r in raws:
            mets[r["ticker"]], too_new[r["ticker"]] = self.metrics(r)
        # common end date: prices ending > 7 days before the industry's newest -> history metrics excluded
        ends = {r["ticker"]: (r.get("px") or {}).get("last_date") for r in raws}
        newest = max([e for e in ends.values() if e] or [None]) if any(ends.values()) else None
        for t, e in ends.items():
            if e and newest and (pd.Timestamp(newest) - pd.Timestamp(e)).days > 7 and not too_new[t]:
                for m in ("downside_dev", "max_drawdown"):
                    mets[t][m] = (None, EXCL)
        members = list(mets)
        secs = [r.get("sector") for r in raws if r.get("sector")]
        sector = max(set(secs), key=secs.count) if secs else None
        others = [r for t, r in self.uniq.items() if t not in mets and not is_foreign(r)]
        allv = {m: {} for m in STOCK_W}
        secv = {m: {} for m in STOCK_W}
        for r in others:
            om, _ = self.metrics(r)
            for m in STOCK_W:
                if om[m][1] == OK and num(om[m][0]):
                    allv[m][r["ticker"]] = om[m][0]
                    if sector is not None and r.get("sector") == sector:
                        secv[m][r["ticker"]] = om[m][0]
        res, bench = score_group("stock", members, mets, allv, STOCK_W, k=k, sector_vals=secv, too_new=too_new)
        tw = sum(STOCK_W.values())
        low = {}
        for t in members:
            excl = sum(STOCK_W[m] for m in STOCK_W if res[t]["rows"][m][0] is None)
            hist = sum(STOCK_W[m] for m in ("downside_dev", "max_drawdown") if res[t]["rows"][m][0] is None)
            low[t] = ((excl - hist) if too_new[t] else excl) / tw > 0.30
        not_scored = {r["ticker"]: not r.get("annual") for r in raws}
        return {"res": res, "bench": bench, "metrics": mets, "too_new": too_new, "low": low, "not_scored": not_scored,
                "foreign": foreign, "members": members}


# ----------------------------------------------------------------------------- ETF groups end to end
def etf_metrics(r, hs, window=WINDOW):
    px = r.get("px") or {}
    out = {k: (None, EXCL) for k in ETF_W}
    if num(r.get("expense_ratio")):
        out["expense_ratio"] = (r["expense_ratio"], OK)
    years = px.get("years") or 0.0
    too_new = years + SLACK < window
    if not too_new:
        rets = px.get("monthly_returns") or []
        n_m = min(len(rets), window * 12) if len(rets) >= window * 12 - 1 else 0
        if n_m >= 12:
            mar = (1 + TBILL) ** (1 / 12) - 1
            rr = rets[-n_m:]
            out["downside_dev"] = (math.sqrt(sum(min(0.0, x - mar) ** 2 for x in rr) / len(rr)) * math.sqrt(12) * 100, OK)
        if px.get(f"mdd_{window}") is not None:
            out["max_drawdown"] = (px[f"mdd_{window}"] * 100, OK)
        if px.get(f"cagr_{window}") is not None:
            out["long_run_return"] = (px[f"cagr_{window}"] * 100, OK)
    hold = sorted(r.get("holdings") or [], key=lambda h: -h["weight"])[:10]
    if hold:
        out["top10_weight"] = (sum(h["weight"] for h in hold) * 100, OK)
    if num(r.get("aum")):
        out["aum"] = (r["aum"] / 1e6, OK)
    if num(r.get("dollar_volume")):
        out["dollar_volume"] = (r["dollar_volume"] / 1e6, OK)
    scored = [(h["weight"], hs[h["symbol"]][1]) for h in hold if h["symbol"] in hs and hs[h["symbol"]][1] is not None]
    wsum = sum(w for w, _ in scored)
    cov = wsum  # app rule: the scored holdings' share of the WHOLE fund
    if SIM["hq_cov"] == "top10" and hold:
        cov = wsum / sum(h["weight"] for h in hold)  # what-if: share of the top-10 weight that is scored
    trust = None
    if hold and scored and cov >= 0.15:
        out["holdings_quality"] = (sum(w * c for w, c in scored) / wsum, OK)
        trust = min(1.0, cov / 0.5)
    if num(px.get("ttm_dividend_yield")):
        out["dividend_yield"] = (px["ttm_dividend_yield"] * 100, OK)
    return out, too_new, trust, cov


class EtfWorld:
    def __init__(self):
        self.funds = load_etfs()
        self.hs = load_holding_scores()
        u = pd.read_csv("results/etf_universe.csv")
        self.cat = dict(zip(u["ticker"], u["morningstar_category"]))
        self.m = {t: etf_metrics(r, self.hs) for t, r in self.funds.items()}

    def group(self, cat, k=K):
        members = [t for t in self.funds if self.cat.get(t) == cat]
        mets = {t: dict(self.m[t][0]) for t in members}
        too_new = {t: self.m[t][1] for t in members}
        trust = {t: self.m[t][2] for t in members}
        ends = {t: (self.funds[t].get("px") or {}).get("last_date") for t in members}
        newest = max([e for e in ends.values() if e] or [""])
        for t, e in ends.items():
            if e and newest and (pd.Timestamp(newest) - pd.Timestamp(e)).days > 7 and not too_new[t]:
                for m in ("downside_dev", "max_drawdown", "long_run_return"):
                    mets[t][m] = (None, EXCL)
        allv = {m: {t: self.m[t][0][m][0] for t in self.funds if self.m[t][0][m][1] == OK} for m in ETF_W}
        for m in ETF_W:  # the group's own (possibly stale-excluded) values replace their universe entries
            for t in members:
                allv[m].pop(t, None)
                if mets[t][m][1] == OK:
                    allv[m][t] = mets[t][m][0]
        all_pct = {}
        for m in ETF_W:
            tk = list(allv[m])
            all_pct[m] = dict(zip(tk, avg_rank_pct([allv[m][t] for t in tk], dirn("etf", m) == "higher")))
        res, bench = score_group("etf", members, mets, allv, ETF_W, k=k, trust=trust, all_pct=all_pct, too_new=too_new)
        tw = sum(ETF_W.values())
        low = {}
        for t in members:
            excl = sum(ETF_W[m] for m in ETF_W if res[t]["rows"][m][0] is None)
            hist = sum(ETF_W[m] for m in ("downside_dev", "max_drawdown", "long_run_return") if res[t]["rows"][m][0] is None)
            low[t] = ((excl - hist) if too_new[t] else excl) / tw > 0.30
        return {"res": res, "bench": bench, "metrics": mets, "too_new": too_new, "low": low, "trust": trust, "members": members}


# ----------------------------------------------------------------------------- the app's own answers (comparison only)
def app_stock(industry, k=K):
    import scoring2 as sc
    import stock_data as sd
    raws, _ = sd.load_industry(industry)
    prep = sc.prepare_industry(raws, sc.DEFAULT_SETTINGS, all_raws=sd.load_all_cached)
    return prep, sc.finalize(prep, penalty=k)


def app_etf(cat, k=K):
    import etf_data as ed
    import etf_groups as eg
    import etf_holdings as eh
    import etf_scoring as es
    mem = eg.membership(eg.load_universe(), eg.load_overrides())
    tk = list(mem.loc[mem["group"] == cat, "ticker"])
    raws = eg.group_raws(tk, ed.load_funds(tk), cat)
    hs, hk = eh.load_scores()
    prep = es.prepare_funds(raws, es.DEFAULT_SETTINGS, hs, hk, all_raws=ed.load_all)
    return prep, es.finalize(prep, penalty=k)


def compare(asset, mine, app_final, nflags_from_app=True):
    """Per name: Composite / Fit / Overall / rank and every metric percentile + Fit, mine vs app (tolerance 0.1)."""
    app = {r["ticker"]: r for r in app_final["results"]}
    nflags = {t: len(r["flags"]) for t, r in app.items()} if nflags_from_app else {}
    ranks = rank_order(mine["res"], mine["too_new"], mine["low"], mine.get("not_scored", {}), nflags)
    mism, n = [], 0
    for t, r in mine["res"].items():
        a = app.get(t)
        if a is None:
            mism.append((t, "missing from app", None, None))
            continue
        n += 1
        for f in ("composite", "fit", "overall"):
            x, y = r[f], a[f]
            if (x is None) != (y is None) or (x is not None and abs(x - y) > 0.1):
                mism.append((t, f, x, y))
        if ranks[t] != a["rank"]:
            mism.append((t, "rank", ranks[t], a["rank"]))
        for m in (STOCK_W if asset == "stock" else ETF_W):
            p, f = r["rows"][m]
            ap, af = a["metrics"][m]["pct"], a["metrics"][m]["fit"]
            if (p is None) != (ap is None) or (p is not None and abs(p - ap) > 0.1):
                mism.append((t, f"{m} percentile", p, ap))
            if (f is None) != (af is None) or (f is not None and abs(f - af) > 0.1):
                mism.append((t, f"{m} fit", f, af))
    extra = [t for t in app if t not in mine["res"]]
    return n, mism, ranks, extra


def compare_metric_values(asset, mine, app_prep):
    """Raw metric values + statuses, mine vs app, for every name in the group."""
    out = []
    src = app_prep["companies"] if asset == "stock" else app_prep["funds"]
    for t, mm in mine["metrics"].items():
        a = src.get(t)
        if a is None:
            continue
        for m, (v, st) in mm.items():
            av, ast = a["metrics"][m]["value"], a["metrics"][m]["status"]
            if ast != st or (v is None) != (av is None) or (v is not None and av is not None and abs(v - av) > 1e-6 * max(1, abs(av))):
                if st == ZERO and ast == ZERO:
                    continue  # forced 0: the value shown is informational
                out.append((t, m, v, st, av, ast))
    return out


# ----------------------------------------------------------------------------- report helpers
def f1(x, nd=1):
    return "—" if x is None else f"{x:,.{nd}f}"


def limits_table(asset, g, keys, units):
    """Part 1: median, Industry-average limit, Laura limit, strictness and pass rates for one group."""
    rows = []
    for m in keys:
        b = g["bench"][m]
        if b is None or ETF_W.get(m, 1) == 0 and asset == "etf":
            continue
        d = dirn(asset, m)
        ind, lau = b["industry_avg"], b["laura"]
        tighter = (ind - lau) if d == "lower" else (lau - ind)
        rel = (tighter / abs(ind) * 100) if ind else None
        own = list(b["own"].values())
        ok = lambda v, lim: v <= lim if d == "lower" else v >= lim
        rows.append({"metric": m, "dir": d, "side": "ceiling" if d == "lower" else "floor", "pool": b["level"], "n": b["n"],
                     "median": b["median"], "industry_limit": ind, "laura_limit": lau, "tighter_units": tighter, "tighter_pct": rel,
                     "pass_industry": sum(ok(v, ind) for v in own) / len(own) * 100 if own else None,
                     "pass_laura": sum(ok(v, lau) for v in own) / len(own) * 100 if own else None,
                     "units": units.get(m, "")})
    return rows


def main():
    net = "--net" in sys.argv
    import etf_scoring as es
    import laura
    import scoring2 as sc
    report = {"generated": str(TODAY)}

    # ---------------- Part 1a: rules table (constants checked against the code's LAURA_TIERS and Industry-average rule)
    rules = []
    for asset, metas in (("stock", sc.METRICS), ("etf", es.METRICS)):
        for mt in metas:
            k = mt["key"]
            d = mt["direction"]
            tier = laura.LAURA_TIERS[asset].get(k)
            lp = tier[1] if tier else laura.AVG_PCT[d]
            ip = laura.AVG_PCT[d]
            # stricter = the limit sits further toward the GOOD side: for a ceiling a lower percentile, for a floor a higher one
            verdict = "same" if lp == ip else ("stricter" if (lp < ip if d == "lower" else lp > ip) else "LOOSER")
            mine_lp = laura_pct(asset, k)
            rules.append({"asset": asset, "metric": k, "label": mt["label"], "direction": d, "doc_direction": dirn(asset, k),
                          "side": "ceiling" if d == "lower" else "floor", "industry_avg_pct": ip, "laura_pct": lp,
                          "laura_tier": tier[0] if tier else "moderate", "verdict": verdict, "weight": mt["weight"],
                          "doc_weight": (STOCK_W if asset == "stock" else ETF_W)[k], "matches_written_rule": mine_lp == lp})
    report["rules"] = rules

    # ---------------- Parts 1b + 2: groups end to end
    sw, ew = StockWorld(), EtfWorld()
    report["groups"] = {}
    units_s = {m["key"]: m["units"] for m in sc.METRICS}
    units_e = {m["key"]: m["units"] for m in es.METRICS}
    app_cache = {}
    for ind in STOCK_INDUSTRIES:
        g = sw.group(ind)
        prep, fin = app_stock(ind)
        app_cache[("stock", ind)] = (prep, fin, g)
        n, mism, ranks, extra = compare("stock", g, fin)
        vals = compare_metric_values("stock", g, prep)
        app_lim = sc.laura_limits(prep["bench"])
        app_avg = sc.default_limits(prep["bench"])
        lim_mism = [(m, g["bench"][m]["laura"], app_lim.get(m), g["bench"][m]["industry_avg"], app_avg.get(m)) for m in STOCK_W
                    if g["bench"][m] and (abs(g["bench"][m]["laura"] - app_lim.get(m, 1e99)) > 1e-9 or abs(g["bench"][m]["industry_avg"] - app_avg.get(m, 1e99)) > 1e-9)]
        report["groups"][ind] = {"asset": "stock", "n": n, "mismatches": mism, "value_mismatches": vals, "limit_mismatches": lim_mism,
                                 "foreign": g["foreign"], "extra_in_app": extra,
                                 "limits": limits_table("stock", g, list(STOCK_W), units_s),
                                 "top10": [{"ticker": t, "rank": ranks[t], **{f: g["res"][t][f] for f in ("composite", "fit", "overall")},
                                            "penalty": (g["res"][t]["composite"] - g["res"][t]["overall"]) if g["res"][t]["overall"] is not None else None,
                                            "app": {f: next(r[f] for r in fin["results"] if r["ticker"] == t) for f in ("composite", "fit", "overall", "rank")}}
                                           for t in sorted(ranks, key=lambda t: (ranks[t] is None, ranks[t] or 0))[:10]],
                                 "too_new": sorted(t for t, v in g["too_new"].items() if v)}
    for cat in ETF_CATS:
        g = ew.group(cat)
        prep, fin = app_etf(cat)
        app_cache[("etf", cat)] = (prep, fin, g)
        n, mism, ranks, extra = compare("etf", g, fin)
        vals = compare_metric_values("etf", g, prep)
        app_lim = es.laura_limits(prep["bench"][cat])
        app_avg = es.default_limits(prep["bench"][cat])
        lim_mism = [(m, g["bench"][m]["laura"], app_lim.get(m), g["bench"][m]["industry_avg"], app_avg.get(m)) for m in ETF_W
                    if g["bench"][m] and (abs(g["bench"][m]["laura"] - app_lim.get(m, 1e99)) > 1e-9 or abs(g["bench"][m]["industry_avg"] - app_avg.get(m, 1e99)) > 1e-9)]
        report["groups"][cat] = {"asset": "etf", "n": n, "mismatches": mism, "value_mismatches": vals, "limit_mismatches": lim_mism,
                                 "extra_in_app": extra,
                                 "limits": limits_table("etf", g, list(ETF_W), units_e),
                                 "top10": [{"ticker": t, "rank": ranks[t], **{f: g["res"][t][f] for f in ("composite", "fit", "overall")},
                                            "penalty": (g["res"][t]["composite"] - g["res"][t]["overall"]) if g["res"][t]["overall"] is not None else None,
                                            "app": {f: next(r[f] for r in fin["results"] if r["ticker"] == t) for f in ("composite", "fit", "overall", "rank")}}
                                           for t in sorted(ranks, key=lambda t: ranks[t])[:10]],
                                 "too_new": sorted(t for t, v in g["too_new"].items() if v)}

    # ---------------- Part 2: worked examples (every step, mine next to the app's)
    ex = {}
    for t, (asset, grp) in {**{s: ("stock", "Entertainment") for s in EX_STOCKS}, **{e: ("etf", "Large Blend") for e in EX_ETFS}}.items():
        prep, fin, g = app_cache[(asset, grp)]
        a = next(r for r in fin["results"] if r["ticker"] == t)
        W = STOCK_W if asset == "stock" else ETF_W
        rows = []
        for m in W:
            v, st = g["metrics"][t][m]
            p, f = g["res"][t]["rows"][m]
            b = g["bench"][m]
            am = a["metrics"][m]
            den = sum(W[x] for x in W if g["res"][t]["rows"][x][0] is not None)
            rows.append({"metric": m, "value": v, "status": st, "app_value": am["value"], "app_status": am["status"],
                         "pool": None if b is None else f"{b['level']} n={b['n']}", "p25": None if b is None else b["p25"],
                         "p75": None if b is None else b["p75"], "spread": None if b is None else b["spread"],
                         "laura_limit": None if b is None else b["laura"], "app_limit": am["limit"],
                         "pct": p, "app_pct": am["pct"], "fit": f, "app_fit": am["fit"], "weight": W[m],
                         "contribution": (W[m] * p / den) if (p is not None and den) else None})
        e = {"asset": asset, "group": grp, "rows": rows, "composite": g["res"][t]["composite"], "fit": g["res"][t]["fit"],
             "overall": g["res"][t]["overall"], "app": {f: a[f] for f in ("composite", "fit", "overall", "rank", "penalty")}}
        if asset == "etf":
            r = ew.funds[t]
            hold = sorted(r.get("holdings") or [], key=lambda h: -h["weight"])[:10]
            e["holdings"] = [{"symbol": h["symbol"], "weight": h["weight"], "db_industry": (ew.hs.get(h["symbol"]) or (None, None))[0],
                              "db_composite": (ew.hs.get(h["symbol"]) or (None, None))[1]} for h in hold]
            e["coverage"], e["trust"] = ew.m[t][3], ew.m[t][2]
            e["app_coverage"], e["app_trust"] = a.get("hq_coverage"), a.get("hq_trust")
        ex[t] = e
    # holdings quality: recompute each example fund's holding Composites with OUR stock pipeline (current default weights)
    hq_check = []
    seen_ind = {}
    for t in EX_ETFS:
        for h in ex[t]["holdings"]:
            ind = h["db_industry"]
            if ind is None or h["db_composite"] is None:
                continue
            if ind not in seen_ind:
                seen_ind[ind] = sw.group(ind) if sw.by_ind.get(ind) else None
            gg = seen_ind[ind]
            mine = gg["res"].get(h["symbol"], {}).get("composite") if gg else None
            hq_check.append({"fund": t, "symbol": h["symbol"], "industry": ind, "db": h["db_composite"], "mine": mine,
                             "diff": None if mine is None else mine - h["db_composite"]})
    report["examples"], report["hq_check"] = ex, hq_check

    # daily-price recomputation of max drawdown / downside deviation for the examples (needs the network)
    if net:
        import yfinance as yf
        chk = []
        for t in EX_STOCKS + EX_ETFS:
            h = yf.Ticker(t).history(start="2016-10-01", end="2026-10-15", auto_adjust=True)["Close"].dropna()
            h.index = h.index.tz_localize(None)
            w = h[h.index >= h.index[-1] - pd.Timedelta(days=int(365.25 * 5))]
            mdd = float((1 - w / w.cummax()).max()) * 100
            me = h.resample("ME").last().dropna().pct_change().dropna().iloc[-60:]
            mar = (1 + TBILL) ** (1 / 12) - 1
            dd = math.sqrt(float(((me - mar).clip(upper=0) ** 2).mean())) * math.sqrt(12) * 100
            asset, grp = ("stock", "Entertainment") if t in EX_STOCKS else ("etf", "Large Blend")
            g = app_cache[(asset, grp)][2]
            chk.append({"ticker": t, "mdd_daily": mdd, "mdd_cached": g["metrics"][t]["max_drawdown"][0],
                        "dd_daily": dd, "dd_cached": g["metrics"][t]["downside_dev"][0], "last_close": str(h.index[-1].date())})
        report["price_recheck"] = chk

    # ---------------- Part 2.6: sensitivity table at k = 0.2 / 0.4 / 0.6, mine vs app
    sens = {}
    for (asset, grp), (prep, _, _) in app_cache.items():
        if grp not in ("Entertainment", "Large Blend"):
            continue
        mod = sc if asset == "stock" else es
        mine_top, app_top = {}, {}
        for kk in (0.2, 0.4, 0.6):
            g = sw.group(grp, k=kk) if asset == "stock" else ew.group(grp, k=kk)
            fin = mod.finalize(prep, penalty=kk)
            nfl = {r["ticker"]: len(r["flags"]) for r in fin["results"]}
            rk = rank_order(g["res"], g["too_new"], g["low"], g.get("not_scored", {}), nfl)
            mine_top[kk] = [t for t in sorted((t for t in rk if rk[t] is not None), key=lambda t: rk[t])][:10]
            app_top[kk] = [r["ticker"] for r in fin["results"] if r["rank"] is not None][:10]
        ps = sc.penalty_sensitivity({kk: mod.finalize(prep, penalty=kk)["results"] for kk in (0.2, 0.4, 0.6)})
        mine_ch = [(a, b, len(set(mine_top[a]) - set(mine_top[b]))) for a, b in ((0.2, 0.4), (0.4, 0.6), (0.2, 0.6))]
        sens[grp] = {"mine": mine_top, "app": app_top, "app_table_top": {str(k): v for k, v in ps["top"].items()},
                     "mine_changes": mine_ch, "app_changes": ps["changes"], "match": mine_top == app_top and mine_ch == ps["changes"]}
    report["sensitivity"] = sens

    # ---------------- Part 2.8: category totals
    def tot(metas, cats):
        t = Counter()
        for m in metas:
            t[{"Cost/valuation": "Cost"}.get(m["category"], m["category"])] += m["weight"]
        return dict(t)
    report["totals"] = {"stock_code": tot(sc.METRICS, None), "etf_code": tot(es.METRICS, None),
                        "stock_doc": DOC_TOTALS["stock"], "etf_doc": DOC_TOTALS["etf"],
                        "stock_sum": sum(m["weight"] for m in sc.METRICS), "etf_sum": sum(m["weight"] for m in es.METRICS),
                        "weights_match_doc": all(m["weight"] == STOCK_W[m["key"]] for m in sc.METRICS) and all(m["weight"] == ETF_W[m["key"]] for m in es.METRICS),
                        "cats_match_doc": all({"Cost/valuation": "Cost"}.get(m["category"], m["category"]) == STOCK_CAT[m["key"]] for m in sc.METRICS)
                        and all({"Cost/valuation": "Cost"}.get(m["category"], m["category"]) == ETF_CAT[m["key"]] for m in es.METRICS)}

    # ---------------- Part 3: sanity sweep over every cached stock and fund
    sweep = {}
    imp = defaultdict(list)
    for t, r in sw.uniq.items():
        if is_foreign(r):
            continue
        mm, _ = sw.metrics(r)
        v = lambda k: mm[k][0] if mm[k][1] == OK else None
        if v("max_drawdown") is not None and not (0 <= v("max_drawdown") <= 100):
            imp["stock drawdown outside 0-100%"].append((t, v("max_drawdown")))
        if v("roic") is not None and v("roic") > 200:
            imp["ROIC > 200%"].append((t, v("roic")))
        if v("revenue_growth") is not None and v("revenue_growth") > 500:
            imp["revenue growth > 500%"].append((t, v("revenue_growth")))
    for t, (mm, *_rest) in ew.m.items():
        v = lambda k: mm[k][0] if mm[k][1] == OK else None
        if v("max_drawdown") is not None and not (0 <= v("max_drawdown") <= 100):
            imp["ETF drawdown outside 0-100%"].append((t, v("max_drawdown")))
        if v("aum") is not None and v("aum") < 0:
            imp["negative AUM"].append((t, v("aum")))
        if v("expense_ratio") is not None and v("expense_ratio") > 3:
            imp["expense ratio > 3%"].append((t, v("expense_ratio")))
    sweep["impossible"] = {k: {"count": len(x), "worst": sorted(x, key=lambda y: -abs(y[1]))[:10]} for k, x in imp.items()}
    for k in ("stock drawdown outside 0-100%", "ROIC > 200%", "revenue growth > 500%", "ETF drawdown outside 0-100%", "negative AUM", "expense ratio > 3%"):
        sweep["impossible"].setdefault(k, {"count": 0, "worst": []})
    # leftover split jumps in the split-adjusted series (EPS and share counts), year over year
    jumps = []
    for t, r in sw.uniq.items():
        co = Co(r)
        if co.n < 2:
            continue
        sh = [co.shares_adj(i) for i in range(co.n)]
        for i in range(1, co.n):
            if sh[i] and sh[i - 1] and sh[i - 1] > 0:
                x = sh[i] / sh[i - 1]
                for R in (10, 2, 3, 0.5, 1 / 3, 0.1):
                    if abs(x / R - 1) < 0.04:
                        jumps.append((t, co.ends[i], round(x, 3), r.get("splits_dropped") or []))
    sweep["share_jumps"] = {"count": len(jumps), "tickers": len({j[0] for j in jumps}),
                            "worst": sorted(jumps, key=lambda j: -max(j[2], 1 / j[2]))[:10]}
    stale_s = [(t, r["_fetched_date"]) for t, r in sw.uniq.items() if (TODAY - date.fromisoformat(r["_fetched_date"])).days > 30]
    stale_e = [(t, r["_fetched_date"]) for t, r in ew.funds.items() if (TODAY - date.fromisoformat(r["_fetched_date"])).days > 30]
    nopx_s = [t for t, r in sw.uniq.items() if not (r.get("px") or {}).get("monthly_returns")]
    nopx_e = [t for t, r in ew.funds.items() if not (r.get("px") or {}).get("monthly_returns")]
    px_end = Counter((r.get("px") or {}).get("last_date") for r in sw.uniq.values())
    sweep["stale"] = {"stocks_fetched_over_30d": len(stale_s), "etfs_fetched_over_30d": len(stale_e),
                      "oldest_stocks": sorted(stale_s, key=lambda x: x[1])[:10], "oldest_etfs": sorted(stale_e, key=lambda x: x[1])[:10],
                      "stocks_no_prices": len(nopx_s), "stocks_no_prices_ex": nopx_s[:10], "etfs_no_prices": len(nopx_e), "etfs_no_prices_ex": nopx_e[:10],
                      "stock_price_end_dates": px_end.most_common(5),
                      "fetched_dates_stocks": Counter(r["_fetched_date"] for r in sw.uniq.values()).most_common(6)}
    small_ind = sorted(((ind, sum(1 for r in rs if not is_foreign(r))) for ind, rs in sw.by_ind.items()), key=lambda x: x[1])
    cats = Counter(ew.cat.get(t) for t in ew.funds)
    sweep["small_groups"] = {"industries_lt8": [x for x in small_ind if x[1] < 8], "n_industries": len(small_ind),
                             "categories_lt8": sorted([(c, n) for c, n in cats.items() if n < 8], key=lambda x: x[1]), "n_categories": len(cats)}
    report["sweep"] = sweep

    report["ties"] = ties_all()
    report["determinism"] = determinism()
    report["whatif"] = whatifs(sw)

    os.makedirs("results", exist_ok=True)
    json.dump(report, open("results/audit_check.json", "w"), indent=1, default=str)
    # short console summary
    for grp, g in report["groups"].items():
        print(f"{grp}: {g['n']} names, {len(g['mismatches'])} score mismatches, {len(g['value_mismatches'])} metric-value mismatches, "
              f"{len(g['limit_mismatches'])} limit mismatches, foreign {g.get('foreign')}, too new {len(g['too_new'])}")
    print("sensitivity match:", {k: v["match"] for k, v in report["sensitivity"].items()})
    print("totals:", report["totals"])
    print("hq check max |diff|:", max((abs(x["diff"]) for x in hq_check if x["diff"] is not None), default=None),
          "missing:", sum(x["mine"] is None for x in hq_check))
    print("ties:", len(report["ties"]["exact"]), "exact,", report["ties"]["within_0_05"], "within 0.05;", report["ties"]["stock_groups"], "industries,", report["ties"]["etf_groups"], "ETF categories")
    print("determinism:", report["determinism"])
    for k, v in report["whatif"].items():
        print("what-if", k, v.get("changes"), (v.get("top") or v).get("USMV rank"))
    print("sweep:", {k: v["count"] for k, v in sweep["impossible"].items()}, "share jumps", sweep["share_jumps"]["count"])


def ties_all():
    """App rankings for every cached stock industry and every Morningstar ETF category: exact Overall ties in each top 10
    and which tie-breaker decided them (Composite, then fewer red flags, then less excluded weight, then ticker A-Z)."""
    import etf_data as ed
    import etf_groups as eg
    import etf_holdings as eh
    import etf_scoring as es
    import scoring2 as sc
    import stock_data as sd
    allc = sd.load_all_cached()
    out = {"stock_groups": 0, "etf_groups": 0, "exact": [], "within_0_05": 0}
    def scan(kind, grp, res):
        top = [r for r in res if r["rank"] is not None][:10]
        for a, b in zip(top, top[1:]):
            if a["overall"] is None or b["overall"] is None:
                continue
            if a["overall"] == b["overall"]:
                by = ("Composite" if a["composite"] != b["composite"] else "red flags" if len(a["flags"]) != len(b["flags"])
                      else "excluded weight" if a["excluded_weight_share"] != b["excluded_weight_share"] else "ticker A-Z")
                out["exact"].append((kind, grp, a["ticker"], b["ticker"], a["rank"], round(a["overall"], 4), by))
            elif abs(a["overall"] - b["overall"]) < 0.05:
                out["within_0_05"] += 1
    for ind in sd.cached_industries():
        raws, _ = sd.load_industry(ind)
        prep = sc.prepare_industry(raws, sc.DEFAULT_SETTINGS, all_raws=lambda: allc)
        scan("stock", ind, sc.finalize(prep)["results"])
        out["stock_groups"] += 1
    mem = eg.membership(eg.load_universe(), eg.load_overrides())
    hs, hk = eh.load_scores()
    alle = ed.load_all()
    cats = [g for g in mem["group"].unique() if g not in eg.THEME_GROUPS]
    for cat in cats:
        tk = list(mem.loc[mem["group"] == cat, "ticker"])
        raws = eg.group_raws(tk, ed.load_funds(tk), cat)
        if not raws:
            continue
        prep = es.prepare_funds(raws, es.DEFAULT_SETTINGS, hs, hk, all_raws=lambda: alle)
        scan("etf", cat, es.finalize(prep)["results"])
        out["etf_groups"] += 1
    return out


DET_SNIPPET = """
import hashlib, json, sys
sys.path.insert(0, '.')
import audit_check as A
h = hashlib.sha1()
for ind in A.STOCK_INDUSTRIES:
    prep, fin = A.app_stock(ind)
    h.update(json.dumps([(r['ticker'], r['rank'], r['composite'], r['fit'], r['overall']) for r in fin['results']]).encode())
for cat in A.ETF_CATS:
    prep, fin = A.app_etf(cat)
    h.update(json.dumps([(r['ticker'], r['rank'], r['composite'], r['fit'], r['overall']) for r in fin['results']]).encode())
print(h.hexdigest())
"""


def determinism():
    """Run the app's scoring for the 6 audit groups in two fresh processes with different hash seeds (set / dict order)."""
    import subprocess
    outs = []
    for seed in ("1", "12345"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        r = subprocess.run([sys.executable, "-c", DET_SNIPPET], capture_output=True, text=True, env=env, cwd=os.getcwd())
        outs.append(r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-300:])
    return {"hashes": outs, "identical": outs[0] == outs[1]}


def whatifs(sw):
    """Estimated effect of each recommended fix on the Entertainment and Large Blend top 10 (our pipeline, same data)."""
    def tops():
        out = {}
        g = sw.group("Entertainment")
        rk = rank_order(g["res"], g["too_new"], g["low"], g["not_scored"], {})
        out["Entertainment"] = [(t, round(g["res"][t]["overall"], 1)) for t in sorted((t for t in rk if rk[t]), key=lambda t: rk[t])[:10]]
        ew = EtfWorld()
        g = ew.group("Large Blend")
        rk = rank_order(g["res"], g["too_new"], g["low"], {}, {})
        out["Large Blend"] = [(t, round(g["res"][t]["overall"], 1)) for t in sorted(rk, key=lambda t: rk[t])[:10]]
        if "USMV" in rk:
            out["USMV rank"] = rk["USMV"]
        return out
    base = tops()
    res = {"baseline": base}
    for name, sim in (("moderate tier at p40 floor / p60 ceiling", {"moderate": {"higher": 40, "lower": 60}}),
                      ("too-new names out of every peer pool", {"drop_too_new": True}),
                      ("holdings-quality coverage measured within the top 10", {"hq_cov": "top10"})):
        old = dict(SIM)
        SIM.update(sim)
        sw._m.clear()
        try:
            t = tops()
        finally:
            SIM.clear(); SIM.update(old); sw._m.clear()
        res[name] = {"top": t, "changes": {g: len({x for x, _ in base[g]} - {x for x, _ in t[g]}) for g in ("Entertainment", "Large Blend")}}
    return res


if __name__ == "__main__":
    main()
