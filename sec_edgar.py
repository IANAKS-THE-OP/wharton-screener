"""
SEC EDGAR fundamentals fetcher.
==================================
WHAT THIS DOES (plain English):

Pulls company financial statement data directly from the SEC's own
filings (the XBRL data companies submit alongside their 10-Ks) instead of
relying on a third party's summary of it. This is the authoritative
source - the same numbers you'd find in the actual 10-K.

Three SEC endpoints are involved:
  1. company_tickers.json - maps ticker symbol -> CIK (SEC's company ID).
  2. submissions/CIK##########.json - company metadata, including the SIC
     industry code/description and the list of filings (so we can find
     the latest 10-K's filing date and build a link to it).
  3. api/xbrl/companyfacts/CIK##########.json - every XBRL-tagged number
     the company has ever reported, across every filing.

SEC requires a descriptive User-Agent on every request and asks that
submitters stay under 10 requests/second - both enforced here. Responses
are cached locally so a second run doesn't hit SEC's servers again within
a week.

A quirk worth knowing (found by testing, not assumed): the `fy`/`fp`
fields on each XBRL fact label the FILING that reported it, not the
period the number itself covers - a single 10-K's comparative tables
report 2-3 years of history, all stamped with that filing's own fy/fp.
So annual values here are deduped by their actual (start, end) period,
not by fy/fp.
"""

import json
import os
import threading
import time
from datetime import date, datetime, timedelta

import requests
import yaml

CACHE_DIR = "sec_cache"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

MIN_REQUEST_INTERVAL = 1.0 / 10  # stay under 10 requests/second
_last_request_time = [0.0]
# Guards _last_request_time so parallel fetches (e.g. scoring a whole
# industry with a thread pool) still serialize to the SEC rate limit
# instead of racing each other past it.
_rate_limit_lock = threading.Lock()


_UA_CACHE = {}


def get_user_agent(config_path="config.yaml"):
    """SEC requires a descriptive User-Agent with a contact email. Looked up in this order (first found wins):
      1. Streamlit secrets: [sec_edgar] user_agent = "..."  (Streamlit Cloud > App settings > Secrets,
         or a local .streamlit/secrets.toml, which is gitignored)
      2. environment variable SEC_USER_AGENT
      3. config.yaml sec_edgar.user_agent (a generic placeholder in the repo - no personal email committed)
    Resolved once per process (threads in the download pool reuse it)."""
    if config_path in _UA_CACHE:
        return _UA_CACHE[config_path]
    ua = None
    try:
        import streamlit as st
        ua = (st.secrets.get("sec_edgar") or {}).get("user_agent") or st.secrets.get("SEC_USER_AGENT")
    except Exception:  # no secrets file / not running under Streamlit
        ua = None
    ua = ua or os.environ.get("SEC_USER_AGENT")
    if not ua and os.path.exists(config_path):
        with open(config_path) as f:
            ua = (yaml.safe_load(f) or {}).get("sec_edgar", {}).get("user_agent")
    if not ua:
        raise RuntimeError(
            "No SEC user agent set. Add [sec_edgar] user_agent = \"WGHSIC Team Jane Doe jane@example.com\" to "
            ".streamlit/secrets.toml (or Streamlit Cloud Secrets), or set SEC_USER_AGENT."
        )
    _UA_CACHE[config_path] = ua
    return ua


def _rate_limited_get(url, headers):
    with _rate_limit_lock:
        elapsed = time.time() - _last_request_time[0]
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)
        resp = requests.get(url, headers=headers, timeout=30)
        _last_request_time[0] = time.time()
    return resp


def _cache_path(name):
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, name)


def _cached_get(url, cache_name, config_path="config.yaml", max_age_days=7):
    """Reads from local cache if fresh enough, otherwise fetches live and
    re-caches. Returns (data, from_cache: bool)."""
    path = _cache_path(cache_name)
    if os.path.exists(path):
        age = datetime.now() - datetime.fromtimestamp(os.path.getmtime(path))
        if age < timedelta(days=max_age_days):
            with open(path) as f:
                return json.load(f), True

    headers = {"User-Agent": get_user_agent(config_path), "Accept-Encoding": "gzip, deflate"}
    resp = _rate_limited_get(url, headers)
    resp.raise_for_status()
    data = resp.json()
    with open(path, "w") as f:
        json.dump(data, f)
    return data, False


# ========================================================================
# STEP 1: ticker -> CIK
# ========================================================================

def check_public_access(config_path="config.yaml"):
    """Confirms SEC EDGAR is reachable with our User-Agent before relying
    on it for anything - returns (accessible: bool, message: str)."""
    try:
        headers = {"User-Agent": get_user_agent(config_path)}
    except RuntimeError as e:
        return False, str(e)
    try:
        resp = requests.get(TICKERS_URL, headers=headers, timeout=20)
    except requests.RequestException as e:
        return False, f"Could not reach SEC EDGAR: {e}"
    if resp.status_code != 200:
        return False, f"SEC EDGAR returned HTTP {resp.status_code} - check the User-Agent format."
    return True, f"SEC EDGAR is accessible (HTTP 200) using User-Agent: {get_user_agent(config_path)}"


