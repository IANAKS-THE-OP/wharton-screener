"""
How ETFs are grouped into peer groups: by INDUSTRY or by MORNINGSTAR CATEGORY, plus THEMES.
============================================================================================
Pure functions (no Streamlit, no network), used by etf_page.py and compare.py.

  "industry"     -> the 145 stock industries (etf_industry.py tags, results/etf_industry_map.csv)
  "morningstar"  -> the fund's Morningstar category from yfinance (Large Blend, Large Value, ...),
                    already stored in the same map as `morningstar_category`

THEMES are detected from the FUND NAME (yfinance gives no fund description for most ETFs):
ESG / sustainable, low / min volatility, dividend / income, quality, equal weight. A theme is an
EXTRA peer group: a fund keeps its industry or category AND appears in every theme it matches
(ESGV is in "Large Blend" and in "Theme: ESG / sustainable"). Themes are offered in both modes.

Peer pools are always the group you picked (the same < 10-peer fallback to all downloaded equity
ETFs applies). Session keys are namespaced by mode (ns()), so a range set for "Theme: Quality" in
one mode never shows up in the other.
"""

import re

import pandas as pd

MODES = {"industry": "Industry (145)", "morningstar": "Morningstar category"}
THEME_PREFIX = "Theme: "

# (label, include pattern, exclude pattern or None). Matched against the fund name, case-insensitive.
THEMES = [
    ("ESG / sustainable",
     r"\besg\b|sustainab|responsib|socially|\bsri\b|climate|carbon|fossil|paris.aligned|net zero|\bkld\b|"
     r"\bimpact\b|sustainable development|\bsdg\b|\bethical\b|\bvegan\b|screened",
     r"social media|social sentiment|truth social|social 50|carbon (credit|allowance)"),
    ("Low / min volatility",
     r"low[ -]?vol|min(imum)?\.? ?vol|managed volatility|disciplined volatility|defined volatility|low beta",
     None),
    ("Dividend / income",
     r"dividend|\bincome\b|high yield|\byield\b|aristocrat|\bpayers?\b|\bgrowers?\b",
     None),
    ("Quality", r"\bquality\b", None),
    ("Equal weight", r"equal[ -]?weight|equally weighted|\bew\b", None),
]
_THEME_RX = [(THEME_PREFIX + lbl, re.compile(inc, re.I), re.compile(exc, re.I) if exc else None) for lbl, inc, exc in THEMES]
THEME_GROUPS = [g for g, _, _ in _THEME_RX]


def themes_for(name):
    """Every theme group whose pattern matches the fund name (possibly none)."""
    n = name or ""
    return [g for g, inc, exc in _THEME_RX if inc.search(n) and not (exc and exc.search(n))]


def ns(mode, group):
    """Session namespace for one peer group. Industry mode keeps the bare group name (unchanged keys);
    Morningstar mode is prefixed so the same name in the two modes never shares ranges or picks."""
    return group if mode == "industry" else f"ms:{group}"


def membership(imap, mode):
    """One row per (ticker, group) for the chosen grouping, themes included.
    Columns: ticker, name, group, morningstar_category, industry_group, basis."""
    col = "group" if mode == "industry" else "morningstar_category"
    base = pd.DataFrame({"ticker": imap["ticker"], "name": imap["name"], "group": imap[col].fillna("Uncategorized"),
                         "morningstar_category": imap["morningstar_category"], "industry_group": imap["group"],
                         "basis": imap["basis"] if mode == "industry" else "Morningstar category (yfinance)"})
    rows = []
    for t, n, cat, ig in zip(imap["ticker"], imap["name"], imap["morningstar_category"], imap["group"]):
        for g in themes_for(n):
            rows.append({"ticker": t, "name": n, "group": g, "morningstar_category": cat, "industry_group": ig,
                         "basis": "fund name (theme)"})
    return pd.concat([base, pd.DataFrame(rows, columns=base.columns)], ignore_index=True)


def morningstar_order(categories, counts, category_group):
    """US size/style first, then sector, then international; each by number of funds (largest first)."""
    rank = {"Broad market (size / style)": 0, "Sector": 1, "International / regional": 2}
    return sorted(categories, key=lambda c: (rank.get(category_group(c), 3), -counts.get(c, 0), c))


