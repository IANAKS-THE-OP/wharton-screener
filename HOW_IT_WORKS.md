## 1. What this is
The **Wharton Investment Screener** is a research tool built for our Wharton Global High School Investment Competition team. The client is Laura Gao: ten fixed $50,000 payments (2033-2042) must be funded with high certainty, and any extra growth goes toward a building contribution. She is a **moderate-risk** client: no big drops, consistent growth, nothing heavy. This tool screens the **equity** side (stocks and equity ETFs); the Treasury/bond planning is done outside it. Every default in the app (comfort ranges and weights) is **Laura's standards** - see section 8.

The tool **ranks candidates that already meet our rules**. It does not predict returns, does not place trades, and does not check what the Wharton simulator (WInS) actually carries. A person on the team makes every buy/sell decision. The score is evidence for our Trading Notes, IPS and Final Report, not a replacement for them.

Two asset types, one idea: *compare each candidate with its peers, and also measure how close it is to the ranges we say we want.*

| Asset type | Peer group | Engine | Metrics |
|---|---|---|---|
| **Stocks** | one of 145 industries | `scoring2.py` | 14 |
| **ETFs** (equity only) | a Morningstar category (Large Blend, Large Value...) or a theme (ESG, low volatility, dividend / income, quality, equal weight) | `etf_scoring.py` | 10 |

## 2. Quick start
```bash
cd wharton-screener
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
./venv/bin/streamlit run app.py          # opens http://localhost:8501
```
1. Pick an **asset type** in the sidebar, then an industry (stocks) or a Morningstar category / theme (ETFs).
2. If it says *not cached yet*, click **Pull live data**. A stock industry takes about 1-3 minutes; an ETF category 1-3 minutes.
3. Read **Ranked results**, click a company/fund in **Breakdown** to see every number, and adjust weights and comfort ranges in **Tune scoring**.
4. Use **Directory** to browse every industry/category and tick a **shortlist**.
5. Download the full **CSV export** for the Final Report.

First-time ETF setup (once): `./venv/bin/python3 build_etf_universe.py` (10-20 min) builds the complete list of every US-listed ETF and its category, then `./venv/bin/python3 etf_groups.py` (seconds) writes the equity-ETF universe `results/etf_universe.csv`. Stock directory (once): `backtest_industries.py` then `build_industry_directory.py`.

## 3. The app, screen by screen
**Sidebar.** Asset type (Stocks, ETFs, How everything works), the industry picker for stocks or the Morningstar category / theme picker for ETFs (click it and type to search) and asset-specific scoring inputs.