def load_ticker_cik_map(config_path="config.yaml", max_age_days=7):
    """Returns {ticker: zero-padded 10-digit CIK string}."""
    data, _ = _cached_get(TICKERS_URL, "company_tickers.json", config_path, max_age_days)
    return {row["ticker"]: str(row["cik_str"]).zfill(10) for row in data.values()}


def get_cik(ticker, config_path="config.yaml"):
    ticker_map = load_ticker_cik_map(config_path)
    return ticker_map.get(ticker.upper())


# ========================================================================
# STEP 2 & 3: submissions (SIC + filing list) and companyfacts (XBRL data)
# ========================================================================

def fetch_submissions(cik, config_path="config.yaml", max_age_days=7):
    url = SUBMISSIONS_URL.format(cik=cik)
    data, _ = _cached_get(url, f"submissions_{cik}.json", config_path, max_age_days)
    return data


def fetch_companyfacts(cik, config_path="config.yaml", max_age_days=7):
    url = COMPANYFACTS_URL.format(cik=cik)
    data, _ = _cached_get(url, f"companyfacts_{cik}.json", config_path, max_age_days)
    return data


def latest_10k_info(submissions):
    """Returns {filed_date, report_date, accession, edgar_url} for the most
    recent 10-K, or None if there isn't one."""
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    for i, form in enumerate(forms):
        if form == "10-K":
            accn = recent["accessionNumber"][i]
            cik_int = submissions["cik"]
            accn_nodash = accn.replace("-", "")
            return {
                "filed_date": recent["filingDate"][i],
                "report_date": recent["reportDate"][i],
                "accession": accn,
                "edgar_url": f"https://www.sec.gov/Archives/edgar/data/{int(cik_int)}/{accn_nodash}/{accn}-index.htm",
            }
    return None


def industry_label(submissions):
    """SEC's own sicDescription is already a readable industry name - no
    need to maintain a separate SIC-code-to-name mapping."""
    return submissions.get("sicDescription") or f"SIC {submissions.get('sic', 'Unknown')}"


# ========================================================================
# XBRL fact parsing: annual (duration) and point-in-time (instant) series
# ========================================================================

def _entries_for_tag(companyfacts, tag, unit="USD"):
    """`companyfacts` is whatever fetch_companyfacts() returns (the full
    API response, with a top-level 'facts' key) - drilling in here means
    every caller can just pass that straight through."""
    return companyfacts.get("facts", {}).get("us-gaap", {}).get(tag, {}).get("units", {}).get(unit, [])


def annual_duration_series(facts, tag, unit="USD"):
    """Full-fiscal-year (duration) values for one tag, e.g. revenue, net
    income. Dedupes by the ACTUAL (start, end) period (not fy/fp - see
    module docstring), keeping the most-recently-filed version of each
    period in case of restatement. Sorted oldest to newest."""
    entries = _entries_for_tag(facts, tag, unit)
    by_period = {}
    for e in entries:
        if e.get("form") != "10-K":
            continue
        start, end = e.get("start"), e.get("end")
        if not start or not end:
            continue
        days = (date.fromisoformat(end) - date.fromisoformat(start)).days
        if not (350 <= days <= 380):  # a full fiscal year, allowing 52/53-week years
            continue
        key = (start, end)
        if key not in by_period or e["filed"] > by_period[key]["filed"]:
            by_period[key] = e
    return sorted(by_period.values(), key=lambda e: e["end"])


def best_annual_series(facts, tag_candidates, unit="USD"):
    """Tries each candidate tag (companies migrate tags over time, e.g.
    'Revenues' -> 'RevenueFromContractWithCustomerExcludingAssessedTax'
    after ASC 606) and returns whichever yields the MOST RECENT data, not
    just the first one that has any data at all - an empty or stale tag
    would otherwise silently win. Returns (series, tag_used)."""
    best_series, best_tag = [], None
    for tag in tag_candidates:
        series = annual_duration_series(facts, tag, unit)
        if series and (not best_series or series[-1]["end"] > best_series[-1]["end"]):
            best_series, best_tag = series, tag
    return best_series, best_tag


def instant_series_at_dates(facts, tag_candidates, end_dates, unit="USD"):
    """Point-in-time (balance sheet) values matched to a specific set of
    fiscal-year-end dates (normally taken from a reliable duration series
    like revenue, which cleanly identifies true FY-end dates). Returns
    ({end_date: val}, tag_used)."""
    for tag in tag_candidates:
        entries = _entries_for_tag(facts, tag, unit)
        by_end = {}
        for e in entries:
            if e.get("form") != "10-K":
                continue
            end = e.get("end")
            if not end:
                continue
            if end not in by_end or e["filed"] > by_end[end]["filed"]:
                by_end[end] = e
        result = {d: by_end[d]["val"] for d in end_dates if d in by_end}
        if result:
            return result, tag
    return {}, None
