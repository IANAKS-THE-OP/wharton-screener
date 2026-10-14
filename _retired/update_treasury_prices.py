"""
Updates bonds.csv with real prices from TreasuryDirect's FedInvest feed.
============================================================================
Run it directly:

    ./venv/bin/python3 update_treasury_prices.py

What it does:
  1. Confirms TreasuryDirect's FedInvest page is publicly accessible.
  2. Finds the most recent business day with a real end-of-day price
     (today's own page never has one - see treasury_prices.py for why).
  3. Matches every non-matured bond in bonds.csv to that day's price by
     coupon + maturity date.
  4. Writes the result into bonds.csv's treasury_price / treasury_price_date
     columns - NEVER touches wins_price, which is yours to fill in by hand.
  5. Reports which bonds matched, which didn't (never guesses), and the
     resulting coupon-vs-solved-yield comparison.
"""

import pandas as pd

import bond_ladder
import treasury_prices as tp


def update_prices(bonds_path="bonds.csv"):
    """Does the actual work and returns a structured result dict, so both
    the CLI below and the Streamlit app can show the same information."""
    result = {
        "accessible": False, "access_message": "", "used_date": None,
        "matched_count": 0, "unmatched": [], "excluded_matured": [],
        "priced_bonds": None, "example": None,
    }

    accessible, msg = tp.check_public_access()
    result["accessible"] = accessible
    result["access_message"] = msg
    if not accessible:
        return result

    treasury_df, used_date = tp.find_latest_closed_prices()
    if treasury_df is None:
        result["access_message"] += " (Could not find any date in the last 10 days with real end-of-day prices.)"
        return result
    result["used_date"] = used_date
    result["price_date_label"] = treasury_df.attrs.get("price_date_label")

    bonds = bond_ladder.load_bonds(bonds_path)
    not_matured, excluded_matured = bond_ladder.exclude_matured_bonds(bonds)
    matched, unmatched = tp.match_bonds_to_prices(not_matured, treasury_df)
    result["excluded_matured"] = excluded_matured
    result["unmatched"] = unmatched

    # Write back into bonds.csv: update treasury_price/treasury_price_date
    # for matched rows, keep every matured row and wins_price untouched.
    full = pd.read_csv(bonds_path)
    if "wins_price" not in full.columns:
        full["wins_price"] = pd.NA
    if "treasury_price" not in full.columns:
        full["treasury_price"] = pd.NA
    if "treasury_price_date" not in full.columns:
        full["treasury_price_date"] = pd.NA
    # An all-blank CSV column gets read in as float64 NaN - force it back to
    # a string-capable dtype before writing date text into it below.
    full["treasury_price_date"] = full["treasury_price_date"].astype(object)

    full["maturity_parsed"] = pd.to_datetime(full["maturity"]).dt.date
    matched_lookup = matched.set_index(["coupon", "maturity"])[["treasury_price", "treasury_price_date"]]

    for idx, row in full.iterrows():
        key = (row["coupon"], row["maturity_parsed"])
        if key in matched_lookup.index:
            full.loc[idx, "treasury_price"] = matched_lookup.loc[key, "treasury_price"]
            full.loc[idx, "treasury_price_date"] = matched_lookup.loc[key, "treasury_price_date"]

    full = full.drop(columns=["maturity_parsed"])
    full.to_csv(bonds_path, index=False)
    result["matched_count"] = len(matched_lookup)

    priced = bond_ladder.compute_yields(matched, default_yield=0.05)
    result["priced_bonds"] = priced

    priced_with_price = priced[priced["has_price"]]
    if not priced_with_price.empty:
        example = priced_with_price.iloc[len(priced_with_price) // 2]
        result["example"] = {
            "coupon": example["coupon"], "maturity": example["maturity"],
            "price": example["price"], "ytm": example["ytm"],
        }

    return result


def print_result(result):
    print("=== STEP 1: checking TreasuryDirect is publicly accessible ===")
    print(f"  {result['access_message']}")
    if not result["accessible"]:
        print("  Stopping here - fix access before trying to fetch prices.")
        return

    print("\n=== STEP 2: finding the most recent closed trading day ===")
    if result["used_date"] is None:
        print("  Could not find any date in the last 10 days with real end-of-day prices. Stopping.")
        return
    print(f"  Using {result['used_date']} ({result.get('price_date_label')}).")

    print("\n=== STEP 3: matching bonds.csv to TreasuryDirect prices ===")
    if result["unmatched"]:
        print(f"  {len(result['unmatched'])} bond(s) could NOT be matched - price left blank, flagged here:")
        for label in result["unmatched"]:
            print(f"    {label}")
    else:
        print("  Every non-matured bond matched successfully.")
    if result["excluded_matured"]:
        print(f"  ({len(result['excluded_matured'])} already-matured bond(s) skipped entirely: {result['excluded_matured']})")
    print(f"\n  Wrote updated prices to bonds.csv ({result['matched_count']} bonds priced).")

    print("\n=== STEP 4: yield to maturity, coupon vs. solved yield ===")
    priced = result["priced_bonds"]
    display = priced[["label", "coupon", "treasury_price", "ytm", "yield_source"]].copy()
    display["ytm_pct"] = (display["ytm"] * 100).round(3)
    display = display.drop(columns=["ytm"])
    print(display.to_string(index=False))

    if result["example"]:
        print("\n=== STEP 5: worked example ===")
        import treasury_prices as tp
        tp.explain_bond(**result["example"])


if __name__ == "__main__":
    print_result(update_prices())
