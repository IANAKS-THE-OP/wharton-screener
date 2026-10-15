"""
How ETFs are grouped into peer groups: MORNINGSTAR CATEGORY + THEMES (nothing else).
====================================================================================
Pure functions (no Streamlit, no network), used by etf_page.py and compare.py.

  Universe     results/etf_universe.csv (ticker, name, morningstar_category, has_holdings), built by
               build_universe() from the ETF membership table + cached top-10 holdings:
               equity categories only (etf_data rules), funds under $5 / leveraged / inverse / VIX out,
               single-stock funds (top holding >= 50%) out. Funds with no Morningstar category
               ("Uncategorized") are kept only if listed in results/etf_uncategorized_keep.csv.
  Category     the fund's Morningstar category from yfinance (Large Blend, Large Value, ...).

THEMES are detected from the FUND NAME (yfinance gives no fund description for most ETFs):
ESG / sustainable, low / min volatility, dividend / income, quality, equal weight. A theme is an
EXTRA peer group: a fund keeps its category AND appears in every theme it matches
(ESGV is in "Large Blend" and in "Theme: ESG / sustainable").

There is NO industry grouping for ETFs (the 145-industry tagger is in _retired/). Stock industries are
used only inside holdings quality, where each top-10 holding is scored against its own stock industry.
Peer pools are always the group you picked (the same < 10-peer fallback to all downloaded equity ETFs applies).
"""

import json
import os
import re
import sqlite3

import pandas as pd

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


def ns(group):
    """Session namespace for one peer group (range keys, picks, snapshots)."""
    return f"ms:{group}"


# ------------------------------------------------------------------ universe
UNIVERSE_PATH = "results/etf_universe.csv"
UNCAT_KEEP_PATH = "results/etf_uncategorized_keep.csv"  # ticker column: funds with no Morningstar category the team keeps
SINGLE_STOCK_SHARE = 0.50
# never equity ETFs, even if someone lists them in the keep file
UNCAT_BAD_NAME = re.compile(r"bond|treasur|income|premium|covered|buffer|floor|bitcoin|\bbtc\b|ether|crypto|currency|"
                            r"bullion|trust|municipal|\bmuni|credit|loan|yield|target|maturity|dividend|\bDB\b", re.I)


def load_universe(path=UNIVERSE_PATH):
    """DataFrame (ticker, name, morningstar_category, has_holdings) or None if not built yet."""
    return pd.read_csv(path) if os.path.exists(path) else None


def build_universe(db_path=None, path=UNIVERSE_PATH, keep_path=UNCAT_KEEP_PATH):
    """Offline (no network): etf_membership + cached top-10 holdings (etf_tophold, etf_raw) -> results/etf_universe.csv."""
    import etf_data as ed
    conn = sqlite3.connect(db_path or ed.DB_PATH)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    hold = {s: json.loads(j) for s, j in conn.execute("SELECT symbol, json FROM etf_tophold")} if "etf_tophold" in tables else {}
    for (js,) in conn.execute("SELECT json FROM etf_raw").fetchall():
        r = json.loads(js)
        if r.get("holdings") and r["ticker"] not in hold:
            hold[r["ticker"]] = r["holdings"]
    members = conn.execute("SELECT symbol, category, json FROM etf_membership").fetchall()
    conn.close()
    keep = set(pd.read_csv(keep_path)["ticker"].str.upper()) if os.path.exists(keep_path) else set()
    rows = []
    for sym, cat, js in members:
        q = json.loads(js)
        name = q.get("longName") or q.get("shortName")
        if cat == "Uncategorized":
            if sym not in keep or UNCAT_BAD_NAME.search(name or ""):
                continue
        elif ed.category_exclusion_reason(cat):
            continue
        if ed.fund_exclusion_reason(name, q.get("regularMarketPrice")):
            continue
        h = hold.get(sym) or []
        if h and max(x["weight"] for x in h) >= SINGLE_STOCK_SHARE:
            continue  # (almost) one stock
        rows.append({"ticker": sym, "name": name, "morningstar_category": cat, "has_holdings": bool(h)})
    df = pd.DataFrame(rows).sort_values("ticker").reset_index(drop=True)
    df.to_csv(path, index=False)
    return df


