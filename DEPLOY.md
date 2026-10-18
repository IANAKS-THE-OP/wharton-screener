# Deploy checklist

The hosted app (Streamlit Community Cloud) runs `app.py` from branch `main` of
`IANAKS-THE-OP/wharton-screener`, using the committed caches `stock_raw.db`, `etf_raw.db` and `holding_scores.db`.
Use the same steps every time.

## 1. Before pushing
- [ ] `./venv/bin/python3 run_tests.py` prints **ALL PASSED**. The `version_info` test fails if the committed caches
      lack fields the code needs (for example `px.mdd_5`).
- [ ] If you changed what gets downloaded (new `px` fields, longer histories), refresh the caches and **commit the
      .db files** along with the code. Each file must stay under GitHub's 100 MB limit (`ls -la *.db`).
- [ ] **Changed a default weight, `LAURA_TIERS` or the default penalty?** Bump `LAURA_VERSION` in `laura.py` and set
      `LAURA_FINGERPRINT` to the new `laura.fingerprint()` (the `laura` self-test fails until you do). Open sessions are then
      reset to Laura standards on their next run.
- [ ] **Changed the stock weights or stock settings?** Rebuild the holdings-quality cache BEFORE running the app or
      `check_app_flows.py` (the first ETF page load clears a stale cache, and the rebuild only rescores what is still in it;
      if it was already cleared, `git checkout holding_scores.db` first):
      `./venv/bin/python3 -c "import etf_holdings as eh; print(eh.rebuild_from_cache())"`, then commit `holding_scores.db`.
- [ ] Optional, about 3 minutes: `./venv/bin/python3 check_app_flows.py` prints **ALL OK**.

## 2. Push
- [ ] `git status` is clean and `git push origin main` succeeded.
- [ ] `git log -1 --format='%h %cs'` gives the hash to expect on the live site.

## 3. Check the version stamp on the live site
- [ ] Open https://wharton-screener-wdkeznnxoczrm9eaeqiq73.streamlit.app and look at the bottom of the sidebar:
      `Version <hash> (<date>) · data <date>` must match step 2.
- [ ] No red **"Data is out of date for this code version"** banner.
- [ ] The stamp is cached for up to 10 minutes, so an old hash right after a push is normal. `app.py` reloads all of the
      screener's own modules when the deployed commit changes, so a mix of old and new files should not happen.
- [ ] If the hash is still old after about 10 minutes, or a page shows an error, reboot the app:
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
- [ ] ETFs → *Large Blend* loads with a max drawdown for USMV (about 9.4% / 17.9% / 33.1%).
- [ ] Stocks → *Entertainment*: NFLX and IMAX have max drawdown values (about 49.9% / 75.9% / 75.9% and 27.6% / 44.2% / 79.4%).
- [ ] In a fresh browser session: the green **Laura standards (default)** badge shows at the top of Tune scoring, above the
      ranked table and on Compare.
- [ ] Tune scoring shows the six-category table with matching Stocks / ETFs totals (Risk 30 · Cost/valuation 18 · Quality 17 ·
      Growth 13 · Safety 17 · Income 5).
- [ ] Under the ranked table: **Penalty strength sensitivity** (top 10 at k = 0.2 / 0.4 / 0.6).

## 5. If the deploy is broken: roll back
- [ ] `git revert --no-edit HEAD && git push origin main` (keeps history; use `HEAD~N..HEAD` for several commits), then
      repeat step 3 and check the stamp shows the revert commit. Reboot the app if it does not pick it up.
