#!/usr/bin/env python3
"""Slice public wastewater feeds into small per-city JSON files for the app.

Sources
  Berlin : RKI AMELAG per-site TSV (GitHub) + national aggregate TSV
  NYC    : NYC Open Data Socrata f7dc-2q9f (COVID concentration) + CDC WVAL (fill-in, flu/RSV, official category)
  DC     : CDC Wastewater Viral Activity Level dataset atcp-73re, site ID:300 (Blue Plains)

COVID series, the threshold readout (five bands against a reference prevalence) and the
lab-change alarms come from wastewater_threshold.py with the frozen parameters in
threshold_config.json (both produced by the methods project in ../methods). Flu and RSV are
reshaped here. Output: data/<city>.json and data/manifest.json (schema 2).
"""
import argparse, csv, datetime as dt, io, json, math, os, re, sys, urllib.request
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wastewater_threshold as wt  # noqa: E402

RKI_SITES = "https://raw.githubusercontent.com/robert-koch-institut/Abwassersurveillance_AMELAG/main/amelag_einzelstandorte.tsv"
RKI_AGG = "https://raw.githubusercontent.com/robert-koch-institut/Abwassersurveillance_AMELAG/main/amelag_aggregierte_kurve.tsv"
NYC_API = "https://data.cityofnewyork.us/resource/f7dc-2q9f.json"
CDC_API = "https://data.cdc.gov/resource/atcp-73re.json"

BERLIN_PLANTS = {"Berlin Ruhleben": "Ruhleben", "Berlin Waßmannsdorf": "Waßmannsdorf", "Berlin Schönerlinde": "Schönerlinde"}
RKI_PATHOGENS = {"covid": "SARS-CoV-2", "fluA": "Influenza A", "fluB": "Influenza B", "rsv": "RSV A/B"}
CDC_PATHOGENS = {"covid": "SARS-CoV-2", "fluA": "Influenza A virus", "rsv": "RSV"}
LABELS = {"covid": "COVID-19", "fluA": "Influenza A", "fluB": "Influenza B", "rsv": "RSV"}
NYC_COUNTIES = ("Kings", "Queens", "Bronx", "Richmond", "New York")
SCHEMA = 2

CFG = wt.load_config()
TODAY = dt.datetime.now(dt.timezone.utc).date()

def log(*a): print(*a, file=sys.stderr)

def fetch(url, cache_dir=None):
    name = os.path.join(cache_dir, url.split("/")[-1].split("?")[0] or "x") if cache_dir else None
    if name and os.path.exists(name):
        return open(name, "rb").read()
    log("GET", url)
    req = urllib.request.Request(url, headers={"User-Agent": "covid-wastewater-app pipeline"})
    with urllib.request.urlopen(req, timeout=300) as r:
        data = r.read()
    if name:
        os.makedirs(cache_dir, exist_ok=True); open(name, "wb").write(data)
    return data

def socrata_all(base, where, select=None, order=None, page=50000):
    out, offset = [], 0
    while True:
        q = f"{base}?$where={urllib.request.quote(where)}&$limit={page}&$offset={offset}"
        if select: q += f"&$select={urllib.request.quote(select)}"
        if order: q += f"&$order={urllib.request.quote(order)}"
        chunk = json.loads(fetch(q))
        out += chunk
        if len(chunk) < page: return out
        offset += page

def week_end(d):  # Sunday of the ISO week containing d
    return d + dt.timedelta(days=6 - d.weekday())

def iso(d): return d.isoformat()

def geo_weighted(pairs):  # [(value, weight)] -> population-weighted geometric mean
    pairs = [(v, w) for v, w in pairs if v is not None and v > 0 and w > 0]
    if not pairs: return None
    return 10 ** (sum(math.log10(v) * w for v, w in pairs) / sum(w for _, w in pairs))

def arith_weighted(pairs):
    pairs = [(v, w) for v, w in pairs if v is not None and w > 0]
    if not pairs: return None
    return sum(v * w for v, w in pairs) / sum(w for _, w in pairs)

def regrid(weekly):
    """Reindex to a continuous 7-day grid; missing weeks get raw=None so the app can see gaps."""
    if not weekly: return weekly
    by = {w["weekEnd"]: w for w in weekly}
    d, end = dt.date.fromisoformat(weekly[0]["weekEnd"]), dt.date.fromisoformat(weekly[-1]["weekEnd"])
    out = []
    while d <= end:
        out.append(by.get(iso(d), {"weekEnd": iso(d), "value": None, "raw": None, "n": 0})); d += dt.timedelta(days=7)
    return out

