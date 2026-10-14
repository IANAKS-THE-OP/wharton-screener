"""
TreasuryDirect FedInvest price lookup
========================================
WHAT THIS DOES (plain English):

Looks up real Treasury note/bond prices from TreasuryDirect's public
FedInvest page (the same price feed the federal government uses to value
Treasury securities each day), and matches them to the bonds in bonds.csv
by coupon rate + maturity date.

IMPORTANT FINDING from testing this page directly: TreasuryDirect's
"Today's Prices" page always shows an END OF DAY price of 0.000000 - that
column only gets filled in once a trading day is fully closed out, which
TreasuryDirect does the NEXT business day. So "today's end-of-day price"
doesn't actually exist until tomorrow. This script instead fetches the
most recent business day that DOES have a real end-of-day price (usually
yesterday) from the Historical Prices page, and always reports exactly
which date it used so nothing is presented as more current than it is.

The page itself requires a login-free session (a CSRF token tied to a
cookie, like any normal form on a website) - not a public JSON API - so
this script drives that same form-submit flow a browser would, using the
`requests` library.

Never guesses a price: if a bond in bonds.csv can't be matched to a row
in the TreasuryDirect data, its treasury_price is left blank and it's
reported separately.
"""

import io
import re
from datetime import date, timedelta

import numpy as np
import pandas as pd
import requests

BASE = "https://www.treasurydirect.gov/GA-FI/FedInvest"
SELECT_URL = f"{BASE}/selectSecurityPriceDate.htm"
POST_URL = f"{BASE}/selectSecurityPriceDate"
DETAIL_URL = f"{BASE}/securityPriceDetail"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

CSRF_PATTERN = re.compile(r'name="_csrf"\s+value="([^"]+)"')
DATE_LABEL_PATTERN = re.compile(r"Prices For:\s*([A-Za-z]+ \d{1,2}, \d{4})")


# ========================================================================
# STEP 1: confirm the page is actually publicly accessible before relying
# on it for anything - requested explicitly, and worth checking every run
# since a government site's structure or access rules can change.
# ========================================================================

def check_public_access():
    """Returns (accessible: bool, message: str)."""
    try:
        resp = requests.get(SELECT_URL, headers=HEADERS, timeout=20)
    except requests.RequestException as e:
        return False, f"Could not reach TreasuryDirect at all (network error): {e}"

    if resp.status_code != 200:
        return False, f"TreasuryDirect returned HTTP {resp.status_code} - not accessible as expected."

    if not CSRF_PATTERN.search(resp.text):
        return False, "Page loaded but didn't contain the expected form - TreasuryDirect's page structure may have changed."

    return True, f"TreasuryDirect FedInvest page is publicly accessible (HTTP 200, no login required)."


# ========================================================================
# STEP 2: fetch one date's prices by driving the same form flow a browser
# would (GET the form for a session + CSRF token, POST the chosen date,
# then GET the results page using that session).
# ========================================================================

def fetch_prices_for_date(price_date, session=None):
    """Returns a DataFrame of that date's Treasury prices, or None if that
    date has no data (weekends, holidays, or a date before Treasury started
    publishing this feed)."""
    sess = session or requests.Session()
    sess.headers.update(HEADERS)

    select_resp = sess.get(SELECT_URL, timeout=20)
    csrf_match = CSRF_PATTERN.search(select_resp.text)
    if not csrf_match:
        raise RuntimeError("Could not find the CSRF token on TreasuryDirect's date-select page.")
    csrf = csrf_match.group(1)

    # `requests` follows the server's redirect automatically here, landing
    # on the results page in this SAME response - a separate follow-up GET
    # to securityPriceDetail hits a one-shot session slot a second time and
    # comes back empty, so we deliberately do NOT issue one.
    detail_resp = sess.post(
        POST_URL,
        data={"priceDate": price_date.isoformat(), "submit": "Show Prices", "_csrf": csrf},
        headers={"Referer": SELECT_URL},
        timeout=20,
    )
    if detail_resp.status_code != 200:
        return None

    try:
        tables = pd.read_html(io.StringIO(detail_resp.text))
    except ValueError:
        return None
    if not tables or "CUSIP" not in tables[0].columns or tables[0].empty:
        return None

    df = tables[0].rename(columns={
        "SECURITY TYPE": "security_type", "RATE": "rate_str", "MATURITY DATE": "maturity_str",
        "CALL DATE": "call_date", "END OF DAY": "end_of_day", "BUY": "buy", "SELL": "sell",
    })
    df["rate"] = df["rate_str"].astype(str).str.rstrip("%").astype(float)
    df["maturity"] = pd.to_datetime(df["maturity_str"], format="%m/%d/%Y").dt.date
    df["end_of_day"] = pd.to_numeric(df["end_of_day"], errors="coerce")

    label_match = DATE_LABEL_PATTERN.search(detail_resp.text)
    df.attrs["price_date_label"] = label_match.group(1) if label_match else price_date.isoformat()
    return df


