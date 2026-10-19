## 1. What this is
The **Wharton Investment Screener** is a research tool built for our Wharton Global High School Investment Competition team. The client is Laura Gao: ten fixed $50,000 payments (2033-2042) must be funded with high certainty, and any extra growth goes toward a building contribution. She is a **moderate-risk** client: no big drops, consistent growth, nothing heavy. This tool screens the **equity** side (stocks and equity ETFs); the Treasury/bond planning is done outside it. Every default in the app (Laura limits, weights and the Laura penalty strength) is **Laura's standards** - see section 8.

The tool **ranks candidates that already meet our rules**. It does not predict returns, does not place trades, and does not check what the Wharton simulator (WInS) actually carries. A person on the team makes every buy/sell decision. The score is evidence for our Trading Notes, IPS and Final Report, not a replacement for them.

Two asset types, one idea: *compare each candidate with its peers, then take points off for every Laura limit it misses.*

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
3. Read **Ranked results**, click a company/fund in **Breakdown** to see every number, and adjust weights, Laura limits and the penalty strength in **Tune scoring**.
4. Use **Directory** to browse every industry/category and tick a **shortlist**.
5. Download the full **CSV export** for the Final Report.

First-time ETF setup (once): `./venv/bin/python3 build_etf_universe.py` (10-20 min) builds the complete list of every US-listed ETF and its category, then `./venv/bin/python3 etf_groups.py` (seconds) writes the equity-ETF universe `results/etf_universe.csv`. Stock directory (once): `backtest_industries.py` then `build_industry_directory.py`.

## 3. The app, screen by screen
**Sidebar.** Asset type (Stocks, ETFs, How everything works), the **History window** dropdown (3, 5 or 10 years; default 5; shared by both pages, section 6), the industry picker for stocks or the Morningstar category / theme picker for ETFs (click it and type to search) and asset-specific scoring inputs. With 3 years selected a caption warns: *3 years starts in late 2023 and excludes the 2022 decline, so risk numbers look better than over a full market cycle.*

