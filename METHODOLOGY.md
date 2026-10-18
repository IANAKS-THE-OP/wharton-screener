# Stock Scoring Methodology (14 metrics)

Research tool for ranking US stocks and equity ETFs. It does not predict returns or trade; a person
makes every buy/sell decision. Equity-ETF scoring is described under "Equity ETFs" at the bottom.
Default Laura limits, weights and penalty strength (k = 0.4) on both pages are **Laura Gao's standards** (`laura.py`, sections E and G).

## A. Universe, periods, definitions
* **Universe:** every company in the picked industry that passes the tradeability filters
  (price >= $5, NYSE / Nasdaq / NYSE American / Cboe BZX, no preferred shares, units, warrants, rights).
  Percentiles and default Laura limits come from this same set. **Foreign filers** (annual report on 20-F / 40-F, no 10-K
  data) are out of scope: left out of every pool and ranking and listed separately as "Not scored: foreign filer".
* **Fundamentals:** SEC EDGAR 10-K annual values, latest fiscal year plus up to 5 prior years.
  A company whose latest 10-K data is older than 16 months is treated as stale and its fundamentals are excluded.
  **Market data:** current price, market cap, beta from yfinance. **Prices:** split- and dividend-adjusted daily closes, 10 years,
  every company ending on the same date (cached prices end 2026-10-14).
  The fiscal year end used is recorded and shown for every company.
* EBIT = OperatingIncomeLoss. EBITDA = EBIT + D&A. Total debt = long-term + current debt (assembled from the
  combined tag, or noncurrent + current pieces, or total LongTermDebt + short-term borrowings; never just the current piece).
  Cash = cash & equivalents. Equity = stockholders' equity (noncontrolling-interest-inclusive tag as fallback).
  Invested capital = debt + equity - cash. Tax rate = tax expense / pretax income clamped to 0-35% (21% if pretax <= 0 or missing).
  Average balance = (beginning + end)/2, else end of year. EV = market cap + debt - cash (+ preferred + minority interest).
  FCF = operating cash flow - capex.
* **Bank/insurer:** SIC 6000-6399, or industry name containing Bank, Insurance, Credit Services, Capital Markets, Mortgage Finance.

## B. The 14 metrics (default weights = Laura Gao's standards; total 100)
Default weights are **Laura Gao's standards**: a moderate-risk client who must fund ten fixed payments, so no big drops, steady and consistent growth, and nothing heavy. Both models use the **same six category totals** (each model sums to 100): **Risk 30 · Cost/valuation 18 · Quality 20 · Growth 10 · Safety 17 · Income 5**. Every metric carries a category label, and both Tune scoring tabs show a table of the six totals for stocks and ETFs side by side (plus this page's current weights). Every weight is still adjustable in Tune scoring. Category per metric: Risk = max drawdown 16, downside deviation 14; Cost/valuation = earnings yield 6, FCF yield 6, Lynch 6; Quality = ROIC 8, Sloan 7, gross profitability 5; Growth = revenue growth 4, Δ ROIC 4, Δ operating margin 2; Safety = net debt/EBITDA 9, interest coverage 8; Income = shareholder yield 5 (`scoring2.CATEGORIES`, `LAURA_CATEGORY_TOTALS`).

| # | Metric | Formula | Better | Wt |
|---|---|---|---|---|
| 1 | ROIC % | EBIT x (1 - tax) / avg invested capital (IC <= 0 or bank: excluded) | higher | 8 |
| 2 | Gross profitability % | gross profit / avg total assets (bank / no COGS: excluded) | higher | 5 |
| 3 | Earnings yield % | EBIT / EV (EV <= 0: excluded; EBIT <= 0: scored 0; bank: excluded) | higher | 6 |
| 4 | FCF yield % | (OCF - capex) / market cap | higher | 6 |
| 5 | Lynch ratio x | (3-yr EPS CAGR % + dividend yield %) / P/E (EPS <= 0: scored 0; start EPS <= 0: excluded) | higher | 6 |
| 6 | Revenue growth % | 3-yr revenue CAGR (fewer years: longest >= 1, labelled) | higher | 4 |
| 7 | Net debt / EBITDA x | (debt - cash) / EBITDA (EBITDA <= 0: scored 0; net cash is valid and best) | lower | 9 |
| 8 | Interest coverage x | EBIT / interest (interest 0, or missing with no debt: scored 100; EBIT <= 0: scored 0; missing with debt: excluded) | higher | 8 |
| 9 | Δ ROIC pp/yr | average year-over-year ROIC change, last 3 years | higher | 4 |
| 10 | Δ Operating margin pp/yr | same, for EBIT / revenue | higher | 2 |
| 11 | Sloan accrual % | (net income - OCF) / avg total assets | lower | 7 |
| 12 | Downside deviation % | sqrt(mean(min(0, r - MAR)^2)) x sqrt(12), the last window x 12 monthly returns (window = 3 / 5 / 10 yrs, default 5), MAR = (1 + T-bill)^(1/12) - 1; less history than the window: excluded (too new) | lower | 14 |
| 13 | Max drawdown % | max over days of (1 - price / running peak), last window years (3 / 5 / 10, default 5); less history than the window: excluded (too new) | lower | 16 |
| 14 | Shareholder yield % | 3-yr avg (dividends + buybacks - stock issued) / market cap | higher | 5 |

