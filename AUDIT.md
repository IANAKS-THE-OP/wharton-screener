# Math & logic audit of the Wharton screener (stocks and ETFs)

*Audit date 2026-10-18, code at commit `a91c2d4`, data as of 2026-10-14. Read-only: no code, weight, limit or data was changed.*
*Every number below was recomputed by `audit_check.py`, a separate program that reads the raw databases directly
(`stock_raw.db`, `etf_raw.db`, `holding_scores.db`, `results/etf_universe.csv`) and re-implements every formula from the written
rules. It imports the app only to put the app's answer next to its own. Run it with `./venv/bin/python3 audit_check.py --net`
(about 2 minutes; `--net` also re-downloads daily prices for the worked examples). Full output: `results/audit_check.json`.*

**How to read this.** The app gives every stock or fund two scores per metric, both 0–100 with 100 best: a **percentile** (how it
ranks against its peers) and a **Fit** (100 if it is on the safe side of Laura's limit, falling to 0 as it goes past it). The
weighted average of the percentiles is the **Composite**, the weighted average of the Fits is the **Fit**, and
**Overall = Composite − 0.4 × (100 − Fit)** (never below 0). A *limit* is a line in real units, e.g. "max drawdown at most 52%".
A *ceiling* is a maximum (for things where lower is better, like losses), a *floor* is a minimum (for things where higher is better, like profits).

## 1. Summary

**The math is right.** For all 602 names in the six groups audited (Entertainment, Semiconductors, Discount Stores, Internet Content &
Information, Large Blend, Large Growth), the independent recomputation matches the app on every raw metric value, every percentile,
every Laura and Industry-average limit, every Fit, every Composite, Fit, Overall and rank: **0 mismatches** (largest difference
about 0.00000000000001). Also confirmed:

* Max drawdown and downside deviation re-computed from freshly downloaded daily prices for IMAX, NFLX, DIS, USMV, VIG and VOO
  match the cached values to within 0.0001 points.
* The k = 0.2 / 0.4 / 0.6 sensitivity table matches a from-scratch recomputation (Entertainment and Large Blend).
* Holdings quality uses the **current** stock scores: all 30 top-10 holdings of USMV, VIG and VOO, re-scored from scratch with
  today's stock weights, equal the cached scores exactly.
* Weights: stocks and ETFs each total exactly 100, and the category totals equal the documented table (Risk 22/30, Cost 18/18,
  Quality 20/20, Growth 20/10, Safety 15/17, Income 5/5).
* No Laura limit is looser than the Industry-average limit, and every limit points the right way (ceilings only on
  lower-is-better metrics, floors only on higher-is-better metrics).
* Foreign filers are out of every pool and ranking. Results are identical when rerun in two fresh processes.

**What is wrong or questionable**, ranked by how much it moves the rankings (section 6 has the fixes and their measured effect):

| # | Finding | Type | Effect on rankings |
|---|---|---|---|
| 1 | **Too-new names still count in the peer pools of every non-history metric.** A fund or stock with less than 5 years of prices is correctly kept out of the drawdown, downside-deviation and long-run-return pools and listed after the fully scored names, but it still counts in the pools (percentiles AND Laura limits) for expense ratio, AUM, top-10 weight, holdings quality, dividend yield, dollar volume, and for stocks every fundamental metric. In Large Blend 196 of 325 funds are too new, so they make up 60% of those pools. | Design gap, undocumented | **Large**: Large Blend top 10 changes by 2 funds and Overalls fall 2–9 points if they are left out; Entertainment: same 10 names, small order changes |
| 2 | **Holdings-quality trust is measured against the whole fund**, so a broad fund whose top 10 is only 15–40% of its assets can never reach full trust. 253 of 286 Large Blend funds with a holdings score are shrunk toward average (median trust 0.77); USMV's holdings-quality percentile 99.6 becomes 65.2 (trust 0.31). Concentrated funds are not shrunk. It works exactly as documented, but it systematically discounts diversified funds. | Design question | **Medium**: Large Blend top 10 changes by 3 funds if coverage is measured within the top 10 instead (USMV stays 4th) |
| 3 | **7 of Laura's 24 limits are identical to the Industry-average limits** (stocks: earnings yield, FCF yield, Lynch, revenue growth, shareholder yield; ETFs: dividend yield, long-run return; number of holdings too, but its weight is 0). For those, "Laura standards" is no stricter than the industry. Some of these floors are at or below zero, so they never bite: shareholder yield ≥ 0% (everyone passes in Entertainment and Semiconductors), ETF dividend yield ≥ 0% in Large Growth, revenue growth ≥ −6.6% in Semiconductors (a company shrinking 6% a year passes Laura's growth floor), Lynch ≥ −0.32 in Semiconductors. | Logic / naming | **Small**: a stricter "moderate" tier changes 1 Entertainment name and 0 Large Blend names |
| 4 | **Small industries are judged against their sector.** 32 of 131 cached stock industries have fewer than 8 US-filer names. Discount Stores has 8, which is under the 10-name minimum for every metric, so every percentile and every Laura limit for COST, WMT, TGT etc. comes from the whole Consumer Defensive sector (about 120 companies). Many limits then pass 100% of Discount Stores (ROIC, gross profitability, revenue growth, interest coverage), so Laura's limits barely act there. This is documented and flagged per metric, but the effect is easy to miss. | Data size, documented | Rankings inside small industries depend on the sector, not the industry |
| 5 | **Rank ties and near-ties.** No exact Overall ties in any audited top 10. Across all 131 industries and 24 ETF categories, 5 exact ties in a top 10, all at Overall 0.0 (the floor) in weak industries, broken by Composite. 14 pairs are within 0.05 points, so they look tied in the 1-decimal display while the hidden decimals decide the order. | Display | None today; worth showing as tied |
| 6 | **Data oddities** (sanity sweep): 10 ROIC values over 200% and 7 revenue-growth values over 500% (small or negative bases, e.g. ARLO ROIC 4,161%); 77 year-over-year share-count jumps of about 2x, 3x or 10x left after the split fix (74 tickers, mostly mergers, SPACs and reverse splits that SEC data could not confirm; two in the audited industries, TKO (UFC + WWE merger, real) and DJT (SPAC)); 4 Large Blend ETFs with no price history at all (SECA, CGFS, JQTY, XXSP) are labelled "too new" instead of "no price data"; 36 stocks have no price history. No drawdowns outside 0–100%, no negative AUM, no expense ratio over 3%, nothing older than 30 days. | Data | Percentiles are rank-based, so outliers barely move scores; only the named companies are affected |
| 7 | **"Industry average" is a misleading name**: it never uses an average. It sets each limit at the worse quartile (75th percentile for a ceiling, 25th for a floor), so a quarter of the industry fails each limit by construction. Suggested name: **"Industry quartile (lenient)"** or **"Bottom-quartile cut-off"**. | Naming | None |
| 8 | **Docs vs code**: 8 places where the docs or on-screen text disagree with the code or are out of date (section 5), e.g. "18 ETF self-tests" (there are 19), the USMV holdings example (99.3 → 65.1 in the docs, 99.6 → 65.2 now), "why not higher ÷ total weight" (the code divides by the weight the company actually has). | Docs | None |

