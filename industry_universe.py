"""
Live industry universe puller.
==================================
WHAT THIS DOES (plain English):

Instead of relying on a pre-built, fixed list of tickers (like the S&P
500 cache), this pulls EVERY US-listed stock yfinance's screener knows
about for a given industry - paging through results since the screener
only returns a few hundred at a time - then narrows it down to what the
team could actually trade: priced at least $5 (the competition rule) and
listed on a major U.S. exchange (not OTC/pink sheets, which WInS almost
certainly doesn't carry).
"""

import re

import yfinance as yf

PAGE_SIZE = 250  # yfinance's screener API max per call, found by testing

# Exchange codes yfinance uses for the major U.S. markets. OTC markets
# (OQB/OQX/PNK and similar) are deliberately excluded - WInS is very
# unlikely to carry pink-sheet/OTC names.
MAJOR_US_EXCHANGE_CODES = {
    "NYQ",   # NYSE
    "NMS",   # Nasdaq Global Select
    "NGM",   # Nasdaq Global Market
    "NCM",   # Nasdaq Capital Market
    "ASE",   # NYSE American
    "BTS",   # Cboe BZX (e.g. CBOE itself) - found by backtest_industries.py
}

# Preferred-share tickers (e.g. NEE-PV, DUK-PA) share the common stock's
# industry classification but aren't ordinary equity - P/E, ROE, etc.
# don't mean the same thing for them, so they're excluded outright.
PREFERRED_SHARE_PATTERN = re.compile(r"-P[A-Z]?$")


# SPAC units / warrants / rights (e.g. KRSP-UN, BBLGW name "C/WTS") - not
# ordinary shares. Found by the backtest: ~35 of these slipped through.
DERIVATIVE_TICKER_PATTERN = re.compile(r"-(UN|U|WT|WS|W|RT|R)$")
DERIVATIVE_NAME_PATTERN = re.compile(r"\b(C/WTS|WARRANTS?|RIGHTS?|UNITS?)\b", re.IGNORECASE)


def is_preferred_share(ticker):
    return bool(PREFERRED_SHARE_PATTERN.search(ticker or ""))


def is_derivative_security(ticker, name):
    return bool(DERIVATIVE_TICKER_PATTERN.search(ticker or "") or DERIVATIVE_NAME_PATTERN.search(name or ""))


