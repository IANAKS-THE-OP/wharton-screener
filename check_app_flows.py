"""
Headless checks of the REAL app on the cached data (streamlit.testing AppTest; no browser, no network for cached groups).
  ./venv/bin/python3 check_app_flows.py          (about 1-2 minutes)
Stocks: Compare with two industries (Semiconductors + Gold) in both modes - own-mode scores equal each industry page.
ETFs: grouping is Morningstar category + themes only (no industry picker); "Large Blend" loads with USMV, ESMV and ESGV,
tier, Laura penalty and why-not-higher columns; USMV / ESMV / ESGV are in their themes; Find-a-fund works without industries;
Laura limits never leak between groups or pages; ETF Compare own mode equals the Large Blend page; pooled = each fund once.
History window dropdown (3 / 5 / 10, default 5, 3-yr caption); penalty strength k = 0 -> Overall = Composite; foreign filers listed
separately, never ranked.
"""
import html
import os
import re
import sys
from streamlit.testing.v1 import AppTest

import etf_scoring as es
import scoring2 as sc

APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")

def cmp_table(at):
    els = at.get("html")
    body = [e.proto.body for e in els if "cmpwrap" in e.proto.body]
    assert body, "no compare table"
    rows = re.findall(r"<tr>(.*?)</tr>", body[-1], re.S)
    out = []
    for r in rows[1:]:
        cells = [html.unescape(re.sub(r"<.*?>", "", c)) for c in re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)]
        out.append(cells)
    return out

def page_scores(at):
    df = at.dataframe[0].value
    import math
    r = lambda v: None if v is None or (isinstance(v, float) and math.isnan(v)) else round(v, 9)
    return {t: (r(o), r(f), r(c)) for t, o, f, c in zip(df.ticker, df.overall, df.fit, df.composite)}

ok = True
def chk(name, cond, detail=""):
    global ok; ok &= bool(cond); print(("PASS " if cond else "FAIL ") + name, detail)


def cat_table():
    """The six-category weight table on Tune scoring (range_ui.category_table) -> DataFrame or None."""
    return next((d.value for d in at.dataframe if "Stocks: growth driver" in d.value.columns), None)


def badges():
    """-> (number of 'Laura standards (default)' badges, number of 'Custom settings' badges) on the page."""
    v = [m.value for m in at.markdown]
    return (sum(x.startswith(":green-badge[") and "Laura standards (default)" in x for x in v),
            sum(x.startswith(":orange-badge[") and "Custom settings" in x for x in v))


def sens_table():
    return next((d.value for d in at.dataframe if any(str(c).startswith("k = 0.4") for c in d.value.columns)), None)


def cat_ok(df, page):
    stk = [sc.LAURA_CATEGORY_TOTALS[c] for c in sc.CATEGORIES] + [100]
    etf = [es.LAURA_CATEGORY_TOTALS[c] for c in sc.CATEGORIES] + [100]
    return (df is not None and list(df["Category"]) == ["Risk", "Cost", "Quality", "Growth", "Safety", "Income", "Total"]
            and list(df["Stocks: growth driver"]) == stk and list(df["ETFs: stability core"]) == etf
            and list(df[f"{page} now"]) == (stk if page == "Stocks" else etf))


def sections():
    """Tune scoring section headers ('#### Risk — 22') and the question caption under each."""
    heads = [m.value[5:] for m in at.markdown if m.value.startswith("#### ")]
    return heads, [c.value for c in at.caption if c.value in sc.CATEGORY_QUESTION.values()]


def sections_ok(totals):
    heads, qs = sections()
    return (heads == [f"{sc.CATEGORY_LABEL[c]} — {totals[c]:g}" for c in sc.CATEGORIES]
            and qs == [sc.CATEGORY_QUESTION[c] for c in sc.CATEGORIES])


at = AppTest.from_file(APP, default_timeout=600); at.run()
hw = at.sidebar.selectbox(key="hist_window")
chk("History window dropdown: 3 / 5 / 10 years, default 5, no 3-yr caption at 5", list(hw.options) == ["3 years", "5 years", "10 years"] and hw.value == 5
    and not any("excludes the 2022 decline" in c.value for c in at.sidebar.caption), str(hw.options))
