"""
Build the weekly SARS-CoV-2 wastewater series for New York City and Washington DC
(US counterpart of pipeline/wastewater.py; see wastewater-threshold-handoff.md,
Validation log "NYC and DC data audit" and "NYC: translating CDC WVAL to copies/L").

Weeks are US surveillance weeks: Sunday-Saturday, labelled by the Saturday
(`week_end`), the convention of both CDC NWSS and NHSN hospital data.

NYC (copies/L, NYC Open Data f7dc-2q9f):
  1. Keeps samples from the comparable period only (`comparable_since`, digital
     PCR; RT-qPCR before April 2023 read ~16.5x lower and is dropped).
  2. One row per plant and sample date: where two lab methods exist for the
     same sample, keeps dPCR-SC2 over dPCR - N1 (the two agree, x0.98).
  3. Per plant and week: mean of log10 copies/L.
  4. Weeks after NYC's newest published week are filled from CDC's WVAL for
     the same plant (fresher feed), translated per plant to copies/L:
        log10 copies/L = slope_p * log10 WVAL + intercept_p
     slope_p is FROZEN (long-run, per plant, from --recalibrate); intercept_p is
     re-estimated on every run from the last INTERCEPT_WEEKS weeks where both
     sources exist, which also absorbs CDC's twice-yearly restatements.
     Filled plant-weeks are marked provisional.
  5. City value = population-weighted mean over reporting plants of
     (plant value - frozen plant offset), as for Berlin (RKI method).
DC (CDC WVAL, Blue Plains, site ID:300):
  Weekly log10 WVAL from the comparable period (Verily lab, from 2024-06-15).
  CDC publishes no concentrations for this site.
  Restatements (user decision 2026-11-01): CDC recomputes its baselines on
  1 Apr and 1 Oct and rewrites the whole WVAL history. Because
  WVAL = exp((ln c - baseline) / sd), a new baseline and sd change log WVAL by
  a straight line: log10 new = alpha + beta * log10 old. The WVAL history used
  for the fit is frozen at --recalibrate (data/dc_wval_snapshot.csv). On every
  run, alpha and beta are estimated from the weeks present in both the
  snapshot and the current file, and the current values are mapped back onto
  the snapshot scale, (log10 new - alpha) / beta, so the fitted conversion
  stays valid. If the mapping doesn't fit well (residual SD > RESTATE_MAX_SD),
  the change is more than a rescaling and the status becomes
  "RESTATEMENT_CHECK" (series still written, readout should be withheld).
Both: trailing 2-week mean (`log10_trailing2`), which the readout uses.

Lab-change alarm (checklist N-d / D-d): a technology value in the NYC feed, or
a CDC `source` value for Blue Plains, that is not in the frozen list and
appears after `comparable_since` -> status "LAB_CHANGE_ALARM", banner, exit
code 2, and the series is NOT updated. Only --recalibrate accepts it
(deliberate refit).

Outputs (data/):
  nyc_wastewater_weekly.csv, dc_wastewater_weekly.csv   city series
  nyc_wastewater_plants.csv                              plant-weeks (diagnostic)
  us_wastewater_params.json   FROZEN: comparable_since, known methods/sources,
                              NYC plant offsets, weights, CDC site map and
                              translation slopes. Written only by --recalibrate.
  us_wastewater_status.json   "ok" / "LAB_CHANGE_ALARM" per city, every run
Raw downloads: data/raw/nyc_f7dc-2q9f.csv, data/raw/cdc_wval_ny.csv,
               data/raw/cdc_wval_dc.csv

Usage:
  python3 pipeline/us_wastewater.py [--refresh] [--recalibrate]
"""

import argparse
import json
import sys
from urllib.parse import urlencode

import numpy as np
import pandas as pd

from common import PROJECT_ROOT, download_cached

RAW = PROJECT_ROOT / "data" / "raw"
NYC_URL = "https://data.cityofnewyork.us/resource/f7dc-2q9f.csv?" + urlencode(
    {"$limit": 500000, "$order": "sample_date"})
