"""
Fit the wastewater -> prevalence conversion for New York City and Washington DC
(US counterpart of pipeline/fit.py; same model, same choice rules).

Model, per city, for each week t of its comparable period:
    log10(prevalence_t) = a + b * (w_{t-L} - W_REF)
  prevalence_t  reference prevalence from local hospital admissions
                (pipeline/us_reference.py)
  w             log10 wastewater, trailing 2-week mean, L weeks earlier
                (wastewater may lead admissions, which lag infection by
                about a week)
  W_REF         NYC 5.0 (100,000 copies/L, same as Berlin); DC 0.5 (WVAL 3.16).
                Only a reference point: 10**a is the prevalence predicted there.

Difference from Berlin's fit: the reference here is a weekly prevalence
estimate built from hospital admission counts that are large (NYC ~50-200 a
week, DC area several hundred), so a least-squares fit on log10 prevalence is
used instead of Berlin's count likelihood for small sentinel numbers.
AIC = n * ln(RSS / n) + 2k (Gaussian errors; k = fitted coefficients).

Choice rules (user decisions 2026-11-01; Validation log "US conversion: final
rules"):
  - slope: FIXED at 1 (prevalence proportional to wastewater). Hospital
    admissions rise less steeply than infections (Berlin check: slope ~0.9 vs
    ~1.0; NYC 0.53), so the free slope fitted to admissions would be too flat.
    Berlin keeps its own fitted slope (pipeline/fit.py).
  - lag: best AIC among the slope-1 fits, lags 0-3
  - level: for each COMPLETE season (ISO week 40-39, >= MIN_LEVEL_WEEKS weeks,
    season over), a is set so that the season's MEAN fitted prevalence equals
    the season's mean reference prevalence (hospitals fix the season total,
    the validated part; the city's wastewater gives the week-to-week shape).
    The final a is the AVERAGE of these season levels, each season counting
    equally (user decision 2026-11-01; Berlin uses the same rule in fit.py).
    Season levels disagree (NYC's 2025/26 well above the others; all three
    cities' latest season reads higher), so the uncertainty of the level is
    reported as the spread between season levels, not only the bootstrap
    (which reflects week-to-week noise within the pooled weeks).
  The free-slope and least-squares fits are still reported, as diagnostics.
Weeks excluded: NYC weeks filled from CDC (provisional); weeks without a
trailing 2-week wastewater value.
Uncertainty: moving-block bootstrap (6-week blocks), as for Berlin.

Inputs:  data/nyc_wastewater_weekly.csv, data/dc_wastewater_weekly.csv,
         data/us_reference_weekly.csv
Outputs: data/us_fit_params.json, data/us_fit_comparison.csv, data/us_fit_weekly.csv
Usage:   python3 pipeline/us_fit.py [--bootstrap 500]
"""

import argparse
import json

import numpy as np
import pandas as pd

from common import PROJECT_ROOT

W_REF = {"nyc": 5.0, "dc": 0.5}
UNITS = {"nyc": "log10 copies/L (NYC Open Data, dPCR; trailing 2-week mean)",
         "dc": "log10 CDC WVAL (Blue Plains, Verily lab; trailing 2-week mean)"}
LAGS = [0, 1, 2, 3]
AIC_MARGIN = 2.0
MIN_LEVEL_WEEKS = 26
BLOCK_LEN = 6
SEED = 20261101
# Threshold and band edges, same as Berlin (Method steps 5-6; C4):
# threshold = 10 cases/100k/day x days_infectious x 4; edges x [1/2, 1/1.4, 1.4, 2].
DAYS_INFECTIOUS = json.loads((PROJECT_ROOT / "data" / "prevalence_params.json").read_text())["days_infectious"]
THRESHOLD = 10 / 1e5 * DAYS_INFECTIOUS * 4
EDGES = [THRESHOLD / 2, THRESHOLD / 1.4, THRESHOLD * 1.4, THRESHOLD * 2]
LABELS = ["Well below", "Just below", "Right around", "Just above", "Well above"]
# Confidence labels shown with the readout (user decision 2026-11-01). Berlin
# has no label. Reasons are in the Validation log ("US conversion: final rules").
CONFIDENCE = {"dc": "Low confidence", "nyc": "Very low confidence"}


