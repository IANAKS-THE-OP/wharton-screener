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


def theme_matches(name):
    """[(theme group, the keyword in the name that matched)] - e.g. [("Theme: Low / min volatility", "Min Vol")]."""
    n = name or ""
    out = []
    for g, inc, exc in _THEME_RX:
        m = inc.search(n)
        if m and not (exc and exc.search(n)):
            out.append((g, m.group(0).strip()))
    return out


def themes_for(name):
    """Every theme group whose pattern matches the fund name (possibly none)."""
    return [g for g, _ in theme_matches(name)]


# ------------------------------------------------------------------ team overrides (results/theme_overrides.csv)
OVERRIDES_PATH = "results/theme_overrides.csv"
OVERRIDE_COLUMNS = ["ticker", "theme", "action"]  # action = add / remove; theme = e.g. "ESG / sustainable"


def _theme_group(label):
    t = str(label or "").strip()
    t = t[len(THEME_PREFIX):] if t.lower().startswith(THEME_PREFIX.lower()) else t
    return next((g for g in THEME_GROUPS if g[len(THEME_PREFIX):].lower() == t.lower()), None)


def load_overrides(path=OVERRIDES_PATH):
    """DataFrame (ticker, theme, action) or an empty one. Bad files never break the page (caller lists problems)."""
    import os
    if not os.path.exists(path):
        return pd.DataFrame(columns=OVERRIDE_COLUMNS)
    try:
        d = pd.read_csv(path, dtype=str, comment="#").fillna("")
    except (OSError, ValueError, pd.errors.ParserError):
        return pd.DataFrame([{"ticker": "", "theme": "", "action": "", "problem": f"could not read {path}"}])
    for c in OVERRIDE_COLUMNS:
        if c not in d:
            d[c] = ""
    return d[OVERRIDE_COLUMNS + (["problem"] if "problem" in d else [])]


def override_report(imap, overrides):
    """One row per override line: ticker, theme, action, result ('applied' or why not)."""
    known = set(imap["ticker"])
    auto = {(t, g) for t, n in zip(imap["ticker"], imap["name"]) for g in themes_for(n)}
    out = []
    for _, o in overrides.iterrows():
        t, g, a = str(o["ticker"]).strip().upper(), _theme_group(o["theme"]), str(o["action"]).strip().lower()
        if o.get("problem"):
            res = o["problem"]
        elif t not in known:
            res = "ignored: ticker not in the equity-ETF universe"
        elif g is None:
            res = f"ignored: unknown theme (use one of {', '.join(x[len(THEME_PREFIX):] for x in THEME_GROUPS)})"
        elif a not in ("add", "remove"):
            res = "ignored: action must be add or remove"
        elif a == "add" and (t, g) in auto:
            res = "no effect: the name already matches this theme"
        elif a == "remove" and (t, g) not in auto:
            res = "no effect: the name doesn't match this theme"
        else:
            res = "applied"
        out.append({"ticker": t, "theme": g or o["theme"], "action": a, "result": res})
    return pd.DataFrame(out, columns=["ticker", "theme", "action", "result"])


def ns(mode, group):
    """Session namespace for one peer group. Industry mode keeps the bare group name (unchanged keys);
    Morningstar mode is prefixed so the same name in the two modes never shares ranges or picks."""
    return group if mode == "industry" else f"ms:{group}"


def membership(imap, mode, overrides=None):
    """One row per (ticker, group) for the chosen grouping, themes included, AFTER the team overrides.
    Columns: ticker, name, group, morningstar_category, industry_group, basis, matched_by
    (matched_by = the keyword in the fund name for a theme, 'override: add' for a team addition, '' otherwise)."""
    col = "group" if mode == "industry" else "morningstar_category"
    base = pd.DataFrame({"ticker": imap["ticker"], "name": imap["name"], "group": imap[col].fillna("Uncategorized"),
                         "morningstar_category": imap["morningstar_category"], "industry_group": imap["group"],
                         "basis": imap["basis"] if mode == "industry" else "Morningstar category (yfinance)", "matched_by": ""})
    rows = []
    info = {t: (n, cat, ig) for t, n, cat, ig in zip(imap["ticker"], imap["name"], imap["morningstar_category"], imap["group"])}
    for t, (n, cat, ig) in info.items():
        for g, kw in theme_matches(n):
            rows.append({"ticker": t, "name": n, "group": g, "morningstar_category": cat, "industry_group": ig,
                         "basis": "fund name (theme)", "matched_by": kw})
    th = pd.DataFrame(rows, columns=base.columns)
    if overrides is not None and len(overrides):
        rep = override_report(imap, overrides)
        for _, o in rep[rep["result"] == "applied"].iterrows():
            if o["action"] == "remove":
                th = th[~((th["ticker"] == o["ticker"]) & (th["group"] == o["theme"]))]
            else:
                n, cat, ig = info[o["ticker"]]
                th = pd.concat([th, pd.DataFrame([{"ticker": o["ticker"], "name": n, "group": o["theme"], "morningstar_category": cat,
                                                   "industry_group": ig, "basis": "team override (theme_overrides.csv)",
                                                   "matched_by": "override: add"}])], ignore_index=True)
    return pd.concat([base, th], ignore_index=True)


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
    ov = pd.DataFrame([{"ticker": "ESGV", "theme": "ESG / sustainable", "action": "remove"},
                       {"ticker": "voo", "theme": "Theme: Quality", "action": "add"},
                       {"ticker": "VOO", "theme": "Momentum", "action": "add"},
                       {"ticker": "ZZZZ", "theme": "Quality", "action": "add"}])
    mo = membership(imap, "morningstar", ov)
    rep = override_report(imap, ov)
    mt = membership(pd.DataFrame({"ticker": ["USMV"], "name": ["iShares MSCI USA Min Vol Factor ETF"], "morningstar_category": ["Large Blend"],
                                  "group": ["Broad US market (diversified)"], "basis": ["x"]}), "morningstar")
    ok4 = (g(mo, "ESGV") == ["Large Blend"] and g(mo, "VOO") == sorted(["Large Blend", Q])
           and mo.loc[(mo.ticker == "VOO") & (mo.group == Q), "matched_by"].iloc[0] == "override: add"
           and list(rep["result"].str.split(":").str[0]) == ["applied", "applied", "ignored", "ignored"]
           and mt.loc[mt.group == L, "matched_by"].iloc[0] == "Min Vol")
    check("G4. Theme overrides (add/remove) apply after name matching, bad rows are listed not applied; matched_by keyword stored",
          ok4, f"ESGV {g(mo, 'ESGV')}, VOO {g(mo, 'VOO')}, results {list(rep['result'])}")
    return results
