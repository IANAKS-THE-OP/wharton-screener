"""
Wharton Investment Screener
===========================
Run it with:

    ./venv/bin/streamlit run app.py

Two asset types plus a guide:
  Stocks -> stock_page.py (scoring2.py, 14 metrics)
  ETFs   -> etf_page.py   (etf_scoring.py, 10 metrics; equity ETFs only)
  How everything works -> guide_page.py (renders HOW_IT_WORKS.md)

Default ranges and weights on both pages are Laura Gao's standards (laura.py).
  Sidebar : pick the asset type and the industry.
  Tabs    : Ranked results | Tune scoring | Directory | How scoring works
"""

import os

import pandas as pd
import streamlit as st

import industry_universe as iu
import stock_data as sd

DIRECTORY_PATH = "results/industry_directory.csv"
SHORTLIST_PATH = "results/industry_shortlist.csv"

st.set_page_config(page_title="Wharton Investment Screener", layout="wide")

# Streamlit deletes a widget's value whenever that widget isn't drawn in a run (e.g. the stock industry
# while the ETF page is open). Re-saving these keys at the top of every run keeps the industry picks,
# comfort ranges, weights, blend and hard limits when you switch pages or industries. (None of these
# widgets has a value= argument, so re-saving them never conflicts with a default.)
PERSIST_PREFIXES = ("industry_choice", "etf2_industry", "etf2_mscat", "etf2_groupby", "etf2_only_with", "etf2_laura_hard",
                    "stkcmp_", "etfcmp_",
                    "stklo|", "stkhi|", "stkext|", "etflo|", "etfhi|", "etfext|",
                    "s2w|", "etf2w|", "s2_blend", "etf2_blend", "s2h|", "etf2h|")
for _k in [k for k in st.session_state.keys() if str(k).startswith(PERSIST_PREFIXES)]:
    st.session_state[_k] = st.session_state[_k]


# ========================================================================
# Sidebar: asset type + group
# ========================================================================

st.sidebar.title("Wharton Screener")
st.sidebar.caption(
    "Research tool only. It ranks candidates; it does not predict returns. "
    "Humans make every final buy/sell call."
)
asset_type = st.sidebar.radio("Asset type", ["Stocks", "ETFs", "How everything works"], key="asset_type")
if asset_type == "How everything works":
    import guide_page
    guide_page.render()
    st.stop()

if asset_type == "Stocks":
    # ONE widget on purpose: click it and type to filter (e.g. "semi"). An
    # earlier separate search box left stale text behind that silently
    # shrank this list, which looked like "can't change industries".
    _counts = {}
    if os.path.exists(DIRECTORY_PATH):
        _d = pd.read_csv(DIRECTORY_PATH)
        _counts = dict(zip(_d["industry"], _d["tradeable"]))
    group = st.sidebar.selectbox(
        f"Industry (all {len(iu.VALID_INDUSTRIES)} - click and type to search)", iu.VALID_INDUSTRIES,
        key="industry_choice",
        format_func=lambda i: f"{i}  ({_counts[i]})" if i in _counts else i,
    )
    kind = "stock"
else:
    kind, group = "etf", "ETFs"  # equity ETFs: everything is built inside etf_page.py

