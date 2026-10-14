"""
Wharton Global High School Investment Competition - Treasury Bond Ladder
==========================================================================
WHAT THIS SCRIPT DOES (in plain English):

Laura Gao needs ten $50,000 payments, one at the start of each year from
2033 through 2042. This planner answers: "how much would we need to set
aside in January 2027 (when the portfolio's growth phase starts) so that,
growing at a specific bond's yield, it becomes $50,000 by each payment
date?" That "cost in 2027" is a present-value number - it's smaller than
$50,000 because money growing at a real yield for years is worth more
later than it is today.

Two different strategies are used, because of a real gap in the bond list:

  - Payments 2037-2042: matched directly to the bond that matures the
    year before (e.g. the 2038 payment uses whatever matures in 2037).
    If more than one bond matures that year, we use the highest-yielding
    one.
  - Payments 2033-2036: nothing in the bond list matures 2032-2035, so
    there's no direct match. Instead we use the 2031-02-15 bond as a
    bridge: money grows at THAT bond's yield until it matures (Feb 2031),
    then grows at an assumed T-bill rate from 2031 until the payment is
    actually needed.

The team trades these bonds on a simulator called WInS, which carries
individual Treasury notes/bonds that Yahoo Finance has NO data for at all.
So a human looks up each bond's price (per $100 face value) on WInS and
types it into bonds.csv by hand - and a bond's yield to maturity (YTM) is
calculated FROM that price, not read off the coupon rate. If a price
hasn't been entered yet, we use a default assumed yield instead (so you
can still see a rough plan before every price is filled in) - the output
table always shows which bonds used a real WInS yield vs. the default.

This tool only PLANS the ladder. It does not buy anything. A human (you
and your team) makes the final purchase decisions on WInS.
"""

import os
from datetime import date

import numpy as np
import pandas as pd
import yaml

# ========================================================================
# STEP 1: Load bonds.csv and config.yaml
# ========================================================================

def load_bonds(path="bonds.csv"):
    """Reads the hand-maintained bond sheet. Two possible price sources:
    `treasury_price` (auto-filled from TreasuryDirect by
    update_treasury_prices.py) and `wins_price` (typed in by hand from the
    actual WInS simulator, since that's what the team actually trades
    against). When both are present, wins_price wins - it's the real
    tradeable price, TreasuryDirect is just a reference/fallback. Either
    can be blank; we leave missing prices as NaN rather than guessing."""
    df = pd.read_csv(path)
    df["maturity"] = pd.to_datetime(df["maturity"]).dt.date
    df["coupon"] = pd.to_numeric(df["coupon"], errors="coerce")

    for col in ["treasury_price", "wins_price"]:
        if col not in df.columns:
            df[col] = pd.NA
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if "treasury_price_date" not in df.columns:
        df["treasury_price_date"] = pd.NA

    # A human-readable label to stand in for a ticker, since individual
    # Treasuries on WInS don't have one: "4.500% 2036-02-15".
    df["label"] = df.apply(lambda r: f"{r['coupon']:.3f}% {r['maturity']}", axis=1)

    df["price"] = df["wins_price"].where(df["wins_price"].notna(), df["treasury_price"])
    df["price_source"] = np.select(
        [df["wins_price"].notna(), df["treasury_price"].notna()],
        ["wins_price", "treasury_price"],
        default=None,
    )
    return df


def load_config(path="config.yaml"):
    with open(path, "r") as f:
        return yaml.safe_load(f)


def exclude_matured_bonds(df, today=None):
    """A bond that has already matured can't be bought - exclude it from
    every calculation, but report which ones so nothing is silently
    dropped. `today` is injectable for testing; defaults to the real date."""
    if today is None:
        today = date.today()
    matured_mask = df["maturity"] < today
    excluded_matured = df.loc[matured_mask, "label"].tolist()
    return df.loc[~matured_mask].copy(), excluded_matured


# ========================================================================
# STEP 2: Bond math (yield to maturity, duration)
# ------------------------------------------------------------------------
# Uses REAL semiannual coupon dates (stepping back from maturity by 6
# calendar months) and the standard "Street convention" fractional-period
# discounting also used by Excel/Google Sheets' YIELD() function: the
# first remaining coupon is discounted by a FRACTION of a period (w = how
# much of the current 6-month coupon period is still ahead of settlement),
# and every coupon after that by one full period more. This is the
# precise, textbook model - a literal table of each coupon's present
# value sums to exactly the price (that's what proves a solved yield is
# correct), and it's what lets us cross-check against Sheets' own YIELD().
# All Treasury notes/bonds in our list pay on the 15th of the month, so
# stepping back 6 calendar months at a time is exact for this dataset.
# ========================================================================