CDC_BASE = "https://data.cdc.gov/resource/atcp-73re.csv?"

# Values used only when (re)calibrating; afterwards they are read from the
# frozen parameter file. Dates from the 2026-11-01 audit.
NYC_COMPARABLE_SINCE = "2023-04-16"      # first week with dPCR only
NYC_METHODS = ["dPCR-SC2", "dPCR - N1"]  # preference order within a plant-day
DC_COMPARABLE_SINCE = "2024-06-15"       # first week from the Verily lab
DC_SOURCES = ["CDC_Verily"]
DC_SITE = "ID:300"
INTERCEPT_WEEKS = 12                     # recent overlap weeks for the CDC translation
SLOPE_SINCE = "2024-03-03"               # first week after the dPCR - N1 assay ended (2024-02-27)
METHOD_DRIFT_MAX = np.log10(1.5)         # NYC vs CDC intercept shift that triggers METHOD_CHECK
RESTATE_MAX_SD = 0.05                    # log10; worse fit = not a pure rescaling
RESTATE_TOL = 0.01                       # |alpha|, |beta - 1| below this = no restatement
RESTATE_DROP_TOP = 0.02                  # top share of old values ignored (CDC caps at the 99th percentile)
MIN_PLANTS_FOR_SLOPE = 20                # overlap weeks a plant needs for its own slope


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------

def cdc_url(where):
    """Socrata query URL for CDC WVAL rows (SARS-CoV-2 only) matching `where`."""
    return CDC_BASE + urlencode({"$where": f"pathogen_target='SARS-CoV-2' AND {where}",
                                 "$select": "site,population_served,source,site_wval,week_end",
                                 "$limit": 500000})


def fetch_all(refresh):
    """Download (or reuse cached copies of) the three source files.
    Returns dict name -> path."""
    files = {"nyc": (NYC_URL, RAW / "nyc_f7dc-2q9f.csv"),
             "cdc_ny": (cdc_url("state_territory='New York'"), RAW / "cdc_wval_ny.csv"),
             "cdc_dc": (cdc_url(f"site='{DC_SITE}'"), RAW / "cdc_wval_dc.csv")}
    for name, (url, path) in files.items():
        print(f"{name}: {'downloaded' if download_cached(url, path, refresh) else 'cached'} ({path.name})")
    return {k: v[1] for k, v in files.items()}


def saturday_week_end(dates):
    """Saturday ending the Sunday-Saturday week that contains each date."""
    return (dates + pd.to_timedelta((5 - dates.dt.weekday) % 7, unit="D")).dt.normalize()


# ---------------------------------------------------------------------------
# NYC
# ---------------------------------------------------------------------------

def load_nyc(path):
    """NYC Open Data rows with a value, as DataFrame: plant, sample_date,
    week_end, technology, log10 (log10 copies/L), population."""
    d = pd.read_csv(path, parse_dates=["sample_date"])
    d = d[d["copies_l"].notna() & (d["copies_l"] > 0)].copy()
    d["week_end"] = saturday_week_end(d["sample_date"])
    d["log10"] = np.log10(d["copies_l"])
    return d.rename(columns={"wrrf_name": "plant", "population_served": "population"})


def nyc_plant_weekly(d, methods):
    """One value per plant-week from NYC's own data.
    Where a plant-day has several methods, keep the first in `methods`
    (preference order). Then mean of log10 per plant and week."""
    rank = {m: i for i, m in enumerate(methods)}
    d = d.assign(rank=d["technology"].map(rank)).sort_values("rank")
    d = d.drop_duplicates(["plant", "sample_date"], keep="first")
    return (d.groupby(["plant", "week_end"])
             .agg(log10=("log10", "mean"), n_samples=("log10", "size"))
             .reset_index().assign(source="NYC Open Data", provisional=False))


