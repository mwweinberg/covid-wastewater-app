"""
Check that wastewater_threshold.py (standard library only) reproduces the
methods project's pipeline: same weekly series and same readouts, from the
same raw files. Run in the methods project:

    python3 handoff/test_against_pipeline.py [YYYY-MM-DD]   # "today" (default 2026-11-01)

Compares, per city:
  - weekly log10 values against data/{berlin,nyc,dc}_wastewater_weekly.csv
    (max absolute difference; should be ~0)
  - the readout against the values logged in wastewater-threshold-handoff.md
    on 2026-11-01 (printed for inspection)
Raw inputs (data/raw/): amelag_einzelstandorte.tsv, nyc_f7dc-2q9f.csv,
cdc_wval_ny.csv, cdc_wval_dc.csv. The CDC files were downloaded already
filtered to SARS-CoV-2, so pathogen_target is added here; the app's own CDC
rows carry it.
"""

import csv
import os
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import wastewater_threshold as wt  # noqa: E402

RAW = os.path.join(ROOT, "data", "raw")


def rows(name, delimiter=",", add=None):
    """Read a raw file into a list of dicts of strings (as the app's feeds give)."""
    with open(os.path.join(RAW, name), encoding="utf-8", newline="") as f:
        out = list(csv.DictReader(f, delimiter=delimiter))
    for r in out:
        r.update(add or {})
    return out


def compare(series, pipeline_csv, week_col):
    """Max |module - pipeline| of weekly log10 values over common weeks."""
    with open(os.path.join(ROOT, "data", pipeline_csv), encoding="utf-8") as f:
        ref = {date.fromisoformat(r[week_col][:10]): float(r[col]) for r in csv.DictReader(f)
               for col in [("log10_load" if "berlin" in pipeline_csv else "log10_weekly")] if r[col]}
    common = set(ref) & set(series["weeks"])
    diffs = [abs(series["weeks"][w]["log10"] - ref[w]) for w in common]
    only = (len(set(series["weeks"]) - set(ref)), len(set(ref) - set(series["weeks"])))
    return max(diffs), len(common), only


def main():
    today = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date(2026, 11, 1)
    cfg = wt.load_config()
    cdc_tag = {"pathogen_target": "SARS-CoV-2"}
    berlin = wt.berlin_series(rows("amelag_einzelstandorte.tsv", "\t"), cfg)
    nyc = wt.nyc_series(rows("nyc_f7dc-2q9f.csv"), rows("cdc_wval_ny.csv", add=cdc_tag), cfg)
    dc = wt.dc_series(rows("cdc_wval_dc.csv", add=cdc_tag), cfg)
    for s, ref, col in [(berlin, "berlin_wastewater_weekly.csv", "week_monday"),
                        (nyc, "nyc_wastewater_weekly.csv", "week_end"), (dc, "dc_wastewater_weekly.csv", "week_end")]:
        print(f"\n== {s['city'].upper()}: status {s['status']}" + (f" ({s['message']})" if s["message"] else ""))
        if s["status"] != "ok":
            continue
        mx, n, only = compare(s, ref, col)
        print(f"   weekly series vs pipeline: max |diff| {mx:.2e} log10 over {n} weeks "
              f"(weeks only in module / only in pipeline: {only[0]} / {only[1]})")
        r = wt.readout(s, cfg, today)
        for k in ["main_line", "confidence_label", "second_line", "prevalence", "as_of", "newest_week_end",
                  "weeks_behind", "projected", "data_delayed", "single_week_after_gap", "gap_weeks", "provisional",
                  "alert_level_ok"]:
            print(f"   {k:22s} {r[k]}")
    print("\nExpected on 2026-11-01 (handoff doc): Berlin 0.191% projected, Just below; "
          "NYC 1.295% Well above (data delayed, provisional); DC 0.224% Right around (single week after a gap)")


if __name__ == "__main__":
    main()