FACE_VALUE = 100.0


def years_between(start_date, end_date):
    return (end_date - start_date).days / 365.25


def _six_months_before(d):
    month = d.month - 6
    year = d.year
    if month <= 0:
        month += 12
        year -= 1
    return date(year, month, d.day)


def coupon_schedule(maturity, settlement_date):
    """Returns (previous_coupon_date, future_dates): every remaining
    semiannual coupon date after settlement (future_dates, sorted,
    ending at maturity), and the most recent coupon date on/before
    settlement (used only to measure the current period's full length)."""
    future_dates = []
    d = maturity
    while d > settlement_date:
        future_dates.append(d)
        d = _six_months_before(d)
    previous_coupon_date = d  # loop exits exactly when d <= settlement_date
    return previous_coupon_date, sorted(future_dates)


def cashflow_table(coupon, future_dates, settlement_date, previous_coupon_date, face=FACE_VALUE):
    """One row per remaining coupon date: the dollar amount paid that date
    (plus face value on the final date) and `period` - how many full
    semiannual periods away it is from settlement (fractional for the
    first one, via the "w" stub fraction)."""
    next_coupon_date = future_dates[0]
    period_days = (next_coupon_date - previous_coupon_date).days
    days_to_next = (next_coupon_date - settlement_date).days
    w = days_to_next / period_days

    n = len(future_dates)
    rows = []
    for k, d in enumerate(future_dates, start=1):
        amount = coupon / 2 + (face if k == n else 0)
        period = (k - 1) + w
        rows.append({"date": d, "amount": amount, "period": period})
    return rows


def accrued_interest(coupon, future_dates, settlement_date, previous_coupon_date):
    """The seller is owed their share of the coupon that's already been
    building up since the last coupon date, even though settlement falls
    mid-period. The QUOTED/traded price (what WInS or TreasuryDirect
    show, and what a human types into bonds.csv) is this "clean" price -
    it already has this amount subtracted out from the bond's true
    economic ("dirty") present value. w (see cashflow_table) is the
    fraction of the current period still AHEAD of settlement, so (1-w)
    is the fraction that's already elapsed and therefore accrued."""
    next_coupon_date = future_dates[0]
    period_days = (next_coupon_date - previous_coupon_date).days
    days_to_next = (next_coupon_date - settlement_date).days
    w = days_to_next / period_days
    return coupon / 2 * (1 - w)


def price_from_yield(y, coupon, future_dates, settlement_date, previous_coupon_date, face=FACE_VALUE):
    """CLEAN price per $100 face value (the quoted/traded price) given
    annual yield `y` (semiannual compounding) and the real remaining
    coupon schedule: the true present value of every future cashflow,
    minus the interest that's already accrued since the last coupon."""
    rows = cashflow_table(coupon, future_dates, settlement_date, previous_coupon_date, face)
    semi_yield = y / 2
    dirty_price = 0.0
    for row in rows:
        if semi_yield == 0:
            dirty_price += row["amount"]
        else:
            dirty_price += row["amount"] / (1 + semi_yield) ** row["period"]
    return dirty_price - accrued_interest(coupon, future_dates, settlement_date, previous_coupon_date)


def solve_ytm(price, coupon, future_dates, settlement_date, previous_coupon_date, lo=0.0, hi=0.30, iterations=60):
    """Bisection search for the annual yield y such that price_from_yield(y)
    equals the observed market price. price_from_yield is monotonically
    DEcreasing in y, so higher price -> lower y, and we can safely bisect.
    No scipy here on purpose - this project keeps dependencies minimal
    (pandas/numpy/pyyaml/yfinance/streamlit only)."""
    args = (coupon, future_dates, settlement_date, previous_coupon_date)
    price_lo = price_from_yield(lo, *args)
    price_hi = price_from_yield(hi, *args)

    # Guard rails: if the observed price is outside what [lo, hi] can
    # produce, the bond is priced at an extreme well outside normal
    # Treasury yields - return the boundary rather than a bogus answer.
    if price >= price_lo:
        return lo
    if price <= price_hi:
        return hi

    for _ in range(iterations):
        mid = (lo + hi) / 2
        price_mid = price_from_yield(mid, *args)
        if abs(price_mid - price) < 1e-10:
            return mid
        # price_from_yield is decreasing in y: if price_mid is too high,
        # the true yield is HIGHER than mid (need more discounting).
        if price_mid > price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def solve_ytm_from_price(price, coupon, maturity, settlement_date=None):
    """Convenience wrapper: builds the coupon schedule and solves YTM in
    one call, given just coupon/maturity/settlement."""
    if settlement_date is None:
        settlement_date = date.today()
    previous_coupon_date, future_dates = coupon_schedule(maturity, settlement_date)
    return solve_ytm(price, coupon, future_dates, settlement_date, previous_coupon_date)