"Scored 0 / 100" metrics are forced to worst / best and are **not** in that metric's ranking pool. "Excluded" metrics
drop out and their weight is removed from the denominator.

## C. Red flags (shown, never scored)
Altman distress (Z < 1.81 for manufacturers SIC 2000-3999, Z'' < 1.1 otherwise; skipped for banks, REITs, utilities),
Piotroski F <= 3 (needs >= 7 computable tests), ROIC below WACC, 3-yr avg dividends + buybacks > 3-yr avg FCF,
low data (> 30% of weight excluded), negative invested capital, EV <= 0, interest data missing, too new for the history window,
stale prices (end > 7 days before the industry's newest), sector/all-stock fallback pool used.

**History window.** Downside deviation and max drawdown use one window for every company, chosen in the sidebar: 3, 5 (default)
or 10 years, ending on the same date (7 days of slack for listing-date rounding). It is never shortened for a company: less history
than the window = *too new for this window* (both metrics excluded, flagged, ranked after every fully scored company). The 3-year
window starts in late 2023 and excludes the 2022 decline, so its risk numbers look better than a full market cycle.

## D. Composite (percentiles)
Pool per metric = industry companies with a valid numeric value. Fewer than 10 -> the sector (yfinance) -> all cached stocks,
with a flag. Rank ascending, ties share the average rank, `p = (rank - 1)/(n - 1) x 100` (n = 1 -> 50).
Higher-is-better score = p; lower-is-better = 100 - p. Forced scores applied after ranking.
`Composite = sum(weight x score) / sum(weights of non-excluded metrics)`.

## E. Fit (one Laura limit per metric, one-sided)
Each metric has ONE **Laura limit** in its own units: a **ceiling** for lower-is-better metrics, a **floor** for higher-is-better.
**Default = Laura's standards, per industry** (`laura.LAURA_TIERS`), a percentile of the industry's own pool (same pool as D,
including the fallback pool): risk tier = industry median P50 (downside deviation, max drawdown, net debt/EBITDA, Sloan: ceilings;
interest coverage: floor); consistency tier = P40 (ROIC, gross profitability, Δ ROIC, Δ operating margin: floors); moderate tier = P25
floor (revenue growth, FCF yield, earnings yield, Lynch, shareholder yield; P75 if it were a ceiling). Values are never scaled by a
percentage. *Industry average* = P75 for every ceiling, P25 for every floor.
`spread = P75 - P25` (zero -> sector spread -> all-stock spread); moving a limit does **not** change it.
Only the bad side counts: lower-is-better `distance = max(0, value - ceiling)`; higher-is-better `distance = max(0, floor - value)`;
`metric fit = 100 x max(0, 1 - (distance / spread) / 2)`. So anything on the good side of the limit = 100, one spread past = 50,
two spreads past = 0. Forced-worst metrics have Fit 0, forced-best 100. Fit = weighted average with the same weights.
Limits affect only Fit; weights affect both.

## F. WACC (display + flag)
Cost of equity = risk-free + beta x ERP (risk-free defaults to the live 10-year yield, ERP 5%; missing beta -> industry median).
Cost of debt = interest / average debt. WACC = E/V x ke + D/V x kd x (1 - tax). Banks/insurers: NM.

## G. Overall and rank
`Overall = max(0, Composite - k x (100 - Fit))`, k = the Laura penalty strength (default 0.4; 0 = pure Composite). A company that
meets every limit (Fit 100) keeps its Composite; with k = 0.4 **each Fit point lost costs 0.4 Overall points**. A sensitivity table under
the ranked results shows the top 10 at k = 0.2 / 0.4 / 0.6 and how many names change (`scoring2.penalty_sensitivity`). **Laura penalty** = Composite - Overall; **why not higher** = each missed limit and its
cost `k x w x (100 - metric fit) / sum(w)` (scaled down only if Overall hits 0, so they always add up to the penalty).
Optional per-metric hard limits ("Must be inside": a value past the Laura limit, a missing or forced-worst value fails). If limits remove
everyone, the full list is shown with a warning. Order: fully scored, then too new for the window, then low data (> 30% of weight
excluded, not counting a too-new company's window metrics), then not scored (no 10-K data; no rank); within each Overall desc;
tie-breakers Composite desc, fewer red flags, less excluded weight, ticker A-Z.

## H-I. Display, export, tests
Ranked table, per-company breakdown, "How scoring works" tab with a live worked example, full CSV export (every raw value,
score, Laura limit and limit used, distance past it, Fit, Laura penalty, why not higher, flag and setting incl. k and the history window,
timestamped), and 28 self-tests (shown in the app's debug area) covering percentiles, ties, forced scores, exclusion rescaling, simple Fit
(good side 100, one spread 50, two spreads 0), limit/weight independence, downside deviation, drawdown, ranking, fallbacks, Laura's
limits (max-drawdown ceiling = each industry's median and differs between industries; tiers; Industry average; unchanged spread),
Overall = Composite at Fit 100 (k = 0.4), k = 0 ranking = Composite ranking, the 3 / 5 / 10 windows (a 4-year-old stock is scored at
3 and too new at 5), too-new ordering, foreign filers out of scope, weights total 100 with the six category totals, and the k = 0.2 / 0.4 / 0.6
sensitivity table. `laura.run_self_tests` (4) covers the default: first load = Laura standards, an older weights-version resets, the
version matches the current defaults.

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

**Metrics (B), default weight (Laura Gao's standards, total 100), same six category totals as the stocks.** max drawdown 18 + downside deviation 12 (Risk 30) · expense ratio 18 (Cost/valuation 18) · holdings quality 8 + top-10 weight 12 (Quality 20; number of holdings 0, still excluded) · long-run return 10 (Growth 10) · AUM 12 + dollar volume 5 (Safety 17) · dividend yield 5 (Income 5).
Max drawdown % (lower, 18) · downside deviation % (lower, 12) · expense ratio % (lower, 18) · holdings quality /100 (higher, 8) · top-10 weight % (lower, 12) · long-run annualized return % (higher, 10) · AUM $M (higher, 12) · average daily dollar volume $M (higher, 5) · trailing-12-month dividend yield % (higher, 5; computed from the dividend history, a real 0% is valid) · number of holdings (higher, 0; **not available from yfinance**, always excluded unless `results/etf_holdings_override.csv` supplies `ticker,n_holdings`, and even then its weight is 0).
Holdings quality = sum(weight x stock Composite) / sum(weights of scored holdings) over the top-10 holdings; each holding is
scored by the stock model (default weights and settings, 5-yr window) against its own stock industry, cached in `holding_scores.db`.
Foreign (non-US-ticker) holdings, foreign filers (20-F / 40-F) and stocks outside the tradeable stock universe are skipped; under 15%
coverage the metric is excluded and flagged. Its **weight never changes** (8 by default). Low coverage shrinks the score instead:
`trust = min(1, coverage / 50%)`, `adjusted percentile = trust x percentile + (1 - trust) x 50`,
`adjusted Fit = trust x Fit + (1 - trust) x 100` (USMV, 15%: trust 0.3, so 0.3 x score + 0.7 x 50). Coverage and trust are in the Breakdown.

**Red flags (C).** AUM < $100M (closure risk) · too new for the history window · prices end > 7 days before the group (stale) · top-10 weight > 50% (concentrated) · look-through
coverage < 25% · expense ratio missing · low data (> 30% of weight excluded, listed last) · category fallback.

**History window (D), common window.** 3, 5 (default) or 10 years (the sidebar dropdown, shared with stocks) for downside deviation,
max drawdown and long-run return, measured
for EVERY fund over the same window ending on the same date (last `window x 12` monthly returns; drawdown and annualized return over
the last `window` years of daily adjusted closes; 7 days of slack for listing-date rounding). The window is never shortened per fund:
a fund with less history is *too new for this window* (three metrics excluded, flagged) and is ranked after every fully scored fund;
a fund whose prices end > 7 days before its group's newest fund is excluded the same way. `history_diagnostic` compares this with the
old rule (5 to window years: whatever history existed), which flattered funds listed after the March 2020 crash. The 3-year window
excludes the 2022 decline (risk looks better than a full cycle).

**Composite, Fit, Overall, rank (E-G).** Identical to the stock system: percentile `(rank-1)/(n-1) x 100` (ties averaged, n = 1 -> 50,
lower-is-better flipped), `Composite = sum(w x score) / sum(w of included)`, one Laura limit per metric in real units, simple
one-sided Fit (section E), `Overall = max(0, Composite - k x (100 - Fit))` with k = 0.4 by default (section G), optional hard limits
(never an empty page).
Order: fully scored, then too new for the window, then low data (> 30% of weight excluded, not counting a too-new fund's history
metrics); within each Overall desc, tie-breakers Composite -> fewer flags -> less excluded weight -> ticker. **Tiers**: funds within
1.0 Overall point (of the new Overall) of their tier's first fund share a tier ("Tier 3: ranks 10-15"). **Laura penalty** = Composite -
Overall. **Why not higher**: each missed Laura limit and its Overall cost `k x w x (100 - fit) / sum(w)`. Export: per metric raw value,
percentile, Laura limit, limit used, distance past it, Fit, weight; Laura penalty; history years used; holdings-quality coverage and trust
(and its percentile / Fit before the trust shrink).
**Default Laura limits, per group** (same rule as section E, each fund using its own group's pool): group median P50 for the risk tier
(expense ratio, downside deviation, max drawdown, top-10 weight: ceilings; AUM, dollar volume: floors), P40 floor for holdings quality,
P25 floor for dividend yield and long-run return. Fit-only; no hard limit is ticked by default.
Two buttons on both pages: *Laura standards* (Laura limits + Laura weights + k = 0.4) and *Industry average* (P75 ceilings / P25 floors;
weights and k unchanged). **Laura standards are always the default.** On first load, and whenever the default weights or limits change (`laura.LAURA_VERSION`, stored in the session as `laura_version`), the app resets the weights, Laura limits, penalty strength and Compare's limit mode to Laura standards. A badge at the top of Tune scoring, the ranked table and Compare says **Laura standards (default)**, or **Custom settings** with a **Reset to Laura standards** button.
**Laura hard limits** (optional checkbox, off by default): Laura's old absolute numbers - expense <= 0.20%, max drawdown <= 30%,
AUM >= $1B, top-10 weight <= 40% - become *must be inside* limits for those four metrics (checked against the absolute values, not the Laura limit).

**Tests (I).** 18 self-tests in the app's debug area (`etf_scoring.run_self_tests`), including simple Fit (100 / 50 / 0), the common
history window (two funds with different listing dates but identical returns over the window score identically; 3 / 5 / 10 all run; a
4-year-old fund is scored at 3 and too new at 5), too-new ordering, tiers, why-not-higher points (= k x w x (100 - fit) / sum(w), summing
to the penalty), Overall = Composite at Fit 100 and k = 0 ranking = Composite ranking, holdings quality (weights never change; >= 50%
coverage unchanged; 15% -> 0.3 x score + 0.7 x 50), Laura's per-group limits, Must be inside on the single limit and the absolute hard limits.

## Compare industries (`compare.py`)
* **Each vs its own industry** (default): each name is prepared and finalized with its own industry / ETF group exactly as on that page (same peer pool and < 10 fallback, same Laura or Industry-average limits, the page's weights and penalty strength k); the rows are then ranked together with the usual order (low data last, Overall ↓, Composite ↓, fewer flags, less excluded weight, ticker). Self-test: scores equal the industry page.
* **Pooled**: the union of the selected industries (each name once) is one peer group; percentiles, p25 / median / p75, spread and Laura limits (`LAURA_TIERS`) are recomputed on it, with the same < 10-peer fallback.
* Hand-tuned limits and Must-be-inside limits are not applied in Compare. Foreign filers are listed separately, never ranked. Laura penalty and why not higher are shown and exported.

## ETF groupings (`etf_groups.py`)
Peer groups are the **Morningstar category** (yfinance) or a **theme** detected from the fund name (ESG / sustainable, low / min volatility, dividend / income, quality, equal weight). A fund keeps its category and also joins every matching theme. There is no ETF industry grouping; stock industries are used only inside holdings quality. Only VIX / volatility products are excluded as "volatility"; low-volatility equity funds are kept.
