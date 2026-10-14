"""
Bond math explained - supporting logic for the app tab of the same name.
===========================================================================
Everything here just packages up bond_ladder.py's already-tested math into
plain-English, checkable pieces: a cashflow-by-cashflow table that proves a
yield is correct, a quick-approximation comparison, a copy-pasteable Google
Sheets formula, and a set of automatic self-tests.
"""

from datetime import date

import bond_ladder


def build_cashflow_table(coupon, maturity, price, ytm, settlement_date=None):
    """Every remaining coupon (and the final face-value repayment), its
    present value at the given yield, running down to the clean (quoted)
    price - the same price that's actually in bonds.csv."""
    if settlement_date is None:
        settlement_date = date.today()

    previous_coupon_date, future_dates = bond_ladder.coupon_schedule(maturity, settlement_date)
    raw_rows = bond_ladder.cashflow_table(coupon, future_dates, settlement_date, previous_coupon_date)
    semi_yield = ytm / 2

    rows = []
    dirty_total = 0.0
    for r in raw_rows:
        years_from_today = (r["date"] - settlement_date).days / 365.25
        pv = r["amount"] if semi_yield == 0 else r["amount"] / (1 + semi_yield) ** r["period"]
        dirty_total += pv
        rows.append({
            "date": r["date"],
            "amount": round(r["amount"], 4),
            "years_from_today": round(years_from_today, 3),
            "present_value": round(pv, 4),
        })

    accrued = bond_ladder.accrued_interest(coupon, future_dates, settlement_date, previous_coupon_date)
    clean_total = dirty_total - accrued

    return {
        "rows": rows,
        "dirty_total": round(dirty_total, 4),
        "accrued_interest": round(accrued, 4),
        "clean_total": round(clean_total, 4),
        "price": price,
    }


def sheets_formula(coupon, maturity, price, settlement_date=None):
    """A literal, copy-pasteable Google Sheets formula using this exact
    bond's numbers. YIELD()'s 6th argument (2) means semiannual coupons,
    matching how these Treasuries actually pay."""
    if settlement_date is None:
        settlement_date = date.today()
    coupon_decimal = coupon / 100
    return (
        f"=YIELD(DATE({settlement_date.year},{settlement_date.month},{settlement_date.day}), "
        f"DATE({maturity.year},{maturity.month},{maturity.day}), {coupon_decimal}, {price}, 100, 2)"
    )


def quick_approximation(coupon, price, maturity, settlement_date=None):
    """The classic shortcut formula, compared against the exact solved
    yield, so you can see how close (and how simple) it is."""
    if settlement_date is None:
        settlement_date = date.today()
    years = bond_ladder.years_between(settlement_date, maturity)
    approx = bond_ladder.approx_yield(coupon, price, years)
    exact = bond_ladder.solve_ytm_from_price(price, coupon, maturity, settlement_date)
    return {
        "approx_yield": approx,
        "exact_yield": exact,
        "difference_bps": (approx - exact) * 10000,
        "years": years,
    }


# ========================================================================
# Self-tests - run automatically, shown as green check / red X in the app.
# ========================================================================

def self_tests():
    results = []

    par_passed = bond_ladder.par_sanity_check(verbose=False)
    results.append({
        "name": "A bond priced at exactly $100 (par) has yield = coupon rate",
        "passed": par_passed,
        "detail": "Checked on three bonds (short/medium/long maturity), settled exactly on a coupon date "
                  "so there's no partial-period noise.",
    })

    today = date.today()
    maturity = bond_ladder._add_months(today, 20 * 6)  # 10 years out, on-cycle
    coupon = 4.500
    previous_coupon_date, future_dates = bond_ladder.coupon_schedule(maturity, today)

    ytm_below = bond_ladder.solve_ytm(95.0, coupon, future_dates, today, previous_coupon_date)
    below_passed = (ytm_below * 100) > coupon
    results.append({
        "name": "A bond priced BELOW $100 has a yield ABOVE its coupon",
        "passed": below_passed,
        "detail": f"A {coupon:.3f}% bond priced at $95.00 solved to a {ytm_below*100:.3f}% yield.",
    })

    ytm_above = bond_ladder.solve_ytm(105.0, coupon, future_dates, today, previous_coupon_date)
    above_passed = (ytm_above * 100) < coupon
    results.append({
        "name": "A bond priced ABOVE $100 has a yield BELOW its coupon",
        "passed": above_passed,
        "detail": f"A {coupon:.3f}% bond priced at $105.00 solved to a {ytm_above*100:.3f}% yield.",
    })

    rebuild_passed, total, _ = bond_ladder.rebuild_sanity_check(verbose=False)
    results.append({
        "name": "Ladder check: every bond yield = 5%, T-bills = 3% -> total near $311,700",
        "passed": rebuild_passed,
        "detail": f"Computed total cost in Jan 2027: ${total:,.2f}.",
    })

    return results