def render_etf_industry_directory():
    import etf_data, etf_industry as ei
    imap = ei.load_map()
    if imap is None:
        st.warning("Run `./venv/bin/python3 etf_industry.py` to build the ETF industry map.")
        return
    st.caption(
        "Every stock industry with the number of equity ETFs tagged to it (sector funds by a clear industry word in their name, checked "
        "against their top-10 holdings, or 80%+ of the top 10 in one industry; other funds need 60%+ AND a matching name). "
        "The entries at the bottom hold diversified funds. "
        "Tick **shortlist** for the groups the team wants. Rebuild with `./venv/bin/python3 etf_industry.py`."
    )
    dd = pd.read_csv(DIRECTORY_PATH) if os.path.exists(DIRECTORY_PATH) else pd.DataFrame(columns=["industry", "sector"])
    sector_of = dict(zip(dd["industry"], dd["sector"]))
    got = etf_data.load_all_tickers()
    rows = []
    for g, x in imap.groupby("group"):
        rows.append({"industry / group": g, "sector": sector_of.get(g, "(diversified)"), "etfs": len(x),
                     "downloaded": int(x["ticker"].isin(got).sum()), "example funds": ", ".join(x["ticker"].head(6))})
    for i in dd["industry"]:
        if i not in set(imap["group"]):
            rows.append({"industry / group": i, "sector": sector_of.get(i), "etfs": 0, "downloaded": 0, "example funds": ""})
    t = pd.DataFrame(rows).sort_values(["sector", "etfs"], ascending=[True, False])
    SL = "results/etf_industry_shortlist.csv"
    sl = set(pd.read_csv(SL)["group"]) if os.path.exists(SL) else set()
    t.insert(0, "shortlist", t["industry / group"].isin(sl))
    f1, f2 = st.columns([2, 1])
    q = f1.text_input("Search name", key="etf_dir_q")
    hide0 = f2.checkbox("Hide industries with no ETFs", value=True, key="etf_dir_hide0")
    view = t[t["etfs"] > 0] if hide0 else t
    if q:
        view = view[view["industry / group"].str.contains(q, case=False, regex=False)]
    ed_ = st.data_editor(view, hide_index=True, width="stretch", key="etf_ind_dir_editor",
                         disabled=[c for c in view.columns if c != "shortlist"],
                         column_config={"shortlist": st.column_config.CheckboxColumn()})
    st.caption(f"{int((t['etfs'] > 0).sum())} groups have ETFs · {int(t['etfs'].sum()):,} equity ETFs in total")
    if st.button("Save shortlist", key="etf_ind_dir_save"):
        keep = (sl - set(view["industry / group"])) | set(ed_.loc[ed_["shortlist"], "industry / group"])
        pd.DataFrame({"group": sorted(keep)}).to_csv(SL, index=False)
        st.success(f"Saved {len(keep)} groups to {SL}")
    st.download_button("Download every ETF with its industry tag (CSV)", imap.to_csv(index=False),
                       file_name="etf_industry_map.csv", mime="text/csv")


def render_directory():
    if kind == "etf":
        return render_etf_industry_directory()
    st.caption(
        "Every industry yfinance's screener knows, with how many stocks are actually tradeable for us. "
        "Tick **shortlist** for the ones the team wants, then save. Rebuild this table with "
        "`./venv/bin/python3 build_industry_directory.py`."
    )
    if not os.path.exists(DIRECTORY_PATH):
        st.warning("results/industry_directory.csv doesn't exist yet - run `build_industry_directory.py`.")
    else:
        d = pd.read_csv(DIRECTORY_PATH)
        shortlisted = set()
        if os.path.exists(SHORTLIST_PATH):
            shortlisted = set(pd.read_csv(SHORTLIST_PATH)["industry"])
        d.insert(0, "shortlist", d["industry"].isin(shortlisted))
        d["cached"] = d["industry"].isin(sd.cached_industries())

        f1, f2, f3 = st.columns([2, 2, 1])
        sectors = sorted(d["sector"].dropna().unique())
        sec_pick = f1.multiselect("Sector", sectors, key="dir_sector")
        name_q = f2.text_input("Search name", key="dir_search")
        min_n = f3.number_input("Min tradeable", 0, 500, 0, key="dir_min")
        view = d.copy()
        if sec_pick:
            view = view[view["sector"].isin(sec_pick)]
        if name_q:
            view = view[view["industry"].str.contains(name_q, case=False, regex=False)]
        view = view[view["tradeable"] >= min_n]

        edited = st.data_editor(
            view, hide_index=True, width="stretch", key="dir_editor",
            disabled=[c for c in view.columns if c != "shortlist"],
            column_config={
                "shortlist": st.column_config.CheckboxColumn(),
                "total_mcap_b": st.column_config.NumberColumn("total mkt cap ($B)", format="%.0f"),
                "median_mcap_b": st.column_config.NumberColumn("median mkt cap ($B)", format="%.1f"),
            },
        )
        st.caption(f"{len(view)} of {len(d)} industries shown · {int(view['tradeable'].sum()):,} tradeable stocks in view")
        if st.button("Save shortlist", key="dir_save"):
            chosen_now = set(edited.loc[edited["shortlist"], "industry"])
            # keep earlier picks that are hidden by the current filter
            keep = (shortlisted - set(view["industry"])) | chosen_now
            os.makedirs("results", exist_ok=True)
            pd.DataFrame({"industry": sorted(keep)}).to_csv(SHORTLIST_PATH, index=False)
            st.success(f"Saved {len(keep)} industries to {SHORTLIST_PATH}")
        st.download_button("Download full directory (CSV)", d.drop(columns=["shortlist", "cached"]).to_csv(index=False),
                           file_name="industry_directory.csv", mime="text/csv")


# ========================================================================
# Route to the page
# ========================================================================

if kind == "stock":
    import stock_page
    st.title(f"Stocks: {group}")
    stock_page.render(group, render_directory)
else:
    import etf_page
    etf_page.render(render_directory)