Nothing in this audit is a math bug: every score is computed exactly as the code intends. Findings 1–3 are about whether what
the code intends is what Laura needs.

## 2. Laura limits vs Industry average

**Where the two rules come from.** Both are percentiles of the same peer pool (the industry or Morningstar category, or the
fallback pool when it has fewer than 10 valid values). *Industry average* = the 75th percentile for a ceiling and the 25th for a
floor, so 75% of peers pass. *Laura* (`laura.LAURA_TIERS`) = the **median** (50th) for the risk tier, the **40th** percentile
(floor) for the consistency tier, and the same 25th / 75th as Industry average for everything else ("moderate"). "Stricter" means
the line sits further toward the good side, so fewer peers pass.

### 2.1 The rule for every metric (from `laura.LAURA_TIERS` and `laura.AVG_PCT`)

| Model | Metric | Better | Limit side | Industry average uses | Laura uses (tier) | Laura vs industry |
|---|---|---|---|---|---|---|
| Stocks | ROIC | higher | floor | 25th percentile | 40th (consistency) | stricter |
| Stocks | Gross profitability | higher | floor | 25th percentile | 40th (consistency) | stricter |
| Stocks | Δ ROIC | higher | floor | 25th percentile | 40th (consistency) | stricter |
| Stocks | Δ operating margin | higher | floor | 25th percentile | 40th (consistency) | stricter |
| Stocks | Earnings yield | higher | floor | 25th percentile | 25th (moderate) | **same (not stricter)** |
| Stocks | FCF yield | higher | floor | 25th percentile | 25th (moderate) | **same (not stricter)** |
| Stocks | Lynch ratio | higher | floor | 25th percentile | 25th (moderate) | **same (not stricter)** |
| Stocks | Revenue growth | higher | floor | 25th percentile | 25th (moderate) | **same (not stricter)** |
| Stocks | Net debt / EBITDA | lower | ceiling | 75th percentile | 50th (risk) | stricter |
| Stocks | Interest coverage | higher | floor | 25th percentile | 50th (risk) | stricter |
| Stocks | Sloan accrual | lower | ceiling | 75th percentile | 50th (risk) | stricter |
| Stocks | Downside deviation | lower | ceiling | 75th percentile | 50th (risk) | stricter |
| Stocks | Max drawdown | lower | ceiling | 75th percentile | 50th (risk) | stricter |
| Stocks | Shareholder yield | higher | floor | 25th percentile | 25th (moderate) | **same (not stricter)** |
| ETFs | Expense ratio | lower | ceiling | 75th percentile | 50th (risk) | stricter |
| ETFs | Downside deviation | lower | ceiling | 75th percentile | 50th (risk) | stricter |
| ETFs | Max drawdown | lower | ceiling | 75th percentile | 50th (risk) | stricter |
| ETFs | Number of holdings | higher | floor | 25th percentile | 25th (moderate) | **same (not stricter)** (weight 0, never used) |
| ETFs | Top-10 weight | lower | ceiling | 75th percentile | 50th (risk) | stricter |
| ETFs | AUM | higher | floor | 25th percentile | 50th (risk) | stricter |
| ETFs | Dollar volume | higher | floor | 25th percentile | 50th (risk) | stricter |
| ETFs | Holdings quality | higher | floor | 25th percentile | 40th (consistency) | stricter |
| ETFs | Dividend yield | higher | floor | 25th percentile | 25th (moderate) | **same (not stricter)** |
| ETFs | Long-run return | higher | floor | 25th percentile | 25th (moderate) | **same (not stricter)** |

### 2.2 Real data: medians, both limits, strictness and pass rates

#### Entertainment (stocks; 31 names)

| Metric | Side | Pool (n) | Median | Industry-average limit | Laura limit | Laura stricter by | % of peers passing: industry → Laura |
|---|---|---|---|---|---|---|---|
| Max drawdown | ceiling | industry (25) | 51.7% | ≤ 73.9% | ≤ 51.7% | 22 pts / 30% tighter | 76% → 52% |
| Downside deviation | ceiling | industry (25) | 20.1% | ≤ 29.6% | ≤ 20.1% | 9.5 pts / 32% tighter | 76% → 52% |
| Earnings yield | floor | industry (17) | 3.53% | ≥ 2.30% | ≥ 2.30% | **0 (same limit)** | 76% → 76% |
| FCF yield | floor | industry (19) | 3.27% | ≥ 0.23% | ≥ 0.23% | **0 (same limit)** | 74% → 74% |
| Lynch ratio | floor | industry (12) | 1.28x | ≥ 0.24x | ≥ 0.24x | **0 (same limit)** | 75% → 75% |
| ROIC | floor | industry (22) | 4.89% | ≥ 0.16% | ≥ 4.06% | 3.9 pts (% not meaningful: limit near 0) | 73% → 59% |
| Sloan accrual | ceiling | industry (24) | -3.83% | ≤ -3.10% | ≤ -3.83% | 0.74 pts / 24% tighter | 79% → 50% |
| Gross profitability | floor | industry (10) | 28.4% | ≥ 13.5% | ≥ 23.5% | 10 pts / 74% tighter | 70% → 60% |
| Revenue growth | floor | industry (29) | 7.56% | ≥ 4.06% | ≥ 4.06% | **0 (same limit)** | 76% → 76% |
| Δ ROIC | floor | industry (23) | 0.84 pp/yr | ≥ -1.09 pp/yr | ≥ 0.60 pp/yr | 1.69 pp/yr / 155% tighter | 74% → 61% |
| Δ operating margin | floor | industry (24) | 1.05 pp/yr | ≥ -0.59 pp/yr | ≥ 0.24 pp/yr | 0.83 pp/yr / 140% tighter | 75% → 58% |
| Net debt / EBITDA | ceiling | industry (21) | 3.30x | ≤ 5.49x | ≤ 3.30x | 2.19x / 40% tighter | 76% → 52% |
| Interest coverage | floor | industry (17) | 3.20x | ≥ 1.45x | ≥ 3.20x | 1.76x / 122% tighter | 76% → 53% |
| Shareholder yield | floor | industry (28) | 0.34% | ≥ 0.00% | ≥ 0.00% | **0 (same limit)** | 100% → 100% |

#### Semiconductors (stocks; 45 names)