**Ranked results.** Every company/fund in the group, sorted best first: Overall, Fit, Composite, metrics inside range (for example 11/14), red-flag icons, data coverage %. ETFs also show a **tier** (funds within 1.0 Overall point of their tier's first fund: treat them as tied, e.g. "Tier 3: ranks 10-15") and **why not higher** (each metric beyond its bad bound and the Overall points it costs), the Fit distribution (how many funds have Fit >= 97) and a *History-window diagnostic* (section 7). The *flag details* column spells every flag out. **Breakdown** below it explains one company or fund metric by metric.

**Tune scoring.** Weights (a slider per metric, live total), the **blend** between Fit and Composite, and per-metric **comfort ranges** (a two-handle slider plus exact low/high boxes, in the metric's own units) with an optional hard limit ("must be inside range"). Under each metric: the industry 25th / median / 75th and Laura's good and bad bounds. The two slider handles are the **good bound** (Fit 100) and the **bad bound** (Fit 80). Exactly **two buttons**, the same on both pages: **Laura standards** (the default: Laura's per-industry ranges *and* Laura's weights) and **Industry average** (every range = the industry's 25th-75th percentile; weights unchanged). See section 8.

**Compare industries.** Pick any number of downloaded industries (stocks) or Morningstar categories and themes (ETFs; there the tab is called **Compare groups**) and rank them in one table. Two modes: **Each vs its own industry** (default; every name keeps exactly the percentile scores and Laura ranges it gets on its own industry page, then all names are ranked together by Overall) and **Pooled** (all selected industries are ONE peer group: percentiles, 25th/median/75th, spread and Laura ranges are recalculated on the pooled set, same `LAURA_TIERS` rules and the same under-10-peer fallback). The table shows rank, rank inside the industry, Overall, Fit, Composite, metrics inside range, red flags and a 0-100 score per metric (percentile or Fit; hover a cell for the raw value, range and both scores), with an industry filter, sort-by-any-column and a CSV download. Above it, a per-industry summary: n, and each metric's median and Laura bound (⚠ = fallback pool), so differences between industries are visible. A **banner** at the top states exactly what is applied (ranges: Laura standards or Industry average; weights: shared with Tune scoring; hard limits: not applied; the data date of each industry) and lists anything on Tune scoring that is ignored here (hand-tuned ranges, ticked Must-be-inside limits, ETF Laura hard limits). Two buttons: **Reset ranges and weights everywhere** (Laura standards; it **also changes Tune scoring**: Laura weights there, and the selected industries' page ranges back to Laura's) and **Industry average** (ranges only, Compare only). **Save snapshot** works here too. Session keys are this tab's own (`stkcmp_…`, `etfcmp_…`), so Compare never changes a page's ranges.

**Save snapshot** (Ranked results and Compare) writes the current table to `results/snapshots/<asset>_<view>_<time>.csv` plus a `.json` with the data date, weights, ranges, blend, settings, hard limits and app version, so a pick can be defended later. Both files are also offered as downloads (the hosted site's disk is temporary). Every ranking shows its **data date** above the table.

**Low data and not scored.** A name with more than 30% of its weight excluded is *low data*; the table's *why* column and the Breakdown list each missing metric and the reason (e.g. "ROIC (EBIT missing)"). A company with **no 10-K data** is **not ranked** at all and says why: "Not scored: files 40-F, not 10-K" (or 20-F; foreign issuers), "no annual report yet" (new listings / shells) or "not in SEC EDGAR". The filer form comes from SEC submissions (`annual_form`). In the cache: 3,253 file 10-K, 426 file 20-F, 94 file 40-F, 6 file 10-KT, 607 have no annual report or no CIK.

**Directory.** The full list of industries (stocks) or Morningstar categories and themes (ETFs) with counts, sector, market caps and a shortlist checkbox that saves to `results/`.

**How scoring works.** The math for the current asset type with a worked example from whatever is selected, plus the live **self-tests**.

## 4. Where the data comes from, and how fresh it is
| Source | Used for | Notes |
|---|---|---|
| **yfinance screener** | the list of every stock in an industry and every ETF in a category | live, no key needed |
| **SEC EDGAR** (10-K XBRL "company facts") | stock fundamentals: income statement, cash flow, balance sheet, up to 6 fiscal years | the authoritative source; limited to 10 requests/second; files cached 7 days |
| **yfinance prices & info** | prices, market cap, beta, sector, fund expense ratio, assets, top-10 holdings, dividend history, 10 years of adjusted closes | adjusted for splits and dividends |
| **NASDAQ Trader listings** | independent check that the stock lists are complete (backtest only) | |

**Caches** (everything is stored locally so a second look is instant; delete a file to force a rebuild):

| File | Holds | Refresh |
|---|---|---|
| `stock_raw.db` | raw SEC + market data per company, by industry | **Refresh industry data** button |
| `etf_raw.db` | raw ETF data per category + the full ETF universe table | **Refresh ETF data**; `build_etf_universe.py` |
| `holding_scores.db` | each holding's stock Composite (used for ETF holdings quality); versioned by a hash of the stock weights/settings, cleared automatically when they change | **Score holdings quality** button |
| `sec_cache/` | raw SEC downloads (large; not in git) | automatic, 7 days |
| `results/` | directories, shortlists, backtest report, exports | rebuild scripts |

## 5. Stocks: step by step
**Universe.** Everything the yfinance screener returns for the industry, kept only if price >= $5 (WInS rule), listed on NYSE / Nasdaq / NYSE American / Cboe BZX, and not a preferred share, warrant, unit or right. About 19,000 US stocks have an industry; about 4,500 pass these filters.

**Raw data per company.** Up to 6 fiscal years from SEC (revenue, EBIT, D&A, interest, taxes, net income, EPS, operating cash flow, capex, dividends, buybacks, issuance, balance sheet) plus 3 years of adjusted prices. Tag fallbacks handle companies that name items differently. A company whose latest 10-K is more than 16 months old is treated as stale and excluded. Foreign filers (20-F) and brand-new listings have no 10-K data and become *low data*.

**The 14 metrics** (default weight, better direction). Default weights are **Laura Gao's standards**: a moderate-risk client who must fund ten fixed payments, so no big drops, steady and consistent growth, and nothing heavy. Risk and balance-sheet metrics (downside deviation, max drawdown, net debt/EBITDA, interest coverage, Sloan accruals) carry the most weight; valuation and growth-momentum metrics (earnings yield, Lynch, Δ operating margin) carry the least. Total 100; every weight is still adjustable in Tune scoring.


| Metric | Weight | Better |
|---|---|---|
| ROIC | 8 | higher |
| Gross profitability | 7 | higher |
| Δ ROIC (pp/yr) | 7 | higher |
| Δ Operating margin (pp/yr) | 5 | higher |
| Earnings yield | 4 | higher |
| FCF yield | 6 | higher |
| Lynch ratio | 4 | higher |
| Revenue growth (3-yr CAGR) | 7 | higher |
| Net debt / EBITDA | 9 | lower |
| Interest coverage | 8 | higher |
| Sloan accrual ratio | 8 | lower |
| Downside deviation | 10 | lower |
| Max drawdown | 10 | lower |
| Shareholder yield | 7 | higher |

Special cases: some situations are **forced** to the worst score (for example EBITDA <= 0 for net debt/EBITDA, EPS <= 0 for Lynch) or best score (no interest and no debt for coverage); others are **excluded** (banks for ROIC, missing data). Forced and excluded companies are not part of that metric's ranking pool.

**Red flags** (shown, never scored): Altman distress, Piotroski F-score <= 3, ROIC below WACC (cost of capital), payouts above free cash flow, low data, negative invested capital, short history, and the use of a fallback peer pool.

## 6. The scoring math (same framework for stocks and equity ETFs)
For **each metric** a company gets two scores from 0 to 100, where **100 is always best**.

1. **Composite (percentile).** Rank the company among its peers that have a valid value. Sort low to high; rank 1 is lowest; ties share the average rank. `p = (rank - 1) / (n - 1) x 100` (n = 1 gives 50). For "higher is better" the score is p; for "lower is better" it is 100 - p.
2. **Fit (comfort range, graded).** A range in the metric's real units running from a **good bound** to a **bad bound** (lower-is-better: low = good, high = bad; higher-is-better: high = good, low = bad); the default is **Laura's per-industry range** (section 8), and *Industry average* gives the peers' 25th to 75th percentile values. At or beyond the good bound Fit = **100**; inside the range Fit falls **linearly from 100 at the good bound to 80 at the bad bound**; beyond the bad bound `fit = max(0, 80 - 50 x distance / spread)`, where distance is how far past the bad bound the value is and spread is the peers' 75th minus 25th percentile (moving a bound never changes the spread). So the lowest possible cost or drawdown is rewarded, not just "inside the range": a fund at the peer group's best decile outscores one sitting on the bad bound. Before this change everything inside the range scored 100, so Fit saturated (91 of 325 Large Blend funds scored 97+) and moving the good-side handle changed nothing.
3. **Weighted averages.** Composite = sum(weight x percentile score) / sum(weights of metrics the company has). Fit uses the same weights. A missing metric is dropped and the denominator shrinks (never treated as zero).
4. **Overall = b x Fit + (1 - b) x Composite.** `b` is the Blend slider (default 50%).

*Worked example:* a stock with ROE-like metric value 5 against a range 10-20 (spread 10), higher is better: distance 5, fit = 100 x (1 - 0.25) = **75**. If it ranks 3rd lowest of 11 peers its percentile score is (3-1)/(11-1) x 100 = **20**. Weights decide how much each counts.

**Ranking.** Companies with more than 30% of their weight excluded are **low data** and always listed last. Otherwise sort by Overall, then Composite, then fewer red flags, then less excluded weight, then ticker A-Z. Optional hard limits can remove companies; if they would remove everyone the full list is shown with a warning, so you never see an empty page.

**Peer pools.** If an industry (stocks) or category (ETFs) has fewer than 10 valid values for a metric, the metric is ranked against the sector (stocks) or all scored equity ETFs instead, and flagged.

## 7. Equity ETFs: step by step
**Universe.** `build_etf_universe.py` pulls **every** ETF on a US exchange (5,731) and labels it with its Morningstar category. After the removals below, about **2,250 equity ETFs** remain.

**Peer groups: Morningstar category + themes (no industries).** The ETF picker lists every **Morningstar category** from yfinance (Large Blend, Large Value, Large Growth, Mid-Cap Blend..., US size/style first, then sector, then international) followed by five **theme groups** found in the fund **name** (yfinance gives no description for most ETFs): *ESG / sustainable* (ESG, sustainable, responsible, climate, carbon, fossil-fuel-free, Paris-aligned, net zero, impact, screened...; "Social Media" / "Truth Social" / carbon-credit funds are not ESG), *Low / min volatility* (low vol, min vol, managed / disciplined / defined volatility, low beta), *Dividend / income*, *Quality*, *Equal weight*. A fund stays in its category AND appears in every theme it matches (USMV: Large Blend + Low volatility; ESMV: Large Blend + ESG + Low volatility; ESGV: Large Blend + ESG). Each theme row stores **matched_by**, the keyword in the name that matched (shown in the theme table and in *Find a fund*). The team can correct themes in **`results/theme_overrides.csv`** (`ticker,theme,action`, action = add / remove), applied after the name matching; every override and its result (applied / ignored and why) is listed on the theme's page. **Find a fund** (sidebar) shows any ticker's Morningstar category and themes.

There is **no industry grouping for ETFs** any more (the 145-industry tagger, its whole-sector / broad-US / international groups and its validation script are in `_retired/`). Stock industries are still used in exactly one place: **holdings quality**, where each top-10 holding is scored against its own stock industry.

**Universe** (`results/etf_universe.csv`, rebuilt offline in seconds with `./venv/bin/python3 etf_groups.py`): every fund in the ETF membership table that passes the equity rules below, minus single-stock funds (top holding >= 50% of the fund). Funds with **no Morningstar category** have no peer group; the 30 that the earlier vetting proved to be real equity sector funds (ITA, JETS, ITB, IYK, FSTA...) are kept in an *Uncategorized* group via `results/etf_uncategorized_keep.csv`, which the team can edit. 2,210 equity ETFs in 43 categories (+ themes).

**Volatility exclusion.** Only VIX / volatility *products* are removed (VIX futures, volatility-premium funds, volatility ETNs, the "Alternative Volatility" category). Low / minimum / managed-volatility **equity** funds (USMV, SPLV, LGLV, FDLO...) are kept; a self-test checks both sides.

**Peer group.** The funds in the selected category or theme are ranked against each other. With fewer than 10 valid values for a metric, that metric is ranked against all downloaded equity ETFs and flagged.

**Downloading.** Click **Pull live ETF data** for the selected group. Big categories (Large Blend, 325 funds) take a few minutes; small ones a minute.

**Removed before scoring (never shown):** bond / fixed income, commodity, leveraged and inverse ("Trading" categories, and fund names containing 2x, 3x, Ultra, Inverse, Short, Bear...), volatility / currency / alternative / derivative-income / crypto, allocation / target-date / convertibles / preferred / long-short, and structured or non-plain-equity categories (defined outcome / buffer funds, target maturity, equity hedged, multi-asset overlay, managed futures / trend, event driven, money market, multistrategy, macro, relative value), funds with **no Morningstar category** unless listed in `results/etf_uncategorized_keep.csv`, option-income / covered-call / buffer / managed-futures funds (by name), and funds priced under $5. "Equity Precious Metals" (gold-miner stocks) stays.

**The 10 metrics** (default weight, better direction): expense ratio (18, lower), max drawdown (18, lower), AUM (12, higher), top-10 weight (12, lower), downside deviation (12, lower), long-run annualized return (10, higher), holdings quality (8, higher), average daily dollar volume (5, higher), trailing-12-month dividend yield (5, higher), number of holdings (0 - always excluded). Default weights are **Laura Gao's standards**: low cost and small drawdowns first (expense ratio 18, max drawdown 18), then size and concentration (AUM 12, top-10 weight 12), downside deviation 12, long-run return 10, holdings quality 8, dollar volume 5, dividend yield 5. Number of holdings has weight 0 because yfinance does not provide it (always excluded). Total 100.

**Holdings quality.** Each of a fund's top-10 holdings is scored by the *stock* model against its own industry; the fund's quality is the weight-averaged Composite of the holdings that could be scored. Foreign listings and stocks outside the tradeable universe are skipped. If scored holdings cover less than 15% of the fund the metric is excluded and flagged. **Between 15% and 50% coverage its weight is reduced in proportion to coverage** (weight x coverage / 100: USMV's holdings quality covers 15% of the fund, so its weight is 8 x 0.15 = 1.2 instead of 8); the Breakdown shows the reduced weight. Because only the top 10 holdings are visible, most diversified funds are under 50% (VOO's top 10 is about 38% of the fund), so for them holdings quality now counts for roughly a third of its nominal weight. Scores are cached, so only the first run is slow.

**History window (common window).** 5 years by default, or 10 (sidebar), for downside deviation, max drawdown and long-run return. **Every fund is measured over the same window ending on the same date**; the window is never shortened for a fund (7 days of slack for listing-date rounding). A fund with less history than the window is **too new for this window**: those three metrics are excluded, it is flagged 🆕 and listed after every fully scored fund (but before low-data funds). A fund whose prices end more than 7 days before the rest of its group is excluded the same way (🕒 stale prices). Why: the old rule let a fund with 5-10 years use whatever it had, so a fund listed in April 2020 was measured from just after the March 2020 crash, which flattered its drawdown, downside deviation and return; 7 of the old Large Blend top 10 were such funds. The *History-window diagnostic* (under the ranked table) re-scores the group under the old rule and shows how many of the top 10 / top 30 have under 10 years of history under each rule, how many were measured over a shortened window, and how many funds move more than 10 ranks. Note: in Large Blend most funds are under 5 years old (196 of 325 are too new even for the 5-year window).

**Limits.** yfinance has no total holdings count, so *number of holdings* is excluded for every fund unless you provide `results/etf_holdings_override.csv` (`ticker,n_holdings`). Holdings quality only sees the top 10 holdings.

**Laura hard limits (optional, off by default).** Laura's original absolute numbers - expense ratio <= 0.20%, max drawdown <= 30%, AUM >= $1B, top-10 weight <= 40% - are no longer ranges. A checkbox in Tune scoring turns them into *Must be inside* limits for those four metrics.

## 8. Laura standards (the defaults)
Laura is a moderate-risk client: no big drops, consistent growth, nothing heavy. Both pages open with her **ranges** and her **weights**; no button is needed. The **Laura standards** button brings both back; **Industry average** switches every range to the industry's 25th-75th percentile and leaves the weights alone.

**Ranges are relative to each industry, never one number for everyone.** Every range runs from a **good bound** (Fit 100 at or beyond it) to a **bad bound** (Fit 80; past it Fit keeps falling, section 6). The good bound is the industry's **best decile**: the **10th percentile** for lower-is-better metrics, the **90th** for higher-is-better. Laura moves the bad bound to a **stricter percentile of the industry's own peer pool** - the same pool the percentile scores use, including the fallback pool when an industry has fewer than 10 valid values (flagged). Values are never multiplied by a percentage, so negative metrics (Sloan, Δ ROIC, net cash) work too. The **spread** in the Fit formula stays the industry 75th - 25th.

| Tier | Stocks | ETFs (per group) | Bad-side bound |
|---|---|---|---|
| **Risk** | downside deviation, max drawdown, net debt/EBITDA, Sloan (high); interest coverage (low) | expense ratio, downside deviation, max drawdown, top-10 weight (high); AUM, dollar volume (low) | industry **median** (50th percentile) |
| **Consistency** | ROIC, gross profitability, Δ ROIC, Δ operating margin (low) | holdings quality (low) | industry **40th percentile** |
| **Moderate** | revenue growth, FCF yield, earnings yield, Lynch, shareholder yield | dividend yield, long-run return | industry **25th percentile** (75th for a lower-is-better metric) |
| *all* | | | good bound: industry **10th** (lower-is-better) / **90th** (higher-is-better) percentile |

Example (illustrative numbers): if Semiconductors' median max drawdown were 45% and Utilities' 25%, Laura's cap is 45% for a chip stock and 25% for a utility - each is held to the standard of its own industry. If the good and bad bounds coincide (a very bunched-up industry), that metric falls back to the industry default and the page says so. The tiers live in one editable dict, `LAURA_TIERS` in `laura.py`, used by both pages and both engines.

**Laura weights** (each set totals 100): stocks - downside deviation 10, max drawdown 10, net debt/EBITDA 9, interest coverage 8, Sloan 8, ROIC 8, gross profitability 7, Δ ROIC 7, revenue growth 7, shareholder yield 7, FCF yield 6, Δ operating margin 5, earnings yield 4, Lynch 4. ETFs - expense ratio 18, max drawdown 18, AUM 12, top-10 weight 12, downside deviation 12, long-run return 10, holdings quality 8, dollar volume 5, dividend yield 5, number of holdings 0 (always excluded).

## 9. Validation and tests
* **Stock/industry backtest** (`backtest_industries.py`, report in `results/backtest_report.txt`): all 145 industries paginate exactly (reported = collected = unique); every S&P 500 stock appears under the correct industry; the exchange-listed names we omit are almost all priced under $5.
* **Self-tests:** 23 for stocks, 17 for ETFs, 4 for ETF grouping/themes, 6 for Compare and 1 for snapshots, shown in each "How scoring works" tab; run them all with `./venv/bin/python3 run_tests.py`. New with the graded Fit / common window: graded Fit values on both engines (good bound 100, bad bound 80, beyond 80 - 50 x d / spread), a fund at the good bound outscores one at the bad bound (both inside), moving the good bound changes Fit, two funds with different listing dates but identical returns over the window get identical scores (the old rule scored them differently), a too-new fund is listed after every fully scored fund, tiers, why-not-higher points, and holdings-quality weight x coverage. The Compare tests check that a name's scores in *Each vs its own industry* equal its own industry page, that *Pooled* recomputes percentiles / median / spread / Laura ranges on the pooled set, the pooled under-10 fallback, and that a fund in a category and a theme appears once per group (own) or once (pooled). `check_app_flows.py` (33 checks) drives the real app headlessly on the cached data: Compare with two stock industries in both modes; the ETF page has no industry widgets and lists only Morningstar categories + themes; USMV, ESMV and ESGV are in Large Blend and in their themes; tier and why-not-higher columns; ESMV is too new for the 5-year window; *Find a fund* by ticker and by name; the history diagnostic runs; ranges never leak between groups or pages.
* **Earlier self-test detail:** They cover percentiles, ties, forced scores, exclusion rescaling, fit, range/weight independence, drawdown, downside deviation, history windows, holdings quality, removals, ranking, and Laura's ranges (the max-drawdown bound equals each industry's median and differs between industries; tiers, unchanged spread, safe fallback; ETF hard limits).
* **Hand checks:** ROIC, net debt/EBITDA, max drawdown, expense ratio and holdings quality were recomputed independently for sample companies and funds and matched the engine exactly.
* **Bugs found by this testing and fixed:** stale fiscal years from an old SEC revenue tag; debt undercounting; SIC-fragmented peer groups; unreachable ETF categories.

## 10. Known limits (read before relying on a score)
* Fundamentals are **SEC-only**, so foreign filers (20-F / 40-F) are **not scored** and new IPOs are low data.
* **No operating-income line:** 217 non-financial 10-K filers (e.g. NEM, GE, JNJ, LLY, PFE, MRK, TJX) never report `OperatingIncomeLoss`, so EBIT is missing and ROIC, Δ ROIC, Δ operating margin, earnings yield, net debt/EBITDA and interest coverage are excluded; many become low data. 651 have no cost-of-revenue / gross-profit tag (gross profitability excluded).
* About 5.5% of US stocks have no industry in yfinance and appear in no industry.
* **WInS availability is not checked.** The tool assumes anything over $5 on a major exchange is tradeable; confirm in WInS.
* Scores rank peers against each other; a high rank does not mean a good investment in absolute terms.
* No taxes, bid/ask spreads, correlation or position sizing. Bonds and Treasuries are not covered by this tool.
* ETFs with less history than the window are *too new for this window* (no drawdown / downside deviation / return) and listed after fully scored funds; in Large Blend that is most funds even at 5 years. ETF peer groups are yfinance's Morningstar categories plus name-based themes; a theme can miss a fund whose name doesn't say it (fix with `results/theme_overrides.csv`).
* Data are as of the last pull; click Refresh before a decision. If a download fails (network / SSL error) the page shows an error with **Retry** and keeps the previously cached data.

## 11. File map
| File | Role |
|---|---|
| `app.py` | the Streamlit app: sidebar, routing, directories |
| `laura.py`, `range_ui.py` | Laura's per-industry ranges (`LAURA_TIERS`) and hard limits; the shared range widgets and the two buttons |
| `stock_page.py`, `scoring2.py`, `stock_data.py` | stock UI, engine, raw data (SEC + yfinance) |
| `etf_page.py`, `etf_scoring.py`, `etf_data.py`, `etf_holdings.py` | equity-ETF UI, engine, raw data, holdings-quality service |
| `etf_universe.py`, `build_etf_universe.py` | live ETF pulls and the full-universe builder |
| `etf_data.py` | ETF raw data cache and eligibility rules |
| `industry_universe.py`, `sec_edgar.py` | stock universe pull and SEC downloader |
| `backtest_industries.py`, `build_industry_directory.py` | validation and the industry directory |
| `compare.py`, `compare_ui.py` | Compare industries (stocks) / Compare groups (ETFs): engine (own / pooled) and the shared tab |
| `etf_groups.py` | ETF universe (`results/etf_universe.csv`), Morningstar-category grouping and name-based themes |
| `run_tests.py`, `check_app_flows.py` | all self-test suites; headless app checks on the cached data |
| `config.yaml`, `.streamlit/secrets.toml.example` | SEC cache settings; the SEC user-agent goes in Streamlit secrets |
| `METHODOLOGY.md` | the formal spec of the scoring (for the Final Report) |
| `_retired/` | removed features kept for reference: Bond ETF page engine (`fundamental_scoring.py`, `market_query.py`), portfolio planner, bond ladder, Treasury prices, v1 bucket screener (`screener.py`, `fetch_universe.py`), the ETF industry tagger and its checks (`etf_industry.py`, `validate_etf_tags.py`, `results/etf_industry_map.csv`) |

## 12. Troubleshooting
* **"not cached yet"**: click the Pull button for that industry/category.
* **Everything is low data**: the company/fund is new or foreign; check the flag details.
* **A fund or stock is missing**: check the removal reasons line (ETFs), the $5 / exchange filters (stocks), or whether the fund's group has been downloaded (**Pull live ETF data**).
* **SEC errors**: set a real name and email as the SEC user agent: `[sec_edgar] user_agent = "..."` in `.streamlit/secrets.toml` (local, gitignored) or in Streamlit Cloud > Settings > Secrets; `SEC_USER_AGENT` env var also works. SEC requires it.
* **Slow first holdings run**: it scores whole industries with the stock pipeline (about 90 minutes for three categories, then instant).
* **Start clean**: delete the `*.db` files; the app rebuilds on the next Pull.

## 13. Glossary
**Composite**: percentile-based score vs peers. **Fit**: closeness to your comfort ranges. **Overall**: blend of the two. **Comfort range**: the low-high band you consider good for a metric. **Spread**: peers' 75th minus 25th percentile. **Pool**: the peers a value is ranked against. **Low data**: more than 30% of weight excluded. **ROIC**: operating profit after tax per dollar of invested capital. **WACC**: blended cost of capital. **Sloan ratio**: how much profit is accounting accruals rather than cash. **Downside deviation**: volatility of returns below a minimum acceptable return. **Max drawdown**: worst peak-to-trough fall. **TTM**: trailing twelve months. **AUM**: assets under management. **Laura standards**: the default ranges and weights (section 8).

## 14. The hosted site (Streamlit Community Cloud)
* **Entry point** `app.py`, **Python 3.13** (3.12 also works; numpy 2.5 needs 3.12+). Packages: `requirements.txt`.
* **Data.** The cloud app does not download anything on start: it reads the committed read-only snapshot `stock_raw.db`, `etf_raw.db` and `holding_scores.db` (plus the CSVs in `results/`). Yahoo often blocks cloud servers, so the *Refresh* buttons may fail there; the page then shows the error and keeps the snapshot. To update the site: click Refresh locally, run `./venv/bin/python3 run_tests.py`, then commit the `.db` files and push.
* **Secrets.** Only the SEC user agent (needed for stock refreshes): paste `[sec_edgar]` / `user_agent = "WGHSIC Team <name> <email>"` into the app's Secrets.
