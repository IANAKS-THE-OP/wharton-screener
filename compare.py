"""
"Compare industries" engine for the Stocks and ETF pages (pure functions - no Streamlit, no network).
=====================================================================================================
Two modes:

  own     "Each vs its own industry" (default). Every name is prepared and finalized with ITS OWN
          industry/group exactly as on that industry's page (same prepare call, same peer pool and
          fallback, same Laura ranges or industry 25th-75th), then all rows are ranked together by
          Overall. A name that is in two selected groups (an ETF in a category AND a theme) appears
          once per group.
  pooled  "Pooled": all selected industries/groups are ONE peer group. Percentiles, p25/median/p75,
          spread and Laura ranges are recalculated on the pooled set (same laura.LAURA_TIERS rules,
          same < 10-peer fallback). Each name appears once.

range_mode: "laura" (Laura standards, the default) or "avg" (Industry average = 25th-75th of the peer
group). Hand-tuned ranges from a page's Tune tab are NOT used here (the page shows a note if they differ).
Weights and blend are the page's own (Tune tab).
"""

import numpy as np

import laura
import scoring2 as sc
import etf_scoring as es

POOLED = "Pooled"


def _ranges(bench, range_mode, engine):
    """Ranges handed to finalize. Laura is finalize's own default, so 'laura' passes nothing extra."""
    return {} if range_mode == "laura" else engine.default_ranges(bench)


# ------------------------------------------------------------------ stocks
def stock_preps(raws_by_group, settings, all_raws=None, cache=None):
    """{group: sc.prepare_industry(...)} - the SAME call the Stocks page makes for one industry.
    cache: optional dict {key: prep} reused between reruns (key includes the data date + settings)."""
    out = {}
    for g, (raws, stamp) in raws_by_group.items():
        key = ("stk", sc.PREP_VERSION, g, stamp, settings["tbill"], settings["rf"], settings["erp"])
        if cache is not None and key in cache:
            out[g] = cache[key]
            continue
        out[g] = sc.prepare_industry(raws, settings, all_raws=all_raws)
        if cache is not None:
            cache[key] = out[g]
    return out


def stock_compare(raws_by_group, settings, weights, blend, mode="own", range_mode="laura", all_raws=None, cache=None):
    """raws_by_group: {industry: (raws, fetched_date)}. -> {"rows": ranked results (each with "group",
    "groups", "group_rank"), "bench": {group or POOLED: bench}, "preps": {...}}."""
    preps = stock_preps(raws_by_group, settings, all_raws, cache)
    rows, bench = [], {g: p["bench"] for g, p in preps.items()}
    if mode == "own":
        for g, p in preps.items():
            fin = sc.finalize(p, weights=weights, ranges=_ranges(p["bench"], range_mode, sc), blend=blend)
            for r in fin["results"]:
                rows.append({**r, "group": g, "groups": [g], "group_rank": r["rank"]})
    else:
        member, pooled = {}, {}
        for g, (raws, _) in raws_by_group.items():
            for r in raws:
                member.setdefault(r["ticker"], []).append(g)
                pooled.setdefault(r["ticker"], r)
        stamp = tuple(sorted((g, s) for g, (_, s) in raws_by_group.items()))
        key = ("stkpool", sc.PREP_VERSION, stamp, settings["tbill"], settings["rf"], settings["erp"])
        p = cache.get(key) if cache is not None else None
        if p is None:
            p = sc.prepare_industry(list(pooled.values()), settings, all_raws=all_raws)
            if cache is not None:
                cache[key] = p
        bench[POOLED] = p["bench"]
        fin = sc.finalize(p, weights=weights, ranges=_ranges(p["bench"], range_mode, sc), blend=blend)
        for r in fin["results"]:
            gs = member[r["ticker"]]
            rows.append({**r, "group": " + ".join(gs), "groups": gs, "group_rank": r["rank"]})
        preps[POOLED] = p
    dates = {g: stamp for g, (_, stamp) in raws_by_group.items()}
    return {"rows": sc.rank(rows), "bench": bench, "preps": preps, "dates": dates}