def trailing_smooth(weekly, log_scale, window=3):
    """Fill 'value' with a trailing window mean of 'raw' over the weekly grid (log-space for concentrations)."""
    weekly = regrid(weekly)
    vals = [w["raw"] for w in weekly]
    for i, w in enumerate(weekly):
        window_vals = [v for v in vals[max(0, i - window + 1): i + 1] if v is not None]
        if not window_vals: w["value"] = None; continue
        if log_scale: w["value"] = 10 ** (sum(math.log10(max(v, 1.0)) for v in window_vals) / len(window_vals))
        else: w["value"] = sum(window_vals) / len(window_vals)
    return weekly

def r(v, nd=1):
    return None if v is None else round(v, nd)

def camel(obj):
    """snake_case keys -> camelCase, recursively, to match the rest of the feed."""
    if isinstance(obj, dict):
        return {re.sub(r"_([a-z0-9])", lambda m: m.group(1).upper(), k): camel(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [camel(x) for x in obj]
    if isinstance(obj, (dt.date, dt.datetime)):
        return obj.isoformat()
    return obj

# ---------------------------------------------------------------- threshold readout (all cities)
def covid_block(series, concentration):
    """Corrected COVID weekly series plus the threshold block for one city's series dict from wt.*_series."""
    city = series["city"]
    since = series["comparable_since"]
    block = {"comparableSince": iso(since), "readout": None, "chart": []}
    if series["status"] != "ok":
        log(f"!! {city}: {series['status']}: {series['message']}")
        block["readout"] = camel({"available": False, "status": series["status"], "message": series["message"]})
        return [], block, None
    readout = wt.readout(series, CFG, TODAY)
    conv = CFG["cities"][city]["conversion"]
    readout["band_edges_level"] = [10 ** x for x in readout["band_edges_wastewater"]]
    block["readout"] = camel(readout)
    block["chart"] = camel(wt.chart_series(series, CFG))
    nd = 0 if concentration else 3
    weeks = series["weeks"]
    berlin = CFG["cities"][city]["week"] == "iso_monday_to_sunday"
    weekly = []
    for w in sorted(weeks):
        t2 = wt._trailing2(weeks, w)
        own = 10 ** weeks[w]["log10"]
        weekly.append({"weekEnd": iso(w + dt.timedelta(days=6) if berlin else w),
                       "value": r(10 ** t2 if t2 is not None else own, nd), "raw": r(own, nd), "n": weeks[w]["n_plants"],
                       "belowLodShare": 1.0 if weeks[w]["below_loq"] else 0.0, "provisional": weeks[w]["provisional"]})
    for w in weekly: w["prevalence"] = 10 ** (conv["a"] + conv["b"] * (math.log10(w["value"]) - conv["w_ref"])) if w["value"] else None
    return regrid(weekly), block, readout

# ---------------------------------------------------------------- Berlin
def build_berlin(args):
    raw = fetch(RKI_SITES, args.cache) if not args.rki_file else open(args.rki_file, "rb").read()
    rows = [x for x in csv.DictReader(io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8"), delimiter="\t") if x["standort"] in BERLIN_PLANTS]
    agg_rows = list(csv.DictReader(io.TextIOWrapper(io.BytesIO(fetch(RKI_AGG, args.cache)), encoding="utf-8"), delimiter="\t"))
    bcfg = CFG["cities"]["berlin"]["series"]
    floors = set(bcfg["floor_values_raw_copies_per_l"])

    def num(x): return None if x in (None, "NA", "") else float(x)

    pathogens = []
    # COVID: corrected series and readout from the threshold module
    series = wt.berlin_series(rows, CFG)
    weekly, block, readout = covid_block(series, concentration=True)
    since = series["comparable_since"]
    sites = []
    for plant, short in BERLIN_PLANTS.items():
        pts = []
        for x in rows:
            if x["standort"] != plant or x["typ"] != "SARS-CoV-2": continue
            d = dt.date.fromisoformat(x["datum"]); v = num(x["viruslast_normalisiert"])
            if d < since or v is None or v <= 0: continue
            below = x.get("unter_bg") == "ja" or num(x["viruslast"]) in floors
            pts.append({"date": iso(d), "value": v, "smoothed": None, "belowLod": below})
        pop = next((int(float(x["einwohner"])) for x in rows if x["standort"] == plant), 0)
        sites.append({"name": short, "population": pop, "points": pts})
    ref = [{"weekEnd": iso(week_end(dt.date.fromisoformat(x["datum"]))), "value": r(float(x["viruslast"]), 0)}
           for x in agg_rows if x["typ"] == "SARS-CoV-2" and x["viruslast"] != "NA" and dt.date.fromisoformat(x["datum"]) >= since]
    pathogens.append({
        "id": "covid", "label": LABELS["covid"], "scale": "concentration", "unit": "gene copies / L",
        "latestSampleDate": iso(series["newest_sample"]) if series.get("newest_sample") else None,
        "comparableSince": iso(since), "weekly": weekly, "sites": sites, "labChanges": [], "officialCategory": None,
        "reference": {"label": "Germany, all sites", "weekly": ref} if ref else None, "threshold": block,
        "note": "Flow-normalised concentrations from Ruhleben, Waßmannsdorf and Schönerlinde, population-weighted with fixed plant offsets, "
                "2-week trailing mean. Only data since the April 2024 laboratory change is shown; earlier values came from other labs and are 20–40× lower.",
    })

    # Flu A/B, RSV: RKI's smoothed series as before (no lab changes in these series)
    for pid, typ in RKI_PATHOGENS.items():
        if pid == "covid": continue
        prow_by_plant = defaultdict(list)
        for x in rows:
            if x["typ"] == typ: prow_by_plant[BERLIN_PLANTS[x["standort"]]].append(x)
        if not prow_by_plant: continue
        sites, weekly_acc, weekly_raw_acc, weekly_lod, lab_changes = [], defaultdict(list), defaultdict(list), defaultdict(list), set()
        latest = None
        for plant, prow in prow_by_plant.items():
            pop = float(prow[0]["einwohner"]); points = []
            for x in prow:
                d = dt.date.fromisoformat(x["datum"]); sm = num(x["vorhersage"]); rv = num(x["viruslast"])
                if x.get("laborwechsel") == "ja": lab_changes.add(x["datum"])
                if sm is not None: weekly_acc[week_end(d)].append((sm, pop))
                if rv is not None:
                    weekly_raw_acc[week_end(d)].append((rv, pop)); weekly_lod[week_end(d)].append(x.get("unter_bg") == "ja")
                    points.append({"date": iso(d), "value": rv, "smoothed": r(sm, 0), "belowLod": x.get("unter_bg") == "ja"})
                    latest = max(latest, d) if latest else d
            sites.append({"name": plant, "population": int(pop), "points": points})
        weekly = []
        for we in sorted(set(weekly_acc) | set(weekly_raw_acc)):
            rawv = geo_weighted(weekly_raw_acc.get(we, [])); sm = geo_weighted(weekly_acc.get(we, [])) or rawv; lod = weekly_lod.get(we, [])
            weekly.append({"weekEnd": iso(we), "value": r(sm, 0), "raw": r(rawv, 0), "n": len(lod), "belowLodShare": r(sum(lod) / len(lod), 2) if lod else None})
        weekly = regrid(weekly)
        if latest: weekly = [w for w in weekly if dt.date.fromisoformat(w["weekEnd"]) <= week_end(latest)]
        ref = [{"weekEnd": iso(week_end(dt.date.fromisoformat(x["datum"]))), "value": r(float(x["viruslast"]), 0)} for x in agg_rows if x["typ"] == typ and x["viruslast"] != "NA"]
        pathogens.append({
            "id": pid, "label": LABELS[pid], "scale": "concentration", "unit": "gene copies / L",
            "latestSampleDate": iso(latest) if latest else None, "comparableSince": weekly[0]["weekEnd"] if weekly else None,
            "weekly": weekly, "sites": sites, "labChanges": sorted({iso(week_end(dt.date.fromisoformat(x))) for x in lab_changes}),
            "officialCategory": None, "reference": {"label": "Germany, all sites", "weekly": ref} if ref else None,
            "note": "Population-weighted blend of Ruhleben, Waßmannsdorf and Schönerlinde using RKI's smoothed series.",
        })
    return {"cityId": "berlin", "cityName": "Berlin", "country": "DE",
            "attribution": "Robert Koch-Institut, Abwassersurveillance AMELAG (CC-BY 4.0)",
            "sourceUrl": "https://github.com/robert-koch-institut/Abwassersurveillance_AMELAG", "pathogens": pathogens}

# ---------------------------------------------------------------- CDC helpers
def cdc_rows(where):
    return socrata_all(CDC_API, where, order="week_end")

def cdc_weekly(rows, pathogen_target):
    """Population-weighted mean WVAL per week across the given rows."""
    acc = defaultdict(list)
    for x in rows:
        if x["pathogen_target"] != pathogen_target or "site_wval" not in x: continue
        pop = float(x.get("population_served") or 0) or 1.0
        acc[x["week_end"]].append((float(x["site_wval"]), pop))
    return [{"weekEnd": we, "raw": r(arith_weighted(acc[we]), 2), "n": len(acc[we])} for we in sorted(acc)]

def wval_pathogen(pid, rows, target, sites, note, category):
    weekly = trailing_smooth(cdc_weekly(rows, target), log_scale=False, window=2)
    for w in weekly: w["value"] = r(w["value"], 2)
    if not weekly: return None
    cat, cat_week, cat_n = category
    return {"id": pid, "label": LABELS[pid], "scale": "wval", "unit": "CDC wastewater viral activity level",
            "latestSampleDate": weekly[-1]["weekEnd"], "comparableSince": weekly[0]["weekEnd"], "weekly": weekly, "sites": sites,
            "labChanges": [], "officialCategory": cat, "officialCategoryWeek": cat_week, "officialCategoryN": cat_n, "reference": None, "note": note}

def dc_category(rows, target):
    """CDC's own category for the newest week of a single site."""
    cand = [x for x in rows if x["pathogen_target"] == target and x.get("site_wval_category")]
    if not cand: return None, None, 0
    x = max(cand, key=lambda x: x["week_end"])
    return x["site_wval_category"], x["week_end"][:10], 1

# ---------------------------------------------------------------- DC
def build_dc(args):
    rows = cdc_rows("state_territory='District of Columbia' AND site='ID:300'")
    note = ("CDC standardized level for DC Water's Blue Plains plant, which serves about 1.6 million people in DC and the Maryland and Virginia suburbs. "
            "Only data since the June 2024 laboratory change is shown. CDC's category for this site runs high: since that change Blue Plains has read about 3× neighbouring sites on CDC's scale.")
    site = [{"name": "Blue Plains", "population": 1600000, "points": []}]
    pathogens = []
    series = wt.dc_series(rows, CFG)
    weekly, block, readout = covid_block(series, concentration=False)
    cat = dc_category(rows, "SARS-CoV-2")
    pathogens.append({"id": "covid", "label": LABELS["covid"], "scale": "wval", "unit": "CDC wastewater viral activity level",
                      "latestSampleDate": iso(series["newest_sample"]) if series.get("newest_sample") else None,
                      "comparableSince": iso(series["comparable_since"]), "weekly": weekly, "sites": site, "labChanges": [],
                      "officialCategory": cat[0], "officialCategoryWeek": cat[1], "officialCategoryN": cat[2], "reference": None,
                      "threshold": block, "note": note})
    for pid in ("fluA", "rsv"):
        p = wval_pathogen(pid, rows, CDC_PATHOGENS[pid], site, note, dc_category(rows, CDC_PATHOGENS[pid]))
        if p: pathogens.append(p)
    return {"cityId": "dc", "cityName": "Washington DC", "country": "US",
            "attribution": "CDC National Wastewater Surveillance System, Wastewater Viral Activity Level",
            "sourceUrl": "https://data.cdc.gov/d/atcp-73re", "pathogens": pathogens}

# ---------------------------------------------------------------- NYC
def build_nyc(args):
    rows = socrata_all(NYC_API, "copies_l IS NOT NULL", order="sample_date")
    county_filter = " OR ".join(f"counties_served like '%{c}%'" for c in NYC_COUNTIES)
    site_map = CFG["cities"]["nyc"]["series"]["cdc_site_map"]
    cdc = [x for x in cdc_rows(f"state_territory='New York' AND ({county_filter})") if x.get("site") in site_map]
    ncfg = CFG["cities"]["nyc"]["series"]

    series = wt.nyc_series(rows, cdc, CFG)
    weekly, block, readout = covid_block(series, concentration=True)
    since = series["comparable_since"]
    # plant points: one row per plant and sample date, digital PCR only, comparable period only
    methods = ncfg["methods_in_preference_order"]
    best, pops = {}, {}
    for x in rows:
        if x.get("technology") not in methods: continue
        d = dt.date.fromisoformat(x["sample_date"][:10])
        if wt.us_saturday(d) < since: continue
        k = (x["wrrf_name"], d)
        if k not in best or methods.index(x["technology"]) < methods.index(best[k][0]):
            best[k] = (x["technology"], float(x["copies_l"]))
        pops[x["wrrf_name"]] = int(float(x.get("population_served") or 0))
    sites = []
    for plant in sorted(pops):
        pts = [{"date": iso(d), "value": v, "smoothed": None, "belowLod": False} for (p, d), (_, v) in sorted(best.items()) if p == plant]
        sites.append({"name": plant, "population": pops[plant], "points": pts})
    cat = wt.cdc_category_nyc(cdc, CFG, "covid")
    pathogens = [{
        "id": "covid", "label": LABELS["covid"], "scale": "concentration", "unit": "gene copies / L",
        "latestSampleDate": iso(series["newest_sample"]) if series.get("newest_sample") else None, "comparableSince": iso(since),
        "weekly": weekly, "sites": sites, "labChanges": [], "officialCategory": cat[0], "officialCategoryWeek": cat[1], "officialCategoryN": cat[2],
        "reference": None, "threshold": block,
        "note": "Population-weighted blend of NYC DEP's 14 treatment plants (digital PCR only, since April 2023), fixed plant offsets, 2-week trailing mean. "
                "Weeks NYC has not yet published are filled from CDC's level for the same plants and marked provisional. "
                "CDC category: median of the plants in the latest CDC week with at least 10 reporting.",
    }]
    cdc_sites = [{"name": n, "population": s["population"], "points": []} for n, s in ((s["name"], s) for s in sites)]
    for pid in ("fluA", "rsv"):
        p = wval_pathogen(pid, cdc, CDC_PATHOGENS[pid], cdc_sites,
                          "NYC does not publish flu or RSV wastewater data; this is CDC's standardized level across the same 14 plants.",
                          wt.cdc_category_nyc(cdc, CFG, pid))
        if p: pathogens.append(p)
    return {"cityId": "nyc", "cityName": "New York City", "country": "US",
            "attribution": "NYC Open Data (DEP / DOHMH) and CDC NWSS",
            "sourceUrl": "https://data.cityofnewyork.us/d/f7dc-2q9f", "pathogens": pathogens}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data"); ap.add_argument("--cache", default=None)
    ap.add_argument("--rki-file", default=None, help="local copy of amelag_einzelstandorte.tsv")
    ap.add_argument("--only", default=None)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    builders = {"berlin": build_berlin, "nyc": build_nyc, "dc": build_dc}
    manifest = {"schemaVersion": SCHEMA, "generatedAt": now, "cities": []}
    for cid, fn in builders.items():
        if args.only and cid != args.only: continue
        try:
            city = fn(args)
        except Exception as e:  # one broken feed must not take the others down
            log(f"!! {cid} failed: {e!r}"); continue
        city.update({"schemaVersion": SCHEMA, "generatedAt": now})
        path = os.path.join(args.out, f"{cid}.json")
        json.dump(city, open(path, "w"), ensure_ascii=False, separators=(",", ":"))
        covid = next((p for p in city["pathogens"] if p["id"] == "covid"), None)
        ro = (covid or {}).get("threshold", {}).get("readout") or {}
        manifest["cities"].append({"cityId": cid, "cityName": city["cityName"], "file": f"{cid}.json",
                                   "pathogens": [p["id"] for p in city["pathogens"]],
                                   "latestSampleDate": max((p["latestSampleDate"] or "") for p in city["pathogens"]),
                                   "readout": ro.get("mainLine") or ro.get("status")})
        log(f"wrote {path} ({os.path.getsize(path)//1024} KB): {ro.get('mainLine') or ro.get('status')} | {ro.get('secondLine', '')}")
    json.dump(manifest, open(os.path.join(args.out, "manifest.json"), "w"), indent=1)

if __name__ == "__main__":
    main()
