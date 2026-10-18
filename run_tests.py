"""
Runs every self-test suite (no network, no Streamlit):  ./venv/bin/python3 run_tests.py
  scoring2      stock engine (26)          etf_scoring   ETF engine (18)
  etf_groups    grouping + themes (4)      compare       Compare industries (6)
  snapshots     Save snapshot (1)         version_info  caches match code (1)
Exit code 1 if anything fails. The same suites are shown under "How scoring works > Self-tests" on each page.
(check_app_flows.py drives the real app headlessly against the cached data; it is slower.)
"""
import sys

import compare
import etf_groups
import etf_scoring
import scoring2
import snapshots
import version_info

failed = 0
for name, mod in [("scoring2", scoring2), ("etf_scoring", etf_scoring), ("etf_groups", etf_groups), ("compare", compare), ("snapshots", snapshots), ("version_info", version_info)]:
    tests = mod.run_self_tests()
    bad = [t for t in tests if not t["passed"]]
    failed += len(bad)
    print(f"{name}: {len(tests) - len(bad)}/{len(tests)} passed")
    for t in bad:
        print(f"   FAIL {t['test']}  {t['detail']}")
print("ALL PASSED" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