# The complete list of industry values yfinance's screener accepts (found
# via EquityQuery's own valid_values - not yfinance's .info 'industry'
# field, which uses a DIFFERENT format: plain hyphens, e.g.
# "Utilities - Regulated Electric", vs the screener's em dash, e.g.
# "Utilities—Regulated Electric". Use these exact strings with yf.screen.
VALID_INDUSTRIES = [
    "Advertising Agencies", "Aerospace & Defense", "Agricultural Inputs", "Airlines",
    "Airports & Air Services", "Aluminum", "Apparel Manufacturing", "Apparel Retail",
    "Asset Management", "Auto & Truck Dealerships", "Auto Manufacturers", "Auto Parts",
    "Banks—Diversified", "Banks—Regional", "Beverages—Brewers", "Beverages—Non-Alcoholic",
    "Beverages—Wineries & Distilleries", "Biotechnology", "Broadcasting", "Building Materials",
    "Building Products & Equipment", "Business Equipment & Supplies", "Capital Markets",
    "Chemicals", "Coking Coal", "Communication Equipment", "Computer Hardware", "Confectioners",
    "Conglomerates", "Consulting Services", "Consumer Electronics", "Copper", "Credit Services",
    "Department Stores", "Diagnostics & Research", "Discount Stores", "Drug Manufacturers—General",
    "Drug Manufacturers—Specialty & Generic", "Education & Training Services",
    "Electrical Equipment & Parts", "Electronic Components", "Electronic Gaming & Multimedia",
    "Electronics & Computer Distribution", "Engineering & Construction", "Entertainment",
    "Farm & Heavy Construction Machinery", "Farm Products", "Financial Conglomerates",
    "Financial Data & Stock Exchanges", "Food Distribution", "Footwear & Accessories",
    "Furnishings, Fixtures & Appliances", "Gambling", "Gold", "Grocery Stores",
    "Health Information Services", "Healthcare Plans", "Home Improvement Retail",
    "Household & Personal Products", "Industrial Distribution", "Information Technology Services",
    "Infrastructure Operations", "Insurance Brokers", "Insurance—Diversified", "Insurance—Life",
    "Insurance—Property & Casualty", "Insurance—Reinsurance", "Insurance—Specialty",
    "Integrated Freight & Logistics", "Internet Content & Information", "Internet Retail",
    "Leisure", "Lodging", "Lumber & Wood Production", "Luxury Goods", "Marine Shipping",
    "Medical Care Facilities", "Medical Devices", "Medical Distribution",
    "Medical Instruments & Supplies", "Metal Fabrication", "Mortgage Finance", "Oil & Gas Drilling",
    "Oil & Gas E&P", "Oil & Gas Equipment & Services", "Oil & Gas Integrated",
    "Oil & Gas Midstream", "Oil & Gas Refining & Marketing", "Other Industrial Metals & Mining",
    "Other Precious Metals & Mining", "Packaged Foods", "Packaging & Containers",
    "Paper & Paper Products", "Personal Services", "Pharmaceutical Retailers",
    "Pollution & Treatment Controls", "Publishing", "REIT—Diversified",
    "REIT—Healthcare Facilities", "REIT—Hotel & Motel", "REIT—Industrial", "REIT—Mortgage",
    "REIT—Office", "REIT—Residential", "REIT—Retail", "REIT—Specialty", "Railroads",
    "Real Estate Services", "Real Estate—Development", "Real Estate—Diversified",
    "Recreational Vehicles", "Rental & Leasing Services", "Residential Construction",
    "Resorts & Casinos", "Restaurants", "Scientific & Technical Instruments",
    "Security & Protection Services", "Semiconductor Equipment & Materials", "Semiconductors",
    "Shell Companies", "Silver", "Software—Application", "Software—Infrastructure", "Solar",
    "Specialty Business Services", "Specialty Chemicals", "Specialty Industrial Machinery",
    "Specialty Retail", "Staffing & Employment Services", "Steel", "Telecom Services",
    "Textile Manufacturing", "Thermal Coal", "Tobacco", "Tools & Accessories", "Travel Services",
    "Trucking", "Uranium", "Utilities—Diversified", "Utilities—Independent Power Producers",
    "Utilities—Regulated Electric", "Utilities—Regulated Gas", "Utilities—Regulated Water",
    "Utilities—Renewable", "Waste Management",
]


def pull_raw_industry_quotes(industry, region="us"):
    """Pages through yfinance's screener until every match for this
    industry (+ region) has been collected. Returns the raw list of quote
    dicts yfinance returns (price, exchange, market cap, etc.)."""
    query = yf.EquityQuery("and", [
        yf.EquityQuery("eq", ["industry", industry]),
        yf.EquityQuery("eq", ["region", region]),
    ])

    quotes = []
    offset = 0
    total = None
    while total is None or offset < total:
        result = yf.screen(query, offset=offset, size=PAGE_SIZE)
        total = result.get("total", 0)
        page = result.get("quotes", [])
        if not page:
            break
        quotes.extend(page)
        offset += len(page)
        if len(page) < PAGE_SIZE:
            break  # short page means we've reached the end

    return quotes, total


def filter_tradeable(quotes, min_price=5.0):
    """Keeps only stocks priced >= min_price, listed on a major U.S.
    exchange, and not a preferred share. Returns (kept_list, found_count,
    kept_count)."""
    kept = []
    for q in quotes:
        price = q.get("regularMarketPrice")
        exchange = q.get("exchange")
        ticker = q.get("symbol")
        if price is None or price < min_price:
            continue
        if exchange not in MAJOR_US_EXCHANGE_CODES:
            continue
        if is_preferred_share(ticker):
            continue
        if is_derivative_security(ticker, q.get("shortName") or q.get("longName")):
            continue
        kept.append({
            "ticker": q.get("symbol"),
            "name": q.get("shortName") or q.get("longName") or q.get("displayName"),
            "price": price,
            "exchange": exchange,
            "full_exchange_name": q.get("fullExchangeName"),
            "market_cap": q.get("marketCap"),
        })
    return kept, len(quotes), len(kept)


def pull_industry_universe(industry, min_price=5.0, region="us"):
    """One-call convenience wrapper: pull everything, filter to
    tradeable, return (kept_list, found_count, kept_count)."""
    quotes, total = pull_raw_industry_quotes(industry, region)
    kept, found, kept_count = filter_tradeable(quotes, min_price)
    return kept, found, kept_count
