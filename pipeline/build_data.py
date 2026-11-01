#!/usr/bin/env python3
"""Slice public wastewater feeds into small per-city JSON files for the app.

Sources
  Berlin : RKI AMELAG per-site TSV (GitHub) + national aggregate TSV
  NYC    : NYC Open Data Socrata f7dc-2q9f (COVID concentration) + CDC WVAL (flu/RSV, official category)
  DC     : CDC Wastewater Viral Activity Level dataset atcp-73re, site ID:300 (Blue Plains)

Output: data/<city>.json and data/manifest.json. The app does all analytics; this
script only reshapes data into weekly, population-weighted series.
"""
import argparse, csv, datetime as dt, io, json, math, os, sys, urllib.request
from collections import defaultdict

RKI_SITES = "https://raw.githubusercontent.com/robert-koch-institut/Abwassersurveillance_AMELAG/main/amelag_einzelstandorte.tsv"
RKI_AGG = "https://raw.githubusercontent.com/robert-koch-institut/Abwassersurveillance_AMELAG/main/amelag_aggregierte_kurve.tsv"
NYC_API = "https://data.cityofnewyork.us/resource/f7dc-2q9f.json"
CDC_API = "https://data.cdc.gov/resource/atcp-73re.json"

BERLIN_PLANTS = {"Berlin Ruhleben": "Ruhleben", "Berlin Waßmannsdorf": "Waßmannsdorf", "Berlin Schönerlinde": "Schönerlinde"}
RKI_PATHOGENS = {"covid": "SARS-CoV-2", "fluA": "Influenza A", "fluB": "Influenza B", "rsv": "RSV A/B"}
CDC_PATHOGENS = {"covid": "SARS-CoV-2", "fluA": "Influenza A virus", "rsv": "RSV"}
LABELS = {"covid": "COVID-19", "fluA": "Influenza A", "fluB": "Influenza B", "rsv": "RSV"}
NYC_COUNTIES = ("Kings", "Queens", "Bronx", "Richmond", "New York")
CDC_COVID_CUTS = [(2.6, "Very Low"), (4.9, "Low"), (7.9, "Moderate"), (11.6, "High"), (math.inf, "Very High")]
CDC_FLUA_CUTS = [(1.6, "Very Low"), (3.1, "Low"), (5.0, "Moderate"), (7.6, "High"), (math.inf, "Very High")]
CDC_RSV_CUTS = [(1.6, "Very Low"), (2.6, "Low"), (3.8, "Moderate"), (5.6, "High"), (math.inf, "Very High")]

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

def socrata_all(base, where, select=None, order=None, cache_dir=None, page=50000):
    out, offset = [], 0
    while True:
        q = f"{base}?$where={urllib.request.quote(where)}&$limit={page}&$offset={offset}"
        if select: q += f"&$select={urllib.request.quote(select)}"
        if order: q += f"&$order={urllib.request.quote(order)}"
        chunk = json.loads(fetch(q, cache_dir=None))
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
        out.append(by.get(iso(d), {"weekEnd": iso(d), "raw": None, "n": 0})); d += dt.timedelta(days=7)
    return out

def trailing_smooth(weekly, log_scale, window=3):
    """Fill 'value' with a trailing window mean of 'raw' over the weekly grid (log-space for concentrations).
    A week with no raw sample and no sample in the window stays None."""
    weekly = regrid(weekly)
    vals = [w["raw"] for w in weekly]
    for i, w in enumerate(weekly):
        window_vals = [v for v in vals[max(0, i - window + 1): i + 1] if v is not None]
        if not window_vals or vals[i] is None and i == len(weekly) - 1: w["value"] = None if not window_vals else w.get("value")
        if not window_vals: w["value"] = None; continue
        if log_scale: w["value"] = 10 ** (sum(math.log10(max(v, 1.0)) for v in window_vals) / len(window_vals))
        else: w["value"] = sum(window_vals) / len(window_vals)
    return weekly

def categorize(v, cuts):
    if v is None: return None
    for cut, label in cuts:
        if v <= cut: return label

def r(v, nd=1):
    return None if v is None else round(v, nd)

