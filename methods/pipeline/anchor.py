"""
Build the independent incidence anchor (method step 2).

Weekly symptomatic SARS-CoV-2 incidence per 100,000 is estimated as
    GrippeWeb ARE incidence (region "Osten", all ages)
  x national sentinel SARS-CoV-2 positivity (ARE-Praxis-Sentinel, Tab. 1)
i.e. "how many people per 100k had a new acute respiratory illness this week"
times "what share of such illnesses are SARS-CoV-2". This is modelled on
RKI's GW-VPR-I (Loenenbach et al. 2024) but differs from it: RKI's version is
for ADULTS and uses an ILI / non-ILI split. Here all ages are used, because
"Osten" is published only for all ages (00+), and the split isn't published.
Children are ~25% of ARE episodes while the sentinel's age mix is unknown,
which may bias the level by roughly +-15% (estimate; review finding C10).
Assumptions are listed in wastewater-threshold-handoff.md, Method step 2.

Inputs:
  GrippeWeb_Daten_des_Wochenberichts.tsv  downloaded from RKI's GitHub (cached)
  data/sentinel_tab1.csv                  from pipeline/sentinel.py

Positivity is modelled from the counts, not the weekly percentages:
  - pooled over a centered 3-week window (positives summed / samples summed),
    because single weeks often have 0-3 positives;
  - with a Jeffreys interval (Beta(x + 0.5, n - x + 0.5)), which behaves well
    when the count is 0.
The anchor is only used to fit the wastewater conversion on past data, never
in real time, so a centered window (which needs the following week) is fine.

Uncertainty: a 95% interval for the incidence combines
  - positivity: Jeffreys posterior of the pooled counts, and
  - ARE incidence: an approximate binomial interval using the number of
    GrippeWeb reports as sample size. This ignores GrippeWeb's age/region
    weighting, so it understates that part somewhat; it is small next to
    the positivity uncertainty anyway.
by Monte Carlo (independent draws, multiplied). The interval covers sampling
noise only, not the method's systematic assumptions (e.g. sentinel patients
being sicker than average); those are handled later as a wider band.

Output: data/anchor_weekly.csv, one row per ISO week where both sources exist.

Usage:
  python3 pipeline/anchor.py
  python3 pipeline/anchor.py --refresh   # re-download GrippeWeb
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from common import PROJECT_ROOT, download_cached

GRIPPEWEB_URL = ("https://raw.githubusercontent.com/robert-koch-institut/"
                 "GrippeWeb_Daten_des_Wochenberichts/main/GrippeWeb_Daten_des_Wochenberichts.tsv")

# GrippeWeb region containing Berlin (verified in RKI's mapping file).
REGION = "Osten"

# Half-width of the centered pooling window for positivity, in weeks
# (1 -> weeks t-1, t, t+1).
POOL_HALF_WIDTH = 1

# Monte Carlo draws per week for the incidence interval; fixed seed so
# re-runs give identical output.
N_DRAWS = 20000
SEED = 20261101


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

def load_grippeweb(path, region):
    """Return weekly ARE incidence for one region, all ages.

    Output: DataFrame indexed by iso_week ("YYYY-Www") with columns
      are_per100k   ARE incidence per 100,000 per week (weighted by RKI)
      are_reports   number of GrippeWeb reports (Meldungen) that week
    """
    g = pd.read_csv(path, sep="\t")
    g = g[(g["Erkrankung"] == "ARE") & (g["Altersgruppe"] == "00+") & (g["Region"] == region)]
    g = g.rename(columns={"Kalenderwoche": "iso_week", "Inzidenz": "are_per100k", "Meldungen": "are_reports"})
    return g.set_index("iso_week")[["are_per100k", "are_reports"]].sort_index()


def load_sentinel(path):
    """Return the weekly sentinel counts from pipeline/sentinel.py, indexed by
    iso_week, with columns week_monday, samples, sars_cov_2_positive."""
    s = pd.read_csv(path, parse_dates=["week_monday"])
    return s.set_index("iso_week")[["week_monday", "samples", "sars_cov_2_positive"]].sort_index()


# ---------------------------------------------------------------------------
# Positivity
# ---------------------------------------------------------------------------

def pooled_positivity(s, half_width):
    """Pool sentinel counts over a centered window of weeks.

    Input: sentinel table (one row per week, consecutive weeks; checked by
    the caller). Output: the table with added columns
      pool_samples, pool_positives   summed counts over the window
      pool_weeks                     weeks actually in the window (fewer at
                                     the start and end of the series)
      positivity                     pool_positives / pool_samples
      positivity_lo, positivity_hi   95% Jeffreys interval
    """
    s = s.copy()
    win = 2 * half_width + 1
    roll = lambda col: s[col].rolling(win, center=True, min_periods=1).sum()
    s["pool_samples"] = roll("samples")
    s["pool_positives"] = roll("sars_cov_2_positive")
    s["pool_weeks"] = s["samples"].rolling(win, center=True, min_periods=1).count().astype(int)
    a = s["pool_positives"] + 0.5
    b = s["pool_samples"] - s["pool_positives"] + 0.5
    s["positivity"] = s["pool_positives"] / s["pool_samples"]
    s["positivity_lo"] = stats.beta.ppf(0.025, a, b)
    s["positivity_hi"] = stats.beta.ppf(0.975, a, b)
    return s


# ---------------------------------------------------------------------------
# Incidence and its interval
# ---------------------------------------------------------------------------

def incidence_interval(df, n_draws, seed):
    """Monte Carlo 95% interval for incidence = ARE rate x positivity.

    For each week:
      positivity ~ Beta(x + 0.5, n - x + 0.5)            (pooled sentinel counts)
      ARE rate   ~ Beta(k + 0.5, m - k + 0.5), k = rate * m, m = reports
    Returns two arrays (lo, hi) in cases per 100,000.
    """
    rng = np.random.default_rng(seed)
    lo, hi = [], []
    for _, r in df.iterrows():
        pos = rng.beta(r["pool_positives"] + 0.5, r["pool_samples"] - r["pool_positives"] + 0.5, n_draws)
        m = r["are_reports"]
        k = r["are_per100k"] / 1e5 * m
        are = rng.beta(k + 0.5, m - k + 0.5, n_draws)
        inc = 1e5 * are * pos
        lo.append(np.quantile(inc, 0.025))
        hi.append(np.quantile(inc, 0.975))
    return np.array(lo), np.array(hi)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true", help="always re-download GrippeWeb")
    ap.add_argument("--grippeweb", default=str(PROJECT_ROOT / "data" / "raw" / "GrippeWeb_Daten_des_Wochenberichts.tsv"))
    ap.add_argument("--sentinel", default=str(PROJECT_ROOT / "data" / "sentinel_tab1.csv"))
    ap.add_argument("--out", default=str(PROJECT_ROOT / "data" / "anchor_weekly.csv"))
    args = ap.parse_args()

    downloaded = download_cached(GRIPPEWEB_URL, Path(args.grippeweb), args.refresh)
    gw = load_grippeweb(Path(args.grippeweb), REGION)
    se = load_sentinel(Path(args.sentinel))
    print(f"GrippeWeb {'downloaded' if downloaded else 'cached'}: {REGION} ARE {gw.index.min()} .. {gw.index.max()}; "
          f"sentinel {se.index.min()} .. {se.index.max()}")

    # The pooling window assumes consecutive weeks; stop if the sentinel
    # series has a gap rather than silently pooling across it.
    gaps = se["week_monday"].diff().dropna().dt.days.ne(7)
    if gaps.any():
        raise SystemExit(f"sentinel series has gaps before: {list(se.index[1:][gaps.values])}")

    se = pooled_positivity(se, POOL_HALF_WIDTH)
    df = se.join(gw, how="inner")
    missing_gw = sorted(set(se.index) - set(gw.index))
    if missing_gw:
        print(f"weeks with sentinel data but no GrippeWeb value (dropped): {missing_gw}")

    # Central estimate and interval.
    df["positivity_week"] = df["sars_cov_2_positive"] / df["samples"]
    df["incidence_per100k"] = df["are_per100k"] * df["positivity"]
    df["incidence_lo"], df["incidence_hi"] = incidence_interval(df, N_DRAWS, SEED)
    df["edge_of_series"] = df["pool_weeks"] < 2 * POOL_HALF_WIDTH + 1

    out = df.reset_index()
    out["week_monday"] = out["week_monday"].dt.date
    cols = ["iso_week", "week_monday", "are_per100k", "are_reports",
            "samples", "sars_cov_2_positive", "positivity_week",
            "pool_weeks", "pool_samples", "pool_positives", "positivity", "positivity_lo", "positivity_hi",
            "incidence_per100k", "incidence_lo", "incidence_hi", "edge_of_series"]
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    out[cols].to_csv(args.out, index=False, float_format="%.6g")

    # Summary for the calibration period.
    cal = df[df.index >= "2024-W14"]
    width = np.log10(cal["incidence_hi"] / cal["incidence_lo"].clip(lower=1e-9))
    print(f"wrote {len(out)} weeks ({out['iso_week'].iloc[0]} .. {out['iso_week'].iloc[-1]}) to {args.out}")
    print(f"calibration period {cal.index.min()} .. {cal.index.max()} ({len(cal)} weeks):")
    print(f"  ARE incidence per 100k: median {cal['are_per100k'].median():.0f} (range {cal['are_per100k'].min():.0f}-{cal['are_per100k'].max():.0f})")
    print(f"  pooled positivity: median {100 * cal['positivity'].median():.1f}% (range {100 * cal['positivity'].min():.1f}-{100 * cal['positivity'].max():.1f}%); "
          f"weeks with 0 pooled positives: {int((cal['pool_positives'] == 0).sum())}")
    print(f"  incidence per 100k: median {cal['incidence_per100k'].median():.0f} (range {cal['incidence_per100k'].min():.0f}-{cal['incidence_per100k'].max():.0f})")
    print(f"  95% interval width (hi/lo): median x{10 ** width.median():.1f}, 90th pct x{10 ** width.quantile(0.9):.1f}, max x{10 ** width.max():.1f}")


if __name__ == "__main__":
    main()