| Metric | Side | Pool (n) | Median | Industry-average limit | Laura limit | Laura stricter by | % of peers passing: industry → Laura |
|---|---|---|---|---|---|---|---|
| Max drawdown | ceiling | industry (36) | 65.9% | ≤ 74.2% | ≤ 65.9% | 8.3 pts / 11% tighter | 75% → 50% |
| Downside deviation | ceiling | industry (36) | 29.8% | ≤ 36.3% | ≤ 29.8% | 6.5 pts / 18% tighter | 75% → 50% |
| Earnings yield | floor | industry (25) | 1.19% | ≥ 0.41% | ≥ 0.41% | **0 (same limit)** | 76% → 76% |
| FCF yield | floor | industry (41) | 0.89% | ≥ 0.21% | ≥ 0.21% | **0 (same limit)** | 76% → 76% |
| Lynch ratio | floor | industry (17) | -0.26x | ≥ -0.32x | ≥ -0.32x | **0 (same limit)** | 76% → 76% |
| ROIC | floor | industry (38) | 3.18% | ≥ -2.72% | ≥ 1.48% | 4.2 pts / 154% tighter | 74% → 61% |
| Sloan accrual | ceiling | industry (42) | -9.41% | ≤ -4.62% | ≤ -9.41% | 4.8 pts / 104% tighter | 74% → 50% |
| Gross profitability | floor | industry (42) | 27.0% | ≥ 18.3% | ≥ 23.1% | 4.8 pts / 26% tighter | 76% → 60% |
| Revenue growth | floor | industry (43) | -2.42% | ≥ -6.59% | ≥ -6.59% | **0 (same limit)** | 74% → 74% |
| Δ ROIC | floor | industry (39) | -2.78 pp/yr | ≥ -6.32 pp/yr | ≥ -3.58 pp/yr | 2.73 pp/yr / 43% tighter | 74% → 59% |
| Δ operating margin | floor | industry (43) | -2.66 pp/yr | ≥ -7.13 pp/yr | ≥ -5.21 pp/yr | 1.92 pp/yr / 27% tighter | 74% → 60% |
| Net debt / EBITDA | ceiling | industry (29) | 0.67x | ≤ 1.96x | ≤ 0.67x | 1.29x / 66% tighter | 76% → 52% |
| Interest coverage | floor | industry (21) | 11.1x | ≥ 2.21x | ≥ 11.1x | 8.88x / 401% tighter | 81% → 52% |
| Shareholder yield | floor | industry (42) | 0.48% | ≥ 0.00% | ≥ 0.00% | **0 (same limit)** | 83% → 83% |

#### Discount Stores (stocks; 8 names; peer pool for every metric is the **sector**, because the industry has fewer than 10 names)

| Metric | Side | Pool (n) | Median | Industry-average limit | Laura limit | Laura stricter by | % of peers passing: industry → Laura |
|---|---|---|---|---|---|---|---|
| Max drawdown | ceiling | sector (123) | 54.6% | ≤ 68.3% | ≤ 54.6% | 14 pts / 20% tighter | 88% → 50% |
| Downside deviation | ceiling | sector (123) | 20.7% | ≤ 28.3% | ≤ 20.7% | 7.6 pts / 27% tighter | 100% → 50% |
| Earnings yield | floor | sector (96) | 6.11% | ≥ 4.12% | ≥ 4.12% | **0 (same limit)** | 75% → 75% |
| FCF yield | floor | sector (115) | 5.17% | ≥ 3.08% | ≥ 3.08% | **0 (same limit)** | 43% → 43% |
| Lynch ratio | floor | sector (87) | 0.39x | ≥ -0.32x | ≥ -0.32x | **0 (same limit)** | 88% → 88% |
| ROIC | floor | sector (112) | 9.54% | ≥ 4.78% | ≥ 7.21% | 2.4 pts / 51% tighter | 100% → 100% |
| Sloan accrual | ceiling | sector (123) | -4.83% | ≤ -2.64% | ≤ -4.83% | 2.2 pts / 83% tighter | 86% → 86% |
| Gross profitability | floor | sector (121) | 33.6% | ≥ 18.0% | ≥ 28.6% | 11 pts / 59% tighter | 100% → 100% |
| Revenue growth | floor | sector (128) | 2.93% | ≥ -2.08% | ≥ -2.08% | **0 (same limit)** | 100% → 100% |
| Δ ROIC | floor | sector (118) | -0.09 pp/yr | ≥ -2.86 pp/yr | ≥ -1.11 pp/yr | 1.75 pp/yr / 61% tighter | 75% → 75% |
| Δ operating margin | floor | sector (124) | -0.01 pp/yr | ≥ -1.25 pp/yr | ≥ -0.41 pp/yr | 0.83 pp/yr / 67% tighter | 88% → 75% |
| Net debt / EBITDA | ceiling | sector (103) | 1.19x | ≤ 2.72x | ≤ 1.19x | 1.53x / 56% tighter | 100% → 88% |
| Interest coverage | floor | sector (101) | 6.94x | ≥ 2.45x | ≥ 6.94x | 4.49x / 184% tighter | 100% → 100% |
| Shareholder yield | floor | sector (119) | 2.84% | ≥ 0.74% | ≥ 0.74% | **0 (same limit)** | 100% → 100% |

#### Internet Content & Information (stocks; 27 names)

| Metric | Side | Pool (n) | Median | Industry-average limit | Laura limit | Laura stricter by | % of peers passing: industry → Laura |
|---|---|---|---|---|---|---|---|
| Max drawdown | ceiling | industry (21) | 75.4% | ≤ 87.3% | ≤ 75.4% | 12 pts / 14% tighter | 76% → 52% |
| Downside deviation | ceiling | industry (21) | 38.9% | ≤ 42.3% | ≤ 38.9% | 3.4 pts / 8% tighter | 76% → 52% |
| Earnings yield | floor | industry (16) | 6.71% | ≥ 3.05% | ≥ 3.05% | **0 (same limit)** | 75% → 75% |
| FCF yield | floor | industry (22) | 7.84% | ≥ 2.37% | ≥ 2.37% | **0 (same limit)** | 73% → 73% |
| Lynch ratio | floor | industry (10) | 1.35x | ≥ 0.75x | ≥ 0.75x | **0 (same limit)** | 70% → 70% |
| ROIC | floor | industry (24) | 15.6% | ≥ -0.91% | ≥ 9.42% | 10 pts (% not meaningful: limit near 0) | 75% → 58% |
| Sloan accrual | ceiling | industry (25) | -12.1% | ≤ -6.23% | ≤ -12.1% | 5.9 pts / 94% tighter | 76% → 52% |
| Gross profitability | floor | industry (24) | 47.4% | ≥ 34.3% | ≥ 46.0% | 12 pts / 34% tighter | 75% → 67% |
| Revenue growth | floor | industry (27) | 9.67% | ≥ 5.24% | ≥ 5.24% | **0 (same limit)** | 74% → 74% |
| Δ ROIC | floor | industry (24) | 2.91 pp/yr | ≥ -0.07 pp/yr | ≥ 0.49 pp/yr | 0.57 pp/yr (% not meaningful: limit near 0) | 83% → 58% |
| Δ operating margin | floor | industry (27) | 2.01 pp/yr | ≥ 0.49 pp/yr | ≥ 1.14 pp/yr | 0.65 pp/yr / 132% tighter | 78% → 63% |
| Net debt / EBITDA | ceiling | industry (20) | 0.12x | ≤ 0.60x | ≤ 0.12x | 0.48x / 80% tighter | 75% → 65% |
| Interest coverage | floor | industry (13) | 105.4x | ≥ 3.20x | ≥ 105.4x | 102.2x (% not meaningful: limit near 0) | 77% → 54% |
| Shareholder yield | floor | industry (24) | 3.41% | ≥ 0.74% | ≥ 0.74% | **0 (same limit)** | 75% → 75% |

