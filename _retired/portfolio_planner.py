"""
Portfolio Planner - Laura Gao's whole portfolio, three ways.
================================================================
WHAT THIS DOES (plain English):

Replaces separate spreadsheet tabs with one model that answers "how much
does Laura end up with at the start of 2033, split into the reserve (safe
money for the ten $50,000 payments), the building contribution, and
Laura's cushion?" - under three different STRATEGIES for how the money is
invested along the way, and three different MARKET SCENARIOS.

Case A - Original: the whole portfolio grows together at one blended
  return from 2027 to 2033, then the reserve is carved out all at once.
Case B - Bonds-first: all of the 2027 contribution goes toward building
  the Treasury ladder immediately; whatever's left over (plus all of
  2028's money) fills in the rest.
Case C - Split: EVERY contribution (2027 and 2028) is split 85/10/5 on
  its own, so the ladder gets built in two pieces across both years.

All three cases use the SAME scenario assumptions and the SAME ladder
bond-matching rules (see bond_ladder.py for the Treasury side) - they're
just different STRATEGIES for sequencing when things get bought.

This tool only PLANS. It does not buy anything - a human decides.
"""

from datetime import date

import pandas as pd

import bond_ladder

# ========================================================================
# Shared inputs
# ========================================================================

CONTRIB_2027 = 300_000
CONTRIB_2028 = 150_000
PAYMENT = 50_000
PAYMENT_YEARS = list(range(2033, 2043))  # 10 payments, Jan 1 of each year
BASE_YEAR = 2027

BOND_ALLOC = 0.85
ETF_ALLOC = 0.10
STOCK_ALLOC = 0.05
BUILDING_PCT = 0.85
CUSHION_PCT = 0.15

BRIDGE_BOND_MATURITY = date(2031, 2, 15)
BRIDGE_CUTOFF_YEAR = 2036  # payments at/before this year use the bridge bond

DEFAULT_SCENARIOS = {
    "Bear":         {"ladder_yield": 0.03, "bond_return": 0.03, "etf_return": -0.06, "stock_return": -0.06, "blended_return": -0.06},
    "Conservative": {"ladder_yield": 0.05, "bond_return": 0.05, "etf_return": 0.06,  "stock_return": 0.07,  "blended_return": 0.06},
    "Aggressive":   {"ladder_yield": 0.04, "bond_return": 0.04, "etf_return": 0.12,  "stock_return": 0.15,  "blended_return": 0.15},
}


def default_inputs():
    """Every editable 'shared input' in one dict, so the sidebar can
    override any of them without touching the module-level constants
    (which stay as the documented defaults the self-checks verify)."""
    return {
        "contrib_2027": CONTRIB_2027, "contrib_2028": CONTRIB_2028,
        "payment": PAYMENT, "payment_years": list(PAYMENT_YEARS), "base_year": BASE_YEAR,
        "bond_alloc": BOND_ALLOC, "etf_alloc": ETF_ALLOC, "stock_alloc": STOCK_ALLOC,
        "building_pct": BUILDING_PCT, "cushion_pct": CUSHION_PCT,
    }


def pv_annuity_due(rate, n, pmt):
    """Excel PV(rate, n, -pmt, 0, 1): present value of n payments of pmt,
    each at the START of a period (the first payment is NOT discounted)."""
    if rate == 0:
        return pmt * n
    pv_ordinary = pmt * (1 - (1 + rate) ** -n) / rate
    return pv_ordinary * (1 + rate)


def ladder_needed_pv(y, inputs=None):
    """Sum over every payment of payment / (1+y)^(payment_year - base_year) -
    how many base_year-dollars it takes to fund the whole ladder."""
    inputs = inputs or default_inputs()
    return sum(inputs["payment"] / (1 + y) ** (yr - inputs["base_year"]) for yr in inputs["payment_years"])


# ========================================================================
# Which bond backs which payment (shared by all three cases)
# ========================================================================

