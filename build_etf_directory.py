"""Counts US-listed ETFs per Morningstar category -> results/etf_category_directory.csv
Run:  ./venv/bin/python3 build_etf_directory.py"""
import pandas as pd
import etf_universe as eu

rows = []
for i, cat in enumerate(eu.all_categories(), 1):
    try:
        n = eu.count_category(cat)
    except Exception:
        n = None
    rows.append({"category": cat, "etfs": n, "is_bond": eu.is_bond_category(cat), "high_yield": eu.is_high_yield_category(cat)})
    print(f"{i}: {cat} {n}", flush=True)
d = pd.DataFrame(rows)
d.to_csv("results/etf_category_directory.csv", index=False)
print("TOTAL", int(d.etfs.fillna(0).sum()), "ETFs in", len(d), "categories")
