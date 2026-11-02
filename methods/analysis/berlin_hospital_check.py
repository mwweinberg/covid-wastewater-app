"""
Re-check the Berlin wastewater -> prevalence conversion against hospital data
(user request 2026-11-01; Validation log "Berlin fit re-checked against hospitals").

Why: our infection estimate (GrippeWeb ARE x sentinel positivity / 0.60) is the
series the Berlin conversion was fitted to, but week to week it agrees less
with Berlin wastewater (r 0.66) than two independent hospital series do
(r 0.80-0.86). If the estimate is noisy or biased in quiet periods, the
fitted slope b (1.22) and the readout in quiet weeks could be off.

What the hospital data can and cannot check:
  - They CAN check the SHAPE: how steeply prevalence rises with wastewater
    (slope b) and how quiet weeks are read relative to wave weeks.
  - They CANNOT check the LEVEL independently: converting hospitalisations to
    infections needs an infection hospitalisation risk (IHR), and Berlin's IHR
    is itself computed from our infection estimate, so season totals match by
    construction. The level's only independent check is England's survey
    (winter 2023/24, Validation log).

Series (all weekly, ISO weeks Mon-Sun, from the current Berlin lab period):
  w        Berlin wastewater, log10 copies/L, trailing 2-week mean
           (data/berlin_wastewater_weekly.csv), the readout's input
  gw       our prevalence estimate (data/prevalence_weekly.csv)
  hosp_be  RKI notified COVID hospitalisations, Berlin, per 100k
           (data/raw/rki_covid19_hospitalisierungen.csv; Sunday 7-day sum)
  sari_de  RKI ICOSARI: severe acute respiratory infection with COVID
           diagnosis, Germany, per 100k (data/raw/rki_sari_hospitalisierungsinzidenz.tsv)
Hospital series are turned into prevalence with a constant per-season factor
chosen so the season's mean equals our estimate's mean (the "IHR" step), so
they share our level and differ only in shape.

Checks:
  1. Correlation and least-squares slope of each log10 prevalence series on w,
     lags 0-2 (wastewater earlier). Same estimator for all series, so the
     slopes are comparable with each other (not with fit.py's count
     likelihood, which gave b = 1.22 on the same data).
  2. Conversion variants, all with the level set by the latest complete
     season (2025/26) mean, as the US option would do:
        current   fit.py's a and b (beta-binomial count fit)
        b=1       prevalence proportional to wastewater
        b=hosp    the slope the hospital series imply
     For each: the band edges in copies/L, the share of weeks in each band, and
     the newest week's reading.

Usage: python3 analysis/berlin_hospital_check.py
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
THRESHOLD = 10 / 1e5 * 7 * 4                       # Method steps 5-6
EDGES = np.array([THRESHOLD / 2, THRESHOLD / 1.4, THRESHOLD * 1.4, THRESHOLD * 2])
LABELS = ["Well below", "Just below", "Right around", "Just above", "Well above"]


def season_of(ts):
    """RKI season (ISO week 40 to week 39) of a Monday date, e.g. '2025_2026'."""
    y, wk, _ = ts.isocalendar()
    s = y if wk >= 40 else y - 1
    return f"{s}_{s + 1}"


def load():
    """All four series on one weekly index (Mondays), from the lab period start."""
    fitp = json.loads((DATA / "fit_params.json").read_text())
    start = pd.Timestamp(fitp["wastewater_calibration"]["calibration_period_start"])
    w = pd.read_csv(DATA / "berlin_wastewater_weekly.csv", parse_dates=["week_monday"]).set_index("week_monday")
    gw = pd.read_csv(DATA / "prevalence_weekly.csv", parse_dates=["week_monday"]).set_index("week_monday")["prevalence"]
    h = pd.read_csv(DATA / "raw" / "rki_covid19_hospitalisierungen.csv", parse_dates=["Datum"], dtype={"Bundesland_Id": str})
    h = h[(h["Altersgruppe"] == "00+") & (h["Bundesland"] == "Berlin") & (h["Datum"].dt.weekday == 6)]
    hosp = h.set_index(h["Datum"] - pd.Timedelta(days=6))["7T_Hospitalisierung_Inzidenz"]
    s = pd.read_csv(DATA / "raw" / "rki_sari_hospitalisierungsinzidenz.tsv", sep="\t")
    s = s[(s["Altersgruppe"] == "00+") & (s["SARI"] == "COVID-19")]
    sari = s.set_index(pd.to_datetime(s["Kalenderwoche"] + "-1", format="%G-W%V-%u"))["Hospitalisierungsinzidenz"]
    t = pd.DataFrame({"w": w["log10_trailing2"], "below_loq": w["below_loq_trailing2"],
                      "gw": gw, "hosp_be": hosp, "sari_de": sari})
    t = t[t.index >= start]
    t["season"] = [season_of(d) for d in t.index]
    return t, fitp


def to_prevalence_scale(t, col):
    """Constant per-season factor so that the season mean of `col` equals our
    estimate's season mean (the IHR step; level shared by construction)."""
    out = pd.Series(np.nan, index=t.index)
    for s, g in t.groupby("season"):
        ok = g[col].notna() & g["gw"].notna() & (g[col] > 0)
        if ok.sum() >= 10:
            out[g.index] = g[col] * g.loc[ok, "gw"].mean() / g.loc[ok, col].mean()
    return out