def assign_bonds_to_payments(scenario_yield, bonds_path="bonds.csv", settlement_date=None):
    """Returns {payment_year: bond_row} using the same rule everywhere:
    payments through BRIDGE_CUTOFF_YEAR all use the bridge bond; payments
    after that use whichever bond matures the year before (highest yield
    if several share that maturity year). A bond's yield is its REAL
    solved YTM when a WInS/TreasuryDirect price is on file, otherwise the
    scenario's ladder yield - see bond_ladder.compute_yields."""
    if settlement_date is None:
        settlement_date = date.today()

    bonds = bond_ladder.load_bonds(bonds_path)
    not_matured, _ = bond_ladder.exclude_matured_bonds(bonds, today=settlement_date)
    priced = bond_ladder.compute_yields(not_matured, default_yield=scenario_yield, settlement_date=settlement_date)

    bridge_rows = priced[priced["maturity"] == BRIDGE_BOND_MATURITY]
    bridge_bond = bridge_rows.iloc[0] if not bridge_rows.empty else None

    assignment = {}
    for year in PAYMENT_YEARS:
        if year <= BRIDGE_CUTOFF_YEAR:
            assignment[year] = bridge_bond
        else:
            target_maturity_year = year - 1
            candidates = priced[priced["maturity"].apply(lambda d: d.year) == target_maturity_year]
            if candidates.empty:
                assignment[year] = None
            else:
                assignment[year] = candidates.sort_values(["ytm", "maturity"], ascending=[False, True]).iloc[0]
    return assignment, priced


# ========================================================================
# Case A - Original
# ========================================================================

def case_a(scenario, inputs=None):
    inputs = inputs or default_inputs()
    r = scenario["blended_return"]
    y = scenario["ladder_yield"]

    portfolio_2033 = inputs["contrib_2027"] * (1 + r) ** 6 + inputs["contrib_2028"] * (1 + r) ** 5
    reserve = pv_annuity_due(y, len(inputs["payment_years"]), inputs["payment"])
    leftover = portfolio_2033 - reserve
    status = "SHORT" if leftover < 0 else "OK"
    building = leftover * inputs["building_pct"]
    cushion = leftover * inputs["cushion_pct"]

    return {
        "case": "A", "scenario": scenario,
        "portfolio_2033": portfolio_2033, "reserve": reserve, "leftover": leftover,
        "building": building, "cushion": cushion, "status": status,
        "total_2033": portfolio_2033,
    }


def case_a_year_by_year(scenario, inputs=None):
    """One row per year 2027-2042. 2027-2032: whole portfolio growing at
    the blended return (no reserve carved out yet). 2033-2042: just the
    reserve, growing at the ladder/reserve yield, paying $50,000 at the
    start of each year - should land near $0 right after the last payment."""
    inputs = inputs or default_inputs()
    r = scenario["blended_return"]
    y = scenario["ladder_yield"]
    rows = []

    balance = 0.0
    growth_years = range(inputs["base_year"], inputs["payment_years"][0])
    for year in growth_years:
        contrib = inputs["contrib_2027"] if year == inputs["base_year"] else (
            inputs["contrib_2028"] if year == inputs["base_year"] + 1 else 0
        )
        start_balance = balance + contrib
        growth = start_balance * r
        end_balance = start_balance + growth
        rows.append({
            "year": year, "phase": "Growth", "start_balance": start_balance,
            "contribution": contrib, "payment": 0, "growth": growth, "end_balance": end_balance,
        })
        balance = end_balance

    reserve = pv_annuity_due(y, len(inputs["payment_years"]), inputs["payment"])
    balance = reserve
    for year in inputs["payment_years"]:
        start_balance = balance
        after_payment = start_balance - inputs["payment"]
        growth = after_payment * y
        end_balance = after_payment + growth
        rows.append({
            "year": year, "phase": "Reserve", "start_balance": start_balance,
            "contribution": 0, "payment": inputs["payment"], "growth": growth, "end_balance": end_balance,
        })
        balance = end_balance

    return pd.DataFrame(rows)


# ========================================================================
# Case B - Bonds-first
# ========================================================================