# ------------------------------------------------------------------ ETFs
def etf_compare(raws_by_group, settings, weights, blend, hq_scores, hq_skips, mode="own", range_mode="laura",
                all_raws=None, cache=None, stamps=None):
    """raws_by_group: {group: raws already relabelled with category = group (etf_groups.group_raws)}.
    stamps: {group: data signature} for the prep cache. Same return shape as stock_compare."""
    stamps = stamps or {}
    hq_sig = (len(hq_scores or {}), len(hq_skips or {}))

    def prep(raws, key):
        if cache is not None and key in cache:
            return cache[key]
        p = es.prepare_funds(raws, settings, hq_scores, hq_skips, all_raws=all_raws)
        if cache is not None:
            cache[key] = p
        return p

    base = ("etf", es.PREP_VERSION, settings["tbill"], settings["window"], hq_sig)
    preps = {g: prep(raws, base + (g, stamps.get(g))) for g, raws in raws_by_group.items() if raws}
    bench = {g: p["bench"][g] for g, p in preps.items()}
    rows = []
    if mode == "own":
        for g, p in preps.items():
            fin = es.finalize(p, weights=weights, ranges_by_cat={g: _ranges(p["bench"][g], range_mode, es)}, blend=blend)
            for r in fin["results"]:
                rows.append({**r, "group": g, "groups": [g], "group_rank": r["rank"]})
    else:
        member, pooled = {}, {}
        for g, raws in raws_by_group.items():
            for r in raws:
                member.setdefault(r["ticker"], []).append(g)
                pooled.setdefault(r["ticker"], {**r, "category": POOLED})
        p = prep(list(pooled.values()), base + (POOLED, tuple(sorted((g, stamps.get(g)) for g in raws_by_group))))
        bench[POOLED] = p["bench"][POOLED]
        fin = es.finalize(p, weights=weights, ranges_by_cat={POOLED: _ranges(p["bench"][POOLED], range_mode, es)}, blend=blend)
        for r in fin["results"]:
            gs = member[r["ticker"]]
            rows.append({**r, "group": " + ".join(gs), "groups": gs, "group_rank": r["rank"]})
        preps[POOLED] = p
    return {"rows": es.rank(rows), "bench": bench, "preps": preps, "dates": {g: data_date(raws) for g, raws in raws_by_group.items()}}


def data_date(raws):
    """'YYYY-MM-DD' (or 'oldest to newest') of the downloads behind a group of funds."""
    d = sorted({str(r.get("fetched") or "")[:10] for r in raws if r.get("fetched")})
    return None if not d else d[0] if len(d) == 1 else f"{d[0]} to {d[-1]}"


# ------------------------------------------------------------------ summary
def summary(bench_by_group, counts, asset, metrics):
    """Per group: n and, for every weighted metric, (median, Laura bad-side bound or range, fallback level).
    -> list of {"group", "n", key: {"median", "laura", "level", "direction"}}."""
    out = []
    for g, bench in bench_by_group.items():
        info = laura.laura_info(bench, asset, metrics)
        row = {"group": g, "n": counts.get(g)}
        for m in metrics:
            b = bench.get(m["key"]) or {}
            q = b.get("q")
            li = info.get(m["key"])
            row[m["key"]] = {"median": q["median"] if q else None, "range": li["range"] if li else None,
                             "bound": li["bound"] if li and not li["fallback"] else None,
                             "tier": li["tier"] if li else None, "level": b.get("level"), "direction": m["direction"]}
        out.append(row)
    return out


# ------------------------------------------------------------------ self-tests
def _same(a, b, tol=1e-9):
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= tol


