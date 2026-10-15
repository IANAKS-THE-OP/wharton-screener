"""
Headless checks of the REAL app on the cached data (streamlit.testing AppTest; no browser, no network for cached groups).
  ./venv/bin/python3 check_app_flows.py          (about 1-2 minutes)
Stocks: Compare with two industries (Semiconductors + Gold) in both modes - own-mode scores equal each industry page.
ETFs: Morningstar "Large Blend" loads and ranks ESGV; the ESG and low-volatility themes load; ranges never leak between
groups, groupings (industry / Morningstar) or pages; ETF Compare own mode equals the Large Blend page; pooled = each fund once.
"""
import html
import os
import re
import sys
from streamlit.testing.v1 import AppTest
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

at = AppTest.from_file(APP, default_timeout=600); at.run()
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
    t, g, o, f, comp = c[2], c[4], c[5], c[6], c[7]
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
at.sidebar.radio(key="etf2_groupby").set_value("morningstar").run()
at.sidebar.selectbox(key="etf2_mscat").set_value("Large Blend").run()
chk("Morningstar Large Blend loads", not at.exception and "Large Blend" in at.title[0].value, at.title[0].value)
lb = at.dataframe[0].value
chk("ESGV ranked in Large Blend", "ESGV" in set(lb.ticker), f"ESGV rank {int(lb[lb.ticker=='ESGV']['rank'].iloc[0])} of {len(lb)}" if "ESGV" in set(lb.ticker) else "")
lb_scores = page_scores(at)
for th in ["Theme: ESG / sustainable", "Theme: Low / min volatility"]:
    at.sidebar.selectbox(key="etf2_mscat").set_value(th).run()
    t = at.dataframe[0].value
    chk(f"{th} loads", not at.exception and len(t) > 0, f"{len(t)} funds; top 5 {list(t.ticker[:5])}; ESGV in={('ESGV' in set(t.ticker))}, USMV in={('USMV' in set(t.ticker))}, SPLV in={('SPLV' in set(t.ticker))}")
# set a custom range on the Morningstar Large Blend page, then check it doesn't leak into industry mode or the theme
at.sidebar.selectbox(key="etf2_mscat").set_value("Large Blend").run()
k = "etflo|ms:Large Blend|max_drawdown"
chk("Morningstar ranges namespaced", k in at.session_state, k)
at.session_state["etfhi|ms:Large Blend|max_drawdown"] = 5.0  # absurdly strict
at.run()
strict = page_scores(at)
chk("custom range changes Large Blend Fit", any(strict[t][1] != lb_scores[t][1] for t in strict if lb_scores.get(t)))
at.sidebar.selectbox(key="etf2_mscat").set_value("Theme: ESG / sustainable").run()
chk("theme's range untouched by Large Blend range", at.session_state["etfhi|ms:Theme: ESG / sustainable|max_drawdown"] != 5.0)
at.sidebar.radio(key="etf2_groupby").set_value("industry").run()
at.sidebar.selectbox(key="etf2_industry").set_value("Broad US market (diversified)").run()
chk("industry-mode Broad US group has its own range", at.session_state["etfhi|Broad US market (diversified)|max_drawdown"] != 5.0)
at.sidebar.selectbox(key="etf2_industry").set_value("Theme: ESG / sustainable").run()
chk("industry-mode ESG theme range separate from Morningstar-mode", at.session_state["etfhi|Theme: ESG / sustainable|max_drawdown"] != 5.0)
at.sidebar.radio(key="asset_type").set_value("Stocks").run()
at.sidebar.radio(key="asset_type").set_value("ETFs").run()
at.sidebar.radio(key="etf2_groupby").set_value("morningstar").run()
chk("Large Blend custom range survives page switches", at.session_state["etfhi|ms:Large Blend|max_drawdown"] == 5.0 and at.sidebar.selectbox(key="etf2_mscat").value == "Theme: ESG / sustainable")
at.sidebar.selectbox(key="etf2_mscat").set_value("Large Blend").run()
# ETF compare in Morningstar mode: own vs page (with page at Laura -> reset Large Blend first)
at.button(key="etf_btn_laura|ms:Large Blend").click().run()
lb_scores = page_scores(at)
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
fnv = t[t.ticker == "FNV"].iloc[0]
nem = t[t.ticker == "NEM"].iloc[0]
chk("Gold: foreign filer FNV unranked with 'Not scored: files 40-F, not 10-K'",
    fnv["why low data / not scored"] == "Not scored: files 40-F, not 10-K" and str(fnv["rank"]) in ("nan", "None", "<NA>"), str(fnv["why low data / not scored"]))
chk("Gold: NEM (10-K filer) low-data reason lists the missing metrics", "ROIC (EBIT missing)" in nem["why low data / not scored"],
    nem["why low data / not scored"][:160])
at.session_state["s2h|Gold|max_drawdown"] = True  # tick a hard limit on the Gold page
at.run()
at.multiselect(key="stkcmp__sel").set_value(["Semiconductors", "Gold"]).run()
infos = " ".join(x.value for x in at.info) + " " + " ".join(x.value for x in at.warning)
chk("Compare banner lists ranges / weights / hard limits / data date", all(w in infos for w in ("**Ranges:**", "**Weights:** shared with Tune scoring", "**Hard limits:** not applied", "**Data as of:**")))
chk("Compare warns that the ticked hard limit on Gold is ignored", "Ignored here" in infos and "Gold: Max drawdown" in infos)
labels = [b.label for b in at.button]
chk("Compare buttons relabelled", "Reset ranges and weights everywhere" in labels and "Industry average" in labels)
at.button(key="snapbtn|cmp|stkcmp_").click().run()
at.button(key="snapbtn|stk|Gold").click().run()
new = sorted(set(glob.glob("results/snapshots/*")) - before)
import json as _j
js = [_j.load(open(x)) for x in new if x.endswith(".json")]
chk("Save snapshot (Compare + Ranked results) writes CSV + JSON with data date, weights, ranges",
    len(new) == 4 and all(j.get("data_date") and j.get("weights") and j.get("ranges") for j in js), str([os.path.basename(x) for x in new]))
for x in new:
    os.remove(x)
at.session_state["s2h|Gold|max_drawdown"] = False
print("ALL OK" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