at.sidebar.selectbox(key="industry_choice").set_value("Semiconductors").run()
semi5 = at.dataframe[0].value
chk("Stocks table has Laura penalty + why not higher next to Fit / Composite", {"Laura penalty", "why not higher", "fit", "composite"} <= set(semi5.columns)
    and list(semi5.columns).index("Laura penalty") == list(semi5.columns).index("fit") + 1, str(list(semi5.columns)[:9]))
st0 = sens_table()
chk("Stocks first load: 'Laura standards (default)' badge on Tune scoring, Ranked results and Compare (no Custom badge)", badges() == (3, 0),
    badges())
chk("Stocks: penalty sensitivity table (top 10 at k = 0.2 / 0.4 / 0.6) under the ranked results", st0 is not None and len(st0) == 10
    and list(st0.columns) == ["rank", "k = 0.2", "k = 0.4 (default) ← now", "k = 0.6"], None if st0 is None else list(st0.columns))
at.session_state["s2w|roic"] = 15.0
at.run()
custom = badges()
at.button(key="s2_badge|res|Semiconductors").click().run()
chk("Changing a weight shows 'Custom settings'; its Reset button restores Laura weights and the default badge",
    custom == (0, 3) and at.session_state["s2w|roic"] == sc.DEFAULT_WEIGHTS["roic"] and badges() == (3, 0), f"{custom} -> {badges()}")
import laura  # noqa: E402
at.session_state["s2w|roic"], at.session_state["s2_penalty"] = 15.0, 0.9
at.session_state[laura.VERSION_KEY] = laura.LAURA_VERSION - 1  # a session saved under the previous defaults
at.run()
chk("Older weights-version in the session: weights and k reset to Laura standards on the next run",
    at.session_state["s2w|roic"] == sc.DEFAULT_WEIGHTS["roic"] and at.session_state["s2_penalty"] == sc.DEFAULT_PENALTY
    and at.session_state[laura.VERSION_KEY] == laura.LAURA_VERSION and badges() == (3, 0), badges())
chk(f"Stocks Tune scoring: side-by-side category table, stocks {sc.category_text(sep='/')} vs ETFs {sc.category_text(es.LAURA_CATEGORY_TOTALS, '/')}", cat_ok(cat_table(), "Stocks"),
    "" if cat_table() is None else cat_table().to_dict("list"))
chk("Stocks Tune scoring: six sections Risk, Cost, Quality, Growth, Safety, Income with live totals + Laura's question", sections_ok(sc.LAURA_CATEGORY_TOTALS),
    sections()[0])
at.session_state["s2w|roic"] = 10.0
at.run()
chk("Stocks section header follows the weight slider (ROIC 8 → 10: Quality — 22)", sections()[0][2] == "Quality — 22", sections()[0][2])
at.session_state["s2w|roic"] = float(sc.DEFAULT_WEIGHTS["roic"])
at.run()
pen_ok = all(abs(o - max(0, c - 0.4 * (100 - f))) < 1e-6 for o, c, f in zip(semi5.overall, semi5.composite, semi5.fit) if o == o and f == f)
chk("Stocks: Overall = max(0, Composite − 0.4 × (100 − Fit)) on every row", pen_ok)
at.sidebar.selectbox(key="hist_window").set_value(3).run()
chk("3-yr window: caption about the 2022 decline, page runs", not at.exception and any("excludes the 2022 decline" in c.value for c in at.sidebar.caption))
at.sidebar.selectbox(key="hist_window").set_value(10).run()
semi10 = at.dataframe[0].value
chk("10-yr window runs and marks more names too new than 5-yr", not at.exception and semi10["flags"].str.contains("too new").sum() > semi5["flags"].str.contains("too new").sum(),
    f"too new: 5-yr {semi5['flags'].str.contains('too new').sum()}, 10-yr {semi10['flags'].str.contains('too new').sum()}")