#### Large Blend (ETFs; 325 names)

| Metric | Side | Pool (n) | Median | Industry-average limit | Laura limit | Laura stricter by | % of peers passing: industry → Laura |
|---|---|---|---|---|---|---|---|
| Max drawdown | ceiling | category (129) | 24.6% | ≤ 26.0% | ≤ 24.6% | 1.4 pts / 5% tighter | 75% → 50% |
| Downside deviation | ceiling | category (129) | 9.94% | ≤ 10.3% | ≤ 9.94% | 0.33 pts / 3% tighter | 75% → 50% |
| Expense ratio | ceiling | category (321) | 0.35% | ≤ 0.65% | ≤ 0.35% | 0.3 pts / 46% tighter | 77% → 51% |
| Holdings quality | floor | category (286) | 62.3/100 | ≥ 60.6/100 | ≥ 62.1/100 | 1.51/100 / 2% tighter | 75% → 60% |
| Top-10 weight | ceiling | category (320) | 40.2% | ≤ 46.9% | ≤ 40.2% | 6.6 pts / 14% tighter | 75% → 50% |
| Long-run return | floor | category (129) | 12.1% | ≥ 10.1% | ≥ 10.1% | **0 (same limit)** | 75% → 75% |
| AUM | floor | category (320) | 292.3 $M | ≥ 60.4 $M | ≥ 292.3 $M | 232.0 $M / 384% tighter | 75% → 50% |
| Dollar volume | floor | category (325) | 0.61 $M | ≥ 0.12 $M | ≥ 0.61 $M | 0.48 $M / 386% tighter | 75% → 50% |
| Dividend yield | floor | category (321) | 0.75% | ≥ 0.28% | ≥ 0.28% | **0 (same limit)** | 75% → 75% |

#### Large Growth (ETFs; 166 names)

| Metric | Side | Pool (n) | Median | Industry-average limit | Laura limit | Laura stricter by | % of peers passing: industry → Laura |
|---|---|---|---|---|---|---|---|
| Max drawdown | ceiling | category (53) | 35.2% | ≤ 43.3% | ≤ 35.2% | 8.1 pts / 19% tighter | 75% → 51% |
| Downside deviation | ceiling | category (53) | 12.7% | ≤ 14.9% | ≤ 12.7% | 2.2 pts / 15% tighter | 75% → 51% |
| Expense ratio | ceiling | category (160) | 0.50% | ≤ 0.65% | ≤ 0.50% | 0.15 pts / 23% tighter | 77% → 56% |
| Holdings quality | floor | category (154) | 62.0/100 | ≥ 60.5/100 | ≥ 61.6/100 | 1.10/100 / 2% tighter | 75% → 60% |
| Top-10 weight | ceiling | category (164) | 53.2% | ≤ 60.0% | ≤ 53.2% | 6.8 pts / 11% tighter | 75% → 50% |
| Long-run return | floor | category (53) | 11.6% | ≥ 8.50% | ≥ 8.50% | **0 (same limit)** | 75% → 75% |
| AUM | floor | category (158) | 183.2 $M | ≥ 43.6 $M | ≥ 183.2 $M | 139.5 $M / 320% tighter | 75% → 50% |
| Dollar volume | floor | category (166) | 0.85 $M | ≥ 0.15 $M | ≥ 0.85 $M | 0.70 $M / 454% tighter | 75% → 50% |
| Dividend yield | floor | category (163) | 0.11% | ≥ 0.00% | ≥ 0.00% | **0 (same limit)** | 100% → 100% |

**How to read "Laura stricter by".** Points for % metrics (e.g. "22 pts / 30% tighter" means Laura's drawdown ceiling is 22 points
lower, which is 30% less than the industry ceiling). When the industry limit is close to zero (ROIC, Δ ROIC, interest coverage in
some industries) a percentage is not meaningful and only the units are shown. "% of peers passing" counts the group's own names
with a valid value; for Discount Stores the limits come from the sector pool, which is why pass rates of 100% appear.

**What the tables show.**
* By design, Laura's risk-tier limits let about **half** the peers pass (≈50–52%) and the consistency tier about **60%**, against
  **75%** under Industry average. Every risk and consistency limit is stricter in every group.
* For the five "moderate" stock metrics and the two "moderate" ETF metrics the Laura limit **equals** the Industry-average limit (flagged
  **same** above), so Laura standards add nothing there.
* Laura is **never looser** and **no direction is wrong** (every lower-is-better metric has a ceiling, every higher-is-better a floor).
* The size of the extra strictness depends on how spread out the industry is: Large Blend's drawdown ceiling is only 1.4 points
  tighter (24.6% vs 26.0%), because diversified funds all fall about the same; Entertainment's is 22 points tighter (51.7% vs 73.9%).

### 2.3 Naming
"Industry average" never takes an average: it is the **worse-quartile cut-off**. A reader would expect a limit at the middle of the
industry; in fact it is the most lenient setting in the app, and Laura's risk limits are the ones at the middle. Suggested names:
**"Industry quartile (lenient)"**, **"Bottom-quartile limits"** or **"Lenient industry limits"**. (Not changed.)

### 2.4 The logic behind Laura's limits
Laura is a moderate-risk client who must fund ten fixed payments, so she cannot afford big drops, wants steady growth, and nothing heavy.