def case_b(scenario, inputs=None):
    inputs = inputs or default_inputs()
    y = scenario["ladder_yield"]
    br, er, sr = scenario["bond_return"], scenario["etf_return"], scenario["stock_return"]

    needed = ladder_needed_pv(y, inputs)
    ladder_2027 = min(needed, inputs["contrib_2027"])
    still_needed = needed - ladder_2027
    ladder_2028 = still_needed * (1 + y)
    ladder_total = ladder_2027 + ladder_2028

    total_contrib = inputs["contrib_2027"] + inputs["contrib_2028"]
    extra_bonds_total = inputs["bond_alloc"] * total_contrib - ladder_total
    extra_bonds_2027 = inputs["contrib_2027"] - ladder_2027  # whatever 2027 cash the ladder didn't use
    extra_bonds_2028 = extra_bonds_total - extra_bonds_2027

    etf_total = inputs["etf_alloc"] * total_contrib     # all bought 2028
    stock_total = inputs["stock_alloc"] * total_contrib  # all bought 2028

    ladder_2033 = ladder_2027 * (1 + y) ** 6 + ladder_2028 * (1 + y) ** 5
    extra_bonds_2033 = extra_bonds_2027 * (1 + br) ** 6 + extra_bonds_2028 * (1 + br) ** 5
    etf_2033 = etf_total * (1 + er) ** 5
    stock_2033 = stock_total * (1 + sr) ** 5

    outside_2033 = extra_bonds_2033 + etf_2033 + stock_2033
    building = outside_2033 * inputs["building_pct"]
    cushion = outside_2033 * inputs["cushion_pct"]
    total_2033 = ladder_2033 + outside_2033

    reserve_a = pv_annuity_due(y, len(inputs["payment_years"]), inputs["payment"])
    ladder_matches_reserve = abs(ladder_2033 - reserve_a) < 2

    return {
        "case": "B", "scenario": scenario,
        "needed": needed, "ladder_2027": ladder_2027, "ladder_2028": ladder_2028, "ladder_total": ladder_total,
        "extra_bonds_2027": extra_bonds_2027, "extra_bonds_2028": extra_bonds_2028, "extra_bonds_total": extra_bonds_total,
        "etf_total": etf_total, "stock_total": stock_total,
        "ladder_2033": ladder_2033, "extra_bonds_2033": extra_bonds_2033, "etf_2033": etf_2033, "stock_2033": stock_2033,
        "outside_2033": outside_2033, "building": building, "cushion": cushion, "total_2033": total_2033,
        "reserve_a": reserve_a, "ladder_matches_reserve": ladder_matches_reserve,
        "pct_locked_2027": ladder_2027 / needed if needed else 0,
        "status": "OK" if ladder_total >= needed - 2 else "SHORT",
    }


# ========================================================================
# Case C - Split
# ========================================================================

def case_c(scenario, inputs=None):
    inputs = inputs or default_inputs()
    y = scenario["ladder_yield"]
    br, er, sr = scenario["bond_return"], scenario["etf_return"], scenario["stock_return"]

    needed = ladder_needed_pv(y, inputs)

    bonds_2027_alloc = inputs["bond_alloc"] * inputs["contrib_2027"]
    ladder_2027 = min(needed, bonds_2027_alloc)
    extra_bonds_2027 = bonds_2027_alloc - ladder_2027
    etf_2027 = inputs["etf_alloc"] * inputs["contrib_2027"]
    stock_2027 = inputs["stock_alloc"] * inputs["contrib_2027"]

    still_needed = needed - ladder_2027
    bonds_2028_alloc = inputs["bond_alloc"] * inputs["contrib_2028"]
    ladder_2028 = still_needed * (1 + y)
    extra_bonds_2028 = bonds_2028_alloc - ladder_2028
    etf_2028 = inputs["etf_alloc"] * inputs["contrib_2028"]
    stock_2028 = inputs["stock_alloc"] * inputs["contrib_2028"]

    ladder_total = ladder_2027 + ladder_2028
    extra_bonds_total = extra_bonds_2027 + extra_bonds_2028
    etf_total = etf_2027 + etf_2028
    stock_total = stock_2027 + stock_2028

    ladder_2033 = ladder_2027 * (1 + y) ** 6 + ladder_2028 * (1 + y) ** 5
    extra_bonds_2033 = extra_bonds_2027 * (1 + br) ** 6 + extra_bonds_2028 * (1 + br) ** 5
    etf_2033 = etf_2027 * (1 + er) ** 6 + etf_2028 * (1 + er) ** 5
    stock_2033 = stock_2027 * (1 + sr) ** 6 + stock_2028 * (1 + sr) ** 5

    outside_2033 = extra_bonds_2033 + etf_2033 + stock_2033
    building = outside_2033 * inputs["building_pct"]
    cushion = outside_2033 * inputs["cushion_pct"]
    total_2033 = ladder_2033 + outside_2033

    reserve_a = pv_annuity_due(y, len(inputs["payment_years"]), inputs["payment"])
    ladder_matches_reserve = abs(ladder_2033 - reserve_a) < 2

    return {
        "case": "C", "scenario": scenario,
        "needed": needed,
        "ladder_2027": ladder_2027, "ladder_2028": ladder_2028, "ladder_total": ladder_total,
        "extra_bonds_2027": extra_bonds_2027, "extra_bonds_2028": extra_bonds_2028, "extra_bonds_total": extra_bonds_total,
        "etf_2027": etf_2027, "etf_2028": etf_2028, "etf_total": etf_total,
        "stock_2027": stock_2027, "stock_2028": stock_2028, "stock_total": stock_total,
        "ladder_2033": ladder_2033, "extra_bonds_2033": extra_bonds_2033, "etf_2033": etf_2033, "stock_2033": stock_2033,
        "outside_2033": outside_2033, "building": building, "cushion": cushion, "total_2033": total_2033,
        "reserve_a": reserve_a, "ladder_matches_reserve": ladder_matches_reserve,
        "pct_locked_2027": ladder_2027 / needed if needed else 0,
        "status": "OK" if ladder_total >= needed - 2 else "SHORT",
    }