at.sidebar.selectbox(key="hist_window").set_value(5).run()
at.slider(key="s2_penalty").set_value(0.0).run()
k0 = at.dataframe[0].value
chk("k = 0 → Overall = Composite for every scored company", all(abs(o - c) < 1e-9 for o, c in zip(k0.overall, k0.composite) if o == o))
at.button(key="stk_btn_laura|Semiconductors").click().run()
chk("Laura standards resets penalty strength to 0.4", abs(at.session_state["s2_penalty"] - 0.4) < 1e-12)
inds = ["Semiconductors", "Gold"]
pages = {}
for g in inds:
    at.sidebar.selectbox(key="industry_choice").set_value(g).run()
    pages[g] = page_scores(at)
at.multiselect(key="stkcmp__sel").set_value(inds).run()
chk("stock compare no exception", not at.exception, [e.value for e in at.exception][:1])
own = cmp_table(at)
# columns: rank, #in, ticker, name, industry, overall, fit, composite, ...
mism = []
for c in own:
    t, g, o, comp, f = c[2], c[4], c[5], c[6], c[7]
    po, pf, pc = pages[g][t]
    if o and po is not None and (abs(float(o) - round(po)) > 0.51 or abs(float(f) - round(pf)) > 0.51 or abs(float(comp) - round(pc)) > 0.51):
        mism.append((t, g, o, po))
chk("own mode: rows = sum of both industries", len(own) == sum(len(p) for p in pages.values()), f"{len(own)} rows")
chk("own mode: every name's Overall/Fit/Composite = its industry page (rounded table cells)", not mism, str(mism[:3]))
at.radio(key="stkcmp__mode").set_value("pooled").run()
chk("pooled mode no exception", not at.exception, [e.value for e in at.exception][:1])
pooled = cmp_table(at)
diff = sum(1 for c in pooled if c[5] and pages[c[4]][c[2]][0] is not None and abs(float(c[5]) - round(pages[c[4]][c[2]][0])) > 0.51)
chk("pooled mode: same names, scores recomputed (some differ)", len(pooled) == len(own) and diff > 0, f"{diff} of {len(pooled)} Overall changed")
# switching back to a page must leave its ranges untouched
at.sidebar.selectbox(key="industry_choice").set_value("Gold").run()
chk("Gold page unchanged after pooled compare", page_scores(at) == pages["Gold"])


# ======================= ETFs
at.sidebar.radio(key="asset_type").set_value("ETFs").run()
chk("ETF page no exception", not at.exception, [e.value for e in at.exception][:1])
chk("ETF first load: 'Laura standards (default)' badge on Tune scoring, Ranked results and Compare; penalty sensitivity table",
    badges() == (3, 0) and sens_table() is not None and len(sens_table()) == 10, badges())
chk(f"ETF Tune scoring: side-by-side category table, both models' totals (each sums to 100)", cat_ok(cat_table(), "ETFs"),
    "" if cat_table() is None else cat_table().to_dict("list"))
chk("ETF Tune scoring: the same six sections in the same order (ETF totals) + Laura's question; number of holdings under Quality, info only",
    sections_ok(es.LAURA_CATEGORY_TOTALS) and any(m.value.startswith("**Number of holdings**") and "info only (weight 0)" in m.value for m in at.markdown),
    sections()[0])
chk("no industry grouping widgets on the ETF page", not any(w.key in ("etf2_groupby", "etf2_industry", "etf2_only_with") for w in list(at.sidebar.radio) + list(at.sidebar.selectbox) + list(at.sidebar.checkbox)),
    [w.key for w in at.sidebar.selectbox])
chk("ETF picker lists Morningstar categories + themes only", all(not o.startswith(("Whole sector", "Broad US market", "International / global")) for o in at.sidebar.selectbox(key="etf2_mscat").options))
at.sidebar.selectbox(key="etf2_mscat").set_value("Large Blend").run()
chk("Morningstar Large Blend loads", not at.exception and "Large Blend" in at.title[0].value, at.title[0].value)
lb = at.dataframe[0].value
chk("USMV, ESMV, ESGV listed in Large Blend", {"USMV", "ESMV", "ESGV"} <= set(lb.ticker),
    "; ".join(f"{t} rank {int(lb[lb.ticker == t]['rank'].iloc[0])} {lb[lb.ticker == t]['tier'].iloc[0]}" for t in ("USMV", "ESMV", "ESGV") if t in set(lb.ticker)) + f" of {len(lb)}")
