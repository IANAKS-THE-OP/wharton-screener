# Deploy checklist

The hosted app (Streamlit Community Cloud) runs `app.py` from branch `main` of
`IANAKS-THE-OP/wharton-screener`, using the committed caches `stock_raw.db`, `etf_raw.db` and `holding_scores.db`.
Use the same steps every time.

## 1. Before pushing
- [ ] `./venv/bin/python3 run_tests.py` prints **ALL PASSED**. The `version_info` test fails if the committed caches
      lack fields the code needs (for example `px.mdd_5`).
- [ ] If you changed what gets downloaded (new `px` fields, longer histories), refresh the caches and **commit the
      .db files** along with the code. Each file must stay under GitHub's 100 MB limit (`ls -la *.db`).
- [ ] Optional, about 2 minutes: `./venv/bin/python3 check_app_flows.py`.

## 2. Push
- [ ] `git status` is clean and `git push origin main` succeeded.
- [ ] `git log -1 --format='%h %cs'` gives the hash to expect on the live site.

## 3. Check the version stamp on the live site
- [ ] Open https://wharton-screener-wdkeznnxoczrm9eaeqiq73.streamlit.app and look at the bottom of the sidebar:
      `Version <hash> (<date>) · data <date>` must match step 2.
- [ ] No red **"Data is out of date for this code version"** banner.
- [ ] If the hash is old after about 5 minutes, reboot the app:
      - On the app, click **Manage app** (bottom right) → **⋮** → **Reboot app**.
      - Or go to https://share.streamlit.io → the app's **⋮** → **Reboot app**.
      - Watch the log for errors. A reboot also clears `st.cache_data`.
- [ ] If the app still shows the old hash, check **⋮ → Settings** on share.streamlit.io: repo
      `IANAKS-THE-OP/wharton-screener`, branch `main`, file `app.py`. If any of these is wrong, delete the app and
      redeploy it with the correct values.

## 4. Smoke test on the live site
- [ ] Stocks → *Publishing* → **NYT** has a max drawdown value (about 27.7% / 49.8% / 49.9% for 3 / 5 / 10 years with
      the 2026-10-14 data).
- [ ] Stocks → *Electronic Components* → **DAKT** has a max drawdown value (about 42.0% / 70.7% / 82.3%).
- [ ] The ranked table has a **Laura penalty** column.
- [ ] The sidebar **History window** dropdown offers 3 / 5 / 10 years, and changing it changes the drawdown values.
- [ ] ETFs → *Large Blend* loads with a max drawdown for USMV.