def compute_duration(ytm, coupon, future_dates, settlement_date, previous_coupon_date, price, h=0.0001):
    """Effective (numerical) modified/Macaulay duration: bump the yield up
    and down by a tiny amount `h` and see how much price moves. Not used
    by the ladder table below (the ladder is driven by yield, not
    duration), but kept as a tested, reusable piece of bond math."""
    args = (coupon, future_dates, settlement_date, previous_coupon_date)
    price_up = price_from_yield(ytm + h, *args)
    price_down = price_from_yield(ytm - h, *args)
    modified_duration = -(price_up - price_down) / (2 * h * price)
    macaulay_duration = modified_duration * (1 + ytm / 2)
    return modified_duration, macaulay_duration


def approx_yield(coupon, price, years, face=FACE_VALUE):
    """The classic quick-approximation formula taught alongside exact YTM:
    yield ~= [coupon + (face-price)/years] / [(face+price)/2]. Handy as a
    sanity check, not as precise as the solved yield above."""
    return (coupon + (face - price) / years) / ((face + price) / 2)


def compute_yields(df, default_yield, settlement_date=None):
    """Adds a `ytm` column: the real yield to maturity solved from a price
    when one's available (wins_price takes priority over treasury_price -
    see load_bonds), otherwise `default_yield` - so the ladder can still be
    planned before every price is known. Also adds `yield_source` so the
    table never pretends a default, or a reference price, is the real
    price the team is actually trading at."""
    if settlement_date is None:
        settlement_date = date.today()

    out = df.copy()
    out["years_to_maturity"] = out["maturity"].apply(lambda m: years_between(settlement_date, m))
    out["has_price"] = out["price"].notna()

    ytms, sources = [], []
    for _, row in out.iterrows():
        if row["has_price"]:
            ytms.append(solve_ytm_from_price(row["price"], row["coupon"], row["maturity"], settlement_date))
            if row["price_source"] == "wins_price":
                sources.append("wins_price")
            else:
                date_label = row.get("treasury_price_date")
                sources.append(f"treasury_price ({date_label})" if pd.notna(date_label) else "treasury_price")
        else:
            ytms.append(default_yield)
            sources.append("default")
    out["ytm"] = ytms
    out["yield_source"] = sources
    return out


# ========================================================================
# Correctness check: a bond priced at exactly par (100) must solve to a
# YTM equal to its own coupon rate. This is a clean, self-contained
# sanity check - no external reference price needed.
# ========================================================================

def _add_months(d, months):
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, d.day)


def par_sanity_check(verbose=True):
    """Picks a few bonds, prices them at par, and checks the YTM solver
    recovers the coupon rate. Also checks that a long-maturity low-coupon
    bond shows meaningfully higher duration than a short-maturity
    high-coupon bond (both at par). Returns True if all checks pass.

    Test maturities are set an EXACT multiple of 6 months from today, so
    settlement lands exactly on a coupon date (no stub period, no accrued
    interest) - this isolates whether the SOLVER itself is correct from
    the tiny, standard, Excel/Sheets-YIELD()-equivalent day-count quirk
    that real stub periods introduce for off-cycle settlement dates."""
    today = date.today()
    test_bonds = [
        {"coupon": 4.500, "maturity": _add_months(today, 20 * 6)},   # 10 years, long/low coupon
        {"coupon": 6.500, "maturity": _add_months(today, 2 * 6)},    # 1 year, short/high coupon
        {"coupon": 3.125, "maturity": _add_months(today, 44 * 6)},   # 22 years, very long/low coupon
    ]

    all_passed = True
    results = []
    for tb in test_bonds:
        previous_coupon_date, future_dates = coupon_schedule(tb["maturity"], today)
        price = 100.0  # par
        ytm = solve_ytm(price, tb["coupon"], future_dates, today, previous_coupon_date)
        ytm_pct = ytm * 100
        passed = abs(ytm_pct - tb["coupon"]) < 0.001
        all_passed = all_passed and passed
        mod_dur, mac_dur = compute_duration(ytm, tb["coupon"], future_dates, today, previous_coupon_date, price)
        results.append({
            "coupon": tb["coupon"], "maturity": tb["maturity"], "ytm_pct": ytm_pct,
            "passed": passed, "macaulay_duration": mac_dur,
        })

    duration_check = results[2]["macaulay_duration"] > results[1]["macaulay_duration"] + 5
    all_passed = all_passed and duration_check

    if verbose:
        print("=== PAR-BOND SANITY CHECK ===")
        for r in results:
            status = "PASS" if r["passed"] else "FAIL"
            print(f"  [{status}] coupon {r['coupon']:.3f}% maturing {r['maturity']}: "
                  f"solved YTM = {r['ytm_pct']:.4f}% (expected ~{r['coupon']:.3f}%), "
                  f"Macaulay duration = {r['macaulay_duration']:.2f} yrs")
        status = "PASS" if duration_check else "FAIL"
        print(f"  [{status}] duration check: long/low-coupon bond ({results[2]['macaulay_duration']:.2f} yrs) "
              f"shows meaningfully higher duration than short/high-coupon bond ({results[1]['macaulay_duration']:.2f} yrs)")
        print(f"  Overall: {'ALL CHECKS PASSED' if all_passed else 'SOME CHECKS FAILED'}")

    return all_passed


