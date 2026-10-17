"""
"How everything works" page: renders HOW_IT_WORKS.md as tabs, plus a live system-status
tab (which caches exist, how big they are, when they were last refreshed) and a button
that runs every self-test.
"""
import logging
import os
import re
import sqlite3

import pandas as pd
import streamlit as st

log = logging.getLogger(__name__)

GUIDE_PATH = "HOW_IT_WORKS.md"


def _sections(text):
    parts = re.split(r"^## ", text, flags=re.M)
    out = []
    for p in parts:
        if not p.strip():
            continue
        title, _, body = p.partition("\n")
        out.append((title.strip(), body))
    return out


def _count(db, sql):
    if not os.path.exists(db):
        return None
    try:
        conn = sqlite3.connect(db)
        v = conn.execute(sql).fetchone()[0]
        conn.close()
        return v
    except sqlite3.Error as e:
        log.warning("status query failed on %s: %s", db, e)
        return None


def _size(path):
    return f"{os.path.getsize(path) / 1e6:.1f} MB" if os.path.exists(path) else "—"


def _status():
    st.markdown("Live view of what is cached on this machine. Missing items are rebuilt by the **Pull** buttons or the build scripts.")
    rows = [
        ("Stock data (companies cached)", "stock_raw.db", _count("stock_raw.db", "SELECT COUNT(*) FROM raw_stock")),
        ("Stock industries pulled", "stock_raw.db", _count("stock_raw.db", "SELECT COUNT(DISTINCT industry) FROM raw_stock")),
        ("Stock data last refreshed", "stock_raw.db", _count("stock_raw.db", "SELECT MAX(fetched_date) FROM raw_stock")),
        ("ETF universe (all US-listed ETFs labelled)", "etf_raw.db", _count("etf_raw.db", "SELECT COUNT(*) FROM etf_membership")),
        ("ETF funds pulled & scored", "etf_raw.db", _count("etf_raw.db", "SELECT COUNT(*) FROM etf_raw")),
        ("ETF categories pulled", "etf_raw.db", _count("etf_raw.db", "SELECT COUNT(*) FROM etf_meta")),
        ("Holdings with a stock Composite", "holding_scores.db", _count("holding_scores.db", "SELECT COUNT(*) FROM holding_scores")),
        ("Holdings skipped (foreign listing or filer / not tradeable)", "holding_scores.db", _count("holding_scores.db", "SELECT COUNT(*) FROM holding_skips")),
    ]
    df = pd.DataFrame([{"item": a, "file": f, "value": v if v is not None else "not built yet", "size": _size(f)} for a, f, v in rows])
    st.dataframe(df.astype(str), hide_index=True, width="stretch")
    files = [("results/industry_directory.csv", "industry directory"), ("results/etf_category_directory.csv", "ETF category directory"),
             ("results/etf_categories_all.csv", "all ETF categories with keep/remove status"), ("results/backtest_report.txt", "stock backtest report"),
             ("results/industry_shortlist.csv", "your industry shortlist"), ("results/etf_category_shortlist.csv", "your ETF category shortlist"),
             ("results/etf_universe.csv", "equity-ETF universe (Morningstar category per fund; etf_groups.py)"),
             ("results/etf_uncategorized_keep.csv", "funds with no Morningstar category the team keeps"),
             ("results/theme_overrides.csv", "team corrections to the name-based themes"),
             ("results/etf_holdings_override.csv", "optional: number-of-holdings override"), ("config.yaml", "settings (SEC user-agent...)")]
    st.dataframe(pd.DataFrame([{"file": f, "what": w, "present": "yes" if os.path.exists(f) else "no"} for f, w in files]),
                 hide_index=True, width="stretch")
    sec = "sec_cache"
    if os.path.isdir(sec):
        st.caption(f"SEC download cache: {len(os.listdir(sec)):,} files (re-downloaded automatically after 7 days).")

    st.subheader("Run every self-test")
    if st.button("Run all self-tests", key="guide_tests"):
        import etf_scoring
        import scoring2
        for name, fn in (("Stock scoring", scoring2.run_self_tests), ("Equity-ETF scoring", etf_scoring.run_self_tests)):
            res = fn()
            ok = sum(1 for r in res if r["passed"])
            (st.success if ok == len(res) else st.error)(f"{name}: {ok} / {len(res)} passed")
            st.dataframe(pd.DataFrame([{"result": "PASS" if r["passed"] else "FAIL", "test": r["test"], "detail": r["detail"]} for r in res]),
                         hide_index=True, width="stretch")


def render():
    st.title("How everything works")
    if not os.path.exists(GUIDE_PATH):
        st.error(f"{GUIDE_PATH} is missing.")
        return
    secs = _sections(open(GUIDE_PATH, encoding="utf-8").read())
    labels = [re.sub(r"^\d+\.\s*", "", t) for t, _ in secs] + ["System status"]
    tabs = st.tabs(labels)
    for tab, (title, body) in zip(tabs, secs):
        with tab:
            st.subheader(title)
            st.markdown(body)
    with tabs[-1]:
        _status()