def run_self_tests():
    results = []

    def check(name, ok, detail=""):
        results.append({"test": name, "passed": bool(ok), "detail": detail})

    # ---------------- stocks: two synthetic industries of 12
    ind_a = [sc._synthetic_raw(i) for i in range(12)]
    ind_b = [sc._synthetic_raw(i) for i in range(60, 72)]
    for r in ind_b:
        r["ticker"] = "B" + r["ticker"]
        r["px"]["mdd"] += 0.2  # industry B draws down more
    settings = dict(sc.DEFAULT_SETTINGS)
    w = dict(sc.DEFAULT_WEIGHTS)
    by = {"Ind A": (ind_a, "d1"), "Ind B": (ind_b, "d1")}
    own = stock_compare(by, settings, w, 0.5, "own")
    page = {g: {r["ticker"]: r for r in sc.finalize(sc.prepare_industry(raws, settings), weights=w, blend=0.5)["results"]}
            for g, (raws, _) in by.items()}
    diffs = []
    for r in own["rows"]:
        p = page[r["group"]][r["ticker"]]
        for k in ("overall", "fit", "composite"):
            if not _same(r[k], p[k]):
                diffs.append((r["ticker"], k))
        for mk in sc.KEYS:
            for f in ("pct", "fit", "range"):
                a, b = r["metrics"][mk][f], p["metrics"][mk][f]
                if (a != b) if f == "range" else not _same(a, b):
                    diffs.append((r["ticker"], mk, f))
    ov = [r["overall"] for r in own["rows"] if not r["low_data"]]
    check("C1. Stocks 'each vs its own industry': every name's Overall/Fit/Composite, per-metric percentile, Fit and Laura range "
          "equal its own industry page; rows ranked together by Overall",
          not diffs and len(own["rows"]) == 24 and ov == sorted(ov, reverse=True), f"{len(own['rows'])} rows, mismatches {diffs[:4]}")

    pooled = stock_compare(by, settings, w, 0.5, "pooled")
    pb = pooled["bench"][POOLED]["max_drawdown"]
    vals = [c["metrics"]["max_drawdown"]["value"] for c in pooled["preps"][POOLED]["companies"].values()]
    exp_pct = dict(zip([t for t in pooled["preps"][POOLED]["companies"]], sc.percentile_scores(vals, False)))
    lr = laura.laura_ranges(pooled["bench"][POOLED], "stock", sc.METRICS)
    pr = {r["ticker"]: r for r in pooled["rows"]}
    ok2 = (pb["pool_n"] == 24 and pb["level"] == "industry"
           and abs(pb["q"]["median"] - float(np.median(vals))) < 1e-9 and abs(pb["spread"] - (np.percentile(vals, 75) - np.percentile(vals, 25))) < 1e-9
           and all(abs(pr[t]["metrics"]["max_drawdown"]["pct"] - exp_pct[t]) < 1e-9 for t in pr)
           and abs(lr["max_drawdown"][1] - float(np.median(vals))) < 1e-9
           and all(pr[t]["metrics"]["max_drawdown"]["range"] == lr["max_drawdown"] for t in pr)
           and abs(lr["max_drawdown"][1] - laura.laura_ranges(own["bench"]["Ind A"], "stock", sc.METRICS)["max_drawdown"][1]) > 1e-6)
    check("C2. Stocks 'pooled': percentiles, median, spread and Laura ranges recomputed on the 24 pooled names (risk bound = pooled median)",
          ok2, f"pooled median {pb['q']['median']:.2f} vs Ind A Laura bound "
               f"{laura.laura_ranges(own['bench']['Ind A'], 'stock', sc.METRICS)['max_drawdown'][1]:.2f}")

    small = {"S1": ([sc._synthetic_raw(i) for i in range(3)], "d"), "S2": ([sc._synthetic_raw(i) for i in range(30, 34)], "d")}
    others = [sc._synthetic_raw(i) for i in range(80, 92)]
    sp = stock_compare(small, settings, w, 0.5, "pooled", all_raws=lambda: others)
    lv = sp["bench"][POOLED]["roic"]["level"]
    flagged = any(f["code"] == "sector_fallback:roic" for f in sp["rows"][0]["flags"])
    check("C3. Pooled set with 7 names (< 10) uses the same sector fallback and flag", lv == "sector" and flagged, f"level {lv}, flag {flagged}")

    avg = stock_compare(by, settings, w, 0.5, "own", range_mode="avg")
    ok4 = all(r["metrics"][k]["range"] == laura.default_ranges(own["bench"][r["group"]]).get(k, (None, None))
              for r in avg["rows"] for k in sc.KEYS if own["bench"][r["group"]][k]["q"])
    check("C4. 'Industry average' ranges = each peer group's 25th-75th", ok4)

    # ---------------- ETFs: two groups + one fund in both (category AND theme)
    ga = [es._fake_fund(i) for i in range(12)]
    gb = [es._fake_fund(i) for i in range(30, 42)]
    for f in gb:
        f["px"]["mdd_10"] += 0.15
    gb.append(dict(ga[0]))  # E00 is in both groups
    eset = dict(es.DEFAULT_SETTINGS)
    ew = dict(es.DEFAULT_WEIGHTS)
    rb = {"Cat A": [{**f, "category": "Cat A"} for f in ga], "Theme B": [{**f, "category": "Theme B"} for f in gb]}
    eo = etf_compare(rb, eset, ew, 0.5, {}, {}, "own")
    epage = {g: {r["ticker"]: r for r in es.finalize(es.prepare_funds(raws, eset), weights=ew, blend=0.5)["results"]} for g, raws in rb.items()}
    ed_ = [(r["group"], r["ticker"]) for r in eo["rows"]
           if not all(_same(r[k], epage[r["group"]][r["ticker"]][k]) for k in ("overall", "fit", "composite"))
           or any(r["metrics"][k]["range"] != epage[r["group"]][r["ticker"]]["metrics"][k]["range"] for k in es.KEYS)]
    check("C5. ETFs 'each vs its own group': scores equal the group page; a fund in two groups appears once per group",
          not ed_ and len(eo["rows"]) == 25 and sum(r["ticker"] == "E00" for r in eo["rows"]) == 2, f"{len(eo['rows'])} rows, mismatches {ed_[:3]}")
    ep = etf_compare(rb, eset, ew, 0.5, {}, {}, "pooled")
    e00 = next(r for r in ep["rows"] if r["ticker"] == "E00")
    vals = sorted(f["px"]["mdd_10"] * 100 for f in {x["ticker"]: x for g in rb.values() for x in g}.values())
    check("C6. ETFs 'pooled': one peer group (fund in both groups counted once), Laura bound = pooled median",
          len(ep["rows"]) == 24 and e00["groups"] == ["Cat A", "Theme B"] and ep["bench"][POOLED]["max_drawdown"]["pool_n"] == 24
          and abs(e00["metrics"]["max_drawdown"]["range"][1] - float(np.median(vals))) < 1e-9,
          f"{len(ep['rows'])} rows, E00 in {e00['groups']}")
    return results