def main():
    t, fitp = load()
    t["hosp_be_prev"] = to_prevalence_scale(t, "hosp_be")
    t["sari_de_prev"] = to_prevalence_scale(t, "sari_de")

    # --- 1. Correlation and slope against wastewater -------------------------
    print("1. log10 prevalence series vs Berlin wastewater (log10, trailing 2-week), least squares")
    slopes = {}
    for col, name in [("gw", "our estimate (GrippeWeb x sentinel)"), ("hosp_be_prev", "Berlin notified hospitalisations"),
                      ("sari_de_prev", "ICOSARI severe COVID, Germany")]:
        res = []
        for lag in (0, 1, 2):
            x = t["w"].shift(lag)                      # wastewater `lag` weeks earlier
            ok = x.notna() & t[col].notna() & (t[col] > 0) & ~t["below_loq"].astype("boolean").fillna(True)
            b = np.polyfit(x[ok], np.log10(t.loc[ok, col]), 1)[0]
            r = np.corrcoef(x[ok], np.log10(t.loc[ok, col]))[0, 1]
            res.append((lag, b, r, int(ok.sum())))
        best = max(res, key=lambda z: z[2])
        slopes[col] = best[1]
        print(f"  {name:40s} " + " | ".join(f"lag {L}: b {b:.2f} r {r:.2f} (n {n})" for L, b, r, n in res))
    print(f"  (fit.py's count-likelihood slope on our estimate: b = {fitp['b']:.2f})")

    # --- 2. Conversion variants ------------------------------------------------
    w_ref, lag = fitp["w_ref_log10"], fitp["lag_weeks"]
    seasons = fitp.get("level_seasons", [fitp["level_season"]])
    lvl = t[t["season"].isin(seasons) & t["w"].notna() & t["gw"].notna()]
    target = lvl["gw"].mean()                          # season-mean prevalence (shared by all series)
    hosp_b = np.mean([slopes["hosp_be_prev"], slopes["sari_de_prev"]])
    variants = {"current (fit.py)": (fitp["a"], fitp["b"])}
    for name, b in [("b = 1, season-mean level", 1.0), (f"b = {hosp_b:.2f} (hospitals), season-mean level", hosp_b),
                    (f"b = {fitp['b']:.2f}, season-mean level", fitp["b"])]:
        a = np.log10(target / np.mean(10 ** (b * (lvl["w"] - w_ref))))
        variants[name] = (a, b)
    ww = t["w"].dropna()
    newest = ww.index.max()
    print(f"\n2. Conversion variants (level season {fitp['level_season']}, season-mean prevalence {100 * target:.3f}%); "
          f"newest week {newest.date()} at {10 ** ww.iloc[-1]:,.0f} copies/L")
    for name, (a, b) in variants.items():
        prev = 10 ** (a + b * (ww - w_ref))
        share = pd.Series(np.digitize(prev, EDGES)).value_counts(normalize=True).reindex(range(5), fill_value=0)
        edges_w = [10 ** (w_ref + (np.log10(e) - a) / b) for e in EDGES]
        p_now = prev.iloc[-1]
        print(f"  {name:42s} {100 * 10 ** a:.3f}% at 100k; edges " + " / ".join(f"{e:,.0f}" for e in edges_w)
              + f"; newest {100 * p_now:.3f}% (1 in {1 / p_now:,.0f}) {LABELS[int(np.digitize(p_now, EDGES))]}")
        print(f"  {'':42s} weeks by band: " + ", ".join(f"{LABELS[i]} {share[i]:.0%}" for i in range(5)))


if __name__ == "__main__":
    main()
