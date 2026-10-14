## 1. What this is
The **Wharton Investment Screener** is a research tool built for our Wharton Global High School Investment Competition team. The client is Laura Gao: ten fixed $50,000 payments (2033-2042) must be funded with high certainty, and any extra growth goes toward a building contribution. She is a **moderate-risk** client: no big drops, consistent growth, nothing heavy. This tool screens the **equity** side (stocks and equity ETFs); the Treasury/bond planning is done outside it. Every default in the app (comfort ranges and weights) is **Laura's standards** - see section 8.

The tool **ranks candidates that already meet our rules**. It does not predict returns, does not place trades, and does not check what the Wharton simulator (WInS) actually carries. A person on the team makes every buy/sell decision. The score is evidence for our Trading Notes, IPS and Final Report, not a replacement for them.

Two asset types, one idea: *compare each candidate with its peers, and also measure how close it is to the ranges we say we want.*

| Asset type | Peer group | Engine | Metrics |
|---|---|---|---|
| **Stocks** | one of 145 industries | `scoring2.py` | 14 |
| **ETFs** (equity only) | the same 145 industries (plus whole-sector / broad / international groups) | `etf_scoring.py` | 10 |

## 2. Quick start
```bash
cd wharton-screener
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
./venv/bin/streamlit run app.py          # opens http://localhost:8501
```
1. Pick an **asset type** in the sidebar, then an industry (the same 145 industries for stocks and ETFs).
2. If it says *not cached yet*, click **Pull live data**. A stock industry takes about 1-3 minutes; an ETF industry group 1-3 minutes.
3. Read **Ranked results**, click a company/fund in **Breakdown** to see every number, and adjust weights and comfort ranges in **Tune scoring**.
4. Use **Directory** to browse every industry/category and tick a **shortlist**.
5. Download the full **CSV export** for the Final Report.

First-time ETF setup (once): `./venv/bin/python3 build_etf_universe.py` (10-20 min) builds the complete list of every US-listed ETF and its category. Stock directory (once): `backtest_industries.py` then `build_industry_directory.py`.

## 3. The app, screen by screen
**Sidebar.** Asset type (Stocks, ETFs, How everything works), the industry picker (click it and type to search) and asset-specific scoring inputs.

**Ranked results.** Every company/fund in the group, sorted best first: Overall, Fit, Composite, metrics inside range (for example 11/14), red-flag icons, data coverage %. The *flag details* column spells every flag out. **Breakdown** below it explains one company or fund metric by metric.