def load_cdc_nyc(path, site_map):
    """CDC WVAL rows for the 14 NYC plants: plant, week_end, lw (log10 WVAL)."""
    c = pd.read_csv(path, parse_dates=["week_end"])
    c = c[c["site"].isin(site_map) & (c["site_wval"] > 0)].copy()
    c["plant"] = c["site"].map(site_map)
    c["lw"] = np.log10(c["site_wval"])
    return c[["plant", "week_end", "lw"]]


def match_cdc_sites(cdc_path, populations):
    """Map CDC site IDs to NYC plants by identical population served (all 14
    matched uniquely in the 2026-11-01 audit). Used only when recalibrating."""
    c = pd.read_csv(cdc_path)
    by_pop = {int(p): name for name, p in populations.items()}
    m = c[c["population_served"].astype(int).isin(by_pop)].groupby("site")["population_served"].first()
    return {site: by_pop[int(p)] for site, p in m.items()}


def translation_slopes(pw, cdc):
    """Per-plant long-run slope of log10 copies/L on log10 WVAL (frozen at
    recalibration), over the weeks where both exist from SLOPE_SINCE on, i.e.
    the dPCR-SC2 assay only. The earlier dPCR - N1 weeks are excluded: CDC
    gave that assay its own baseline, so the WVAL relationship differs there
    (x2-4), and the tested accuracy (x1.03 typical) holds for SC2-only slopes
    (review finding, 2026-11-01). Plants with too few overlap weeks get the
    median slope."""
    j = pw.merge(cdc, on=["plant", "week_end"])
    j = j[j["week_end"] >= pd.Timestamp(SLOPE_SINCE)]
    slopes = {p: float(np.polyfit(g["lw"], g["log10"], 1)[0]) for p, g in j.groupby("plant")
              if len(g) >= MIN_PLANTS_FOR_SLOPE}
    med = float(np.median(list(slopes.values())))
    return {p: slopes.get(p, med) for p in pw["plant"].unique()}


def cdc_fill(pw, cdc, slopes):
    """Plant-weeks after NYC's newest published week, translated from CDC.

    For each plant: intercept = mean of (log10 copies/L - slope * log10 WVAL)
    over the last INTERCEPT_WEEKS weeks where both sources exist; translated
    value = slope * log10 WVAL + intercept. Returns rows in the same layout as
    nyc_plant_weekly(), marked provisional, plus the intercepts used."""
    last_nyc_week = pw["week_end"].max()
    j = pw.merge(cdc, on=["plant", "week_end"]).sort_values("week_end")
    rows, intercepts = [], {}
    for plant, g in j.groupby("plant"):
        recent = g.tail(INTERCEPT_WEEKS)
        b = slopes[plant]
        intercepts[plant] = float((recent["log10"] - b * recent["lw"]).mean())
        new = cdc[(cdc["plant"] == plant) & (cdc["week_end"] > last_nyc_week)]
        for _, r in new.iterrows():
            rows.append({"plant": plant, "week_end": r["week_end"], "log10": b * r["lw"] + intercepts[plant],
                         "n_samples": np.nan, "source": "CDC WVAL (translated)", "provisional": True})
    return pd.DataFrame(rows, columns=list(pw.columns)), intercepts


def plant_offsets(pw):
    """Each plant's typical level relative to the others (log10), from weeks
    where all plants report from NYC's own data (same method as Berlin)."""
    wide = pw[~pw["provisional"]].pivot(index="week_end", columns="plant", values="log10").dropna()
    return wide.sub(wide.mean(axis=1), axis=0).mean().to_dict()


def city_weekly(pw, offsets, weights):
    """Population-weighted mean over reporting plants of (value - offset)."""
    pw = pw.assign(adj=pw["log10"] - pw["plant"].map(offsets), w=pw["plant"].map(weights))
    pw["wx"] = pw["adj"] * pw["w"]
    g = pw.groupby("week_end")
    return pd.DataFrame({"log10_weekly": g["wx"].sum() / g["w"].sum(),
                         "n_plants": g.size(),
                         "provisional": g["provisional"].any()})


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------

