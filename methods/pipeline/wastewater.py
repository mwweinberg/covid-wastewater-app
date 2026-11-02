"""
Build the Berlin weekly SARS-CoV-2 wastewater series (method step 1).

Source: AMELAG site-level data (RKI / UBA),
https://github.com/robert-koch-institut/Abwassersurveillance_AMELAG,
file amelag_einzelstandorte.tsv. See wastewater-threshold-handoff.md,
Data sources §1 and Method step 1, for the reasoning behind each choice below.

What it does:
  1. Keeps SARS-CoV-2 samples from the three Berlin plants (Ruhleben,
     Schoenerlinde, Wassmannsdorf).
  2. Uses the FROZEN calibration period: its start is the latest lab change
     (laborwechsel == "ja") of any Berlin plant at the time of the last refit,
     read from data/berlin_wastewater_params.json. Values from before that
     date are not comparable and are dropped. If the data contain a lab change
     AFTER the frozen start, the script raises a LAB CHANGE ALARM (writes
     status "LAB_CHANGE_ALARM", prints a banner, exits with code 2) and does
     not update the series. Only --recalibrate re-detects the start and
     re-estimates the offsets (part of a deliberate refit).
  3. Marks samples below the limit of quantification (below_loq): RKI's flag
     unter_bg == "ja", OR a raw viruslast equal to a known reporting floor
     (1,000 copies/L), which RKI's flag misses for Berlin.
  4. Per plant and ISO week (Monday-Sunday): mean of log10(viruslast_normalisiert).
  5. Combines the plants as RKI does for its national curve: removes each
     plant's typical level difference ("offset") and takes the population-
     weighted mean. Without offsets, the city value would jump whenever a
     high- or low-reading plant drops out. Note: the offsets sum to zero but
     their POPULATION-WEIGHTED mean is not zero (-0.024 log10 at the 2026-11-01
     calibration), so they also shift every week by a constant (x1.057). That
     is harmless only because the same frozen offsets are used for fitting and
     at runtime; never re-estimate them outside a refit.
  6. Adds two light smoothings: a trailing 2-week mean (usable in real time,
     because it needs no future week) and a centered 3-week mean (for
     inspection only; it needs next week's value).

Below-LOQ handling (decided 2026-11-01): weeks with any below-LOQ sample keep
their value as reported but get below_loq = True. The calibration fit (step 4)
excludes them; a sensitivity check keeps them.

Outputs (project-relative defaults):
  data/berlin_wastewater_weekly.csv   one row per week (city series)
  data/berlin_wastewater_plants.csv   one row per (plant, week), diagnostics
  data/berlin_wastewater_params.json  FROZEN period start, plant offsets and
                                      weights, floor values: what the app
                                      needs to build the same series without
                                      refitting. Written only by --recalibrate.
  data/berlin_wastewater_status.json  "ok" or "LAB_CHANGE_ALARM", written on
                                      every run
  data/raw/amelag_einzelstandorte.tsv downloaded source file (cache)

Usage:
  python3 pipeline/wastewater.py                # normal run (frozen parameters)
  python3 pipeline/wastewater.py --refresh      # always re-download
  python3 pipeline/wastewater.py --recalibrate  # refit only: new period/offsets
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from common import PROJECT_ROOT, download_cached, iso_week_label

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AMELAG_URL = ("https://raw.githubusercontent.com/robert-koch-institut/"
              "Abwassersurveillance_AMELAG/main/amelag_einzelstandorte.tsv")

# Berlin plants as named in the 'standort' column.
BERLIN_PLANTS = ["Berlin Ruhleben", "Berlin Schönerlinde", "Berlin Waßmannsdorf"]

# Raw viruslast values (copies/L) known to be reporting floors that RKI's
# unter_bg flag does not mark (Data sources §1). Extend this list if the
# "possible new floor values" check below finds another one.
FLOOR_VALUES = [1000.0]

# Re-download the source file if the cached copy is older than this.
MAX_CACHE_AGE_H = 24


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def fetch_amelag(path, refresh):
    """Download amelag_einzelstandorte.tsv to `path` unless a copy younger
    than MAX_CACHE_AGE_H exists (see common.download_cached)."""
    return download_cached(AMELAG_URL, path, refresh, MAX_CACHE_AGE_H)


def load_berlin_samples(path):
    """Read the AMELAG file and return Berlin SARS-CoV-2 samples.

    Output: DataFrame with one row per sample: standort, datum (Timestamp),
    viruslast (raw copies/L), viruslast_normalisiert, einwohner, laborwechsel,
    unter_bg. Rows without a normalized value (gaps, model-only rows) are
    dropped here; lab-change rows are kept because they mark period starts.
    """
    d = pd.read_csv(path, sep="\t", dtype={"laborwechsel": str, "unter_bg": str})
    d = d[(d["typ"] == "SARS-CoV-2") & d["standort"].isin(BERLIN_PLANTS)].copy()
    d["datum"] = pd.to_datetime(d["datum"])
    return d


# ---------------------------------------------------------------------------
# Calibration period
# ---------------------------------------------------------------------------

def calibration_start(d):
    """Return (start_date, lab_changes) for the current calibration period.

    lab_changes: DataFrame of all laborwechsel == "ja" rows (plant, date).
    start_date: the latest lab change of ANY Berlin plant. From that date on,
    every plant is on its current lab, so values are comparable over time.
    If a plant has never had a lab change, it doesn't constrain the start.
    """
    changes = d.loc[d["laborwechsel"] == "ja", ["standort", "datum"]].sort_values("datum")
    if changes.empty:
        return d["datum"].min(), changes
    return changes["datum"].max(), changes


# ---------------------------------------------------------------------------
# Below-LOQ detection
# ---------------------------------------------------------------------------

def mark_below_loq(d, floor_values):
    """Add a boolean column below_loq to the sample table.

    A sample is below LOQ if RKI flags it (unter_bg == "ja") or its raw
    viruslast equals one of the known floor values. The raw value is used
    because the normalized value is flow-adjusted and so differs slightly
    from sample to sample even when the lab reported the floor.
    """
    d = d.copy()
    d["below_loq"] = (d["unter_bg"] == "ja") | d["viruslast"].isin(floor_values)
    return d


def possible_new_floors(d, floor_values, quantile=0.10, min_repeats=3):
    """Maintenance check: raw values at the low end that repeat exactly.

    A reporting floor shows up as the same raw number appearing many times
    among the lowest samples. Returns a Series {raw value: count} of values
    below the given quantile that occur at least min_repeats times and are not
    already in floor_values. An empty result is the normal case.
    """
    low = d.loc[d["viruslast"] <= d["viruslast"].quantile(quantile), "viruslast"]
    counts = low.value_counts()
    counts = counts[(counts >= min_repeats) & ~counts.index.isin(floor_values)]
    return counts


# ---------------------------------------------------------------------------
# Weekly series
# ---------------------------------------------------------------------------

def plant_weekly(d):
    """Aggregate samples to one value per plant and ISO week.

    Output columns: standort, week_monday, log10_load (mean of the samples'
    log10 viruslast_normalisiert), n_samples, n_below_loq.
    The mean is taken on the log scale (= geometric mean of the loads), as
    RKI does, because loads vary over orders of magnitude.
    """
    d = d.copy()
    d["log10"] = np.log10(d["viruslast_normalisiert"])
    # Monday of the ISO week of each sample date.
    d["week_monday"] = (d["datum"] - pd.to_timedelta(d["datum"].dt.weekday, unit="D")).dt.normalize()
    g = d.groupby(["standort", "week_monday"])
    return g.agg(log10_load=("log10", "mean"),
                 n_samples=("log10", "size"),
                 n_below_loq=("below_loq", "sum")).reset_index()


def plant_offsets(pw):
    """Estimate each plant's typical level relative to the other plants.

    Uses only weeks where all plants report and no plant-week is below LOQ,
    so the estimate isn't distorted by missing plants or floor values.
    For each such week, compute the mean across plants; a plant's offset is
    the average of (its value - that weekly mean). Offsets sum to zero, so
    removing them doesn't change the series level when all plants report.
    Returns {standort: offset in log10 units}.
    """
    wide = pw.pivot(index="week_monday", columns="standort", values="log10_load")
    bad = pw[pw["n_below_loq"] > 0]["week_monday"].unique()
    clean = wide.dropna().drop(index=bad, errors="ignore")
    dev = clean.sub(clean.mean(axis=1), axis=0)
    return dev.mean().to_dict()


def city_weekly(pw, offsets, weights):
    """Combine plant-weeks into the Berlin series.

    Value per week = population-weighted mean over the plants that reported
    that week, of (plant value - plant offset). Weights are the plants'
    connected inhabitants (einwohner). Also reports how many plants and
    samples went into each week and whether any sample was below LOQ.
    """
    pw = pw.copy()
    pw["adj"] = pw["log10_load"] - pw["standort"].map(offsets)
    pw["w"] = pw["standort"].map(weights)
    pw["wx"] = pw["adj"] * pw["w"]
    g = pw.groupby("week_monday")
    out = pd.DataFrame({
        "log10_load": g["wx"].sum() / g["w"].sum(),
        "n_plants": g.size(),
        "n_samples": g["n_samples"].sum(),
        "n_below_loq": g["n_below_loq"].sum(),
    })
    out["below_loq"] = out["n_below_loq"] > 0
    return out


def add_smoothing(city):
    """Add the two smoothed versions of log10_load (see module docstring).

    The series is first put on a complete weekly index so that a missing
    week is a gap, not silently skipped. A smoothed value needs all its input
    weeks; its below-LOQ flag is True if any input week is below LOQ.
    - trailing2: mean of this week and the previous one (real-time usable)
    - centered3: mean of previous, this and next week (inspection only)
    """
    full = pd.date_range(city.index.min(), city.index.max(), freq="7D")
    c = city.reindex(full)
    c.index.name = "week_monday"
    x, flag = c["log10_load"], c["below_loq"].astype("float")  # NaN for missing weeks

    c["log10_trailing2"] = x.rolling(2).mean()
    c["below_loq_trailing2"] = flag.rolling(2).max().astype("boolean") if len(c) else flag
    c["log10_centered3"] = x.rolling(3, center=True).mean()
    c["below_loq_centered3"] = flag.rolling(3, center=True).max().astype("boolean") if len(c) else flag
    return c


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def lab_change_alarm(changes, frozen_start):
    """Return the lab changes (rows of `changes`) dated after the frozen
    calibration start. Any such row means wastewater values from that date on
    are not comparable with the values the conversion was fitted on."""
    return changes[changes["datum"] > frozen_start]


def write_status(out_dir, status, frozen_start, new_changes, data_through):
    """Write data/berlin_wastewater_status.json, which downstream steps (and
    the app's module) read to know whether the series may be used.
    status: "ok" or "LAB_CHANGE_ALARM"."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "berlin_wastewater_status.json").write_text(json.dumps({
        "status": status,
        "frozen_calibration_period_start": str(frozen_start.date()),
        "new_lab_changes": [{"plant": r["standort"], "date": str(r["datum"].date())} for _, r in new_changes.iterrows()],
        "data_through": str(data_through.date()),
    }, indent=2, ensure_ascii=False), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true", help="always re-download the AMELAG file")
    ap.add_argument("--raw", default=str(PROJECT_ROOT / "data" / "raw" / "amelag_einzelstandorte.tsv"))
    ap.add_argument("--out-dir", default=str(PROJECT_ROOT / "data"))
    ap.add_argument("--recalibrate", action="store_true",
                    help="deliberately start a new calibration: re-detect the period start from the latest lab "
                         "change, re-estimate plant offsets, and overwrite the frozen parameters. Only as part "
                         "of a refit (then re-run anchor.py and fit.py).")
    args = ap.parse_args()
    out_dir = Path(args.out_dir)
    params_path = out_dir / "berlin_wastewater_params.json"

    # 1. Data.
    downloaded = fetch_amelag(Path(args.raw), args.refresh)
    d = load_berlin_samples(Path(args.raw))
    data_through = d.loc[d["viruslast_normalisiert"].notna(), "datum"].max()
    print(f"AMELAG file {'downloaded' if downloaded else 'cached'}; "
          f"Berlin SARS-CoV-2 rows: {len(d)}, last sample {data_through.date()}")

    # 2. Calibration period. Normally FROZEN: the start date, plant offsets,
    # weights and floor values were fixed when the conversion was fitted and
    # are read back here, so a new lab change can't silently become a new
    # start (methodology review finding A1, 2026-11-01). Only --recalibrate
    # (or a missing parameter file) re-detects and re-estimates them.
    detected_start, changes = calibration_start(d)
    print("lab changes (laborwechsel == 'ja'):")
    for _, r in changes.iterrows():
        print(f"  {r['standort']:22s} {r['datum'].date()}")
    recalibrate = args.recalibrate or not params_path.exists()
    if recalibrate:
        frozen = None
        start = detected_start
        floor_values = FLOOR_VALUES
        print(f"RECALIBRATING: calibration period starts {start.date()} (latest lab change); "
              f"offsets will be re-estimated and frozen")
    else:
        frozen = json.loads(params_path.read_text(encoding="utf-8"))
        start = pd.Timestamp(frozen["calibration_period_start"])
        floor_values = frozen["floor_values_raw_copies_per_l"]
        print(f"calibration period start (frozen): {start.date()}")
        new_changes = lab_change_alarm(changes, start)
        if not new_changes.empty:
            # Loud, unmissable, and no new series is written: the old
            # conversion must not be applied to values from a new lab.
            write_status(out_dir, "LAB_CHANGE_ALARM", start, new_changes, data_through)
            banner = "!" * 78
            msg = "; ".join(f"{r['standort']} on {r['datum'].date()}" for _, r in new_changes.iterrows())
            print(f"\n{banner}\nLAB CHANGE ALARM: new lab change after the frozen calibration start "
                  f"({start.date()}): {msg}.\nWastewater values from that date on are not comparable. "
                  f"Thresholds are invalid until a refit (Method step 9).\nThe weekly series was NOT updated.\n"
                  f"{banner}\n", file=sys.stderr)
            sys.exit(2)
    d = d[(d["datum"] >= start) & d["viruslast_normalisiert"].notna() & (d["viruslast_normalisiert"] > 0)]

    # 3. Below-LOQ samples.
    d = mark_below_loq(d, floor_values)
    print(f"samples in period: {len(d)}; below LOQ: {int(d['below_loq'].sum())} "
          f"(RKI flag: {int((d['unter_bg'] == 'ja').sum())}, floor value: {int(d['viruslast'].isin(floor_values).sum())})")
    new_floors = possible_new_floors(d, floor_values)
    print("possible new floor values: " + (", ".join(f"{v:g} (x{n})" for v, n in new_floors.items()) or "none"))

    # 4-5. Weekly series, with frozen offsets and weights unless recalibrating.
    pw = plant_weekly(d)
    if recalibrate:
        weights = d.groupby("standort")["einwohner"].first().to_dict()
        offsets = plant_offsets(pw)
    else:
        weights, offsets = frozen["plant_weights_einwohner"], frozen["plant_offsets_log10"]
        # Diagnostic only: how far would re-estimated offsets drift from the frozen ones?
        drift = {k: plant_offsets(pw)[k] - v for k, v in offsets.items()}
        print("offset drift vs frozen (diagnostic, not applied): " +
              ", ".join(f"{k.replace('Berlin ', '')} {v:+.3f}" for k, v in drift.items()))
    city = city_weekly(pw, offsets, weights)
    city = add_smoothing(city)
    missing = city.index[city["log10_load"].isna()]
    print(f"weeks: {city['log10_load'].notna().sum()} ({city.index.min().date()} .. {city.index.max().date()}), "
          f"missing: {[str(m.date()) for m in missing] or 'none'}; "
          f"weeks with <3 plants: {int((city['n_plants'] < 3).sum())}; below-LOQ weeks: {int(city['below_loq'].sum())}")
    print("plant offsets (log10, relative to plant mean): " +
          ", ".join(f"{k.replace('Berlin ', '')} {v:+.3f}" for k, v in offsets.items()))

    # Dynamic range, for comparison with the handoff's earlier estimate
    # (25th-75th percentile spread ~9x, 10th-90th ~70x).
    q = city["log10_load"].dropna().quantile([0.10, 0.25, 0.75, 0.90])
    print(f"dynamic range (weekly): 25-75% x{10 ** (q[0.75] - q[0.25]):.1f}, 10-90% x{10 ** (q[0.90] - q[0.10]):.1f}")
    ok = city[~city["below_loq"]]["log10_load"].dropna().quantile([0.10, 0.25, 0.75, 0.90])
    print(f"  excluding below-LOQ weeks: 25-75% x{10 ** (ok[0.75] - ok[0.25]):.1f}, 10-90% x{10 ** (ok[0.90] - ok[0.10]):.1f}")

    # 6. Outputs.
    out_dir.mkdir(parents=True, exist_ok=True)
    weekly = city.reset_index()
    weekly.insert(0, "iso_week", [iso_week_label(t) for t in weekly["week_monday"]])
    weekly["load"] = 10 ** weekly["log10_load"]
    weekly["week_monday"] = weekly["week_monday"].dt.date
    cols = ["iso_week", "week_monday", "log10_load", "load", "n_plants", "n_samples", "n_below_loq",
            "below_loq", "log10_trailing2", "below_loq_trailing2", "log10_centered3", "below_loq_centered3"]
    weekly[cols].to_csv(out_dir / "berlin_wastewater_weekly.csv", index=False, float_format="%.5f")

    pw_out = pw.copy()
    pw_out["week_monday"] = pw_out["week_monday"].dt.date
    pw_out.to_csv(out_dir / "berlin_wastewater_plants.csv", index=False, float_format="%.5f")

    # Frozen parameters are written only when (re)calibrating; on normal runs
    # the file is left untouched.
    if recalibrate:
        params = {
            "description": "FROZEN parameters to rebuild the Berlin weekly wastewater series "
                           "(pipeline/wastewater.py). Written only by --recalibrate, as part of a refit.",
            "calibration_period_start": str(start.date()),
            "plants": BERLIN_PLANTS,
            "plant_weights_einwohner": weights,
            "plant_offsets_log10": offsets,
            "floor_values_raw_copies_per_l": floor_values,
            "value": "log10 of viruslast_normalisiert; weekly mean per plant (ISO weeks), "
                     "minus plant offset, population-weighted mean over reporting plants",
            "frozen_from_data_through": str(data_through.date()),
        }
        params_path.write_text(json.dumps(params, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"FROZE new parameters in {params_path}")
    write_status(out_dir, "ok", start, changes.iloc[0:0], data_through)
    print(f"wrote berlin_wastewater_weekly.csv ({len(weekly)} weeks), berlin_wastewater_plants.csv, "
          f"berlin_wastewater_status.json to {out_dir}")


if __name__ == "__main__":
    main()
