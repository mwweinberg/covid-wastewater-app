"""
2022 sanity check (method step 8): does the current wastewater -> prevalence
conversion give roughly the right answer for early 2022, when case numbers
were still a semi-reasonable anchor (free rapid tests for everyone until
30 June 2022)? We only look for agreement within about a factor of 10.

Steps:
  1. Berlin wastewater in H1 2022: only Ruhleben has data (from 2022-02-11),
     measured by an earlier lab. Two lab changes (~2023-04-01, ~2024-04-03)
     separate it from today's values, so it has to be "bridged" onto today's
     scale.
  2. Bridge: compare Ruhleben with a national curve that excludes Berlin,
     built with RKI's method (each site-lab combination's typical level
     removed, then population-weighted mean). The national curve serves as a
     common yardstick across the lab changes. Two estimates of each lab jump:
       - local: Ruhleben-minus-national in the 8 weeks after the change vs
         the 8 weeks before (less affected by slow changes in which plants
         are in the national curve)
       - segment: mean Ruhleben-minus-national over the whole lab period
  3. Bridged Ruhleben -> Berlin-city equivalent: add back Ruhleben's plant
     offset from data/berlin_wastewater_params.json; trailing 2-week mean as
     in the app.
  4. Predicted prevalence: the current fit (data/fit_params.json).
  5. Case-based prevalence: Berlin reported cases per week (RKI 7-day
     incidence dataset, by report date) x under-ascertainment factor (2, 4,
     6; 4 central, as in the handoff's Wachter conversion) x (7 days
     infectious / 7).
  6. Compare for 2022-W07 .. 2022-W25 (free testing period).

Inputs: data/raw/amelag_einzelstandorte.tsv,
        data/raw/COVID-19-Faelle_7-Tage-Inzidenz_Bundeslaender.csv
        (https://github.com/robert-koch-institut/COVID-19_7-Tage-Inzidenz_in_Deutschland),
        data/fit_params.json, data/berlin_wastewater_params.json
Usage:  python3 analysis/sanity_2022.py
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUHLEBEN = "Berlin Ruhleben"
BERLIN_ID = 11            # Bundesland_id of Berlin in the RKI incidence file
MIN_SITES = 10            # as in RKI's aggregation
WINDOW_WEEKS = 8          # local bridge window on each side of a lab change
DAYS_INFECTIOUS = 7       # best guess (Method step 3)
ASCERTAINMENT = (2, 4, 6) # true infections per reported case, early 2022
CHECK_FROM, CHECK_TO = "2022-02-14", "2022-06-26"   # Mondays of 2022-W07 .. W25


def monday(dates):
    """Monday of the ISO week for each date."""
    return (dates - pd.to_timedelta(dates.dt.weekday, unit="D")).dt.normalize()


def site_weeks(path):
    """Weekly mean log10(viruslast_normalisiert) per site and lab segment.
    A new segment starts at each laborwechsel == 'ja' sample."""
    d = pd.read_csv(path, sep="\t", usecols=["standort", "datum", "viruslast_normalisiert",
                                              "einwohner", "laborwechsel", "typ"])
    d = d[d["typ"] == "SARS-CoV-2"].copy()
    d["datum"] = pd.to_datetime(d["datum"])
    d = d.sort_values(["standort", "datum"])
    d["segment"] = d.groupby("standort")["laborwechsel"].transform(lambda s: (s == "ja").cumsum())
    d = d[d["viruslast_normalisiert"] > 0]
    d["week"] = monday(d["datum"])
    d["x"] = np.log10(d["viruslast_normalisiert"])
    g = d.groupby(["standort", "segment", "week"]).agg(x=("x", "mean"), pop=("einwohner", "first")).reset_index()
    return g


def national_curve(g):
    """RKI-style national curve from the site-segment weeks in g (Berlin
    already removed): per-segment offset = mean deviation from the cross-site
    weekly mean; weighted mean of offset-adjusted values; >= MIN_SITES."""
    wk_mean = g.groupby("week")["x"].transform("mean")
    g = g.assign(dev=g["x"] - wk_mean)
    g["off"] = g.groupby(["standort", "segment"])["dev"].transform("mean")
    g["adj"] = g["x"] - g["off"]
    agg = g.groupby("week").apply(lambda t: pd.Series({"n": len(t), "x": np.average(t["adj"], weights=t["pop"])}),
                                  include_groups=False)
    return agg.loc[agg["n"] >= MIN_SITES, "x"]


def main():
    g = site_weeks(PROJECT_ROOT / "data" / "raw" / "amelag_einzelstandorte.tsv")
    nat = national_curve(g[~g["standort"].str.startswith("Berlin ")])
    r = g[g["standort"] == RUHLEBEN].set_index("week")[["segment", "x"]]
    # A lab-change week can hold samples from both labs; drop such weeks so
    # each week belongs to exactly one lab.
    r = r[~r.index.duplicated(keep=False)]
    diff = (r["x"] - nat.reindex(r.index)).dropna()
    seg = r["segment"].reindex(diff.index)
    print(f"Ruhleben weeks with a national comparison: {len(diff)}; segments: {sorted(seg.unique())}")

    # Lab change dates = first week of each new segment.
    starts = r.groupby("segment").apply(lambda t: t.index.min(), include_groups=False)
    jumps_local, jumps_seg = [], []
    seg_mean = diff.groupby(seg).mean()
    for s in sorted(seg.unique())[1:]:
        t0 = starts[s]
        before = diff[(diff.index < t0) & (diff.index >= t0 - pd.Timedelta(weeks=WINDOW_WEEKS))]
        after = diff[(diff.index >= t0) & (diff.index < t0 + pd.Timedelta(weeks=WINDOW_WEEKS))]
        jl, js = after.mean() - before.mean(), seg_mean[s] - seg_mean[s - 1]
        jumps_local.append(jl)
        jumps_seg.append(js)
        print(f"lab change at {t0.date()}: local jump {jl:+.2f} log10 (x{10 ** jl:.1f}; {len(before)}/{len(after)} weeks), "
              f"segment-mean jump {js:+.2f} (x{10 ** js:.1f})")
    bridges = {"local": sum(jumps_local), "segment": sum(jumps_seg)}

    # Fit and plant offset.
    fp = json.loads((PROJECT_ROOT / "data" / "fit_params.json").read_text())
    wp = json.loads((PROJECT_ROOT / "data" / "berlin_wastewater_params.json").read_text())
    r_off = wp["plant_offsets_log10"][RUHLEBEN]

    # Case-based prevalence, Berlin, by ISO week of report date.
    c = pd.read_csv(PROJECT_ROOT / "data" / "raw" / "COVID-19-Faelle_7-Tage-Inzidenz_Bundeslaender.csv",
                    dtype={"Bundesland_id": int})
    c = c[(c["Bundesland_id"] == BERLIN_ID) & (c["Altersgruppe"] == "00+")].copy()
    c["week"] = monday(pd.to_datetime(c["Meldedatum"]))
    cw = c.groupby("week").agg(cases=("Faelle_neu", "sum"), pop=("Bevoelkerung", "first"))
    cw["weekly_inc"] = cw["cases"] / cw["pop"]

    seg0 = r[r["segment"] == 0]["x"]
    full = pd.date_range(seg0.index.min(), seg0.index.max(), freq="7D")
    for name, bridge in bridges.items():
        city = (seg0 + bridge - r_off).reindex(full)       # Berlin-city equivalent, today's scale
        w2 = city.rolling(2).mean()
        out = pd.DataFrame({"w_trailing2": w2})
        out["prev_wastewater"] = 10 ** (fp["a"] + fp["b"] * (out["w_trailing2"] - fp["w_ref_log10"]))
        out = out.join(cw["weekly_inc"], how="left")
        for k in ASCERTAINMENT:
            out[f"prev_cases_x{k}"] = out["weekly_inc"] * k * DAYS_INFECTIOUS / 7
        sel = out.loc[CHECK_FROM:CHECK_TO].dropna(subset=["prev_wastewater", "weekly_inc"])
        ratio = sel["prev_wastewater"] / sel["prev_cases_x4"]
        print(f"\n=== bridge '{name}': total {bridge:+.2f} log10 (x{10 ** bridge:.0f}) ===")
        print(f"weeks compared: {len(sel)}; wastewater-based / case-based (x4) prevalence: "
              f"median x{ratio.median():.2f}, range x{ratio.min():.2f} .. x{ratio.max():.2f}")
        print(f"  (vs x2 cases: median x{(sel['prev_wastewater'] / sel['prev_cases_x2']).median():.2f}; "
              f"vs x6 cases: median x{(sel['prev_wastewater'] / sel['prev_cases_x6']).median():.2f})")
        show = sel.assign(week=[d.date() for d in sel.index])[["week", "prev_wastewater", "prev_cases_x2", "prev_cases_x4", "prev_cases_x6"]]
        print((show.set_index("week") * 100).round(2).to_string())


if __name__ == "__main__":
    main()