def build_table(city, lag):
    """Join reference week t with wastewater week t-lag for one city.
    Returns DataFrame: week_end, season, w, y (log10 prevalence)."""
    w = pd.read_csv(PROJECT_ROOT / "data" / f"{city}_wastewater_weekly.csv", parse_dates=["week_end"])
    if "provisional" in w:
        w = w[~w["provisional"].astype("boolean").fillna(False).astype(bool)]
    w = w[["week_end", "log10_trailing2"]].dropna()
    w["week_end"] = w["week_end"] + pd.to_timedelta(7 * lag, unit="D")   # wastewater earlier
    r = pd.read_csv(PROJECT_ROOT / "data" / "us_reference_weekly.csv", parse_dates=["week_end"])
    r = r[(r["city"] == city) & (r["prevalence"] > 0)]
    t = r.merge(w, on="week_end", how="inner")
    return pd.DataFrame({"week_end": t["week_end"], "season": t["season"],
                         "w": t["log10_trailing2"] - W_REF[city], "y": np.log10(t["prevalence"])}).reset_index(drop=True)


def fit(d, fixed_slope=None):
    """Least-squares fit of y = a + b*w. Returns a, b, rss, aic, n, k."""
    if fixed_slope is None:
        b, a = np.polyfit(d["w"], d["y"], 1)
        k = 2
    else:
        b, k = fixed_slope, 1
        a = float((d["y"] - b * d["w"]).mean())
    rss = float(((d["y"] - a - b * d["w"]) ** 2).sum())
    n = len(d)
    return {"a": float(a), "b": float(b), "rss": rss, "aic": n * np.log(rss / n) + 2 * k, "n_weeks": n, "n_params": k,
            "resid_sd_log10": float(np.sqrt(rss / max(n - k, 1)))}


def level_from_mean(d, b):
    """Level a such that the mean of the fitted prevalence 10**(a + b*w) over
    the weeks in d equals the mean reference prevalence 10**y (season totals
    match). Unlike a least-squares level, this is driven by the wave weeks,
    where admissions track infections best."""
    return float(np.log10(np.mean(10 ** d["y"]) / np.mean(10 ** (b * d["w"]))))