# ---------------------------------------------------------------- Berlin
def build_berlin(args):
    raw = fetch(RKI_SITES, args.cache) if not args.rki_file else open(args.rki_file, "rb").read()
    rows = csv.DictReader(io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8"), delimiter="\t")
    by_path = defaultdict(lambda: defaultdict(list))  # pathogen -> plant -> rows
    lab_changes = defaultdict(set)
    for row in rows:
        plant = BERLIN_PLANTS.get(row["standort"])
        if not plant: continue
        for pid, typ in RKI_PATHOGENS.items():
            if row["typ"] == typ:
                by_path[pid][plant].append(row)
                if row.get("laborwechsel") == "ja": lab_changes[pid].add(row["datum"])
    agg_rows = list(csv.DictReader(io.TextIOWrapper(io.BytesIO(fetch(RKI_AGG, args.cache)), encoding="utf-8"), delimiter="\t"))

    pathogens = []
    for pid, typ in RKI_PATHOGENS.items():
        plants = by_path.get(pid)
        if not plants: continue
        sites, weekly_acc, weekly_raw_acc, weekly_lod = [], defaultdict(list), defaultdict(list), defaultdict(list)
        latest = None
        for plant, prow in plants.items():
            pop = float(prow[0]["einwohner"])
            points = []
            for x in prow:
                d = dt.date.fromisoformat(x["datum"])
                sm = None if x["vorhersage"] == "NA" else float(x["vorhersage"])
                rv = None if x["viruslast"] == "NA" else float(x["viruslast"])
                if sm is not None: weekly_acc[week_end(d)].append((sm, pop))
                if rv is not None:
                    weekly_raw_acc[week_end(d)].append((rv, pop))
                    weekly_lod[week_end(d)].append(x.get("unter_bg") == "ja")
                    points.append({"date": iso(d), "value": rv, "smoothed": r(sm, 0), "belowLod": x.get("unter_bg") == "ja"})
                    latest = max(latest, d) if latest else d
            sites.append({"name": plant, "population": int(pop), "points": points})
        weekly = []
        for we in sorted(set(weekly_acc) | set(weekly_raw_acc)):
            raw = geo_weighted(weekly_raw_acc.get(we, []))
            sm = geo_weighted(weekly_acc.get(we, [])) or raw
            lod = weekly_lod.get(we, [])
            weekly.append({"weekEnd": iso(we), "value": r(sm, 0), "raw": r(raw, 0), "n": len(lod),
                           "belowLodShare": r(sum(lod) / len(lod), 2) if lod else None})
        weekly = regrid(weekly)
        for w in weekly: w.setdefault("value", None); w.setdefault("belowLodShare", None)
        # drop trailing weeks that are pure GAM extrapolation beyond the last real sample
        if latest: weekly = [w for w in weekly if dt.date.fromisoformat(w["weekEnd"]) <= week_end(latest)]
        ref = []
        for x in agg_rows:
            if x["typ"] != typ or x["viruslast"] == "NA": continue
            ref.append({"weekEnd": iso(week_end(dt.date.fromisoformat(x["datum"]))), "value": r(float(x["viruslast"]), 0)})
        pathogens.append({
            "id": pid, "label": LABELS[pid], "scale": "concentration", "unit": "gene copies / L",
            "latestSampleDate": iso(latest) if latest else None, "weekly": weekly, "sites": sites,
            "labChanges": sorted({iso(week_end(dt.date.fromisoformat(x))) for x in lab_changes[pid]}), "officialCategory": None,
            "reference": {"label": "Germany, all sites", "weekly": ref} if ref else None,
            "note": "Population-weighted blend of Ruhleben, Waßmannsdorf and Schönerlinde using RKI's smoothed series.",
        })
    return {"cityId": "berlin", "cityName": "Berlin", "country": "DE",
            "attribution": "Robert Koch-Institut, Abwassersurveillance AMELAG (CC-BY 4.0)",
            "sourceUrl": "https://github.com/robert-koch-institut/Abwassersurveillance_AMELAG", "pathogens": pathogens}

# ---------------------------------------------------------------- CDC helper
def cdc_rows(where, args):
    return socrata_all(CDC_API, where, order="week_end")

def cdc_weekly(rows, pathogen_target):
    """Population-weighted mean WVAL per week across the given rows."""
    acc, pops, latest, cat = defaultdict(list), {}, None, None
    for x in rows:
        if x["pathogen_target"] != pathogen_target or "site_wval" not in x: continue
        pop = float(x.get("population_served") or 0) or 1.0
        acc[x["week_end"]].append((float(x["site_wval"]), pop))
    weekly = [{"weekEnd": we, "raw": r(arith_weighted(acc[we]), 2), "n": len(acc[we])} for we in sorted(acc)]
    return weekly

def wval_pathogen(pid, rows, target, cuts, sites, note):
    weekly = trailing_smooth(cdc_weekly(rows, target), log_scale=False, window=2)
    for w in weekly: w["value"] = r(w["value"], 2)
    if not weekly: return None
    return {"id": pid, "label": LABELS[pid], "scale": "wval", "unit": "CDC wastewater viral activity level",
            "latestSampleDate": weekly[-1]["weekEnd"], "weekly": weekly, "sites": sites, "labChanges": [],
            "officialCategory": categorize(weekly[-1]["raw"], cuts), "reference": None, "note": note}

# ---------------------------------------------------------------- DC
def build_dc(args):
    rows = cdc_rows("state_territory='District of Columbia' AND site='ID:300'", args)
    note = "CDC standardized level for DC Water's Blue Plains plant, which serves about 1.6 million people in DC and the Maryland and Virginia suburbs. CDC publishes no raw concentrations for this site."
    pathogens = []
    for pid, target, cuts in (("covid", "SARS-CoV-2", CDC_COVID_CUTS), ("fluA", "Influenza A virus", CDC_FLUA_CUTS), ("rsv", "RSV", CDC_RSV_CUTS)):
        p = wval_pathogen(pid, rows, target, cuts, [{"name": "Blue Plains", "population": 1600000, "points": []}], note)
        if p: pathogens.append(p)
    return {"cityId": "dc", "cityName": "Washington DC", "country": "US",
            "attribution": "CDC National Wastewater Surveillance System, Wastewater Viral Activity Level",
            "sourceUrl": "https://data.cdc.gov/d/atcp-73re", "pathogens": pathogens}

# ---------------------------------------------------------------- NYC
def build_nyc(args):
    rows = socrata_all(NYC_API, "copies_l IS NOT NULL", order="sample_date")
    acc, sites = defaultdict(list), defaultdict(lambda: {"points": [], "population": 0})
    latest = None
    for x in rows:
        d = dt.date.fromisoformat(x["sample_date"][:10]); v = float(x["copies_l"]); pop = float(x.get("population_served") or 0)
        if pop <= 0: continue
        acc[week_end(d)].append((v, pop))
        s = sites[x["wrrf_name"]]; s["population"] = int(pop); s["points"].append({"date": iso(d), "value": v, "smoothed": None, "belowLod": False})
        latest = max(latest, d) if latest else d
    weekly = [{"weekEnd": iso(we), "raw": r(geo_weighted(acc[we]), 0), "n": len(acc[we])} for we in sorted(acc)]
    weekly = trailing_smooth(weekly, log_scale=True, window=3)
    for w in weekly: w["value"] = r(w["value"], 0)
    county_filter = " OR ".join(f"counties_served like '%{c}%'" for c in NYC_COUNTIES)
    cdc = cdc_rows(f"state_territory='New York' AND ({county_filter})", args)
    # keep only sites whose population matches one of the 14 DEP plants (filters out upstate sites that also list 'New York' county)
    dep_pops = {s["population"] for s in sites.values()}
    cdc = [x for x in cdc if int(float(x.get("population_served") or 0)) in dep_pops]
    covid_wval = cdc_weekly(cdc, "SARS-CoV-2")
    official = categorize(covid_wval[-1]["raw"], CDC_COVID_CUTS) if covid_wval else None
    pathogens = [{
        "id": "covid", "label": LABELS["covid"], "scale": "concentration", "unit": "gene copies / L",
        "latestSampleDate": iso(latest), "weekly": weekly,
        "sites": [{"name": n, **s} for n, s in sorted(sites.items())], "labChanges": [], "officialCategory": official, "reference": None,
        "note": "Population-weighted blend of NYC DEP's 14 treatment plants, 3-week trailing smoothing. Official category from CDC's level for the same plants.",
    }]
    cdc_sites = [{"name": n, "population": s["population"], "points": []} for n, s in sorted(sites.items())]
    for pid, target, cuts in (("fluA", "Influenza A virus", CDC_FLUA_CUTS), ("rsv", "RSV", CDC_RSV_CUTS)):
        p = wval_pathogen(pid, cdc, target, cuts, cdc_sites, "NYC does not publish flu or RSV wastewater data; this is CDC's standardized level across the same 14 plants.")
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
    manifest = {"schemaVersion": 1, "generatedAt": now, "cities": []}
    for cid, fn in builders.items():
        if args.only and cid != args.only: continue
        try:
            city = fn(args)
        except Exception as e:  # one broken feed must not take the others down
            log(f"!! {cid} failed: {e!r}"); continue
        city.update({"schemaVersion": 1, "generatedAt": now})
        path = os.path.join(args.out, f"{cid}.json")
        json.dump(city, open(path, "w"), ensure_ascii=False, separators=(",", ":"))
        manifest["cities"].append({"cityId": cid, "cityName": city["cityName"], "file": f"{cid}.json",
                                   "pathogens": [p["id"] for p in city["pathogens"]],
                                   "latestSampleDate": max((p["latestSampleDate"] or "") for p in city["pathogens"])})
        log(f"wrote {path} ({os.path.getsize(path)//1024} KB)")
    json.dump(manifest, open(os.path.join(args.out, "manifest.json"), "w"), indent=1)

if __name__ == "__main__":
    main()
