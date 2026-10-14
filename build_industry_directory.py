"""
Builds results/industry_directory.csv - one row per industry, so the team
can decide which industries are worth screening.

Needs results/industry_members.csv + industry_inventory.csv from
backtest_industries.py (run that first), then looks up each industry's
sector (yfinance's screener can filter by sector, so we pull each sector's
tickers once and map every industry to its majority sector).

Run:  ./venv/bin/python3 build_industry_directory.py
"""

import pandas as pd
import yfinance as yf

SECTORS = ["Basic Materials", "Communication Services", "Consumer Cyclical", "Consumer Defensive", "Energy",
           "Financial Services", "Healthcare", "Industrials", "Real Estate", "Technology", "Utilities"]

members = pd.read_csv("results/industry_members.csv")
inventory = pd.read_csv("results/industry_inventory.csv")

# ticker -> sector, only for the tickers we care about (the tradeable ones)
wanted = set(members["ticker"])
sector_of = {}
for sector in SECTORS:
    q = yf.EquityQuery("and", [yf.EquityQuery("eq", ["sector", sector]), yf.EquityQuery("eq", ["region", "us"])])
    offset, total = 0, None
    while total is None or offset < total:
        r = yf.screen(q, offset=offset, size=250)
        total = r.get("total", 0)
        page = r.get("quotes", [])
        if not page:
            break
        for item in page:
            if item["symbol"] in wanted:
                sector_of[item["symbol"]] = sector
        offset += len(page)
        if len(page) < 250:
            break
    print(f"{sector}: done ({len(sector_of)} tickers mapped so far)", flush=True)

members["sector"] = members["ticker"].map(sector_of)

rows = []
for _, inv in inventory.iterrows():
    m = members[members["industry"] == inv["industry"]].sort_values("market_cap", ascending=False)
    sector = m["sector"].mode().iloc[0] if m["sector"].notna().any() else None
    caps = m["market_cap"].dropna() / 1e9
    rows.append({
        "industry": inv["industry"],
        "sector": sector,
        "tradeable": int(inv["kept_tradeable"]),
        "all_listed_incl_otc_penny": int(inv["reported"]),
        "total_mcap_b": round(caps.sum(), 1),
        "median_mcap_b": round(caps.median(), 2) if len(caps) else None,
        "top_5_by_mcap": ", ".join(m["ticker"].head(5)),
    })

out = pd.DataFrame(rows).sort_values(["sector", "tradeable"], ascending=[True, False])
out.to_csv("results/industry_directory.csv", index=False)
print(f"Wrote results/industry_directory.csv ({len(out)} industries, {int(out.tradeable.sum()):,} tradeable stocks)")
print(f"Industries with no sector found: {out.sector.isna().sum()}")