# ========================================================================
# Case C: exactly what to buy in 2027 (the WInS portfolio box)
# ========================================================================

def wins_portfolio_2027(scenario, inputs=None, bonds_path="bonds.csv", settlement_date=None):
    """Allocates the 2027 bond budget (85% of $300,000) across the actual
    ladder bonds, buying the LONGEST maturities first - so if the budget
    runs out partway through, it's the shortest-dated piece (the Feb 2031
    bridge bond, which backs 4 different payments) that gets split
    between 2027 and 2028, exactly as instructed."""
    inputs = inputs or default_inputs()
    y = scenario["ladder_yield"]
    assignment, priced = assign_bonds_to_payments(y, bonds_path, settlement_date)

    # Group payments by the bond that backs them (the bridge bond backs several).
    bond_groups = {}
    for year in inputs["payment_years"]:
        bond_row = assignment.get(year)
        label = bond_row["label"] if bond_row is not None else f"NO BOND FOR {year}"
        bond_groups.setdefault(label, {"bond_row": bond_row, "years": [], "cost_2027": 0.0})
        cost = inputs["payment"] / (1 + y) ** (year - inputs["base_year"])
        bond_groups[label]["years"].append(year)
        bond_groups[label]["cost_2027"] += cost

    # Buy longest maturity first. The bridge bond is inherently the
    # shortest-dated of the group, so sorting by maturity descending
    # naturally buys it last - matching "least rate risk, bought last."
    ordered = sorted(
        bond_groups.items(),
        key=lambda kv: kv[1]["bond_row"]["maturity"] if kv[1]["bond_row"] is not None else date.min,
        reverse=True,
    )

    budget_2027 = inputs["bond_alloc"] * inputs["contrib_2027"]
    remaining_budget = budget_2027
    rows = []
    for label, info in ordered:
        cost = info["cost_2027"]
        bought_2027 = min(cost, remaining_budget) if remaining_budget > 0 else 0.0
        bought_2028 = cost - bought_2027
        remaining_budget -= bought_2027

        price = info["bond_row"]["price"] if info["bond_row"] is not None and info["bond_row"]["has_price"] else None
        face_value_2027 = (bought_2027 / (price / 100)) if (price and bought_2027 > 0) else None

        rows.append({
            "bond": label,
            "funds_payments": ", ".join(str(yr) for yr in info["years"]),
            "maturity": info["bond_row"]["maturity"] if info["bond_row"] is not None else None,
            "cost_2027_dollars_total": round(cost, 2),
            "bought_2027": round(bought_2027, 2),
            "bought_2028": round(bought_2028, 2),
            "price_used": price,
            "face_value_to_buy_2027": round(face_value_2027, 2) if face_value_2027 is not None else None,
        })

    etf_2027 = inputs["etf_alloc"] * inputs["contrib_2027"]
    stock_2027 = inputs["stock_alloc"] * inputs["contrib_2027"]

    return {
        "bonds": pd.DataFrame(rows),
        "etf_2027": etf_2027,
        "stock_2027": stock_2027,
        "budget_2027": budget_2027,
    }


# ========================================================================
# Case C: 2028 stress test
# ========================================================================