def add_trailing2(city):
    """Complete weekly index (a missing week is a gap), then the trailing
    2-week mean; it needs both weeks, so a gap blanks the next week too."""
    full = pd.date_range(city.index.min(), city.index.max(), freq="7D")
    c = city.reindex(full)
    c.index.name = "week_end"
    c["log10_trailing2"] = c["log10_weekly"].rolling(2).mean()
    return c


def restatement_transform(snapshot, current):
    """Estimate how CDC rescaled DC's WVAL since the snapshot.

    Inputs: two Series of log10 WVAL indexed by week_end (frozen snapshot;
    current download). Fits log10 current = alpha + beta * log10 snapshot by
    least squares over the weeks present in both, ignoring the highest
    RESTATE_DROP_TOP share of snapshot values (CDC caps extreme values, which
    breaks the straight line there). Returns dict: alpha, beta, resid_sd,
    n_weeks, restated (bool: the rescaling is not the identity)."""
    j = pd.concat([snapshot.rename("old"), current.rename("new")], axis=1).dropna()
    j = j[j["old"] <= j["old"].quantile(1 - RESTATE_DROP_TOP)]
    beta, alpha = np.polyfit(j["old"], j["new"], 1)
    resid = j["new"] - (alpha + beta * j["old"])
    return {"alpha": float(alpha), "beta": float(beta), "resid_sd": float(resid.std(ddof=2)),
            "n_weeks": int(len(j)), "restated": bool(abs(alpha) > RESTATE_TOL or abs(beta - 1) > RESTATE_TOL)}