def block_bootstrap(d, fixed_slope, n_boot, level_fn=None):
    """Moving-block bootstrap, blocks of BLOCK_LEN consecutive weeks.
    Without level_fn: refits (a, b) by least squares. With level_fn(d, b):
    returns the level from that function for the fixed slope. Returns an
    array of shape (n_boot, 2) with (a, b)."""
    rng = np.random.default_rng(SEED)
    n = len(d)
    starts = np.arange(n - BLOCK_LEN + 1)
    out = []
    for _ in range(n_boot):
        idx = np.concatenate([np.arange(s, s + BLOCK_LEN) for s in rng.choice(starts, n // BLOCK_LEN + 1)])[:n]
        sample = d.iloc[idx].reset_index(drop=True)
        if level_fn is None:
            r = fit(sample, fixed_slope)
            out.append((r["a"], r["b"]))
        else:
            out.append((level_fn(sample, fixed_slope), fixed_slope))
    return np.array(out)


def fit_city(city, n_boot):
    """All model variants and the chosen model for one city. Returns
    (comparison rows, params dict, per-week table)."""
    rows = []
    tables = {L: build_table(city, L) for L in LAGS}
    common = set.intersection(*(set(t["week_end"]) for t in tables.values()))
    fits = {}
    for L in LAGS:   # same weeks for every lag, so AICs are comparable
        d = tables[L][tables[L]["week_end"].isin(common)].reset_index(drop=True)
        for slope in ["free", "1"]:
            fits[(L, slope)] = fit(d, None if slope == "free" else 1.0)
            rows.append({"city": city, "variant": "main", "lag": L, "slope": slope, **fits[(L, slope)]})
    # Slope fixed at 1 (user decision 2026-11-01); lag = best AIC among slope-1 fits.
    lag = min(LAGS, key=lambda L: fits[(L, "1")]["aic"])
    slope_rule = "1 (fixed, user decision 2026-11-01)"
    fixed = 1.0
    d = tables[lag]
    chosen = fit(d, fixed)
    rows.append({"city": city, "variant": "chosen_all_weeks", "lag": lag, "slope": "1", **chosen})
    for season, g in d.groupby("season"):   # season drift of the mean-matched level
        if len(g) >= 10:
            g = g.reset_index(drop=True)
            r = fit(g, 1.0)
            r["a"] = level_from_mean(g, 1.0)
            rows.append({"city": city, "variant": f"season_{season}_mean_level", "lag": lag, "slope": "1", **r})
    # A season counts as complete once it is over (its ISO week 39, late
    # September, has passed) and it has >= MIN_LEVEL_WEEKS usable weeks. Unlike
    # Berlin, the reference may stop before week 39 (NYC: the state's weekly
    # hospital file ends 2026-08-22), so "data reach week 39" is not required.
    complete = [s for s, g in d.groupby("season") if len(g) >= MIN_LEVEL_WEEKS
                and pd.Timestamp.today() > pd.Timestamp(f"{s[5:]}-09-30")]
    # Level = average (in log10, i.e. geometric) of the complete seasons' levels.
    season_levels = {s: level_from_mean(d[d["season"] == s].reset_index(drop=True), chosen["b"]) for s in complete}
    level_season = "average of " + ", ".join(sorted(complete))
    level_d = d[d["season"].isin(complete)].reset_index(drop=True)
    level = fit(level_d, chosen["b"])
    level["a"] = float(np.mean(list(season_levels.values())))
    rows.append({"city": city, "variant": "final_level_average_of_seasons", "lag": lag, "slope": "1", **level})

    def avg_level(sample, b):
        """Bootstrap helper: the same season-average rule on a resampled table."""
        lv = [level_from_mean(g.reset_index(drop=True), b) for s, g in sample.groupby("season") if len(g) >= 10]
        return float(np.mean(lv))

    b_ci = np.array([1.0, 1.0])
    a_ci = np.percentile(block_bootstrap(level_d, chosen["b"], n_boot, avg_level)[:, 0], [2.5, 97.5])
    a, b = level["a"], chosen["b"]
    edges_w = [W_REF[city] + (np.log10(p) - a) / b for p in EDGES]
    wk = d.assign(city=city, prevalence_observed=10 ** d["y"], prevalence_fitted=10 ** (a + b * d["w"]))
    params = {"a": a, "a_ci95_bootstrap": list(map(float, a_ci)), "b": b, "b_ci95_bootstrap": list(map(float, b_ci)),
              "slope_rule": slope_rule, "lag_weeks": lag, "w_ref": W_REF[city], "wastewater_units": UNITS[city],
              "level_season": level_season, "a_pooled_all_weeks": chosen["a"],
              "prevalence_at_w_ref_pct": 100 * 10 ** a,
              "residual_sd_log10_all_weeks": chosen["resid_sd_log10"],
              "n_weeks_slope": chosen["n_weeks"], "n_weeks_level": level["n_weeks"],
              "weeks": [str(d["week_end"].min().date()), str(d["week_end"].max().date())],
              "band_edges_prevalence": EDGES, "band_edges_wastewater": edges_w,
              "aic_slope1_minus_free": fits[(lag, "1")]["aic"] - fits[(lag, "free")]["aic"],
              "free_slope_diagnostic": fits[(lag, "free")]["b"],
              "level_rule": "average of the complete seasons' levels; each season's level matches its mean "
                            "hospital-based prevalence (user decision 2026-11-01)",
              "season_levels_pct_at_w_ref": {s: 100 * 10 ** v for s, v in season_levels.items()},
              # Main uncertainty of the level: how far the seasons disagree
              # (lowest and highest season level), not the bootstrap range.
              "level_range_between_seasons_pct_at_w_ref": [100 * 10 ** min(season_levels.values()),
                                                           100 * 10 ** max(season_levels.values())],
              "a_ci95_bootstrap_note": "within-season week-to-week noise only; see level_range_between_seasons",
              "confidence": CONFIDENCE[city]}
    return rows, params, wk


def latest_readout(city, p):
    """Diagnostic: category of the newest trailing 2-week wastewater value
    (including provisional NYC weeks), without projection."""
    w = pd.read_csv(PROJECT_ROOT / "data" / f"{city}_wastewater_weekly.csv", parse_dates=["week_end"])
    last = w.dropna(subset=["log10_weekly"]).iloc[-1]
    x = last["log10_trailing2"] if pd.notna(last["log10_trailing2"]) else last["log10_weekly"]
    prev = 10 ** (p["a"] + p["b"] * (x - p["w_ref"]))
    cat = LABELS[int(np.digitize(prev, EDGES))]
    used = "trailing 2-week" if pd.notna(last["log10_trailing2"]) else "single week (previous week missing)"
    return f"{city.upper()} newest week {last['week_end'].date()} ({used}): {100 * prev:.3f}% (1 in {1 / prev:,.0f}) -> {cat} the threshold"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bootstrap", type=int, default=500)
    args = ap.parse_args()
    all_rows, all_params, weekly = [], {}, []
    for city in ["nyc", "dc"]:
        rows, params, wk = fit_city(city, args.bootstrap)
        all_rows += rows
        all_params[city] = params
        weekly.append(wk)
    comp = pd.DataFrame(all_rows)
    comp["prev_at_w_ref_pct"] = 100 * 10 ** comp["a"]
    comp.to_csv(PROJECT_ROOT / "data" / "us_fit_comparison.csv", index=False, float_format="%.6g")
    pd.concat(weekly).to_csv(PROJECT_ROOT / "data" / "us_fit_weekly.csv", index=False, float_format="%.6g")
    all_params["description"] = ("Wastewater -> prevalence conversion for NYC and DC (pipeline/us_fit.py): "
                                 "log10(prevalence) = a + b * (w - w_ref), w lag_weeks earlier. Reference: local "
                                 "hospital admissions / Berlin-method IHR (pipeline/us_reference.py).")
    all_params["threshold_prevalence"] = THRESHOLD
    (PROJECT_ROOT / "data" / "us_fit_params.json").write_text(json.dumps(all_params, indent=2), encoding="utf-8")

    pd.set_option("display.width", 170)
    print(comp[["city", "variant", "lag", "slope", "n_weeks", "a", "b", "prev_at_w_ref_pct", "resid_sd_log10", "aic"]]
          .round(3).to_string(index=False))
    for city in ["nyc", "dc"]:
        p = all_params[city]
        unit = (lambda v: f"{10 ** v:,.0f} copies/L") if city == "nyc" else (lambda v: f"WVAL {10 ** v:.2f}")
        print(f"\n{city.upper()}: lag {p['lag_weeks']}, slope {p['slope_rule']} (AIC slope=1 minus free "
              f"{p['aic_slope1_minus_free']:+.1f}); b = {p['b']:.3f} (95% CI {p['b_ci95_bootstrap'][0]:.3f}.."
              f"{p['b_ci95_bootstrap'][1]:.3f}); level from {p['level_season']}: {p['prevalence_at_w_ref_pct']:.3f}% at "
              f"{unit(p['w_ref'])}; residual SD {p['residual_sd_log10_all_weeks']:.3f} log10 "
              f"(x{10 ** p['residual_sd_log10_all_weeks']:.2f})")
        print("  band edges (0.14/0.20/0.39/0.56%): " + " / ".join(unit(e) for e in p["band_edges_wastewater"]))
        print("  " + latest_readout(city, p))


if __name__ == "__main__":
    main()