chk("tier + Laura penalty + why-not-higher columns", {"tier", "why not higher", "Laura penalty"} <= set(lb.columns) and lb["tier"].notna().all())
chk("ETF: no history-window radio on the page (the sidebar dropdown is shared)", not any(w.key == "etf2_window" for w in at.sidebar.radio))
chk("ESMV (4.9 yrs) is too new for the 5-yr window and listed after fully scored funds",
    "too new" in lb[lb.ticker == "ESMV"]["flags"].iloc[0] and int(lb[lb.ticker == "ESMV"]["rank"].iloc[0]) > max(int(x) for x, f in zip(lb["rank"], lb["flags"]) if "too new" not in f and "low data" not in f))
lb_scores = page_scores(at)
for th, need in [("Theme: ESG / sustainable", {"ESGV", "ESMV"}), ("Theme: Low / min volatility", {"USMV", "ESMV"})]:
    at.sidebar.selectbox(key="etf2_mscat").set_value(th).run()
    t = at.dataframe[0].value
    chk(f"{th} loads with {sorted(need)}", not at.exception and need <= set(t.ticker), f"{len(t)} funds; top 5 {list(t.ticker[:5])}")
at.sidebar.text_input(key="etf2_lookup").set_value("USMV").run()
found = " ".join(m.value for m in at.sidebar.markdown)
chk("Find a fund: USMV → Morningstar Large Blend + low-vol theme, no industry", "Morningstar: *Large Blend*" in found and "Low / min volatility" in found and "Industry" not in found, found[-160:])
at.sidebar.text_input(key="etf2_lookup").set_value("min vol").run()
found = " ".join(m.value for m in at.sidebar.markdown)
chk("Find a fund by name ('min vol') lists USMV", "**USMV**" in found and not at.exception)
at.sidebar.text_input(key="etf2_lookup").set_value("").run()
# set a custom range on the Morningstar Large Blend page, then check it doesn't leak into industry mode or the theme
at.sidebar.selectbox(key="etf2_mscat").set_value("Large Blend").run()
k = "etflim|ms:Large Blend|max_drawdown"
chk("Morningstar Laura limits namespaced", k in at.session_state, k)
at.session_state["etflim|ms:Large Blend|max_drawdown"] = 5.0  # absurdly strict ceiling
at.run()
strict = page_scores(at)
chk("custom limit changes Large Blend Fit", any(strict[t][1] != lb_scores[t][1] for t in strict if lb_scores.get(t)))
at.sidebar.selectbox(key="etf2_mscat").set_value("Theme: ESG / sustainable").run()
chk("theme's limit untouched by Large Blend limit", at.session_state["etflim|ms:Theme: ESG / sustainable|max_drawdown"] != 5.0)
at.sidebar.selectbox(key="etf2_mscat").set_value("Large Value").run()
chk("Large Value has its own limit", at.session_state["etflim|ms:Large Value|max_drawdown"] != 5.0)
at.sidebar.selectbox(key="etf2_mscat").set_value("Theme: ESG / sustainable").run()
at.sidebar.radio(key="asset_type").set_value("Stocks").run()
at.sidebar.radio(key="asset_type").set_value("ETFs").run()
chk("Large Blend custom limit survives page switches", at.session_state["etflim|ms:Large Blend|max_drawdown"] == 5.0 and at.sidebar.selectbox(key="etf2_mscat").value == "Theme: ESG / sustainable")
at.sidebar.selectbox(key="etf2_mscat").set_value("Large Blend").run()
# ETF compare in Morningstar mode: own vs page (with page at Laura -> reset Large Blend first)
at.button(key="etf_btn_laura|ms:Large Blend").click().run()
lb_scores = page_scores(at)
at.toggle(key="etf2_diag|Large Blend").set_value(True).run()
chk("history-window diagnostic runs", not at.exception and any("move more than 10 ranks" in m.value for m in at.markdown),
    next((m.value for m in at.markdown if "move more than 10 ranks" in m.value), ""))
