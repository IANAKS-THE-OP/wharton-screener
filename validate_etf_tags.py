"""
Checks the ETF sifter against funds whose industry is known (run after etf_industry.py).
  positive controls: well-known industry ETFs must land in the right stock industry
  negative controls: broad / whole-sector funds (SPY, QQQ, XLK, XLV...) must NOT get an industry
  eligibility:       no kept fund may have a leveraged / inverse / bond / crypto / option-income style name
Writes results/etf_tag_validation.txt and exits non-zero on any failure.   Run: ./venv/bin/python3 validate_etf_tags.py
"""
import re
import sys

import pandas as pd

POS = {"SMH": "Semiconductors", "SOXX": "Semiconductors", "XSD": "Semiconductors", "PSI": "Semiconductors", "KRE": "Banks—Regional",
       "KBE": "Banks—Regional|Banks—Diversified", "IAT": "Banks—Regional", "XBI": "Biotechnology", "IBB": "Biotechnology",
       "ARKG": "Biotechnology", "FBT": "Biotechnology", "ITA": "Aerospace & Defense", "XAR": "Aerospace & Defense",
       "PPA": "Aerospace & Defense", "JETS": "Airlines", "GDX": "Gold", "GDXJ": "Gold", "RING": "Gold", "SIL": "Silver",
       "URA": "Uranium", "URNM": "Uranium", "TAN": "Solar", "XOP": "Oil & Gas E&P", "AMLP": "Oil & Gas Midstream",
       "OIH": "Oil & Gas Equipment & Services", "XES": "Oil & Gas Equipment & Services", "XHB": "Residential Construction",
       "ITB": "Residential Construction", "KIE": "Insurance—Diversified|Insurance—Property & Casualty",
       "IGV": "Software—Application|Software—Infrastructure", "CIBR": "Software—Infrastructure", "HACK": "Software—Infrastructure",
       "IHI": "Medical Devices|Medical Instruments & Supplies", "XPH": "Drug Manufacturers—Specialty & Generic|Drug Manufacturers—General",
       "COPX": "Copper", "CRAK": "Oil & Gas Refining & Marketing", "BOAT": "Marine Shipping", "FCG": "Oil & Gas E&P",
       "QABA": "Banks—Regional", "KBWP": "Insurance—Property & Casualty", "HAUS": "REIT—Residential", "SLX": "Steel", "PICK": "Other Industrial Metals & Mining"}
NEG = ["SPY", "VOO", "IVV", "QQQ", "VTI", "IWM", "VTV", "VUG", "VEA", "VWO", "VT", "ACWI", "EFA", "XLK", "XLF", "XLV", "XLE", "XLY",
       "XLP", "XLI", "XLU", "XLB", "XLRE", "XLC", "DIA", "SCHD", "VIG", "MTUM", "USMV", "VGT", "VHT", "VFH", "VDE", "VPU", "IYW", "IYH", "IYF",
       # regressions found in the full audit: whole-sector / broad / single-stock funds that were once given an industry
       "GXPC", "GDOC", "GTEK", "GTOP", "CDL", "CDC", "FTXN", "PBOG", "FDN", "XRT", "XTN", "IHF"]
# must be ABSENT from the universe entirely (physical trusts, leveraged/inverse, single-stock, SPAC, option-income, gold+bond strategies)
GONE = ["GLD", "GLDM", "GLL", "GDT", "GDE", "GDMN", "GLDW", "GDXW", "YSAG", "TMH", "GSKH", "NVOH", "BPH", "ASMH", "ARMH", "STHH",
        "SAPH", "TESL", "SPAQ", "GIND", "GINN", "GPIQ", "GTPE", "SLV", "USO", "UNG", "TQQQ", "SQQQ"]
BAD_NAME = re.compile(r"\b(2x|3x|-1x|ultra|ultrapro|inverse|short|bear)\b|leverag|\bbond|treasur|buffer|covered call|option income|"
                      r"premium income|bitcoin|ether\b|crypto|vix|money market|preferred|convertible|commodity|futures", re.I)

d = pd.read_csv("results/etf_industry_map.csv").set_index("ticker")
out, fails = [], 0
wrong = [(t, d.loc[t, "group"], e) for t, e in POS.items() if t in d.index and d.loc[t, "group"] not in e.split("|")]
gone = [t for t in POS if t not in d.index]
n_pos = len(POS) - len(wrong) - len(gone)
out.append(f"positive controls: {n_pos}/{len(POS)} correct. wrong={wrong} missing={gone}")
fails += len(wrong) + len(gone)
inmap = [t for t in NEG if t in d.index]
bad = [(t, d.loc[t, "group"]) for t in inmap if d.loc[t, "kind"] == "industry"]
out.append(f"negative controls: {len(inmap) - len(bad)}/{len(NEG)} correct. wrongly given an industry={bad} missing={[t for t in NEG if t not in d.index]}")
fails += len(bad) + (len(NEG) - len(inmap))
present = [t for t in GONE if t in d.index]
out.append(f"must-be-absent: {len(GONE) - len(present)}/{len(GONE)} correct. wrongly present={present}")
fails += len(present)
# reviewed by hand: these hold company STOCKS despite "crypto" / "bitcoin" / "commodity" in the name
OK_NAMES = {"CCNR", "BLCK", "BCOR"}
leak = d[d["name"].fillna("").str.contains(BAD_NAME) & ~d.index.isin(OK_NAMES)]
out.append(f"eligibility: {len(leak)} kept funds have a non-equity-looking name {list(leak.index[:15])}")
fails += len(leak)
out.append(f"universe: {len(d)} funds, {(d.kind == 'industry').sum()} industry ETFs in {d[d.kind == 'industry'].group.nunique()} industries")
txt = "\n".join(out)
open("results/etf_tag_validation.txt", "w").write(txt + "\n")
print(txt)
print("PASS" if fails == 0 else f"FAIL ({fails})")
sys.exit(1 if fails else 0)