def group_raws(tickers, have, group):
    """The raws a group is scored with: each downloaded fund with its category overwritten by the group name
    (the peer group). The ETF page and Compare both build their raws with this, so they score identically."""
    return [{**have[t], "category": group} for t in tickers if t in have]


def run_self_tests():
    import etf_data as ed
    import etf_industry as ei
    results = []

    def check(name, ok, detail=""):
        results.append({"test": name, "passed": bool(ok), "detail": detail})

    t = {n: themes_for(n) for n in [
        "Vanguard ESG U.S. Stock ETF", "iShares MSCI USA Min Vol Factor ETF", "Invesco S&P 500 Low Volatility ETF",
        "Invesco S&P 500 High Dividend Low Volatility ETF", "Invesco S&P 500 Equal Weight ETF", "iShares MSCI USA Quality Factor ETF",
        "Global X Social Media ETF", "Truth Social American Icons ETF", "Vanguard S&P 500 ETF", "SoFi Social 50 ETF"]}
    E, L, D, Q, W = (THEME_PREFIX + x for x in ("ESG / sustainable", "Low / min volatility", "Dividend / income", "Quality", "Equal weight"))
    ok = (t["Vanguard ESG U.S. Stock ETF"] == [E] and t["iShares MSCI USA Min Vol Factor ETF"] == [L]
          and t["Invesco S&P 500 Low Volatility ETF"] == [L] and set(t["Invesco S&P 500 High Dividend Low Volatility ETF"]) == {L, D}
          and t["Invesco S&P 500 Equal Weight ETF"] == [W] and t["iShares MSCI USA Quality Factor ETF"] == [Q]
          and t["Global X Social Media ETF"] == [] and t["Truth Social American Icons ETF"] == [] and t["Vanguard S&P 500 ETF"] == []
          and t["SoFi Social 50 ETF"] == [])
    check("G1. Themes from fund names: ESG, low/min vol, dividend, quality, equal weight; 'Social Media'/'Truth Social' are not ESG", ok, str(t))

    keep = ["iShares MSCI USA Min Vol Factor ETF", "Invesco S&P 500 Low Volatility ETF", "Fidelity Low Volatility Factor ETF",
            "VictoryShares US 500 Volatility Wtd ETF", "AB US Low Volatility Equity ETF"]
    drop = ["ProShares VIX Short-Term Futures ETF", "Simplify Volatility Premium ETF", "Volatility Shares Trust - Volatility Premium Plus ETF",
            "iPath Series B S&P 500 VIX Short-Term Futures ETN"]
    ok2 = (all(ed.fund_exclusion_reason(n, 50.0) is None and not ei.UNCAT_BAD_NAME.search(n) for n in keep)
           and all(ed.fund_exclusion_reason(n, 50.0) or ei.UNCAT_BAD_NAME.search(n) for n in drop)
           and ed.category_exclusion_reason("Alternative Volatility") and ed.category_exclusion_reason("Large Blend") is None)
    check("G2. Low-volatility EQUITY funds pass the volatility exclusion; VIX / volatility-premium products are removed", ok2,
          f"kept {len(keep)}, removed {len(drop)}")

    imap = pd.DataFrame({"ticker": ["ESGV", "VOO", "SMH"], "name": ["Vanguard ESG U.S. Stock ETF", "Vanguard S&P 500 ETF", "VanEck Semiconductor ETF"],
                         "morningstar_category": ["Large Blend", "Large Blend", "Technology"],
                         "group": ["Broad US market (diversified)", "Broad US market (diversified)", "Semiconductors"],
                         "basis": ["x", "x", "y"]})
    ms, ind = membership(imap, "morningstar"), membership(imap, "industry")
    g = lambda m, tk: sorted(m.loc[m["ticker"] == tk, "group"])
    ok3 = (g(ms, "ESGV") == sorted(["Large Blend", E]) and g(ind, "ESGV") == sorted(["Broad US market (diversified)", E])
           and g(ms, "SMH") == ["Technology"] and g(ind, "SMH") == ["Semiconductors"]
           and ns("industry", E) != ns("morningstar", E) and ns("industry", "Semiconductors") == "Semiconductors")
    check("G3. Grouping: ESGV is in its category/industry AND the ESG theme; mode-specific session namespaces", ok3,
          f"morningstar {g(ms, 'ESGV')}, industry {g(ind, 'ESGV')}")
    return results
