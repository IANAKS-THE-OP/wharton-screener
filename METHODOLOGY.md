# Stock Scoring Methodology (14 metrics)

Research tool for ranking US stocks and equity ETFs. It does not predict returns or trade; a person
makes every buy/sell decision. Equity-ETF scoring is described under "Equity ETFs" at the bottom.
Default comfort ranges and weights on both pages are **Laura Gao's standards** (`laura.py`, section E).

## A. Universe, periods, definitions
* **Universe:** every company in the picked industry that passes the tradeability filters
  (price >= $5, NYSE / Nasdaq / NYSE American / Cboe BZX, no preferred shares, units, warrants, rights).
  Percentiles and default comfort ranges come from this same set.
* **Fundamentals:** SEC EDGAR 10-K annual values, latest fiscal year plus up to 5 prior years.
  A company whose latest 10-K data is older than 16 months is treated as stale and its fundamentals are excluded.
  **Market data:** current price, market cap, beta from yfinance. **Prices:** split- and dividend-adjusted closes.
  The fiscal year end used is recorded and shown for every company.
* EBIT = OperatingIncomeLoss. EBITDA = EBIT + D&A. Total debt = long-term + current debt (assembled from the
  combined tag, or noncurrent + current pieces, or total LongTermDebt + short-term borrowings; never just the current piece).
  Cash = cash & equivalents. Equity = stockholders' equity (noncontrolling-interest-inclusive tag as fallback).
  Invested capital = debt + equity - cash. Tax rate = tax expense / pretax income clamped to 0-35% (21% if pretax <= 0 or missing).
  Average balance = (beginning + end)/2, else end of year. EV = market cap + debt - cash (+ preferred + minority interest).
  FCF = operating cash flow - capex.
* **Bank/insurer:** SIC 6000-6399, or industry name containing Bank, Insurance, Credit Services, Capital Markets, Mortgage Finance.

## B. The 14 metrics (default weights = Laura Gao's standards; total 100)
Default weights are **Laura Gao's standards**: a moderate-risk client who must fund ten fixed payments, so no big drops, steady and consistent growth, and nothing heavy. Risk and balance-sheet metrics (downside deviation, max drawdown, net debt/EBITDA, interest coverage, Sloan accruals) carry the most weight; valuation and growth-momentum metrics (earnings yield, Lynch, Δ operating margin) carry the least. Total 100; every weight is still adjustable in Tune scoring.

| # | Metric | Formula | Better | Wt |
|---|---|---|---|---|
| 1 | ROIC % | EBIT x (1 - tax) / avg invested capital (IC <= 0 or bank: excluded) | higher | 8 |
| 2 | Gross profitability % | gross profit / avg total assets (bank / no COGS: excluded) | higher | 7 |
| 3 | Earnings yield % | EBIT / EV (EV <= 0: excluded; EBIT <= 0: scored 0; bank: excluded) | higher | 4 |
| 4 | FCF yield % | (OCF - capex) / market cap | higher | 6 |
| 5 | Lynch ratio x | (3-yr EPS CAGR % + dividend yield %) / P/E (EPS <= 0: scored 0; start EPS <= 0: excluded) | higher | 4 |
| 6 | Revenue growth % | 3-yr revenue CAGR (fewer years: longest >= 1, labelled) | higher | 7 |
| 7 | Net debt / EBITDA x | (debt - cash) / EBITDA (EBITDA <= 0: scored 0; net cash is valid and best) | lower | 9 |
| 8 | Interest coverage x | EBIT / interest (interest 0, or missing with no debt: scored 100; EBIT <= 0: scored 0; missing with debt: excluded) | higher | 8 |
| 9 | Δ ROIC pp/yr | average year-over-year ROIC change, last 3 years | higher | 7 |
| 10 | Δ Operating margin pp/yr | same, for EBIT / revenue | higher | 5 |
| 11 | Sloan accrual % | (net income - OCF) / avg total assets | lower | 8 |
| 12 | Downside deviation % | sqrt(mean(min(0, r - MAR)^2)) x sqrt(12), 36 monthly returns, MAR = (1 + T-bill)^(1/12) - 1; < 24 months: excluded | lower | 10 |
| 13 | Max drawdown % | max over days of (1 - price / running peak), last 3 years; < 24 months: excluded | lower | 10 |
| 14 | Shareholder yield % | 3-yr avg (dividends + buybacks - stock issued) / market cap | higher | 7 |

"Scored 0 / 100" metrics are forced to worst / best and are **not** in that metric's ranking pool. "Excluded" metrics
drop out and their weight is removed from the denominator.

## C. Red flags (shown, never scored)
Altman distress (Z < 1.81 for manufacturers SIC 2000-3999, Z'' < 1.1 otherwise; skipped for banks, REITs, utilities),
Piotroski F <= 3 (needs >= 7 computable tests), ROIC below WACC, 3-yr avg dividends + buybacks > 3-yr avg FCF,
low data (> 30% of weight excluded), negative invested capital, EV <= 0, interest data missing, short history,
sector/all-stock fallback pool used.