def stress_test_2028(scenario, inputs=None, shock=0.01):
    """How much does completing the ladder in 2028 cost if 2028 yields
    turn out 1% lower or higher than assumed - and does the planned extra
    bonds pool for 2028 still cover it?"""
    inputs = inputs or default_inputs()
    y = scenario["ladder_yield"]
    c = case_c(scenario, inputs)
    still_needed = c["needed"] - c["ladder_2027"]
    bonds_2028_alloc = inputs["bond_alloc"] * inputs["contrib_2028"]

    results = {}
    for label, stressed_y in [("1% lower", y - shock), ("1% higher", y + shock)]:
        ladder_2028_stressed = still_needed * (1 + stressed_y)
        extra_bonds_2028_stressed = bonds_2028_alloc - ladder_2028_stressed
        covered = extra_bonds_2028_stressed >= 0
        results[label] = {
            "yield_used": stressed_y,
            "ladder_2028_cost": ladder_2028_stressed,
            "extra_bonds_2028": extra_bonds_2028_stressed,
            "covered": covered,
            "shortfall": max(0.0, -extra_bonds_2028_stressed),
        }
    return results


# ========================================================================
# Self-checks against the team's spreadsheet
# ========================================================================

SELF_CHECK_TARGETS = [
    {"case": "A", "scenario": "Conservative", "field": "portfolio_2033", "target": 626290},
    {"case": "A", "scenario": "Conservative", "field": "reserve", "target": 405391},
    {"case": "A", "scenario": "Conservative", "field": "building", "target": 187764},
    {"case": "A", "scenario": "Conservative", "field": "cushion", "target": 33135},
    {"case": "A", "scenario": "Bear", "field": "portfolio_2033", "target": 317047},
    {"case": "A", "scenario": "Bear", "field": "reserve", "target": 439305},
    {"case": "A", "scenario": "Bear", "field": "status", "target": "SHORT"},
    {"case": "A", "scenario": "Aggressive", "field": "portfolio_2033", "target": 995622},
    {"case": "A", "scenario": "Aggressive", "field": "building", "target": 487777},
    {"case": "A", "scenario": "Aggressive", "field": "cushion", "target": 86078},
    {"case": "B", "scenario": "Conservative", "field": "ladder_total", "target": 302635},
    {"case": "B", "scenario": "Conservative", "field": "extra_bonds_total", "target": 79865},
    {"case": "B", "scenario": "Conservative", "field": "building", "target": 164652},
    {"case": "B", "scenario": "Conservative", "field": "cushion", "target": 29056},
    {"case": "B", "scenario": "Conservative", "field": "total_2033", "target": 599099},
    {"case": "B", "scenario": "Bear", "field": "building", "target": 54476},
    {"case": "B", "scenario": "Bear", "field": "cushion", "target": 9613},
    {"case": "B", "scenario": "Aggressive", "field": "building", "target": 155349},
    {"case": "B", "scenario": "Aggressive", "field": "cushion", "target": 27415},
    {"case": "C", "scenario": "Conservative", "field": "ladder_2027", "target": 255000},
    {"case": "C", "scenario": "Conservative", "field": "ladder_2028", "target": 49885},
    {"case": "C", "scenario": "Conservative", "field": "extra_bonds_total", "target": 77615},
    {"case": "C", "scenario": "Conservative", "field": "building", "target": 165511},
    {"case": "C", "scenario": "Conservative", "field": "cushion", "target": 29208},
    {"case": "C", "scenario": "Conservative", "field": "total_2033", "target": 600109},
    {"case": "C", "scenario": "Bear", "field": "building", "target": 51461},
    {"case": "C", "scenario": "Bear", "field": "cushion", "target": 9081},
    {"case": "C", "scenario": "Aggressive", "field": "building", "target": 162727},
    {"case": "C", "scenario": "Aggressive", "field": "cushion", "target": 28717},
]


def run_self_checks(scenarios=None):
    scenarios = scenarios or DEFAULT_SCENARIOS
    case_fns = {"A": case_a, "B": case_b, "C": case_c}
    results = []
    for check in SELF_CHECK_TARGETS:
        result = case_fns[check["case"]](scenarios[check["scenario"]])
        actual = result[check["field"]]
        if isinstance(check["target"], str):
            passed = actual == check["target"]
            detail = f"actual={actual}, target={check['target']}"
        else:
            passed = abs(actual - check["target"]) <= 2
            detail = f"actual=${actual:,.2f}, target=${check['target']:,}, diff=${actual - check['target']:,.2f}"
        results.append({
            "case": check["case"], "scenario": check["scenario"], "field": check["field"],
            "passed": passed, "detail": detail,
        })
    return results