def find_latest_closed_prices(max_days_back=10):
    """Walks backward from yesterday (today's END OF DAY is never populated
    - see module docstring) until it finds a date with real, non-zero
    end-of-day prices. Returns (DataFrame, date_used) or (None, None) with
    the reason printed along the way."""
    d = date.today() - timedelta(days=1)
    sess = requests.Session()
    sess.headers.update(HEADERS)

    for _ in range(max_days_back):
        df = fetch_prices_for_date(d, session=sess)
        if df is not None and (df["end_of_day"] > 0).any():
            return df, d
        d -= timedelta(days=1)

    return None, None


# ========================================================================
# STEP 3: match bonds.csv rows to TreasuryDirect rows by coupon + maturity
# ========================================================================

def match_bonds_to_prices(bonds_df, treasury_df, rate_tolerance=0.001):
    """Adds treasury_price and treasury_price_date_used columns to a copy
    of bonds_df. Matches on (coupon, maturity) - never guesses: a bond with
    no match keeps a blank treasury_price and is listed in `unmatched`."""
    out = bonds_df.copy()
    out["treasury_price"] = pd.NA
    unmatched = []

    price_date_label = treasury_df.attrs.get("price_date_label", "unknown date")

    for idx, bond in out.iterrows():
        candidates = treasury_df[
            (treasury_df["maturity"] == bond["maturity"])
            & ((treasury_df["rate"] - bond["coupon"]).abs() < rate_tolerance)
        ]
        if candidates.empty:
            unmatched.append(bond["label"] if "label" in bond else f"{bond['coupon']}% {bond['maturity']}")
            continue
        # Coupon + maturity uniquely identified every bond we tested with;
        # if TreasuryDirect ever has a genuine duplicate, take the first and
        # don't pretend there's no ambiguity - still better than guessing a
        # price outright, but flag it so a human can double check.
        if len(candidates) > 1:
            unmatched.append(
                f"{bond['label'] if 'label' in bond else bond['coupon']}: "
                f"{len(candidates)} ambiguous matches in TreasuryDirect data, used the first"
            )
        out.loc[idx, "treasury_price"] = float(candidates.iloc[0]["end_of_day"])

    out["treasury_price_date"] = price_date_label

    # Recompute the effective price now that treasury_price has been
    # refreshed - wins_price (if present) still wins over the new value.
    if "wins_price" not in out.columns:
        out["wins_price"] = pd.NA
    out["price"] = out["wins_price"].where(out["wins_price"].notna(), out["treasury_price"])
    out["price_source"] = np.select(
        [out["wins_price"].notna(), out["treasury_price"].notna()],
        ["wins_price", "treasury_price"],
        default=None,
    )
    return out, unmatched


# ========================================================================
# Plain-English worked example for one bond
# ========================================================================

def explain_bond(coupon, maturity, price, ytm, settlement_date=None):
    """Prints out, in plain English, exactly what you pay, what you get
    back, and how that nets out to the stated yield - for one bond."""
    import bond_ladder  # local import avoids a circular import at module load time

    if settlement_date is None:
        settlement_date = date.today()

    n_periods = bond_ladder.years_between(settlement_date, maturity) * 2
    n_whole_coupons = int(n_periods)  # whole semiannual coupons remaining (ignores the final fractional stub)
    coupon_payment = coupon / 2

    print(f"=== WORKED EXAMPLE: {coupon:.3f}% bond maturing {maturity} ===")
    print(f"  You pay ${price:.4f} today (per $100 face value) - TreasuryDirect's end-of-day price.")
    print(f"  You then receive ${coupon_payment:.4f} every 6 months (semiannual coupons on a {coupon:.3f}% "
          f"annual rate) for roughly {n_whole_coupons} payments until maturity ({maturity}).")
    print(f"  At maturity you also get back the $100.00 face value.")
    print(f"  Solving for the single discount rate that makes ${price:.4f} today equal to all of those future "
          f"payments gives a yield to maturity of {ytm*100:.3f}% - compare that to the {coupon:.3f}% coupon "
          f"rate printed on the bond itself.")