def membership(universe, overrides=None):
    """One row per (ticker, group): the Morningstar category plus every matching theme, AFTER the team overrides.
    Columns: ticker, name, group, morningstar_category, basis, matched_by
    (matched_by = the keyword in the fund name for a theme, 'override: add' for a team addition, '' otherwise)."""
    imap = universe
    base = pd.DataFrame({"ticker": imap["ticker"], "name": imap["name"], "group": imap["morningstar_category"].fillna("Uncategorized"),
                         "morningstar_category": imap["morningstar_category"], "basis": "Morningstar category (yfinance)", "matched_by": ""})
    rows = []
    info = {t: (n, cat) for t, n, cat in zip(imap["ticker"], imap["name"], imap["morningstar_category"])}
    for t, (n, cat) in info.items():
        for g, kw in theme_matches(n):
            rows.append({"ticker": t, "name": n, "group": g, "morningstar_category": cat,
                         "basis": "fund name (theme)", "matched_by": kw})
    th = pd.DataFrame(rows, columns=base.columns)
    if overrides is not None and len(overrides):
        rep = override_report(imap, overrides)
        for _, o in rep[rep["result"] == "applied"].iterrows():
            if o["action"] == "remove":
                th = th[~((th["ticker"] == o["ticker"]) & (th["group"] == o["theme"]))]
            else:
                n, cat = info[o["ticker"]]
                th = pd.concat([th, pd.DataFrame([{"ticker": o["ticker"], "name": n, "group": o["theme"], "morningstar_category": cat,
                                                   "basis": "team override (theme_overrides.csv)",
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
    ok2 = (all(ed.fund_exclusion_reason(n, 50.0) is None and not UNCAT_BAD_NAME.search(n) for n in keep)
           and all(ed.fund_exclusion_reason(n, 50.0) or UNCAT_BAD_NAME.search(n) for n in drop)
           and ed.category_exclusion_reason("Alternative Volatility") and ed.category_exclusion_reason("Large Blend") is None
           and UNCAT_BAD_NAME.search("Invesco DB Energy Fund"))
    check("G2. Low-volatility EQUITY funds pass the volatility exclusion; VIX / volatility-premium products and DB commodity funds are removed", ok2,
          f"kept {len(keep)}, removed {len(drop)}")

    uni = pd.DataFrame({"ticker": ["ESGV", "VOO", "SMH"], "name": ["Vanguard ESG U.S. Stock ETF", "Vanguard S&P 500 ETF", "VanEck Semiconductor ETF"],
                        "morningstar_category": ["Large Blend", "Large Blend", "Technology"]})
    ms = membership(uni)
    g = lambda m, tk: sorted(m.loc[m["ticker"] == tk, "group"])
    ok3 = (g(ms, "ESGV") == sorted(["Large Blend", E]) and g(ms, "SMH") == ["Technology"] and g(ms, "VOO") == ["Large Blend"]
           and set(ms.columns) == {"ticker", "name", "group", "morningstar_category", "basis", "matched_by"}
           and ns(E) == "ms:" + E)
    check("G3. Grouping = Morningstar category + themes only (no industry column): ESGV in Large Blend AND the ESG theme; SMH in Technology", ok3,
          f"ESGV {g(ms, 'ESGV')}, SMH {g(ms, 'SMH')}, columns {sorted(ms.columns)}")
    ov = pd.DataFrame([{"ticker": "ESGV", "theme": "ESG / sustainable", "action": "remove"},
                       {"ticker": "voo", "theme": "Theme: Quality", "action": "add"},
                       {"ticker": "VOO", "theme": "Momentum", "action": "add"},
                       {"ticker": "ZZZZ", "theme": "Quality", "action": "add"}])
    mo = membership(uni, ov)
    rep = override_report(uni, ov)
    mt = membership(pd.DataFrame({"ticker": ["USMV"], "name": ["iShares MSCI USA Min Vol Factor ETF"], "morningstar_category": ["Large Blend"]}))
    ok4 = (g(mo, "ESGV") == ["Large Blend"] and g(mo, "VOO") == sorted(["Large Blend", Q])
           and mo.loc[(mo.ticker == "VOO") & (mo.group == Q), "matched_by"].iloc[0] == "override: add"
           and list(rep["result"].str.split(":").str[0]) == ["applied", "applied", "ignored", "ignored"]
           and mt.loc[mt.group == L, "matched_by"].iloc[0] == "Min Vol")
    check("G4. Theme overrides (add/remove) apply after name matching, bad rows are listed not applied; matched_by keyword stored",
          ok4, f"ESGV {g(mo, 'ESGV')}, VOO {g(mo, 'VOO')}, results {list(rep['result'])}")
    return results


if __name__ == "__main__":
    u = build_universe()
    print(f"wrote {UNIVERSE_PATH}: {len(u)} equity ETFs in {u['morningstar_category'].nunique()} Morningstar categories "
          f"({int((u['morningstar_category'] == 'Uncategorized').sum())} uncategorized kept from {UNCAT_KEEP_PATH})")