at.toggle(key="etf2_diag|Large Blend").set_value(False).run()
at.multiselect(key="etfcmp_|morningstar_sel").set_value(["Large Blend", "Theme: Low / min volatility"]).run()
chk("ETF compare no exception", not at.exception, [e.value for e in at.exception][:1])
rows = cmp_table(at)
mm = [(c[2], c[5], lb_scores[c[2]][0]) for c in rows if c[4] == "Large Blend" and c[5] and lb_scores[c[2]][0] is not None and abs(float(c[5]) - round(lb_scores[c[2]][0])) > 0.51]
chk("ETF own mode: Large Blend rows equal the Large Blend page", not mm and sum(c[4] == "Large Blend" for c in rows) == len(lb_scores), f"{len(rows)} rows, mismatches {mm[:3]}")
chk("USMV appears twice (Large Blend + low-vol theme)", sum(c[2] == "USMV" for c in rows) == 2)
at.radio(key="etfcmp_|morningstar_mode").set_value("pooled").run()
rows = cmp_table(at)
chk("ETF pooled: each fund once", len(rows) == len({c[2] for c in rows}) and not at.exception, f"{len(rows)} rows")
# ======================= follow-ups: banner, buttons, not scored / low data, snapshots
import glob
before = set(glob.glob("results/snapshots/*"))
at.sidebar.radio(key="asset_type").set_value("Stocks").run()
at.sidebar.selectbox(key="industry_choice").set_value("Gold").run()
t = at.dataframe[0].value
nem = t[t.ticker == "NEM"].iloc[0]
fl = [e for e in at.expander if e.label.startswith("Not scored: foreign filer")]
ftab = fl[0].dataframe[0].value if fl and fl[0].dataframe else None
chk("Gold: foreign filer FNV not ranked, listed under 'Not scored: foreign filer' with its 40-F reason",
    "FNV" not in set(t.ticker) and ftab is not None and "FNV" in set(ftab.ticker)
    and "files 40-F" in ftab[ftab.ticker == "FNV"]["reason"].iloc[0], fl[0].label if fl else "no expander")
chk("Gold: NEM (10-K filer) low-data reason lists the missing metrics", "ROIC (EBIT missing)" in nem["why low data / not scored / too new"],
    nem["why low data / not scored / too new"][:160])
at.session_state["s2h|Gold|max_drawdown"] = True  # tick a hard limit on the Gold page
at.run()
at.multiselect(key="stkcmp__sel").set_value(["Semiconductors", "Gold"]).run()
infos = " ".join(x.value for x in at.info) + " " + " ".join(x.value for x in at.warning)
chk("Compare banner lists limits / weights + penalty / hard limits / data date", all(w in infos for w in ("**Laura limits:**", "**Weights:** shared with Tune scoring", "**Laura penalty strength** k = 0.40", "**Hard limits:** not applied", "**Data as of:**")))
chk("Compare lists foreign filers separately", any(e.label.startswith("Not scored: foreign filer") for e in at.expander))
chk("Compare warns that the ticked hard limit on Gold is ignored", "Ignored here" in infos and "Gold: Max drawdown" in infos)
labels = [b.label for b in at.button]
chk("Compare buttons relabelled", "Reset limits and weights everywhere" in labels and "Industry average" in labels)
at.button(key="snapbtn|cmp|stkcmp_").click().run()
at.button(key="snapbtn|stk|Gold").click().run()
new = sorted(set(glob.glob("results/snapshots/*")) - before)
import json as _j
js = [_j.load(open(x)) for x in new if x.endswith(".json")]
chk("Save snapshot (Compare + Ranked results) writes CSV + JSON with data date, weights, Laura limits",
    len(new) == 4 and all(j.get("data_date") and j.get("weights") and j.get("limits") for j in js), str([os.path.basename(x) for x in new]))
for x in new:
    os.remove(x)
at.session_state["s2h|Gold|max_drawdown"] = False
print("ALL OK" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