**Ranked results.** Every company/fund in the group, sorted best first: Overall, Composite, Fit, **Laura penalty** (Overall points lost = Composite − Overall), metrics meeting their Laura limit (for example 11/14), **why not higher** (each missed Laura limit and the Overall points it costs), red-flag icons, data coverage %. ETFs also show a **tier** (funds within 1.0 Overall point of their tier's first fund: treat them as tied, e.g. "Tier 3: ranks 10-15"), the Fit distribution (how many funds have Fit >= 97) and a *History-window diagnostic* (section 7). Stocks that file a 20-F / 40-F are not in the table: they are listed in a collapsed **Not scored: foreign filer** box under it. The *flag details* column spells every flag out. **Breakdown** below it explains one company or fund metric by metric.

**Tune scoring.** A badge at the top: **Laura standards (default)** or **Custom settings** + **Reset to Laura standards** (also above the ranked table and on Compare). Weights (a slider per metric, live total, and a table of the six category totals for stocks and ETFs), the **Laura penalty strength** k (0 to 1, step 0.05; default 0.4; 0 = pure Composite), and ONE **Laura limit** per metric (a one-handle slider plus an exact box, in the metric's own units): a **ceiling** for lower-is-better metrics, a **floor** for higher-is-better ones, with an optional hard limit ("Must be inside": remove names that miss it). Under each metric: the industry 25th / median / 75th and where Laura's limit comes from. Exactly **two buttons**, the same on both pages: **Laura standards** (the default: Laura's per-industry limits, Laura's weights and k = 0.4) and **Industry average** (every limit = the industry's worse quartile: 75th percentile for a ceiling, 25th for a floor; weights and k unchanged). See section 8.

**Compare industries.** Pick any number of downloaded industries (stocks) or Morningstar categories and themes (ETFs; there the tab is called **Compare groups**) and rank them in one table. Two modes: **Each vs its own industry** (default; every name keeps exactly the percentile scores and Laura limits it gets on its own industry page, then all names are ranked together by Overall) and **Pooled** (all selected industries are ONE peer group: percentiles, 25th/median/75th, spread and Laura limits are recalculated on the pooled set, same `LAURA_TIERS` rules and the same under-10-peer fallback). The table shows rank, rank inside the industry, Overall, Composite, Fit, Laura penalty (hover it for why not higher), metrics meeting their limit, red flags and a 0-100 score per metric (percentile or Fit; hover a cell for the raw value, Laura limit and both scores), with an industry filter, sort-by-any-column and a CSV download. Foreign filers are listed separately (never ranked). Above it, a per-industry summary: n, and each metric's median and Laura limit (⚠ = fallback pool), so differences between industries are visible. A **banner** at the top states exactly what is applied (Laura limits: Laura standards or Industry average; weights and penalty strength k: shared with Tune scoring; hard limits: not applied; the data date of each industry) and lists anything on Tune scoring that is ignored here (hand-tuned limits, ticked Must-be-inside limits, ETF Laura hard limits). Two buttons: **Reset limits and weights everywhere** (Laura standards; it **also changes Tune scoring**: Laura weights and k = 0.4 there, and the selected industries' page limits back to Laura's) and **Industry average** (limits only, Compare only). **Save snapshot** works here too. Session keys are this tab's own (`stkcmp_…`, `etfcmp_…`), so Compare never changes a page's limits.

**Save snapshot** (Ranked results and Compare) writes the current table to `results/snapshots/<asset>_<view>_<time>.csv` plus a `.json` with the data date, weights, Laura limits, penalty strength, settings (incl. the history window), hard limits and app version, so a pick can be defended later. Both files are also offered as downloads (the hosted site's disk is temporary). Every ranking shows its **data date** above the table.

**Low data, too new and not scored.** A name with more than 30% of its weight excluded is *low data*; the table's *why* column and the Breakdown list each missing metric and the reason (e.g. "ROIC (EBIT missing)"). A name with less price history than the History window is *too new for this window* (🆕) and listed after the fully scored names. **Foreign filers** (20-F foreign private issuers, 40-F Canadian issuers: no 10-K) are **out of scope** for the stock model: they are left out of every peer pool and ranking and listed in the collapsed **Not scored: foreign filer** box with the reason ("files 20-F, not 10-K"). Any other company with **no 10-K data** is **not ranked** and says why: "no annual report yet" (new listings / shells) or "not in SEC EDGAR". The filer form comes from SEC submissions (`annual_form`). In the cache: 3,253 file 10-K, 426 file 20-F, 94 file 40-F, 6 file 10-KT, 607 have no annual report or no CIK; 520 cached companies are foreign filers with no 10-K data.

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
| `holding_scores.db` | each holding's stock Composite (used for ETF holdings quality, at the stock defaults incl. the 5-yr window); versioned by a hash of the stock weights/settings, cleared automatically when they change | **Score holdings quality** button; offline: `etf_holdings.rebuild_from_cache()` |
| `sec_cache/` | raw SEC downloads (large; not in git) | automatic, 7 days |
| `results/` | directories, shortlists, backtest report, exports | rebuild scripts |

**Price-only refresh:** `./venv/bin/python3 refresh_prices.py 2026-10-15` re-downloads 10 years of adjusted daily closes for every cached stock and ETF, ending on that (exclusive) date, so every 3 / 5 / 10-year window ends on the same day. Fundamentals, holdings and fetch dates are untouched.

## 5. Stocks: step by step
**Universe.** Everything the yfinance screener returns for the industry, kept only if price >= $5 (WInS rule), listed on NYSE / Nasdaq / NYSE American / Cboe BZX, and not a preferred share, warrant, unit or right. About 19,000 US stocks have an industry; about 4,500 pass these filters.

**Raw data per company.** Up to 6 fiscal years from SEC (revenue, EBIT, D&A, interest, taxes, net income, EPS, operating cash flow, capex, dividends, buybacks, issuance, balance sheet) plus 10 years of adjusted daily prices. Tag fallbacks handle companies that name items differently. A company whose latest 10-K is more than 16 months old is treated as stale and excluded. Foreign filers (20-F / 40-F) have no 10-K data and are out of scope (listed, never ranked); brand-new listings have no 10-K data and are not ranked.

**The 14 metrics** (category, default weight, better direction). Default weights are **Laura Gao's standards**: a moderate-risk client who must fund ten fixed payments, so no big drops, steady and consistent growth, and nothing heavy. Both models use the **same six categories with role-based totals** (each model sums to 100): **stocks, the growth driver: Risk 22 · Cost 18 · Quality 20 · Growth 20 · Safety 15 · Income 5**; **ETFs, the stability core: Risk 30 · Cost 18 · Quality 20 · Growth 10 · Safety 17 · Income 5**. Stocks are the growth driver (more growth, less risk weight); ETFs are the stability core (more risk and diversification weight); bonds guarantee the payments. Every metric carries a category label, and both Tune scoring tabs show the two models' category totals side by side (plus this page's current weights). Every weight is still adjustable in Tune scoring.


| Category (total) | Metric | Weight | Better |
|---|---|---|---|
| Risk (22) | Max drawdown | 12 | lower |
| Risk (22) | Downside deviation | 10 | lower |
| Cost (18) | Earnings yield | 6 | higher |
| Cost (18) | FCF yield | 6 | higher |
| Cost (18) | Lynch ratio | 6 | higher |
| Quality (20) | ROIC | 8 | higher |
| Quality (20) | Sloan accrual ratio | 7 | lower |
| Quality (20) | Gross profitability | 5 | higher |
| Growth (20) | Revenue growth (3-yr CAGR) | 8 | higher |
| Growth (20) | Δ ROIC (pp/yr) | 7 | higher |
| Growth (20) | Δ Operating margin (pp/yr) | 5 | higher |
| Safety (15) | Net debt / EBITDA | 8 | lower |
| Safety (15) | Interest coverage | 7 | higher |
| Income (5) | Shareholder yield | 5 | higher |

**Split-adjusted Lynch ratio.** SEC per-share numbers are not split-adjusted: each value is on the share basis of the 10-K it came from (later 10-Ks restate only the years they show). Netflix's 10-for-1 split in November 2025 left 2022 EPS at $9.95 (old shares) next to 2025 EPS at $2.53 (new shares), so the 3-yr EPS CAGR read −36.6% and Lynch −1.33 (0th percentile in Entertainment) although earnings grew. The Lynch ratio (P/E and EPS CAGR) now uses split-adjusted per-share earnings: net income ÷ diluted shares, each year's share count converted to today's basis with the yfinance split history (splits after the filing that reported the value); when net income or shares are missing, or the share count is implausible (more than 4x away from market cap ÷ price), SEC diluted EPS ÷ the same split factor. It stays per share, so dilution and buybacks still count. yfinance also books spin-offs as fractional "splits" (GE, FDX, MMM); a split is used only if SEC's own restated filings show the share count changing by that ratio (or, with no filing on both sides yet, the ratio is a clean n-for-m). NFLX: EPS CAGR +36.4%, Lynch +1.32. Earnings yield, FCF yield and shareholder yield use dollar totals and market cap, so splits never affected them; the Piotroski dilution test uses the same split-adjusted share counts. Full before/after list: `results/split_audit_2026-10-18.csv`.

Special cases: some situations are **forced** to the worst score (for example EBITDA <= 0 for net debt/EBITDA, EPS <= 0 for Lynch) or best score (no interest and no debt for coverage); others are **excluded** (banks for ROIC, missing data). Forced and excluded companies are not part of that metric's ranking pool.

**Red flags** (shown, never scored): Altman distress, Piotroski F-score <= 3, ROIC below WACC (cost of capital), payouts above free cash flow, low data, negative invested capital, too new for the history window, stale prices, and the use of a fallback peer pool.

## 6. The scoring math (same framework for stocks and equity ETFs)
For **each metric** a company gets two scores from 0 to 100, where **100 is always best**.

1. **Composite (percentile).** Rank the company among its peers that have a valid value. Sort low to high; rank 1 is lowest; ties share the average rank. `p = (rank - 1) / (n - 1) x 100` (n = 1 gives 50). For "higher is better" the score is p; for "lower is better" it is 100 - p.
2. **Fit (one Laura limit per metric, one-sided).** Each metric has ONE limit in its real units: a **ceiling** for lower-is-better metrics, a **floor** for higher-is-better ones. The default is **Laura's per-industry limit** (section 8); *Industry average* uses the peers' worse quartile (75th percentile for a ceiling, 25th for a floor). Only the bad side counts: distance = max(0, value − ceiling) or max(0, floor − value), and `fit = 100 x max(0, 1 - (distance / spread) / 2)`, where spread is the peers' 75th minus 25th percentile (moving the limit never changes the spread). Anything on the good side of the limit scores **100**; one spread past it **50**; two spreads past **0**. (This replaced a graded Fit with a second "good" bound at the 10th / 90th percentile; the good side now never matters, so Laura's limit is the only number to set.)
3. **Weighted averages.** Composite = sum(weight x percentile score) / sum(weights of metrics the company has). Fit uses the same weights. A missing metric is dropped and the denominator shrinks (never treated as zero).
4. **Overall = max(0, Composite - k x (100 - Fit))**, k = the *Laura penalty strength* slider (default **0.4**; *Laura standards* resets it; k = 0 gives pure Composite). With k = 0.4 **each Fit point lost costs 0.4 Overall points**; a name that meets every Laura limit keeps its Composite. The ranked table is followed by a sensitivity table at k = 0.2 / 0.4 / 0.6 (section 8). The **Laura penalty** column is Composite − Overall; **why not higher** lists each missed limit with the points it cost: k x weight x (100 - Fit) / sum of weights (these add up to the penalty).

*Worked example:* a stock's max drawdown is 42% against Laura's ceiling of 34% (the industry median), with an industry spread of 16 points: distance 8 = half a spread, fit = 100 x (1 - 0.5 / 2) = **75**. If its overall Fit is 90 and its Composite 70, Overall = 70 - 0.4 x (100 - 90) = **66** (Laura penalty 4).

**History window.** Downside deviation, max drawdown and (ETFs) long-run return use the sidebar's **History window**: 3, 5 or 10 years, default **5**. Every stock and fund is measured over the **same window ending on the same date** (the cached prices all end 2026-10-14); the window is never shortened for one name (7 days of slack for listing-date rounding). A name with less history than the window is **too new for this window**: those metrics are excluded, it is flagged 🆕 and listed after every fully scored name (but before low-data names). Prices that end more than 7 days before the rest of the group are excluded the same way (🕒 stale prices). **3 years starts in late 2023 and excludes the 2022 decline, so risk numbers look better than over a full market cycle**; the app says so when 3 is selected. (Stocks used a fixed 3-year window before this change.)

**Ranking.** Fully scored names first, then too new for this window, then **low data** (more than 30% of the weight excluded, not counting the window metrics of a too-new name), then (stocks) not scored. Within each: Overall, then Composite, then fewer red flags, then less excluded weight, then ticker A-Z. Optional hard limits can remove names; if they would remove everyone the full list is shown with a warning, so you never see an empty page.

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

**The 10 metrics** (category, default weight): max drawdown 18 + downside deviation 12 (Risk 30) · expense ratio 18 (Cost 18) · holdings quality 8 + top-10 weight 12 (Quality 20; number of holdings 0, still excluded) · long-run return 10 (Growth 10) · AUM 12 + dollar volume 5 (Safety 17) · dividend yield 5 (Income 5). Lower is better for max drawdown, downside deviation, expense ratio and top-10 weight; higher for the rest. Default weights are **Laura Gao's standards** for the **stability core**: the same six categories as the stock model, with more risk and diversification weight (Risk 30 · Cost 18 · Quality 20 · Growth 10 · Safety 17 · Income 5; stocks: Risk 22 · Growth 20 · Safety 15). Number of holdings has weight 0 because yfinance does not provide it (always excluded). Total 100.

**Holdings quality.** Each of a fund's top-10 holdings is scored by the *stock* model against its own industry; the fund's quality is the weight-averaged Composite of the holdings that could be scored. Foreign listings, foreign filers (20-F / 40-F, e.g. TSM, ARM: out of scope for the stock model) and stocks outside the tradeable universe are skipped. If scored holdings cover less than 15% of the fund the metric is excluded and flagged. **Its weight is always 8**, whatever the coverage, so no other metric's weight changes. Instead, low coverage pulls its **score** toward average: **trust = min(1, coverage / 50%)**; adjusted percentile = trust x percentile + (1 - trust) x 50; adjusted Fit = trust x Fit + (1 - trust) x 100. Example: USMV's scored top 10 covers 15.3% of the fund, so trust = 0.31 and its percentile 99.3 becomes 0.31 x 99.3 + 0.69 x 50 = 65.1 (at exactly 15%: 0.3 x score + 0.7 x 50). Because only the top 10 holdings are visible, most diversified funds are under 50% (253 of 325 Large Blend funds). The Breakdown shows coverage, trust and both the raw and adjusted scores. Scores are cached, so only the first run is slow.

**History window (common window).** The sidebar dropdown (3, 5 or 10 years; default 5) sets the window for downside deviation, max drawdown and long-run return; the rules are in section 6. Why a common window: the old rule let a fund with 5-10 years use whatever it had, so a fund listed in April 2020 was measured from just after the March 2020 crash, which flattered its drawdown, downside deviation and return; 7 of the old Large Blend top 10 were such funds. The *History-window diagnostic* (under the ranked table) re-scores the group under the old rule and shows how many of the top 10 / top 30 have under 10 years of history under each rule, how many were measured over a shortened window, and how many funds move more than 10 ranks. Note: in Large Blend most funds are under 5 years old (196 of 325 are too new even for the 5-year window).

**Limits.** yfinance has no total holdings count, so *number of holdings* is excluded for every fund unless you provide `results/etf_holdings_override.csv` (`ticker,n_holdings`). Holdings quality only sees the top 10 holdings.

**Laura hard limits (optional, off by default).** Laura's original absolute numbers - expense ratio <= 0.20%, max drawdown <= 30%, AUM >= $1B, top-10 weight <= 40% - are not the defaults. A checkbox in Tune scoring turns them into *Must be inside* limits for those four metrics (instead of the per-group Laura limit).

## 8. Laura standards (the defaults)
Laura is a moderate-risk client: no big drops, consistent growth, nothing heavy. Both pages open with her **limits**, her **weights** and a **penalty strength of 0.4**; no button is needed. The **Laura standards** button brings all three back; **Industry average** switches every limit to the industry's worse quartile (75th percentile for a ceiling, 25th for a floor) and leaves the weights and k alone.

**Limits are relative to each industry, never one number for everyone.** Each metric has ONE Laura limit on its bad side (a ceiling for lower-is-better, a floor for higher-is-better): a **percentile of the industry's own peer pool** - the same pool the percentile scores use, including the fallback pool when an industry has fewer than 10 valid values (flagged). Values are never multiplied by a percentage, so negative metrics (Sloan, Δ ROIC, net cash) work too. The **spread** in the Fit formula stays the industry 75th - 25th.

| Tier | Stocks | ETFs (per group) | Laura limit |
|---|---|---|---|
| **Risk** | downside deviation, max drawdown, net debt/EBITDA, Sloan (ceiling); interest coverage (floor) | expense ratio, downside deviation, max drawdown, top-10 weight (ceiling); AUM, dollar volume (floor) | industry **median** (50th percentile) |
| **Consistency** | ROIC, gross profitability, Δ ROIC, Δ operating margin (floor) | holdings quality (floor) | industry **40th percentile** |
| **Moderate** | revenue growth, FCF yield, earnings yield, Lynch, shareholder yield (floor) | dividend yield, long-run return (floor) | industry **25th percentile** (75th for a ceiling) |

Example (illustrative numbers): if Semiconductors' median max drawdown were 45% and Utilities' 25%, Laura's ceiling is 45% for a chip stock and 25% for a utility - each is held to the standard of its own industry. The tiers live in one editable dict, `LAURA_TIERS` in `laura.py`, used by both pages and both engines.

**Laura weights** (role-based: same six categories, different totals; each set totals 100). Stocks are the growth driver (more growth, less risk weight); ETFs are the stability core (more risk and diversification weight); bonds guarantee the payments.

| Category | Laura's question | Stocks (growth driver) | ETFs (stability core) |
|---|---|---|---|
| Risk | How much can it drop? | **22**: max drawdown 12, downside deviation 10 | **30**: max drawdown 18, downside deviation 12 |
| Cost | Are we overpaying? | **18**: earnings yield 6, FCF yield 6, Lynch ratio 6 | **18**: expense ratio 18 |
| Quality | Is what we own good and well-built? | **20**: ROIC 8, Sloan accrual ratio 7, gross profitability 5 | **20**: top-10 weight 12, holdings quality 8; number of holdings info only (weight 0) |
| Growth | Is it growing? | **20**: revenue growth 8, Δ ROIC 7, Δ operating margin 5 | **10**: long-run return 10 |
| Safety | Could it fail? | **15**: net debt / EBITDA 8, interest coverage 7 | **17**: AUM 12, dollar volume 5 |
| Income | What does it pay Laura? | **5**: shareholder yield 5 | **5**: dividend yield 5 |

Risk is measured identically for both; the other categories use the measure that fits a single company versus a fund. Both pages show these six sections on Tune scoring in this order, each headed with its live total (e.g. "Risk — 22" on Stocks, "Risk — 30" on ETFs; it follows the sliders) and Laura's question underneath.

The **Laura standards** button restores these weights (and k = 0.4 and Laura's limits).

**Laura standards are always the default.** On first load, and whenever the default weights or limits change (`laura.LAURA_VERSION`, stored in the session as `laura_version`), the app resets the weights, Laura limits, penalty strength and Compare's limit mode to Laura standards. A badge at the top of Tune scoring, the ranked table and Compare says **Laura standards (default)**, or **Custom settings** with a **Reset to Laura standards** button.

**What k = 0.4 means:** each Fit point lost costs 0.4 Overall points (Fit 90 → −4, Fit 75 → −10); a name with Fit 100 keeps its Composite. Under every ranked table a **penalty strength sensitivity** table shows the top 10 at k = 0.2, 0.4 and 0.6 (same weights and limits) and how many of the top 10 change between them.

## 9. Validation and tests
* **Stock/industry backtest** (`backtest_industries.py`, report in `results/backtest_report.txt`): all 145 industries paginate exactly (reported = collected = unique); every S&P 500 stock appears under the correct industry; the exchange-listed names we omit are almost all priced under $5.
* **Self-tests:** 31 for stocks, 19 for ETFs, 4 for the Laura-standards default, 4 for ETF grouping/themes, 6 for Compare, 1 for snapshots and 1 for the data check (66), shown in each "How scoring works" tab; run them all with `./venv/bin/python3 run_tests.py`. New with the split fix: a company with a 10-for-1, 3-for-2 or 1-for-10 reverse split mid-history gets the same Lynch ratio, EPS CAGR and earnings yield as the identical company with no split (also via the SEC-EPS fallback), and real dilution (shares +10%/yr) still lowers per-share growth. New with Laura standards as the default: each model's weights total 100, every metric has a category, and each model's category totals are its role-based Laura totals (stocks Risk 22 / Cost 18 / Quality 20 / Growth 20 / Safety 15 / Income 5; ETFs Risk 30 / 18 / 20 / 10 / 17 / 5); a first load gets Laura weights, limits and k = 0.4; a session saved under an older weights-version is reset (other settings kept); `LAURA_VERSION` must be bumped whenever the defaults change (fingerprint check); the k = 0.2 / 0.4 / 0.6 sensitivity table matches the engine at each k. Earlier, with simple Fit / the Laura penalty: a value on the good side of the limit scores 100, one spread past 50, two spreads past 0 (both directions, both engines); with k = 0.4 a name with Fit 100 has Overall = Composite and every Overall = max(0, Composite - 0.4 x (100 - Fit)); with k = 0 the ranking equals the Composite ranking; why-not-higher points add up to the Laura penalty; holdings quality: coverage never changes any weight, coverage >= 50% leaves the score unchanged, 15% gives 0.3 x score + 0.7 x 50 (and 0.3 x Fit + 0.7 x 100); windows 3 / 5 / 10 all run and a 4-year-old stock / fund is scored at 3 years and too new at 5; a foreign filer is out of every pool and ranking and listed separately; Laura limits = median / 40th / 25th-or-75th per industry and *Industry average* = 75th / 25th. Earlier tests kept: two funds with different listing dates but identical returns over the window get identical scores, a too-new name is listed after every fully scored one, tiers. The Compare tests check that a name's scores (incl. the Laura penalty and limits) in *Each vs its own industry* equal its own industry page, that *Pooled* recomputes percentiles / median / spread / Laura limits on the pooled set, the pooled under-10 fallback, and that a fund in a category and a theme appears once per group (own) or once (pooled). `check_app_flows.py` (53 checks; incl. NFLX's split-adjusted Lynch ratio is positive) drives the real app headlessly on the cached data: the six Tune scoring sections (Risk, Cost, Quality, Growth, Safety, Income) in the same order on both pages, each header showing its live total (it follows the sliders) and Laura's question, number of holdings under ETF Quality as info only; the Laura standards (default) badge on Tune scoring, the ranked table and Compare on first load (stocks and ETFs); changing a weight shows Custom settings and its Reset button restores Laura's; an older weights-version in the session is reset; the penalty sensitivity table; the side-by-side category table on both Tune scoring tabs (each model's Laura totals); the History window dropdown (3 / 5 / 10, default 5, the 3-year caption, 10 years marks more names too new); the Laura penalty and why-not-higher columns and Overall = max(0, Composite - 0.4 x (100 - Fit)) on every row; k = 0 gives Overall = Composite; *Laura standards* resets k to 0.4; Compare with two stock industries in both modes; foreign filers (FNV on the Gold page) listed separately, not ranked; the ETF page has no industry widgets and lists only Morningstar categories + themes; USMV, ESMV and ESGV are in Large Blend and in their themes; ESMV is too new for the 5-year window; *Find a fund*; the history diagnostic runs; Laura limits never leak between groups or pages.
* **Earlier self-test detail:** They cover percentiles, ties, forced scores, exclusion rescaling, fit, limit/weight independence, drawdown, downside deviation, history windows, holdings quality, removals, ranking, and Laura's limits (the max-drawdown ceiling equals each industry's median and differs between industries; tiers, unchanged spread; ETF hard limits).
* **Hand checks:** ROIC, net debt/EBITDA, max drawdown, expense ratio and holdings quality were recomputed independently for sample companies and funds and matched the engine exactly.
* **Bugs found by this testing and fixed:** stale fiscal years from an old SEC revenue tag; debt undercounting; SIC-fragmented peer groups; unreachable ETF categories.

## 10. Known limits (read before relying on a score)
* Fundamentals are **SEC-only**, so foreign filers (20-F / 40-F) are **out of scope** (listed, never ranked, skipped in holdings quality) and new IPOs are not ranked or low data.
* **No operating-income line:** 217 non-financial 10-K filers (e.g. NEM, GE, JNJ, LLY, PFE, MRK, TJX) never report `OperatingIncomeLoss`, so EBIT is missing and ROIC, Δ ROIC, Δ operating margin, earnings yield, net debt/EBITDA and interest coverage are excluded; many become low data. 651 have no cost-of-revenue / gross-profit tag (gross profitability excluded). **NEM (Newmont)** checked in its SEC company facts: no `OperatingIncomeLoss`, no `GrossProfit` / `CostOfRevenue` (its cost line is `CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization`, which excludes D&A and is not gross-profit cost), no `InterestExpense` after 2013 (only `InterestPaidNet`, cash paid) and no company-specific tags. Its 2023 EPS was negative, so the Lynch EPS growth is not meaningful. The data is genuinely not in its XBRL, not a parsing bug, so NEM stays low data (52% of the weight excluded).
* About 5.5% of US stocks have no industry in yfinance and appear in no industry.
* **WInS availability is not checked.** The tool assumes anything over $5 on a major exchange is tradeable; confirm in WInS.
* Scores rank peers against each other; a high rank does not mean a good investment in absolute terms.
* No taxes, bid/ask spreads, correlation or position sizing. Bonds and Treasuries are not covered by this tool.
* Names with less history than the window are *too new for this window* (no drawdown / downside deviation / return) and listed after fully scored names; in Large Blend that is most funds even at 5 years. The 3-year window leaves out 2022. ETF peer groups are yfinance's Morningstar categories plus name-based themes; a theme can miss a fund whose name doesn't say it (fix with `results/theme_overrides.csv`).
* Data are as of the last pull; click Refresh before a decision. If a download fails (network / SSL error) the page shows an error with **Retry** and keeps the previously cached data.

## 11. File map
| File | Role |
|---|---|
| `app.py` | the Streamlit app: sidebar, routing, directories |
| `laura.py`, `range_ui.py` | Laura's per-industry limits (`LAURA_TIERS`) and hard limits; the shared Laura-limit widgets and the two buttons |
| `stock_page.py`, `scoring2.py`, `stock_data.py` | stock UI, engine, raw data (SEC + yfinance) |
| `etf_page.py`, `etf_scoring.py`, `etf_data.py`, `etf_holdings.py` | equity-ETF UI, engine, raw data, holdings-quality service |
| `etf_universe.py`, `build_etf_universe.py` | live ETF pulls and the full-universe builder |
| `etf_data.py` | ETF raw data cache and eligibility rules |
| `industry_universe.py`, `sec_edgar.py` | stock universe pull and SEC downloader |
| `backtest_industries.py`, `build_industry_directory.py` | validation and the industry directory |
| `compare.py`, `compare_ui.py` | Compare industries (stocks) / Compare groups (ETFs): engine (own / pooled) and the shared tab |
| `etf_groups.py` | ETF universe (`results/etf_universe.csv`), Morningstar-category grouping and name-based themes |
| `run_tests.py`, `check_app_flows.py` | all self-test suites; headless app checks on the cached data |
| `refresh_prices.py` | price-only refresh (10 years, fixed end date) of every cached stock and ETF |
| `config.yaml`, `.streamlit/secrets.toml.example` | SEC cache settings; the SEC user-agent goes in Streamlit secrets |
| `METHODOLOGY.md` | the formal spec of the scoring (for the Final Report) |
| `_retired/` | removed features kept for reference: Bond ETF page engine (`fundamental_scoring.py`, `market_query.py`), portfolio planner, bond ladder, Treasury prices, v1 bucket screener (`screener.py`, `fetch_universe.py`), the ETF industry tagger and its checks (`etf_industry.py`, `validate_etf_tags.py`, `results/etf_industry_map.csv`) |

## 12. Troubleshooting
* **"not cached yet"**: click the Pull button for that industry/category.
* **Everything is low data / too new**: the company/fund is new (try a shorter History window) or has thin SEC data; check the flag details. Foreign filers are in the *Not scored: foreign filer* box.
* **A fund or stock is missing**: check the removal reasons line (ETFs), the $5 / exchange filters (stocks), or whether the fund's group has been downloaded (**Pull live ETF data**).
* **SEC errors**: set a real name and email as the SEC user agent: `[sec_edgar] user_agent = "..."` in `.streamlit/secrets.toml` (local, gitignored) or in Streamlit Cloud > Settings > Secrets; `SEC_USER_AGENT` env var also works. SEC requires it.
* **Slow first holdings run**: it scores whole industries with the stock pipeline (about 90 minutes for three categories, then instant).
* **Start clean**: delete the `*.db` files; the app rebuilds on the next Pull.

## 13. Glossary
**Composite**: percentile-based score vs peers. **Fit**: how far a name is past its Laura limits (100 = meets all). **Overall**: Composite minus the Laura penalty k x (100 - Fit). **Laura limit**: the one ceiling (lower-is-better) or floor (higher-is-better) per metric. **Laura penalty**: Overall points lost for missed limits. **History window**: the 3 / 5 / 10 years every name's risk is measured over. **Spread**: peers' 75th minus 25th percentile. **Pool**: the peers a value is ranked against. **Low data**: more than 30% of weight excluded. **ROIC**: operating profit after tax per dollar of invested capital. **WACC**: blended cost of capital. **Sloan ratio**: how much profit is accounting accruals rather than cash. **Downside deviation**: volatility of returns below a minimum acceptable return. **Max drawdown**: worst peak-to-trough fall. **TTM**: trailing twelve months. **AUM**: assets under management. **Laura standards**: the default limits, weights and k = 0.4 (section 8).

## 14. The hosted site (Streamlit Community Cloud)
* **Entry point** `app.py`, **Python 3.13** (3.12 also works; numpy 2.5 needs 3.12+). Packages: `requirements.txt`.
* **Data.** The cloud app does not download anything on start: it reads the committed read-only snapshot `stock_raw.db`, `etf_raw.db` and `holding_scores.db` (plus the CSVs in `results/`). Yahoo often blocks cloud servers, so the *Refresh* buttons may fail there; the page then shows the error and keeps the snapshot. To update the site: click Refresh locally (or run `refresh_prices.py` for prices only), run `./venv/bin/python3 run_tests.py`, then commit the `.db` files and push. `holding_scores.db` must match the stock model's hash (`etf_holdings.rebuild_from_cache()` rebuilds it offline), otherwise the site clears it on first load.
* **Secrets.** Only the SEC user agent (needed for stock refreshes): paste `[sec_edgar]` / `user_agent = "WGHSIC Team <name> <email>"` into the app's Secrets.