**Tune scoring.** Weights (a slider per metric, live total), the **blend** between Fit and Composite, and per-metric **comfort ranges** (a two-handle slider plus exact low/high boxes, in the metric's own units) with an optional hard limit ("must be inside range"). Under each metric: the industry 25th / median / 75th and Laura's bound for that metric. Exactly **two buttons**, the same on both pages: **Laura standards** (the default: Laura's per-industry ranges *and* Laura's weights) and **Industry average** (every range = the industry's 25th-75th percentile; weights unchanged). See section 8.

**Directory.** The full list of industries (stocks) or categories (ETFs) with counts, sector, market caps and a shortlist checkbox that saves to `results/`.

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
2. **Fit (comfort range).** A range [low, high] in the metric's real units; the default is **Laura's per-industry range** (section 8), and *Industry average* gives the peers' 25th to 75th percentile values. Only the bad side counts: if higher is better, `distance = max(0, low - value)`; if lower is better, `distance = max(0, value - high)`. Then `fit = 100 x max(0, 1 - (distance / spread) / 2)`, where spread is the peers' 75th minus 25th percentile (Laura's tighter bound never changes the spread). Inside the range scores 100, one spread outside scores 50, two or more score 0.
3. **Weighted averages.** Composite = sum(weight x percentile score) / sum(weights of metrics the company has). Fit uses the same weights. A missing metric is dropped and the denominator shrinks (never treated as zero).
4. **Overall = b x Fit + (1 - b) x Composite.** `b` is the Blend slider (default 50%).

*Worked example:* a stock with ROE-like metric value 5 against a range 10-20 (spread 10), higher is better: distance 5, fit = 100 x (1 - 0.25) = **75**. If it ranks 3rd lowest of 11 peers its percentile score is (3-1)/(11-1) x 100 = **20**. Weights decide how much each counts.

**Ranking.** Companies with more than 30% of their weight excluded are **low data** and always listed last. Otherwise sort by Overall, then Composite, then fewer red flags, then less excluded weight, then ticker A-Z. Optional hard limits can remove companies; if they would remove everyone the full list is shown with a warning, so you never see an empty page.

**Peer pools.** If an industry (stocks) or category (ETFs) has fewer than 10 valid values for a metric, the metric is ranked against the sector (stocks) or all scored equity ETFs instead, and flagged.

## 7. Equity ETFs: step by step
**Universe.** `build_etf_universe.py` pulls **every** ETF on a US exchange (5,731) and labels it with its Morningstar category. After the removals below, about **2,250 equity ETFs** remain.

**Industries, same list as the stocks.** The ETF picker uses the **same 145 industries** as the Stocks page. Morningstar has no industry level, so `etf_industry.py` tags each ETF from what it holds and what it is called:
0. **Single-stock funds** (one holding >= 50% of the fund, e.g. "Toyota ADRhedged") are dropped. **Physical trusts and notes** (GLD, silver trusts) have no stock holdings and are dropped.
1. Take the fund's top-10 holdings and map each to its stock industry (`results/industry_members.csv`).
2. **"Select Sector SPDR" funds** (XLK, XLV, XLU...) and funds **named after a whole sector** (Communication Services, Health Care...) are whole-sector funds, never an industry.
3. **Sector-category and uncategorized funds:** a clear industry word in the fund **name** (semiconductor, biotech, regional bank, gold, uranium, solar, oil services...) decides, unless the top-10 holdings contradict it. With no such word, the holdings must be **pure**: one industry >= 80% of the top-10 weight. "Goldman Sachs" is not "gold".
4. **Size/style and international funds:** the name must agree with the holdings (>= 60% of the top-10 in one industry). Holdings alone never tag them, and a name alone never tags them.
5. Everything else is diversified and goes to an entry at the end of the list: **Whole sector: <stock sector>**, **Thematic / other**, **Broad US market (diversified)** (SPY, VTV, QQQ...) or **International / global**.
6. Funds with **no Morningstar category** (about 800) are in the universe only if rule 3 proves they are an industry equity ETF with real stock holdings (ITA, JETS and ITB are in this group); otherwise they are dropped.

Result (after a full audit of every tagged fund): about 180 industry ETFs across 34 industries, plus about 2,030 diversified funds. The other industries have no ETF, so the picker hides them by default (untick *Hide industries with no ETFs* to see all 145). Only the top 10 holdings are visible, so a fund can still be tagged wrongly; the Breakdown shows the holdings so you can check. Rebuild with `./venv/bin/python3 etf_industry.py` (about a minute; holdings are cached).

**How we know the sifter works.** `validate_etf_tags.py` (writes `results/etf_tag_validation.txt`) checks four things: 43 well-known industry ETFs (SMH, KRE, XBI, ITA, JETS, GDX, URA, XOP, AMLP, CRAK, BOAT...) must land in the right industry (43/43); 49 broad, whole-sector or previously mis-tagged funds (SPY, QQQ, XLK, XLV, XLU, GXPC, CDL, FDN...) must NOT get an industry (49/49); 28 funds that must be absent from the universe entirely (GLD, GLL, silver trusts, single-stock ADR funds, a SPAC fund, weekly-pay option funds) are absent (28/28); and no kept fund may have a leveraged / inverse / bond / crypto / option-income style name (0 leaks). A line-by-line audit of every tagged fund found real faults that the first control list missed, now fixed and added to the checks: physical gold/silver trusts and an UltraShort fund counted as equity, Goldman Sachs funds tagged "Gold" (name match on "gold"), single-stock ADR funds tagged as industries, dividend funds tagged Utilities from their top 10, and a Communication Services fund tagged as one industry. The controls are a sample, not a proof: a fund nobody listed can still be tagged wrongly.

**Peer group.** The funds tagged to the same industry/group are ranked against each other. With fewer than 10 valid values for a metric, that metric is ranked against all downloaded equity ETFs and flagged.

**Downloading.** Click **Pull live ETF data** for the selected group. Big groups (Broad US market, 1,045 funds) take a long time; industry groups take a minute or two.

**Removed before scoring (never shown):** bond / fixed income, commodity, leveraged and inverse ("Trading" categories, and fund names containing 2x, 3x, Ultra, Inverse, Short, Bear...), volatility / currency / alternative / derivative-income / crypto, allocation / target-date / convertibles / preferred / long-short, and structured or non-plain-equity categories (defined outcome / buffer funds, target maturity, equity hedged, multi-asset overlay, managed futures / trend, event driven, money market, multistrategy, macro, relative value), funds with **no Morningstar category** unless they prove to be an industry equity ETF (rule 6 above), option-income / covered-call / buffer / managed-futures funds (by name), and funds priced under $5. "Equity Precious Metals" (gold-miner stocks) stays.

**The 10 metrics** (default weight, better direction): expense ratio (18, lower), max drawdown (18, lower), AUM (12, higher), top-10 weight (12, lower), downside deviation (12, lower), long-run annualized return (10, higher), holdings quality (8, higher), average daily dollar volume (5, higher), trailing-12-month dividend yield (5, higher), number of holdings (0 - always excluded). Default weights are **Laura Gao's standards**: low cost and small drawdowns first (expense ratio 18, max drawdown 18), then size and concentration (AUM 12, top-10 weight 12), downside deviation 12, long-run return 10, holdings quality 8, dollar volume 5, dividend yield 5. Number of holdings has weight 0 because yfinance does not provide it (always excluded). Total 100.

**Holdings quality.** Each of a fund's top-10 holdings is scored by the *stock* model against its own industry; the fund's quality is the weight-averaged Composite of the holdings that could be scored. Foreign listings and stocks outside the tradeable universe are skipped. If scored holdings cover less than 15% of the fund the metric is excluded and flagged. Scores are cached, so only the first run is slow.

**History window.** 5 or 10 years (sidebar) for downside deviation, max drawdown and return. A fund with at least 5 but fewer than the window's years uses what it has (flagged); under 5 years those three metrics are excluded.

**Limits.** yfinance has no total holdings count, so *number of holdings* is excluded for every fund unless you provide `results/etf_holdings_override.csv` (`ticker,n_holdings`). Holdings quality only sees the top 10 holdings.

**Laura hard limits (optional, off by default).** Laura's original absolute numbers - expense ratio <= 0.20%, max drawdown <= 30%, AUM >= $1B, top-10 weight <= 40% - are no longer ranges. A checkbox in Tune scoring turns them into *Must be inside* limits for those four metrics.

## 8. Laura standards (the defaults)
Laura is a moderate-risk client: no big drops, consistent growth, nothing heavy. Both pages open with her **ranges** and her **weights**; no button is needed. The **Laura standards** button brings both back; **Industry average** switches every range to the industry's 25th-75th percentile and leaves the weights alone.

**Ranges are relative to each industry, never one number for everyone.** For each metric only the "bad side" bound matters for Fit (lower-is-better: values above *high* lose points; higher-is-better: values below *low* lose points). Laura moves that bound to a **stricter percentile of the industry's own peer pool** - the same pool the percentile scores use, including the fallback pool when an industry has fewer than 10 valid values (flagged). Values are never multiplied by a percentage, so negative metrics (Sloan, Δ ROIC, net cash) work too. The other side stays at the industry default (25th for lower-is-better, 75th for higher-is-better), and the **spread** in the Fit formula stays the industry 75th - 25th.

| Tier | Stocks | ETFs (per group) | Bad-side bound |
|---|---|---|---|
| **Risk** | downside deviation, max drawdown, net debt/EBITDA, Sloan (high); interest coverage (low) | expense ratio, downside deviation, max drawdown, top-10 weight (high); AUM, dollar volume (low) | industry **median** (50th percentile) |
| **Consistency** | ROIC, gross profitability, Δ ROIC, Δ operating margin (low) | holdings quality (low) | industry **40th percentile** |
| **Moderate** | revenue growth, FCF yield, earnings yield, Lynch, shareholder yield | dividend yield, long-run return | unchanged, 25th-75th |

Example (illustrative numbers): if Semiconductors' median max drawdown were 45% and Utilities' 25%, Laura's cap is 45% for a chip stock and 25% for a utility - each is held to the standard of its own industry. If the stricter percentile equals the other bound (a very bunched-up industry), that metric falls back to the industry default and the page says so. The tiers live in one editable dict, `LAURA_TIERS` in `laura.py`, used by both pages and both engines.

**Laura weights** (each set totals 100): stocks - downside deviation 10, max drawdown 10, net debt/EBITDA 9, interest coverage 8, Sloan 8, ROIC 8, gross profitability 7, Δ ROIC 7, revenue growth 7, shareholder yield 7, FCF yield 6, Δ operating margin 5, earnings yield 4, Lynch 4. ETFs - expense ratio 18, max drawdown 18, AUM 12, top-10 weight 12, downside deviation 12, long-run return 10, holdings quality 8, dollar volume 5, dividend yield 5, number of holdings 0 (always excluded).

## 9. Validation and tests
* **Stock/industry backtest** (`backtest_industries.py`, report in `results/backtest_report.txt`): all 145 industries paginate exactly (reported = collected = unique); every S&P 500 stock appears under the correct industry; the exchange-listed names we omit are almost all priced under $5.
* **Self-tests:** 20 for stocks and 14 for ETFs, shown in each "How scoring works" tab. They cover percentiles, ties, forced scores, exclusion rescaling, fit, range/weight independence, drawdown, downside deviation, history windows, holdings quality, removals, ranking, and Laura's ranges (the max-drawdown bound equals each industry's median and differs between industries; tiers, unchanged spread, safe fallback; ETF hard limits).
* **Hand checks:** ROIC, net debt/EBITDA, max drawdown, expense ratio and holdings quality were recomputed independently for sample companies and funds and matched the engine exactly.
* **Bugs found by this testing and fixed:** stale fiscal years from an old SEC revenue tag; debt undercounting; SIC-fragmented peer groups; unreachable ETF categories.

## 10. Known limits (read before relying on a score)
* Fundamentals are **SEC-only**, so foreign filers and new IPOs are low data.
* About 5.5% of US stocks have no industry in yfinance and appear in no industry.
* **WInS availability is not checked.** The tool assumes anything over $5 on a major exchange is tradeable; confirm in WInS.
* Scores rank peers against each other; a high rank does not mean a good investment in absolute terms.
* No taxes, bid/ask spreads, correlation or position sizing. Bonds and Treasuries are not covered by this tool.
* Funds under 5 years old are low data by design. ETF peer groups come from our industry tagging (section 7), which only sees each fund's top 10 holdings.
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
| `etf_industry.py`, `validate_etf_tags.py` | tags every ETF with a stock industry from its holdings and name (`results/etf_industry_map.csv`); checks the tagging against known funds |
| `industry_universe.py`, `sec_edgar.py` | stock universe pull and SEC downloader |
| `backtest_industries.py`, `build_industry_directory.py` | validation and the industry directory |
| `config.yaml` | SEC user-agent |
| `METHODOLOGY.md` | the formal spec of the scoring (for the Final Report) |
| `_retired/` | removed features kept for reference: Bond ETF page engine (`fundamental_scoring.py`, `market_query.py`), portfolio planner, bond ladder, Treasury prices, v1 bucket screener (`screener.py`, `fetch_universe.py`) |

## 12. Troubleshooting
* **"not cached yet"**: click the Pull button for that industry/category.
* **Everything is low data**: the company/fund is new or foreign; check the flag details.
* **A fund or stock is missing**: check the removal reasons line (ETFs), the $5 / exchange filters (stocks), or whether the fund's group has been downloaded (**Pull live ETF data**).
* **SEC errors**: set a real name and email in `config.yaml` (`sec_edgar.user_agent`); SEC requires it.
* **Slow first holdings run**: it scores whole industries with the stock pipeline (about 90 minutes for three categories, then instant).
* **Start clean**: delete the `*.db` files; the app rebuilds on the next Pull.

## 13. Glossary
**Composite**: percentile-based score vs peers. **Fit**: closeness to your comfort ranges. **Overall**: blend of the two. **Comfort range**: the low-high band you consider good for a metric. **Spread**: peers' 75th minus 25th percentile. **Pool**: the peers a value is ranked against. **Low data**: more than 30% of weight excluded. **ROIC**: operating profit after tax per dollar of invested capital. **WACC**: blended cost of capital. **Sloan ratio**: how much profit is accounting accruals rather than cash. **Downside deviation**: volatility of returns below a minimum acceptable return. **Max drawdown**: worst peak-to-trough fall. **TTM**: trailing twelve months. **AUM**: assets under management. **Laura standards**: the default ranges and weights (section 8).