## D. Composite (percentiles)
Pool per metric = industry companies with a valid numeric value. Fewer than 10 -> the sector (yfinance) -> all cached stocks,
with a flag. Rank ascending, ties share the average rank, `p = (rank - 1)/(n - 1) x 100` (n = 1 -> 50).
Higher-is-better score = p; lower-is-better = 100 - p. Forced scores applied after ranking.
`Composite = sum(weight x score) / sum(weights of non-excluded metrics)`.

## E. Fit (comfort ranges in real units, graded)
Every range runs from a **good bound** to a **bad bound** (lower-is-better: low = good, high = bad; higher-is-better: high = good,
low = bad); the two slider handles are these bounds. **Default range = Laura's standards, per industry** (`laura.LAURA_TIERS`), all
percentiles of the industry's own pool (same pool as D, including the fallback pool):
good bound = P10 for lower-is-better, P90 for higher-is-better (`laura.GOOD_PCT`);
bad bound: risk tier = industry median (downside deviation, max drawdown, net debt/EBITDA, Sloan: high = P50; interest coverage: low = P50);
consistency tier = 40th percentile (ROIC, gross profitability, Δ ROIC, Δ operating margin: low = P40);
moderate tier = P25 (revenue growth, FCF yield, earnings yield, Lynch, shareholder yield; P75 if lower-is-better).
Values are never scaled by a percentage. If the good and bad bounds coincide, the metric falls back to P25-P75 (shown on the page); a range is
never zero-width (P25 = P75 -> P5-P95). *Industry average* = P25-P75 for every metric (good = the better quartile).
`spread = P75 - P25` (zero -> sector spread -> all-stock spread); moving a bound does **not** change it.
`metric fit` = 100 at or beyond the good bound; inside the range `100 - 20 x (distance from the good bound / range width)` (100 -> 80);
beyond the bad bound `max(0, 80 - 50 x distance past the bad bound / spread)`. Fit = weighted average with the same weights.
Ranges affect only Fit; weights affect both.

## F. WACC (display + flag)
Cost of equity = risk-free + beta x ERP (risk-free defaults to the live 10-year yield, ERP 5%; missing beta -> industry median).
Cost of debt = interest / average debt. WACC = E/V x ke + D/V x kd x (1 - tax). Banks/insurers: NM.

## G. Overall and rank
`Overall = b x Fit + (1 - b) x Composite` (b default 50%). Optional per-metric hard limits ("must be inside range"; a missing
or forced-worst value fails). If limits remove everyone, the full list is shown with a warning. Order: low-data companies last;
Overall desc; tie-breakers Composite desc, fewer red flags, less excluded weight, ticker A-Z.

