"""
Convert weekly symptomatic incidence to prevalence (method step 3).

Prevalence here = share of the population infectious at a given time:
    prevalence = weekly symptomatic incidence
                 x (days infectious / 7)      new this week -> infectious now
                 / symptomatic fraction       add infections without symptoms
(steady-state approximation; less accurate when incidence changes fast).

Parameters (decided 2026-11-01, see wastewater-threshold-handoff.md, Method
step 3 and the Validation log):
  SYMPTOMATIC_FRACTION = 0.60  best guess (plausible range 0.40-0.80)
  DAYS_INFECTIOUS      = 7     best guess (plausible range 5-10)
The best guesses are used both for what the app displays and for decisions.
The ranges are written out as context only.

Input:  data/anchor_weekly.csv (pipeline/anchor.py)
Output: data/prevalence_weekly.csv, one row per week:
  prevalence                  best guess (fraction, e.g. 0.002 = 0.2%)
  prevalence_lo / _hi         95% sampling interval (from the anchor's
                              incidence interval, same multiplier)
  prevalence_range_lo / _hi   context only: the plausible-range parameters at
                              their extremes, applied to the sampling interval
                              (lowest: fraction 0.80, 5 days, sampling lo;
                              highest: fraction 0.40, 10 days, sampling hi)
        data/prevalence_params.json, the parameters used.

Usage: python3 pipeline/prevalence.py
"""

import argparse
import json
from pathlib import Path

import pandas as pd

from common import PROJECT_ROOT

# Best guesses (drive display and decisions).
SYMPTOMATIC_FRACTION = 0.60
DAYS_INFECTIOUS = 7

# Plausible ranges (context only).
SYMPTOMATIC_FRACTION_RANGE = (0.40, 0.80)
DAYS_INFECTIOUS_RANGE = (5, 10)


def multiplier(fraction, days):
    """Factor converting symptomatic incidence (per person per week) to
    prevalence: (days infectious / 7) / symptomatic fraction."""
    return (days / 7) / fraction


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--anchor", default=str(PROJECT_ROOT / "data" / "anchor_weekly.csv"))
    ap.add_argument("--out", default=str(PROJECT_ROOT / "data" / "prevalence_weekly.csv"))
    ap.add_argument("--params-out", default=str(PROJECT_ROOT / "data" / "prevalence_params.json"))
    args = ap.parse_args()

    a = pd.read_csv(args.anchor)

    # Multipliers: best guess, and the two extremes of the plausible ranges.
    m_best = multiplier(SYMPTOMATIC_FRACTION, DAYS_INFECTIOUS)
    m_low = multiplier(SYMPTOMATIC_FRACTION_RANGE[1], DAYS_INFECTIOUS_RANGE[0])   # most symptoms, shortest
    m_high = multiplier(SYMPTOMATIC_FRACTION_RANGE[0], DAYS_INFECTIOUS_RANGE[1])  # fewest symptoms, longest

    # Incidence is per 100,000; divide by 1e5 to get a fraction of people.
    out = a[["iso_week", "week_monday", "incidence_per100k", "incidence_lo", "incidence_hi",
             "pool_positives", "edge_of_series"]].copy()
    out["prevalence"] = out["incidence_per100k"] / 1e5 * m_best
    out["prevalence_lo"] = out["incidence_lo"] / 1e5 * m_best
    out["prevalence_hi"] = out["incidence_hi"] / 1e5 * m_best
    out["prevalence_range_lo"] = out["incidence_lo"] / 1e5 * m_low
    out["prevalence_range_hi"] = out["incidence_hi"] / 1e5 * m_high
    out.to_csv(args.out, index=False, float_format="%.6g")

    params = {
        "description": "Incidence-to-prevalence conversion (pipeline/prevalence.py). "
                       "Best guesses drive display and decisions; ranges are context only.",
        "symptomatic_fraction": SYMPTOMATIC_FRACTION,
        "symptomatic_fraction_range": list(SYMPTOMATIC_FRACTION_RANGE),
        "days_infectious": DAYS_INFECTIOUS,
        "days_infectious_range": list(DAYS_INFECTIOUS_RANGE),
        "multiplier_best": m_best,
        "multiplier_range": [m_low, m_high],
        "formula": "prevalence = weekly symptomatic incidence (fraction) * (days_infectious / 7) / symptomatic_fraction",
    }
    Path(args.params_out).write_text(json.dumps(params, indent=2), encoding="utf-8")

    cal = out[out["iso_week"] >= "2024-W14"]
    print(f"multiplier: best {m_best:.3f}, range {m_low:.3f}-{m_high:.3f}")
    print(f"wrote {len(out)} weeks to {args.out}")
    print(f"calibration period ({len(cal)} weeks): prevalence median {100 * cal['prevalence'].median():.2f}%, "
          f"max {100 * cal['prevalence'].max():.2f}% ({cal.loc[cal['prevalence'].idxmax(), 'iso_week']})")


if __name__ == "__main__":
    main()
