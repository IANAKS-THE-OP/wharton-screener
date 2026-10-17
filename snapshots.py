"""
"Save snapshot": freeze a ranking so a pick can be defended later.
==================================================================
Writes results/snapshots/<asset>_<view>_<YYYYmmdd_HHMMSS>.csv (the full table: every value, score, Laura limit, weight)
and a matching .json (data date, weights, Laura limits, penalty strength, settings, hard limits, peer-group mode, app version).
On the hosted site the disk is temporary, so the two files are also offered as downloads right after saving.
"""

import json
import os
import re
import subprocess
from datetime import datetime

import streamlit as st

DIR = "results/snapshots"


def _slug(s):
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")[:60] or "view"


def _commit():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def save(asset, view, table, meta, out_dir=DIR):
    """table: DataFrame; meta: JSON-able dict. -> (csv_path, json_path). Pure file I/O (tested)."""
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now()
    base = os.path.join(out_dir, f"{asset}_{_slug(view)}_{stamp:%Y%m%d_%H%M%S}")
    table.to_csv(base + ".csv", index=False)
    meta = {"saved_at": stamp.isoformat(timespec="seconds"), "asset": asset, "view": view, "rows": len(table),
            "app_commit": _commit(), **meta}
    with open(base + ".json", "w") as f:
        json.dump(meta, f, indent=2, default=str)
    return base + ".csv", base + ".json"


def button(key, asset, view, table_fn, meta_fn, col=None):
    """Draw the button; on click write the files and show where (plus downloads). table_fn/meta_fn are only
    called on click, so the page doesn't build the snapshot every run."""
    where = col or st
    if where.button("Save snapshot", key=f"snapbtn|{key}",
                    help="Writes the current table (with data date, weights, Laura limits and settings) to results/snapshots/ "
                         "so the ranking behind a pick can be shown later."):
        try:
            paths = save(asset, view, table_fn(), meta_fn())
        except OSError as e:
            st.error(f"Couldn't write the snapshot: {e}")
            return
        st.session_state[f"_snap|{key}"] = paths
    paths = st.session_state.get(f"_snap|{key}")
    if paths and all(os.path.exists(p) for p in paths):
        st.success(f"Saved `{paths[0]}` and `{os.path.basename(paths[1])}`.")
        d1, d2 = st.columns(2)
        with open(paths[0]) as f:
            d1.download_button("Download snapshot table (CSV)", f.read(), file_name=os.path.basename(paths[0]), mime="text/csv", key=f"snapdl|{key}|csv")
        with open(paths[1]) as f:
            d2.download_button("Download snapshot settings (JSON)", f.read(), file_name=os.path.basename(paths[1]),
                               mime="application/json", key=f"snapdl|{key}|json")


def run_self_tests():
    import tempfile

    import pandas as pd
    results = []
    with tempfile.TemporaryDirectory() as d:
        c, j = save("stock", "Gold / test", pd.DataFrame({"ticker": ["A", "B"], "overall": [70.0, 60.0]}),
                    {"data_date": "2026-10-14", "weights": {"roic": 8}, "limits": {"roic": 1.0}}, out_dir=d)
        ok = (pd.read_csv(c)["ticker"].tolist() == ["A", "B"] and json.load(open(j))["data_date"] == "2026-10-14"
              and json.load(open(j))["limits"]["roic"] == 1.0 and os.path.basename(c).startswith("stock_Gold_test_"))
    results.append({"test": "S1. Save snapshot writes the table CSV + settings JSON (data date, weights, Laura limits)", "passed": ok,
                    "detail": os.path.basename(c)})
    return results