# ========================================================================
# STEP 3: The ladder itself
# ========================================================================

def build_ladder(bonds_df, base_date, payment_years, payment_amount,
                  bridge_bond_maturity, bridge_cutoff_year, tbill_rate):
    """
    bonds_df must already have a `ytm` column (see compute_yields).

    For payment_year <= bridge_cutoff_year: cost is discounted in two
    stages through the bridge bond - grow at the bridge bond's own yield
    from base_date to its maturity, then at tbill_rate from there to the
    payment date (reversed, since we're discounting back to base_date).

    For payment_year > bridge_cutoff_year: matched directly to whichever
    bond matures in (payment_year - 1), picking the highest-yield one if
    several share that maturity year, and discounted at that bond's own
    yield for the whole span from base_date to the payment date.
    """
    bridge_rows = bonds_df[bonds_df["maturity"] == bridge_bond_maturity]
    bridge_bond = bridge_rows.iloc[0] if not bridge_rows.empty else None

    rows = []
    for payment_year in payment_years:
        payment_date = date(payment_year, 1, 1)
        total_years = years_between(base_date, payment_date)

        if payment_year <= bridge_cutoff_year:
            if bridge_bond is None:
                rows.append({
                    "payment_year": payment_year, "bond_used": None, "yield": None,
                    "years": round(total_years, 2), "cost_2027": None, "face_value_to_buy": None,
                    "price_used": None, "yield_source": None,
                    "status": f"ERROR: bridge bond maturing {bridge_bond_maturity} not found in bonds.csv",
                })
                continue

            bridge_to_payment_years = years_between(bridge_bond_maturity, payment_date)
            value_at_bridge_maturity = payment_amount / (1 + tbill_rate) ** bridge_to_payment_years

            base_to_bridge_years = years_between(base_date, bridge_bond_maturity)
            cost_2027 = value_at_bridge_maturity / (1 + bridge_bond["ytm"]) ** base_to_bridge_years

            chosen = bridge_bond
            strategy = f"bridge via {bridge_bond['label']} then {tbill_rate*100:.2f}% T-bills"
        else:
            target_maturity_year = payment_year - 1
            candidates = bonds_df[bonds_df["maturity"].apply(lambda d: d.year) == target_maturity_year]

            if candidates.empty:
                rows.append({
                    "payment_year": payment_year, "bond_used": None, "yield": None,
                    "years": round(total_years, 2), "cost_2027": None, "face_value_to_buy": None,
                    "price_used": None, "yield_source": None,
                    "status": f"ERROR: no bond matures in {target_maturity_year}",
                })
                continue

            # Highest yield wins; maturity date is just a deterministic tiebreaker.
            chosen = candidates.sort_values(["ytm", "maturity"], ascending=[False, True]).iloc[0]
            cost_2027 = payment_amount / (1 + chosen["ytm"]) ** total_years
            strategy = "direct match"

        price_used = chosen["price"] if chosen["has_price"] else None
        face_value = cost_2027 / (price_used / 100) if price_used is not None else None

        rows.append({
            "payment_year": payment_year,
            "bond_used": chosen["label"],
            "yield": round(chosen["ytm"], 5),
            "years": round(total_years, 2),
            "cost_2027": round(cost_2027, 2),
            "face_value_to_buy": round(face_value, 2) if face_value is not None else None,
            "price_used": price_used,
            "yield_source": chosen["yield_source"],
            "status": strategy,
        })

    return pd.DataFrame(rows)


