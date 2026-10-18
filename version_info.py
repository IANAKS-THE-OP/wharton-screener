"""
Which version is live, and does its data match the code?
  sidebar_footer()  : git commit + data date at the bottom of the sidebar on every page (so the hosted site can be checked
                      against `git log -1` - see DEPLOY.md).
  data_banner()     : red banner when the committed caches lack the fields this code needs (e.g. px.mdd_5), instead of
                      the scorer quietly excluding those metrics.
Both are cheap (one git call + two SQLite queries) and cached for 10 minutes.
"""

import os
import sqlite3
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))

# (db file, table, px fields every row's JSON must have). The scorers read mdd_<window> / cagr_<window> for the
# 3 / 5 / 10-yr history window; caches from before the 10-yr histories only had a 3-yr 'mdd'.
REQUIRED = [
    ("stock_raw.db", "raw_stock", ["years", "mdd_3", "mdd_5", "mdd_10"]),
    ("etf_raw.db", "etf_raw", ["years", "mdd_3", "mdd_5", "mdd_10", "cagr_3", "cagr_5", "cagr_10"]),
]
# A handful of warrants / units with no price history are still stored in the old format; anything beyond this
# share of rows means the cache is older than the code.
MAX_MISSING_SHARE = 0.01


def git_commit():
    """-> (short hash, commit date YYYY-MM-DD) of the running checkout, or ("unknown", "") if git isn't available."""
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%h %cs"], cwd=HERE, capture_output=True, text=True, timeout=5)
        h, d = out.stdout.split()
        return h, d
    except Exception:  # no git binary / no .git folder / unexpected output
        return os.environ.get("GIT_COMMIT", "unknown")[:7], ""


def data_check():
    """-> {"data_date": newest fetched_date across the caches, "problems": [text, ...]} (empty problems = data matches code)."""
    problems, dates = [], []
    for fname, table, fields in REQUIRED:
        path = os.path.join(HERE, fname)
        if not os.path.exists(path):
            problems.append(f"{fname} is missing")
            continue
        try:
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as c:
                missing_expr = " OR ".join(f"json_type(json, '$.px.{f}') IS NULL" for f in fields)
                n, bad, newest = c.execute(f"SELECT COUNT(*), SUM({missing_expr}), MAX(fetched_date) FROM {table}").fetchone()
        except sqlite3.Error as e:
            problems.append(f"{fname}: {e}")
            continue
        bad = bad or 0
        if newest:
            dates.append(newest)
        if not n:
            problems.append(f"{fname} has no rows")
        elif bad / n > MAX_MISSING_SHARE:
            problems.append(f"{fname}: {bad:,} of {n:,} rows lack px.{{{', '.join(fields)}}} (cache built by an older version)")
    return {"data_date": max(dates) if dates else "unknown", "problems": problems}


def _cached(fn):
    import streamlit as st
    return st.cache_data(ttl=600, show_spinner=False)(fn)


def data_banner():
    import streamlit as st
    chk = _cached(data_check)()
    if chk["problems"]:
        st.error("**Data is out of date for this code version.** Max drawdown / downside deviation / long-run return "
                 "would be excluded for most names, so rankings are not trustworthy. Refresh the caches locally, commit "
                 "them and redeploy (DEPLOY.md).\n\n" + "\n".join(f"- {p}" for p in chk["problems"]))


def sidebar_footer():
    import streamlit as st
    h, d = _cached(git_commit)()
    chk = _cached(data_check)()
    st.sidebar.divider()
    st.sidebar.caption(f"Version `{h}`" + (f" ({d})" if d else "") + f" · data {chk['data_date']}"
                       + (" · ⚠️ data out of date" if chk["problems"] else ""))


def run_self_tests():
    chk = data_check()
    return [{"test": "V1. Committed caches have the px fields the code needs (mdd_3/5/10, ETF cagr_3/5/10)",
             "passed": not chk["problems"], "detail": "; ".join(chk["problems"]) or f"data {chk['data_date']}"}]