* **Risk tier at the median** (stocks: max drawdown, downside deviation, net debt/EBITDA, Sloan accruals, interest coverage; ETFs: expense
  ratio, drawdown, downside deviation, top-10 concentration, AUM, trading volume). *Why it fits:* these are the ways an investment can
  hurt a payment schedule (a deep loss right before a payment is due, a balance sheet that cracks, a fund that closes or can't be sold).
  Asking for "at least as safe as the typical peer" is a natural moderate-risk line. *Honest caveats:* (a) the median moves with the
  industry, so in a volatile industry "median drawdown" can still be a 66% loss (Semiconductors) or 75% (Internet Content): Laura's
  limits are relative, not absolute; (b) Sloan accruals (an earnings-quality signal) and expense ratio are not really "risk", they sit in
  this tier for strictness; (c) AUM and dollar volume at the median fail half the funds on size, including perfectly liquid $300M funds.
* **Consistency tier at the 40th percentile** (ROIC, gross profitability, Δ ROIC, Δ operating margin; ETF holdings quality). *Why it fits:*
  Laura wants businesses that earn well and keep improving, a bit stricter than "not in the bottom quarter". *Honest caveat:* Δ ROIC
  and Δ operating margin measure **improvement**, not **consistency**. A company that collapsed and rebounded scores the same as one
  that improved steadily; a measure of stability (for example the standard deviation of ROIC) would match the label better.
* **Moderate tier = the Industry-average 25th percentile** (revenue growth, FCF yield, earnings yield, Lynch, shareholder yield; ETF dividend
  yield and long-run return). *Why it fits:* valuation and payout metrics are "nice to have" for a moderate client, so only the bottom
  quarter is penalised. *Hard to justify:* **revenue growth** sits here although "steady growth" is one of Laura's three stated goals,
  and in a shrinking industry the floor is negative (Semiconductors −6.6% a year, Discount Stores' sector −2.1%), so Laura's growth floor
  can be met by a company that is getting smaller. Floors at 0% (shareholder yield, dividend yield in some groups) never fail anyone.

## 3. Step-by-step worked examples (IMAX, NFLX, DIS; USMV, VIG, VOO)

Each table shows, metric by metric: the raw value (recomputed from the raw data; the app's value is identical in every row, so it is
not repeated), the peer pool, Laura's limit, the spread used by Fit, the percentile, the Fit, the weight and the points the metric adds
to the Composite (weight × percentile ÷ total weight of the metrics the name has).

### 3.1 Each step, checked
1. **Raw values.** All 14 stock metrics and 10 ETF metrics re-implemented from the 10-K numbers and the price block, including the
   forced cases (EBIT ≤ 0 → earnings yield 0; EPS ≤ 0 → Lynch 0; no interest and no debt → coverage 100) and exclusions (banks, missing
   data, negative invested capital). 0 differences across all 602 names. Max drawdown and downside deviation recomputed from re-downloaded
   daily prices (5 years to 2026-10-14) for the six examples: IMAX 44.21% / 20.11%, NFLX 75.95% / 32.32%, DIS 55.15% / 22.08%,
   USMV 17.93% / 8.13%, VIG 20.39% / 8.55%, VOO 24.52% / 9.76%, all equal to the cached values within 0.0001 points.
2. **Peer group.** Stocks: the cached industry, minus foreign filers (Entertainment drops NIPG and MANU; Semiconductors drops 11 incl. TSM
   and ARM; Internet Content drops 15 incl. BIDU and SPOT). ETFs: the Morningstar category from `results/etf_universe.csv` (Large Blend 325).
   Too-new names (stocks: Entertainment 6, Semiconductors 9; ETFs: Large Blend 196, Large Growth 113) are listed after every fully scored
   name and are **out of the history-metric pools**, but **in** every other metric's pool (finding 1).
3. **Percentiles.** Average-rank method: sort low to high, ties share the average rank, p = (rank − 1) ÷ (n − 1) × 100, n = 1 → 50;
   lower-is-better metrics use 100 − p. Missing values are left out of the pool (not counted as zero). Forced 0 / 100 names are left out
   of the pool and get 0 / 100 afterwards. Fewer than 10 valid values → sector pool, then all stocks (ETFs: all cached equity ETFs).
   Matches the app on every name.
4. **Composite.** Σ(weight × percentile) ÷ Σ(weights of the metrics the name has). Weights total 100. An **excluded** metric drops out and
   the denominator shrinks (IMAX has no Lynch ratio, so it is divided by 94); a **forced-0** metric stays in with 0 (and Fit 0). Checked
   on every name.
5. **Fit.** distance = max(0, value − ceiling) or max(0, floor − value); Fit = 100 × max(0, 1 − (distance ÷ spread) ÷ 2), spread = p75 − p25.
   On the good side → 100 (every row with distance 0 above). Edge cases: spread 0 → the sector spread, then the all-stock (all-ETF)
   spread (no audited group needed it); missing value → metric excluded from Composite and Fit alike; weight 0 (ETF number of holdings)
   → no data for any fund, so it never enters either average. NFLX max drawdown: (75.95 − 51.68) ÷ 39.06 = 0.62 spreads past the
   ceiling → Fit = 100 × (1 − 0.62 ÷ 2) = 68.9.
6. **Overall.** max(0, Composite − 0.4 × (100 − Fit)), computed on unrounded numbers (the table rounds to 1 decimal only for display),
   so names that look tied can be ordered by hidden decimals (finding 5). The k = 0.2 / 0.4 / 0.6 table: our top 10 at each k and our
   "names that change" counts equal the app's (Entertainment 1 / 0 / 1, Large Blend 0 / 0 / 0).
7. **Holdings quality.** Weighted average of the stock Composites of the scored top-10 holdings; trust = min(1, coverage ÷ 50%);
   adjusted percentile = trust × p + (1 − trust) × 50; adjusted Fit = trust × Fit + (1 − trust) × 100; under 15% coverage → excluded
   (39 Large Blend funds). USMV: coverage 15.3% → trust 0.306; raw percentile 99.6 → 0.306 × 99.6 + 0.694 × 50 = 65.2. The holdings'
   Composites are today's stock scores (all 30 re-scored from scratch, identical).
8. **Category totals.** Stocks Risk 22 · Cost 18 · Quality 20 · Growth 20 · Safety 15 · Income 5 = 100; ETFs 30 · 18 · 20 · 10 · 17 · 5 = 100.
   Each metric's weight and category equal the documented table.

**NFLX Lynch ratio, by hand (split-adjusted).** Netflix split 10-for-1 on 2025-11-17. SEC values for 2020–2022 were last reported in
10-Ks filed before the split (old share basis), 2023–2025 in the January 2026 10-K (already restated). So 2022 diluted shares
451.3M × 10 = 4,513M; EPS = net income ÷ shares: 2022 $4,492M ÷ 4,513M = $0.995, 2025 $10,981M ÷ 4,344M = $2.528. 3-yr EPS growth =
(2.528 ÷ 0.995)^(1/3) − 1 = **36.4%** a year; P/E = $69.55 ÷ 2.528 = 27.5; dividend yield 0. Lynch = (36.4 + 0) ÷ 27.5 = **1.32** (app: 1.32).

#### IMAX (Entertainment)

| Metric | Raw value (mine = app) | Pool | Laura limit | Spread (p75 − p25) | Percentile score | Fit | Weight | Points to Composite |
|---|---|---|---|---|---|---|---|---|
| Max drawdown | 44.2% | industry n=25 | 51.7% | 39.1% | 58.3 | 100.0 | 12 | 7.45 |
| Downside deviation | 20.1% | industry n=25 | 20.1% | 12.7% | 50.0 | 100.0 | 10 | 5.32 |
| Earnings yield | 3.05% | industry n=17 | 2.30% | 2.61% | 43.8 | 100.0 | 6 | 2.79 |
| FCF yield | 4.21% | industry n=19 | 0.23% | 5.76% | 61.1 | 100.0 | 6 | 3.90 |
| Lynch ratio |  excluded | industry n=12 | 0.24x | 1.70x | — | — | 6 | — |
| ROIC | 31.4% | industry n=22 | 4.06% | 11.9% | 90.5 | 100.0 | 8 | 7.70 |
| Sloan accrual | -10.7% | industry n=24 | -3.83% | 3.89% | 95.7 | 100.0 | 7 | 7.12 |
| Gross profitability | 28.6% | industry n=10 | 23.5% | 24.7% | 55.6 | 100.0 | 5 | 2.96 |
| Revenue growth | 10.9% | industry n=29 | 4.06% | 8.28% | 64.3 | 100.0 | 8 | 5.47 |
| Δ ROIC | 11.2 pp/yr | industry n=23 | 0.60 pp/yr | 4.96 pp/yr | 95.5 | 100.0 | 7 | 7.11 |
| Δ operating margin | 7.38 pp/yr | industry n=24 | 0.24 pp/yr | 3.62 pp/yr | 91.3 | 100.0 | 5 | 4.86 |
| Net debt / EBITDA | -1.03x | industry n=21 | 3.30x | 4.09x | 95.0 | 100.0 | 8 | 8.09 |
| Interest coverage | 11.4x | industry n=17 | 3.20x | 2.67x | 87.5 | 100.0 | 7 | 6.52 |
| Shareholder yield | 0.00% | industry n=28 | 0.00% | 1.68% | 18.5 | 100.0 | 5 | 0.99 |

Mine: Composite **70.26**, Fit **100.00**, Overall = max(0, 70.26 − 0.4 × (100 − 100.00)) = **70.26**. App: Composite 70.26, Fit 100.00, Overall 70.26, rank 1. Difference: 1.4e-14.

#### NFLX (Entertainment)

| Metric | Raw value (mine = app) | Pool | Laura limit | Spread (p75 − p25) | Percentile score | Fit | Weight | Points to Composite |
|---|---|---|---|---|---|---|---|---|
| Max drawdown | 75.9% | industry n=25 | 51.7% | 39.1% | 20.8 | 68.9 | 12 | 2.50 |
| Downside deviation | 32.3% | industry n=25 | 20.1% | 12.7% | 20.8 | 52.0 | 10 | 2.08 |
| Earnings yield | 4.52% | industry n=17 | 2.30% | 2.61% | 68.8 | 100.0 | 6 | 4.12 |
| FCF yield | 3.27% | industry n=19 | 0.23% | 5.76% | 50.0 | 100.0 | 6 | 3.00 |
| Lynch ratio | 1.32x | industry n=12 | 0.24x | 1.70x | 54.5 | 100.0 | 6 | 3.27 |
| ROIC | 35.6% | industry n=22 | 4.06% | 11.9% | 95.2 | 100.0 | 8 | 7.62 |
| Sloan accrual | 1.52% | industry n=24 | -3.83% | 3.89% | 0.0 | 31.2 | 7 | 0.00 |
| Gross profitability | 40.1% | industry n=10 | 23.5% | 24.7% | 77.8 | 100.0 | 5 | 3.89 |
| Revenue growth | 12.6% | industry n=29 | 4.06% | 8.28% | 82.1 | 100.0 | 8 | 6.57 |
| Δ ROIC | 6.07 pp/yr | industry n=23 | 0.60 pp/yr | 4.96 pp/yr | 90.9 | 100.0 | 7 | 6.36 |
| Δ operating margin | 3.89 pp/yr | industry n=24 | 0.24 pp/yr | 3.62 pp/yr | 82.6 | 100.0 | 5 | 4.13 |
| Net debt / EBITDA | 0.40x | industry n=21 | 3.30x | 4.09x | 90.0 | 100.0 | 8 | 7.20 |
| Interest coverage | 17.2x | industry n=17 | 3.20x | 2.67x | 93.8 | 100.0 | 7 | 6.56 |
| Shareholder yield | 2.47% | industry n=28 | 0.00% | 1.68% | 88.9 | 100.0 | 5 | 4.44 |

Mine: Composite **61.76**, Fit **86.66**, Overall = max(0, 61.76 − 0.4 × (100 − 86.66)) = **56.42**. App: Composite 61.76, Fit 86.66, Overall 56.42, rank 6. Difference: 7.1e-15.

#### DIS (Entertainment)

| Metric | Raw value (mine = app) | Pool | Laura limit | Spread (p75 − p25) | Percentile score | Fit | Weight | Points to Composite |
|---|---|---|---|---|---|---|---|---|
| Max drawdown | 55.2% | industry n=25 | 51.7% | 39.1% | 33.3 | 95.6 | 12 | 4.21 |
| Downside deviation | 22.1% | industry n=25 | 20.1% | 12.7% | 41.7 | 92.3 | 10 | 4.39 |
| Earnings yield | 7.69% | industry n=17 | 2.30% | 2.61% | 81.2 | 100.0 | 6 | 5.13 |
| FCF yield | 5.39% | industry n=19 | 0.23% | 5.76% | 66.7 | 100.0 | 6 | 4.21 |
| Lynch ratio | 3.78x | industry n=12 | 0.24x | 1.70x | 90.9 | 100.0 | 6 | 5.74 |
| ROIC | 12.2% | industry n=22 | 4.06% | 11.9% | 76.2 | 100.0 | 8 | 6.42 |
| Sloan accrual | -2.89% | industry n=24 | -3.83% | 3.89% | 17.4 | 87.9 | 7 | 1.28 |
| Gross profitability |  excluded | industry n=10 | 23.5% | 24.7% | — | — | 5 | — |
| Revenue growth | 4.51% | industry n=29 | 4.06% | 8.28% | 35.7 | 100.0 | 8 | 3.01 |
| Δ ROIC | 1.98 pp/yr | industry n=23 | 0.60 pp/yr | 4.96 pp/yr | 63.6 | 100.0 | 7 | 4.69 |
| Δ operating margin | 1.31 pp/yr | industry n=24 | 0.24 pp/yr | 3.62 pp/yr | 60.9 | 100.0 | 5 | 3.20 |
| Net debt / EBITDA | 1.59x | industry n=21 | 3.30x | 4.09x | 65.0 | 100.0 | 8 | 5.47 |
| Interest coverage | 9.69x | industry n=17 | 3.20x | 2.67x | 81.2 | 100.0 | 7 | 5.99 |
| Shareholder yield | 1.72% | industry n=28 | 0.00% | 1.68% | 77.8 | 100.0 | 5 | 4.09 |

Mine: Composite **57.83**, Fit **97.74**, Overall = max(0, 57.83 − 0.4 × (100 − 97.74)) = **56.93**. App: Composite 57.83, Fit 97.74, Overall 56.93, rank 5. Difference: 0.0e+00.

#### USMV (Large Blend)

| Metric | Raw value (mine = app) | Pool | Laura limit | Spread (p75 − p25) | Percentile score | Fit | Weight | Points to Composite |
|---|---|---|---|---|---|---|---|---|
| Max drawdown | 17.9% | category n=129 | 24.6% | 3.30% | 96.9 | 100.0 | 18 | 17.44 |
| Downside deviation | 8.13% | category n=129 | 9.94% | 0.59% | 98.4 | 100.0 | 12 | 11.81 |
| Expense ratio | 0.15% | category n=321 | 0.35% | 0.50% | 75.2 | 100.0 | 18 | 13.53 |
| Holdings quality | 67.4/100 | category n=286 | 62.1/100 | 2.66/100 | 65.2 | 100.0 | 8 | 5.21 |
| Top-10 weight | 15.3% | category n=320 | 40.2% | 12.0% | 95.9 | 100.0 | 12 | 11.51 |
| Number of holdings | not available (excluded) | — | — | — | — | — | 0 | — |
| Long-run return | 7.62% | category n=129 | 10.1% | 2.81% | 9.4 | 55.2 | 10 | 0.94 |
| AUM | 23,164 $M | category n=320 | 292.3 $M | 1,691 $M | 95.0 | 100.0 | 12 | 11.40 |
| Dollar volume | 187.9 $M | category n=325 | 0.61 $M | 4.50 $M | 95.7 | 100.0 | 5 | 4.78 |
| Dividend yield | 1.41% | category n=321 | 0.28% | 0.72% | 91.9 | 100.0 | 5 | 4.59 |

Mine: Composite **81.22**, Fit **95.52**, Overall = max(0, 81.22 − 0.4 × (100 − 95.52)) = **79.43**. App: Composite 81.22, Fit 95.52, Overall 79.43, rank 4. Difference: 0.0e+00.

#### VIG (Large Blend)

| Metric | Raw value (mine = app) | Pool | Laura limit | Spread (p75 − p25) | Percentile score | Fit | Weight | Points to Composite |
|---|---|---|---|---|---|---|---|---|
| Max drawdown | 20.4% | category n=129 | 24.6% | 3.30% | 92.2 | 100.0 | 18 | 16.59 |
| Downside deviation | 8.55% | category n=129 | 9.94% | 0.59% | 95.3 | 100.0 | 12 | 11.44 |
| Expense ratio | 0.04% | category n=321 | 0.35% | 0.50% | 94.5 | 100.0 | 18 | 17.02 |
| Holdings quality | 65.5/100 | category n=286 | 62.1/100 | 2.66/100 | 79.3 | 100.0 | 8 | 6.35 |
| Top-10 weight | 33.6% | category n=320 | 40.2% | 12.0% | 78.7 | 100.0 | 12 | 9.44 |
| Number of holdings | not available (excluded) | — | — | — | — | — | 0 | — |
| Long-run return | 10.6% | category n=129 | 10.1% | 2.81% | 28.9 | 100.0 | 10 | 2.89 |
| AUM | 127,598 $M | category n=320 | 292.3 $M | 1,691 $M | 98.4 | 100.0 | 12 | 11.81 |
| Dollar volume | 244.1 $M | category n=325 | 0.61 $M | 4.50 $M | 96.9 | 100.0 | 5 | 4.85 |
| Dividend yield | 1.52% | category n=321 | 0.28% | 0.72% | 93.4 | 100.0 | 5 | 4.67 |

Mine: Composite **85.06**, Fit **100.00**, Overall = max(0, 85.06 − 0.4 × (100 − 100.00)) = **85.06**. App: Composite 85.06, Fit 100.00, Overall 85.06, rank 1. Difference: 0.0e+00.

#### VOO (Large Blend)

| Metric | Raw value (mine = app) | Pool | Laura limit | Spread (p75 − p25) | Percentile score | Fit | Weight | Points to Composite |
|---|---|---|---|---|---|---|---|---|
| Max drawdown | 24.5% | category n=129 | 24.6% | 3.30% | 54.7 | 100.0 | 18 | 9.84 |
| Downside deviation | 9.76% | category n=129 | 9.94% | 0.59% | 72.7 | 100.0 | 12 | 8.72 |
| Expense ratio | 0.03% | category n=321 | 0.35% | 0.50% | 96.6 | 100.0 | 18 | 17.38 |
| Holdings quality | 62.1/100 | category n=286 | 62.1/100 | 2.66/100 | 44.0 | 100.0 | 8 | 3.52 |
| Top-10 weight | 37.8% | category n=320 | 40.2% | 12.0% | 63.3 | 100.0 | 12 | 7.60 |
| Number of holdings | not available (excluded) | — | — | — | — | — | 0 | — |
| Long-run return | 13.3% | category n=129 | 10.1% | 2.81% | 78.9 | 100.0 | 10 | 7.89 |
| AUM | 1,729,312 $M | category n=320 | 292.3 $M | 1,691 $M | 99.7 | 100.0 | 12 | 11.96 |
| Dollar volume | 4,997 $M | category n=325 | 0.61 $M | 4.50 $M | 99.7 | 100.0 | 5 | 4.98 |
| Dividend yield | 1.05% | category n=321 | 0.28% | 0.72% | 81.2 | 100.0 | 5 | 4.06 |

Mine: Composite **75.96**, Fit **100.00**, Overall = max(0, 75.96 − 0.4 × (100 − 100.00)) = **75.96**. App: Composite 75.96, Fit 100.00, Overall 75.96, rank 7. Difference: 0.0e+00.

## 4. Sanity sweep (every cached stock and fund)

| Check | Count | Worst examples |
|---|---|---|
| Max drawdown outside 0–100% (stocks / ETFs) | 0 / 0 | — |
| Negative AUM | 0 | — |
| Expense ratio over 3% | 0 | — |
| ROIC over 200% | 10 | ARLO 4,161%, MANH 2,276%, TSSI 1,007%, MEDP 753%, KROS 468%, PTON 463%, DPZ 323%, NATH 273%, RHP 270%, CTGO 246% (tiny or near-zero invested capital, e.g. buybacks wiping out equity) |
| Revenue growth over 500% a year | 7 | SEPN 1,644%, IOVA 1,389%, CRMD 1,077%, QXO 1,020%, IBRX 679%, XLO 590%, TGTX 505% (biotechs going from almost no revenue to some; QXO's acquisitions) |
| Share count jumps of ~10x / 3x / 2x / ½ / ⅓ / 1/10 between years, after split adjustment | 77 jumps in 74 tickers | RYM 10.3x (2024), LYEL 10.3x (2021), SMCIP 10.0x, AKTX 10.0x (2015; splits that SEC data contradicted were dropped), WHLRD/WHLRP 0.10x (2022), BNED 9.9x, BMNR/BMNP 9.8x (2025), IBTA 3.1x. In the audited industries: TKO (2023, UFC + WWE merger: real) and DJT (SPAC merger: real). These affect only the Lynch ratio and the Piotroski dilution test of those names |
| Fetched more than 30 days ago | 0 stocks, 0 ETFs | every stock fetched 2026-10-14 |
| Missing price history | 36 stocks without monthly returns (32 with no prices at all), 20 ETFs | stocks IVAI, IHD, IAE, CHAD, TRXB...; ETFs TLIE, HQDG, AELV, XXSP, SECA, JQTY, CGFS... (4 in Large Blend, shown as "too new" rather than "no price data") |
| Stock price histories not ending 2026-10-14 | 3 end 2026-10-09 (inside the 7-day slack, so still scored), 32 have none | — |
| Stock industries with fewer than 8 names | 32 of 131 | Airports & Air Services 0, Silver 1, Department Stores 2, Textile Manufacturing 2, Wineries & Distilleries 3, Confectioners 3, Copper 3, Auto Manufacturers 4, Medical Distribution 4, Uranium 4 |
| ETF categories with fewer than 8 *downloaded* funds | 15 of 25 | Health 1, Infrastructure 1, Global Small/Mid 1, Real Estate 2, Foreign Large Value 2, Small Value 3, Technology 3 (the universe lists 192 Technology ETFs; most categories have simply not been pulled yet, and a pull fixes it) |
| Exact Overall ties inside a top 10 (all 131 industries + 24 ETF categories) | 5 | Other Industrial Metals & Mining MP / USGO / ALOY (all Overall 0.0), Consulting Services FORR / GRNQ, Integrated Freight FWRD / CYRX, Footwear FWDI / FOSL, all at Overall 0.0 and all broken by Composite. Near-ties under 0.05 points: 14. None in the six audited groups |
| Same results when re-run | identical | two fresh processes with different hash seeds produce byte-identical rankings for all six groups |

**How ties are broken** (code and docs agree): Overall, then Composite, then fewer red flags, then less excluded weight, then ticker A–Z.
ETFs also get *tiers* (funds within 1.0 Overall point of the tier's first fund share a tier).

## 5. Docs vs code

| # | Where | Says | Code does | Matters? |
|---|---|---|---|---|
| 1 | METHODOLOGY.md, ETF "Tests (I)" | "18 self-tests" | 19 ETF self-tests | Count only |
| 2 | HOW_IT_WORKS.md §7 (holdings quality example) | USMV percentile 99.3 → 65.1 | 99.6 → 65.2 with today's holding scores | Stale example |
| 3 | On-screen *How scoring works* (both pages), HOW_IT_WORKS §6 | why-not-higher points = k × weight × (100 − Fit) ÷ **total weight** | ÷ the weight of the metrics the name **has** (the same denominator as Composite); differs whenever a metric is excluded (e.g. IMAX: ÷ 94) | Slightly wrong wording |
| 4 | METHODOLOGY.md B5, HOW_IT_WORKS table | Lynch uses the "3-yr EPS CAGR" | uses the longest run of valid years back from the latest, so a 2-yr CAGR when only 3 years are valid (the detail text says "2-yr") | Undocumented fallback |
| 5 | METHODOLOGY.md B9–B10 | Δ ROIC / Δ operating margin = average change "last 3 years" | averages however many consecutive changes exist (1–3) in the last 4 fiscal years, labelled "fewer than 3 available" | Undocumented fallback |
| 6 | METHODOLOGY.md A, HOW_IT_WORKS §6 | "the cached prices all end 2026-10-14" | 3 stocks end 2026-10-09 (still scored, inside the 7-day slack), 32 stocks and 20 ETFs have no prices | Minor |
| 7 | All docs (pools) | "pool = peers with a valid value"; too-new names have their history metrics excluded | too-new names stay in the pools (and set the Laura limits) for every other metric | Undocumented, ranking-relevant (finding 1) |
| 8 | HOW_IT_WORKS §6 *Peer pools* | fewer than 10 → "the sector (stocks)" | sector, then **all stocks** if the sector also has fewer than 10 (METHODOLOGY.md D says this correctly) | Incomplete |
| — | On-screen ETF page | holdings quality = stock Composite of each holding | true, but always with the **default** stock weights and the **5-year** window, whatever the sidebar window or the stock page's sliders say (METHODOLOGY.md says so; the ETF page does not) | Clarity |

Everything else checked agrees: the percentile formula, tie handling, n = 1 → 50, the direction flip, Composite / Fit / Overall formulas,
k = 0.4, the 3 / 5 / 10-year window and its 7-day slack, the stale-price rule, the forced 0 / 100 cases, the Laura tiers (on both
Tune scoring captions, both How tabs, HOW_IT_WORKS §8 and METHODOLOGY.md E), the Industry-average rule, the weights and category totals,
the trust formula, the 15% coverage cut-off and the rank order.

## 6. Recommended fixes (not applied: waiting for your approval)

Effects were measured with `audit_check.py` on the same data (its what-if switches; the app is unchanged).

| # | Fix | Entertainment top 10 | Large Blend top 10 |
|---|---|---|---|
| A | **Keep too-new names out of every peer pool** (or at least out of the pools that set Laura's limits), so fully scored names are only compared with each other; too-new names would then be scored against that same pool. Alternatively keep today's behaviour and document it. | Same 10 names; small order changes (MCS 10th → 9th, MSGS 9th → 10th); Overalls move up to ±2.4 (NFLX 56.4 → 54.0, DIS 56.9 → 58.6) | **2 of 10 change**: RECS and FLRG drop out, IVV and QLV come in; FLQL 5th → 9th; USMV stays 4th; Overalls fall 2–9 points (JQUA 83.5 → 77.6, FLQL 79.0 → 69.9) |
| B | **Measure holdings-quality coverage within the top 10** (scored weight ÷ top-10 weight), so a diversified fund isn't discounted just because its top 10 is a small slice. Keep the 50% trust rule on that measure. | No change (stocks) | **3 of 10 change**: SPTM, VOO and SPYM drop out, QUS, AVUS and QLV come in; USMV 79.4 → 82.2, still 4th |
| C | **Make Laura's "moderate" tier stricter than Industry average** (for example the 40th percentile for floors / 60th for ceilings), and **move revenue growth to the consistency tier** to match "steady growth". At minimum, floor the moderate floors at a value that can fail (e.g. revenue growth ≥ 0%). | 1 of 10 changes (p40 test): MSGS out, RSVR in | 0 of 10 change; USMV 4th → 5th (Overall 79.4 → 78.3) |
| D | **Rename "Industry average"** to "Industry quartile (lenient)" (button, captions, docs). | No change | No change |
| E | **Show near-ties**: mark names within 0.1 Overall points as tied (the ETF page already has tiers; add the same to stocks). | No change in order | No change in order |
| F | **Label funds with no prices as "no price data"**, not "too new" (SECA, CGFS, JQTY, XXSP in Large Blend). | No change | No change (they rank after the top 10 either way) |
| G | **Fix the 8 doc mismatches** in section 5. | No change | No change |
| H | Optional data clean-ups: cap or flag ROIC > 200% / revenue growth > 500% in the breakdown; review the 74 tickers with leftover 2x / 3x / 10x share jumps. | No change in the top 10s | No change |

A and B are the two decisions that actually move the Large Blend list; they are policy choices for the team rather than bugs.