def alarm(city, what, since):
    """Print the lab-change banner for one city."""
    banner = "!" * 78
    print(f"\n{banner}\nLAB CHANGE ALARM ({city}): {what} after the comparable period start ({since}).\n"
          f"Values from then on are not comparable. Thresholds are invalid until a refit (Method step 9).\n"
          f"The {city} series was NOT updated.\n{banner}\n", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true", help="always re-download")
    ap.add_argument("--recalibrate", action="store_true",
                    help="refit only: re-estimate and freeze offsets, site map, translation slopes, known methods")
    args = ap.parse_args()
    out = PROJECT_ROOT / "data"
    params_path = out / "us_wastewater_params.json"
    paths = fetch_all(args.refresh)
    recal = args.recalibrate or not params_path.exists()
    if recal:
        P = {"nyc": {"comparable_since": NYC_COMPARABLE_SINCE, "methods": NYC_METHODS},
             "dc": {"comparable_since": DC_COMPARABLE_SINCE, "sources": DC_SOURCES, "site": DC_SITE}}
        print("RECALIBRATING: NYC offsets, CDC site map and translation slopes will be re-estimated and frozen")
    else:
        P = json.loads(params_path.read_text(encoding="utf-8"))
    status = {}

    # --- NYC ---------------------------------------------------------------
    d = load_nyc(paths["nyc"])
    since = pd.Timestamp(P["nyc"]["comparable_since"])
    d = d[d["week_end"] >= since]
    unknown = sorted(set(d["technology"]) - set(P["nyc"]["methods"]))
    if unknown and not recal:
        first = d[d["technology"].isin(unknown)]["sample_date"].min().date()
        status["nyc"] = {"status": "LAB_CHANGE_ALARM", "new_methods": unknown, "first_sample": str(first)}
        alarm("NYC", f"new lab method(s) {unknown}, first sample {first}", since.date())
    else:
        if recal:
            P["nyc"]["methods"] = [m for m in NYC_METHODS if m in set(d["technology"])] + unknown
        pw = nyc_plant_weekly(d, P["nyc"]["methods"])
        weights = d.groupby("plant")["population"].first().astype(float).to_dict()
        if recal:
            P["nyc"]["cdc_site_map"] = match_cdc_sites(paths["cdc_ny"], weights)
        cdc = load_cdc_nyc(paths["cdc_ny"], P["nyc"]["cdc_site_map"])
        # CDC restatements, as for DC: map each plant's current CDC values back
        # onto the scale frozen at recalibration (data/nyc_cdc_snapshot.csv),
        # so that the frozen translation slopes stay valid.
        snap_path = out / "nyc_cdc_snapshot.csv"
        restate = {}
        if recal:
            cdc.to_csv(snap_path, index=False, float_format="%.6f")
        else:
            snap = pd.read_csv(snap_path, parse_dates=["week_end"])
            mapped = []
            for plant, g in cdc.groupby("plant"):
                s = snap[snap["plant"] == plant].set_index("week_end")["lw"]
                rs = restatement_transform(s, g.set_index("week_end")["lw"])
                restate[plant] = rs
                mapped.append(g.assign(lw=(g["lw"] - rs["alpha"]) / rs["beta"]))
            cdc = pd.concat(mapped, ignore_index=True)
            n_restated = sum(r["restated"] for r in restate.values())
            print(f"NYC: CDC rescaling since snapshot: {n_restated} of {len(restate)} plants restated; "
                  f"max residual SD {max(r['resid_sd'] for r in restate.values()):.3f}")
        if recal:
            P["nyc"].update(plant_weights_population=weights, plant_offsets_log10=plant_offsets(pw),
                            cdc_translation_slopes=translation_slopes(pw, cdc))
        fill, intercepts = cdc_fill(pw, cdc, P["nyc"]["cdc_translation_slopes"])
        # Silent method change check (checklist N-d): NYC's own copies/L
        # relative to CDC's (snapshot-scale) WVAL for the same plant-weeks is
        # summarized by the per-plant intercepts. A population-weighted shift
        # of more than x1.5 against the frozen intercepts means NYC's values
        # moved without a technology label change -> METHOD_CHECK.
        if recal:
            P["nyc"]["cdc_intercepts_at_freeze"] = intercepts
        wts = P["nyc"]["plant_weights_population"]
        shift = sum((intercepts[p] - P["nyc"]["cdc_intercepts_at_freeze"][p]) * wts[p] for p in intercepts) / \
            sum(wts[p] for p in intercepts)
        pw_all = pd.concat([pw, fill], ignore_index=True)
        city = add_trailing2(city_weekly(pw_all, P["nyc"]["plant_offsets_log10"], P["nyc"]["plant_weights_population"]))
        city["copies_per_l_trailing2"] = 10 ** city["log10_trailing2"]
        city.reset_index().to_csv(out / "nyc_wastewater_weekly.csv", index=False, float_format="%.5f")
        pw_all.to_csv(out / "nyc_wastewater_plants.csv", index=False, float_format="%.5f")
        nyc_last = pw["week_end"].max().date()
        prov = city.index[city["provisional"].astype("boolean").fillna(False).astype(bool)]
        method_ok = abs(shift) <= METHOD_DRIFT_MAX and all(r["resid_sd"] <= RESTATE_MAX_SD for r in restate.values())
        status["nyc"] = {"status": "ok" if method_ok else "METHOD_CHECK",
                         "nyc_open_data_through_week": str(nyc_last),
                         "series_through_week": str(city.index.max().date()),
                         "provisional_weeks_from_cdc": [str(x.date()) for x in prov],
                         "nyc_vs_cdc_shift_log10": shift, "cdc_intercepts_this_run": intercepts,
                         "cdc_restatement_by_plant": restate}
        print(f"NYC: NYC-vs-CDC level shift since freeze: x{10 ** shift:.2f} (alarm beyond x1.5)")
        if not method_ok:
            print(f"\n{'!' * 78}\nNYC: copies/L moved against CDC's index by x{10 ** shift:.2f} (or CDC's history changed "
                  f"beyond a rescaling) without a lab-method label change. Possible silent method change: withhold "
                  f"the NYC readout until checked or refitted.\n{'!' * 78}\n", file=sys.stderr)
        print(f"NYC: {city['log10_weekly'].notna().sum()} weeks {city.index.min().date()}..{city.index.max().date()}; "
              f"NYC Open Data through {nyc_last}; provisional (CDC) weeks: {[str(x.date()) for x in prov] or 'none'}")

    # --- DC ----------------------------------------------------------------
    c = pd.read_csv(paths["cdc_dc"], parse_dates=["week_end"])
    c = c[(c["site"] == P["dc"]["site"]) & (c["week_end"] >= pd.Timestamp(P["dc"]["comparable_since"]))]
    new_src = sorted(set(c["source"].dropna()) - set(P["dc"]["sources"]))
    if new_src and not recal:
        first = c[c["source"].isin(new_src)]["week_end"].min().date()
        status["dc"] = {"status": "LAB_CHANGE_ALARM", "new_sources": new_src, "first_week": str(first)}
        alarm("DC", f"new CDC data source(s) {new_src}, first week {first}", P["dc"]["comparable_since"])
    else:
        c = c[c["site_wval"] > 0]
        dcw = c.groupby("week_end").agg(log10_weekly=("site_wval", lambda s: np.log10(s).mean()),
                                        source=("source", "first"))
        # Restatements: map current CDC values onto the frozen snapshot scale.
        snap_path = out / "dc_wval_snapshot.csv"
        if recal:
            dcw["log10_weekly"].reset_index().to_csv(snap_path, index=False, float_format="%.6f")
            rs = {"alpha": 0.0, "beta": 1.0, "resid_sd": 0.0, "n_weeks": int(dcw["log10_weekly"].notna().sum()),
                  "restated": False}
            print(f"DC: froze WVAL snapshot ({rs['n_weeks']} weeks) in {snap_path.name}")
        else:
            snap = pd.read_csv(snap_path, parse_dates=["week_end"]).set_index("week_end")["log10_weekly"]
            rs = restatement_transform(snap, dcw["log10_weekly"])
            dcw["log10_weekly_as_published"] = dcw["log10_weekly"]
            dcw["log10_weekly"] = (dcw["log10_weekly"] - rs["alpha"]) / rs["beta"]
            print(f"DC: CDC rescaling since snapshot: alpha {rs['alpha']:+.3f}, beta {rs['beta']:.3f}, residual SD "
                  f"{rs['resid_sd']:.3f} over {rs['n_weeks']} weeks -> {'RESTATED, values mapped back' if rs['restated'] else 'no restatement'}")
        dcw = add_trailing2(dcw)
        dcw["wval_trailing2"] = 10 ** dcw["log10_trailing2"]
        dcw.reset_index().to_csv(out / "dc_wastewater_weekly.csv", index=False, float_format="%.5f")
        gaps = dcw.index[dcw["log10_weekly"].isna()]
        ok = rs["resid_sd"] <= RESTATE_MAX_SD
        status["dc"] = {"status": "ok" if ok else "RESTATEMENT_CHECK",
                        "series_through_week": str(dcw.index.max().date()),
                        "missing_weeks": [str(x.date()) for x in gaps], "cdc_restatement": rs}
        if not ok:
            print(f"\n{'!' * 78}\nDC: CDC changed the WVAL history in a way that is not a pure rescaling (residual SD "
                  f"{rs['resid_sd']:.3f} > {RESTATE_MAX_SD}). Withhold the DC readout until checked or refitted.\n{'!' * 78}\n",
                  file=sys.stderr)
        print(f"DC: {dcw['log10_weekly'].notna().sum()} weeks {dcw.index.min().date()}..{dcw.index.max().date()}; "
              f"missing weeks: {len(gaps)}")

    # --- Outputs -----------------------------------------------------------
    if recal:
        P["description"] = ("FROZEN parameters for the NYC and DC weekly wastewater series "
                            "(pipeline/us_wastewater.py). Written only by --recalibrate, as part of a refit.")
        params_path.write_text(json.dumps(P, indent=2, default=str), encoding="utf-8")
        print(f"FROZE parameters in {params_path.name}")
    (out / "us_wastewater_status.json").write_text(json.dumps(status, indent=2, default=str), encoding="utf-8")
    if any(s["status"] != "ok" for s in status.values()):
        sys.exit(2)


if __name__ == "__main__":
    main()