## H-I. Display, export, tests
Ranked table, per-company breakdown, "How scoring works" tab with a live worked example, full CSV export (every raw value,
score, fit, flag and setting, timestamped), and 23 self-tests (shown in the app's debug area) covering percentiles, ties,
forced scores, exclusion rescaling, fit, range/weight independence, downside deviation, drawdown, ranking, fallbacks and Laura's
ranges (max-drawdown bound = each industry's median and differs between industries; tiers; unchanged spread; safe fallback).

## Equity ETFs (10 metrics, `etf_scoring.py`)
Same framework as the stock system. Bonds and bond ETFs are not part of this tool (the old Bond ETF page was retired);
bond, leveraged, inverse, commodity and crypto funds are filtered out of the ETF universe.

**Universe (A).** Live pull by Morningstar category (`etf_universe` / `etf_data`). Removed before scoring and never shown:
bond / fixed-income categories, commodity categories, `Trading` (leveraged / inverse) categories, volatility / currency /
alternative / derivative-income / digital-asset categories, and (our additions) allocation, target-date, convertibles,
preferred-stock, long-short, market-neutral, managed-futures, bear-market, tactical and "Other" categories; funds whose
name contains 2x / 3x / Ultra / UltraPro / Inverse / Short / Bear / -1x..-3x; funds priced under $5. "Equity Precious Metals"
(miner stocks) stays. The page shows "X funds found, Y removed (reasons), Z scored". Peer group = the fund's **Morningstar category** (yfinance) or a **theme** from the fund name (ESG / sustainable, low / min volatility, dividend / income, quality, equal weight); a fund is in its category and every matching theme. No industry grouping for ETFs (the industry tagger is in `_retired/`). Universe = `results/etf_universe.csv` (`etf_groups.build_universe`): the rules above, single-stock funds (top holding >= 50%) out, uncategorized funds only if listed in `results/etf_uncategorized_keep.csv`;
a metric with < 10 valid values in the group is ranked against all scored equity ETFs and flagged. 

**Metrics (B), default weight (Laura Gao's standards, total 100).** low cost and small drawdowns first (expense ratio 18, max drawdown 18), then size and concentration (AUM 12, top-10 weight 12), downside deviation 12, long-run return 10, holdings quality 8, dollar volume 5, dividend yield 5. Number of holdings has weight 0 because yfinance does not provide it (always excluded). Total 100.
Expense ratio % (lower, 18) · max drawdown % (lower, 18) · AUM $M (higher, 12) · top-10 weight % (lower, 12) · downside deviation % (lower, 12) · long-run annualized return % (higher, 10) · holdings quality /100 (higher, 8) · average daily dollar volume $M (higher, 5) · trailing-12-month dividend yield % (higher, 5; computed from the dividend history, a real 0% is valid) · number of holdings (higher, 0; **not available from yfinance**, always excluded unless `results/etf_holdings_override.csv` supplies `ticker,n_holdings`, and even then its weight is 0).
Holdings quality = sum(weight x stock Composite) / sum(weights of scored holdings) over the top-10 holdings; each holding is
scored by the stock model (default weights and settings) against its own stock industry, cached in `holding_scores.db`. Foreign
(non-US-ticker) holdings and stocks outside the tradeable stock universe are skipped; under 15% coverage the metric is
excluded and flagged; from 15% to under 50% coverage its weight is `weight x coverage / 100` for that fund (USMV, 15%: 8 -> 1.2).

**Red flags (C).** AUM < $100M (closure risk) · too new for the history window · prices end > 7 days before the group (stale) · top-10 weight > 50% (concentrated) · look-through
coverage < 25% · expense ratio missing · low data (> 30% of weight excluded, listed last) · category fallback.

**History window (D), common window.** 5 years (default) or 10 for downside deviation, max drawdown and long-run return, measured
for EVERY fund over the same window ending on the same date (last `window x 12` monthly returns; drawdown and annualized return over
the last `window` years of daily adjusted closes; 7 days of slack for listing-date rounding). The window is never shortened per fund:
a fund with less history is *too new for this window* (three metrics excluded, flagged) and is ranked after every fully scored fund;
a fund whose prices end > 7 days before its group's newest fund is excluded the same way. `history_diagnostic` compares this with the
old rule (5 to window years: whatever history existed), which flattered funds listed after the March 2020 crash.

**Composite, Fit, Overall, rank (E-G).** Identical to the stock system: percentile `(rank-1)/(n-1) x 100` (ties averaged, n = 1 -> 50,
lower-is-better flipped), `Composite = sum(w x score) / sum(w of included)`, comfort ranges in real units defaulting to the
Laura ranges, graded Fit (section E), `Overall = b x Fit + (1 - b) x Composite`, optional hard limits (never an empty page).
Order: fully scored, then too new for the window, then low data (> 30% of weight excluded, not counting a too-new fund's history
metrics); within each Overall desc, tie-breakers Composite -> fewer flags -> less excluded weight -> ticker. **Tiers**: funds within
1.0 Overall point of their tier's first fund share a tier ("Tier 3: ranks 10-15"). **Why not higher**: each metric beyond its bad bound
and its Overall cost `b x w x (100 - fit) / sum(w)`. Export: per metric raw value, percentile, Laura good/bad bounds, bounds used,
fit, effective and nominal weight; history years used; holdings-quality coverage.
**Default comfort ranges = Laura standards, per group** (same rule as section E, each fund using its own group's pool): good bound P10
(lower-is-better) / P90 (higher-is-better); bad bound at the group median for the risk tier (expense ratio, downside deviation, max drawdown,
top-10 weight: high = P50; AUM, dollar volume: low = P50), P40 for holdings quality, P25 for dividend yield and long-run return. Fit-only; no hard limit is ticked by default.
Two buttons on both pages: *Laura standards* (Laura ranges + Laura weights) and *Industry average* (P25-P75 ranges, weights unchanged).
**Laura hard limits** (optional checkbox, off by default): Laura's old absolute numbers - expense <= 0.20%, max drawdown <= 30%,
AUM >= $1B, top-10 weight <= 40% - become *must be inside* limits for those four metrics (checked against the absolute values, not the range).

**Tests (I).** 17 self-tests in the app's debug area (`etf_scoring.run_self_tests`), including graded Fit, the common history window
(two funds with different listing dates but identical returns over the window score identically), too-new ordering, tiers,
why-not-higher points, holdings-quality weight x coverage, Laura's per-group ranges and the absolute hard limits.

## Compare industries (`compare.py`)
* **Each vs its own industry** (default): each name is prepared and finalized with its own industry / ETF group exactly as on that page (same peer pool and < 10 fallback, same Laura or 25th-75th ranges, the page's weights and blend); the rows are then ranked together with the usual order (low data last, Overall ↓, Composite ↓, fewer flags, less excluded weight, ticker). Self-test: scores equal the industry page.
* **Pooled**: the union of the selected industries (each name once) is one peer group; percentiles, p25 / median / p75, spread and Laura ranges (`LAURA_TIERS`) are recomputed on it, with the same < 10-peer fallback.
* Hand-tuned ranges and Must-be-inside limits are not applied in Compare.

## ETF groupings (`etf_groups.py`)
Peer groups are the **Morningstar category** (yfinance) or a **theme** detected from the fund name (ESG / sustainable, low / min volatility, dividend / income, quality, equal weight). A fund keeps its category and also joins every matching theme. There is no ETF industry grouping; stock industries are used only inside holdings quality. Only VIX / volatility products are excluded as "volatility"; low-volatility equity funds are kept.
