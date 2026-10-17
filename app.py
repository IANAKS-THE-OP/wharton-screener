"""
Wharton Investment Screener
===========================
Run it with:

    ./venv/bin/streamlit run app.py

Two asset types plus a guide:
  Stocks -> stock_page.py (scoring2.py, 14 metrics)
  ETFs   -> etf_page.py   (etf_scoring.py, 10 metrics; equity ETFs only)
  How everything works -> guide_page.py (renders HOW_IT_WORKS.md)

Default Laura limits and weights on both pages are Laura Gao's standards (laura.py).
  Sidebar : pick the asset type, the history window (3 / 5 / 10 yrs), then the stock industry or the ETF Morningstar category / theme.
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
# Laura limits, weights, penalty strength, history window and hard limits when you switch pages or industries.
# (None of these widgets has a value= argument, so re-saving them never conflicts with a default.)
PERSIST_PREFIXES = ("industry_choice", "etf2_mscat", "etf2_laura_hard", "hist_window",
                    "stkcmp_", "etfcmp_",
                    "stklim|", "stkext|", "etflim|", "etfext|",
                    "s2w|", "etf2w|", "s2_penalty", "etf2_penalty", "s2h|", "etf2h|")
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

# ONE history window for both pages: downside deviation, max drawdown (and the ETF long-run return) are measured for
# every stock / fund over these same years, ending on the same date.
st.session_state.setdefault("hist_window", 5)
st.sidebar.selectbox("History window", [3, 5, 10], key="hist_window", format_func=lambda y: f"{y} years",
                     help="Downside deviation, max drawdown and (ETFs) long-run return use this window for every stock / fund, "
                          "ending on the same date. Less history than the window = 'too new for this window': those metrics are "
                          "excluded and the name is listed after the fully scored ones. Default 5 years.")
if st.session_state["hist_window"] == 3:
    st.sidebar.caption("3 years starts in late 2023 and excludes the 2022 decline, so risk numbers look better than over a full market cycle.")

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
    kind, group = "etf", "ETFs"  # equity ETFs (Morningstar category + themes): everything is built inside etf_page.py

def render_directory():
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
    etf_page.render()