# ========================================================================
# Check requested alongside the rebuild: with every bond yield forced to
# 5% and the T-bill rate at 3%, the total cost in Jan 2027 across all ten
# payments should land at roughly $311,700. This isolates the LADDER
# ARITHMETIC from the separately-tested YTM solver (yields are forced by
# hand here, not computed from price).
# ========================================================================

def rebuild_sanity_check(bonds_path="bonds.csv", verbose=True):
    bonds = load_bonds(bonds_path)
    bonds, _ = exclude_matured_bonds(bonds)
    bonds = bonds.copy()
    bonds["ytm"] = 0.05
    bonds["has_price"] = False
    bonds["yield_source"] = "forced 5% (check)"

    ladder = build_ladder(
        bonds,
        base_date=date(2027, 1, 1),
        payment_years=range(2033, 2043),
        payment_amount=50000,
        bridge_bond_maturity=date(2031, 2, 15),
        bridge_cutoff_year=2036,
        tbill_rate=0.03,
    )

    total = ladder["cost_2027"].sum()
    expected = 311700
    passed = abs(total - expected) < 200

    if verbose:
        print("=== REBUILD CHECK: all yields=5%, T-bills=3% ===")
        print(ladder[["payment_year", "bond_used", "years", "cost_2027", "status"]].to_string(index=False))
        print(f"\n  Total cost in Jan 2027: ${total:,.2f}  (expected ~${expected:,})")
        print(f"  [{'PASS' if passed else 'FAIL'}]")

    return passed, total, ladder


# ========================================================================
# Putting it all together
# ========================================================================

def run_ladder(bonds_path="bonds.csv", config_path="config.yaml", results_dir="results"):
    cfg = load_config(config_path)
    ladder_cfg = cfg["bond_ladder"]

    raw_bonds = load_bonds(bonds_path)
    not_matured, excluded_matured = exclude_matured_bonds(raw_bonds)
    priced = compute_yields(not_matured, default_yield=ladder_cfg["default_yield"])

    base_date = pd.to_datetime(ladder_cfg["base_date"]).date()
    bridge_bond_maturity = pd.to_datetime(ladder_cfg["bridge_bond_maturity"]).date()
    payment_years = list(range(ladder_cfg["payment_start_year"], ladder_cfg["payment_end_year"] + 1))

    ladder = build_ladder(
        priced,
        base_date=base_date,
        payment_years=payment_years,
        payment_amount=ladder_cfg["payment_amount"],
        bridge_bond_maturity=bridge_bond_maturity,
        bridge_cutoff_year=ladder_cfg["bridge_cutoff_year"],
        tbill_rate=ladder_cfg["tbill_rate"],
    )

    total_cost_2027 = ladder["cost_2027"].sum(skipna=True)

    os.makedirs(results_dir, exist_ok=True)
    out_path = os.path.join(results_dir, f"ladder_{date.today()}.csv")
    ladder.to_csv(out_path, index=False)

    result = {
        "bonds_priced": priced,
        "ladder": ladder,
        "total_cost_2027": total_cost_2027,
        "excluded_matured": excluded_matured,
        "inputs_used": {
            "default_yield": ladder_cfg["default_yield"],
            "tbill_rate": ladder_cfg["tbill_rate"],
            "base_date": str(base_date),
            "bridge_bond_maturity": str(bridge_bond_maturity),
            "bridge_cutoff_year": ladder_cfg["bridge_cutoff_year"],
        },
        "out_path": out_path,
    }

    print_summary(result)
    return result


def print_summary(result):
    print("\n=== BOND LADDER: PRICED BONDS (YTM) ===")
    bp = result["bonds_priced"]
    cols = ["label", "price", "yield_source", "years_to_maturity", "ytm"]
    display = bp[cols].copy()
    display["ytm_pct"] = (display["ytm"] * 100).round(3)
    display = display.drop(columns=["ytm"])
    display["years_to_maturity"] = display["years_to_maturity"].round(2)
    print(display.to_string(index=False))

    print("\n=== LADDER: payment year -> bond used -> cost in Jan 2027 ===")
    print(result["ladder"].to_string(index=False))

    print(f"\nTotal cost in Jan 2027 for all payments: ${result['total_cost_2027']:,.2f}")
    print(f"Inputs used: {result['inputs_used']}")

    print("\n=== EXCLUSIONS ===")
    if result["excluded_matured"]:
        print(f"  {len(result['excluded_matured'])} bond(s) already matured (excluded from all math):")
        for label in result["excluded_matured"]:
            print(f"    {label}")
    else:
        print("  No matured bonds.")

    print(f"\nFull ladder saved to: {result['out_path']}")


if __name__ == "__main__":
    par_sanity_check()
    print()
    rebuild_sanity_check()
    print()
    run_ladder()
