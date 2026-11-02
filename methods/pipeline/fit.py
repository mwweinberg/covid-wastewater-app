"""
Fit the wastewater -> prevalence conversion (method step 4).

Model, for each week t of the calibration period:
    log10(prevalence_t) = a + b * (w_{t-L} - W_REF)
where
    w_{t-L}  Berlin wastewater, log10 copies/L, trailing 2-week mean
             (the same smoothing the app can compute in real time), taken
             L weeks earlier (L = lag; wastewater may lead illness)
    W_REF    5.0, i.e. 100,000 copies/L. So 10**a is the prevalence the
             model predicts at 100,000 copies/L, and b is the slope
             (b = 1: prevalence proportional to wastewater, a single
             conversion factor).
The model is fitted to the raw sentinel COUNTS, not to log prevalence
estimates (see wastewater-threshold-handoff.md, Validation log, "Incidence
anchor"; and the plain-language explanation logged with step 4):
    predicted prevalence -> symptomatic incidence (divide by the step 3
    multiplier) -> predicted positivity (divide by that week's GrippeWeb ARE
    rate) -> how likely are the observed positives out of the swabs taken?
The parameters that make the observed counts most likely are chosen.

Week-to-week variation: positives vary more than pure chance explains
(drift, Berlin vs national, sentinel composition). A beta-binomial
distribution is used instead of a binomial: same mean, plus a fitted
"extra variation" parameter (kappa; smaller kappa = more extra variation).
Without it, the uncertainty of a and b would come out too small.

Uncertainty of a and b: moving-block bootstrap (blocks of consecutive weeks
resampled and the model refitted), because neighbouring weeks are not
independent.

Weeks used: calibration period weeks with wastewater (at lag L), GrippeWeb
and sentinel counts; weeks whose trailing wastewater value includes a
below-LOQ week are excluded (decision 2026-11-01) and included only in a
sensitivity run.

Comparisons run:
  - lag 0, 1, 2 weeks, each with free slope and with slope fixed at 1
    (all on the same set of weeks, so likelihoods are comparable)
  - sensitivity: below-LOQ weeks included; unsmoothed wastewater
  - per season (RKI seasons, KW 40 to KW 39), slope fixed at 1: does the
    conversion factor drift?

Model choice rule (fixed before looking at results, 2026-11-01):
  - lag: the one with the best AIC among the slope-free fits
  - slope: fixed at 1 unless the free slope improves AIC by more than 2
AIC = 2 * (number of parameters) - 2 * log-likelihood; lower is better.

Level rule (user decision 2026-11-01, revised the same day): the final slope b
comes from all weeks; the final level a is the AVERAGE (in log10) of the
levels of all COMPLETE seasons (KW 40-39), each refitted with b held fixed and
each season counting equally. First rule was "latest complete season only"
(review finding B7; chosen as the more cautious option, x1.33 above the
all-weeks level). Revised because the season-to-season difference is within
the quarter-to-quarter noise (x1.5), so there is no evidence of real drift,
and the best guess (not the cautious end) should drive the readout; the
latest season (2025/26) was also unusual (an autumn wave, then a very quiet
2026). Same rule as NYC/DC (pipeline/us_fit.py). Raise again at every refit.

Inputs:  data/berlin_wastewater_weekly.csv, data/anchor_weekly.csv,
         data/prevalence_params.json
Outputs: data/fit_params.json      chosen model and its parameters
         data/fit_comparison.csv   all model variants
         data/fit_weekly.csv       per week: observed vs fitted prevalence

Usage: python3 pipeline/fit.py [--bootstrap 500]
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import optimize, stats

from common import PROJECT_ROOT

W_REF = 5.0             # log10 copies/L reference point (100,000 copies/L)
LAGS = [0, 1, 2]
BLOCK_LEN = 6           # weeks per bootstrap block
SEED = 20261101
AIC_MARGIN = 2.0        # free slope must beat slope=1 by more than this
MIN_LEVEL_WEEKS = 26    # usable weeks a season needs to set the level


# ---------------------------------------------------------------------------
# Data assembly
# ---------------------------------------------------------------------------

def frozen_calibration():
    """Read the frozen wastewater calibration (pipeline/wastewater.py) and its
    status. Refuses to continue if the status is a lab-change alarm: the
    series would then mix labs. Returns (params dict, calibration start ISO
    week label, e.g. "2024-W14")."""
    status = json.loads((PROJECT_ROOT / "data" / "berlin_wastewater_status.json").read_text(encoding="utf-8"))
    if status["status"] != "ok":
        raise SystemExit(f"wastewater status is {status['status']} ({status['new_lab_changes']}); "
                         f"recalibrate first (python3 pipeline/wastewater.py --recalibrate), see Method step 9")
    params = json.loads((PROJECT_ROOT / "data" / "berlin_wastewater_params.json").read_text(encoding="utf-8"))
    y, w, _ = pd.Timestamp(params["calibration_period_start"]).isocalendar()
    return params, f"{y}-W{w:02d}"


def season_of(iso_week):
    """RKI season (KW 40 to KW 39) of an ISO week label, e.g. "2024-W45" ->
    "2024_2025"."""
    y, w = int(iso_week[:4]), int(iso_week[6:])
    start = y if w >= 40 else y - 1
    return f"{start}_{start + 1}"


def as_bool(series):
    """CSV booleans come back as True/False or the strings 'True'/'False' or
    blank (blank = not computable, e.g. first week of a rolling window).
    Returns a boolean Series where blank counts as True (i.e. 'don't use')."""
    return ~(series.astype(str).str.strip() == "False")


def build_table(lag, ww_col, flag_col, include_below_loq, cal_start_week):
    """Join anchor week t with wastewater week t-lag.

    Inputs:
      lag                weeks wastewater is shifted (wastewater earlier)
      ww_col, flag_col   wastewater column and its below-LOQ flag
      include_below_loq  sensitivity switch
      cal_start_week     first ISO week of the frozen calibration period
    Output: DataFrame with iso_week, season, w (log10 wastewater), x
    (positives), n (swabs), are (ARE rate as a fraction), below_loq.
    """
    a = pd.read_csv(PROJECT_ROOT / "data" / "anchor_weekly.csv", parse_dates=["week_monday"])
    w = pd.read_csv(PROJECT_ROOT / "data" / "berlin_wastewater_weekly.csv", parse_dates=["week_monday"])
    w = w[["week_monday", ww_col, flag_col]].rename(columns={ww_col: "w", flag_col: "below_loq"})
    w["below_loq"] = as_bool(w["below_loq"])
    # Shift wastewater forward by `lag` weeks so it lines up with the later anchor week.
    w["week_monday"] = w["week_monday"] + pd.to_timedelta(7 * lag, unit="D")
    t = a.merge(w, on="week_monday", how="inner")
    t = t[(t["iso_week"] >= cal_start_week) & t["w"].notna() & (t["samples"] > 0)]
    if not include_below_loq:
        t = t[~t["below_loq"]]
    return pd.DataFrame({
        "iso_week": t["iso_week"].values,
        "season": [season_of(s) for s in t["iso_week"]],
        "w": t["w"].values,
        "x": t["sars_cov_2_positive"].values.astype(int),
        "n": t["samples"].values.astype(int),
        "are": t["are_per100k"].values / 1e5,
        "below_loq": t["below_loq"].values,
    })


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

def predicted_positivity(a, b, w, are, mult):
    """Positivity implied by the model for each week.
    prevalence = 10**(a + b*(w - W_REF)); incidence = prevalence / mult;
    positivity = incidence / ARE rate. Clipped into (0, 1) because a poor
    candidate can imply impossible values."""
    prev = 10 ** (a + b * (w - W_REF))
    return np.clip(prev / mult / are, 1e-9, 1 - 1e-9)


def neg_loglik(params, d, mult, fixed_slope):
    """Negative beta-binomial log-likelihood of the observed counts.
    params = (a, b, log_kappa), or (a, log_kappa) if fixed_slope is set."""
    if fixed_slope is None:
        a, b, log_k = params
    else:
        (a, log_k), b = params, fixed_slope
    k = np.exp(log_k)
    p = predicted_positivity(a, b, d["w"].values, d["are"].values, mult)
    return -stats.betabinom.logpmf(d["x"].values, d["n"].values, p * k, (1 - p) * k).sum()


def fit(d, mult, fixed_slope=None, start=None):
    """Maximum-likelihood fit. Returns dict with a, b, kappa, loglik, aic,
    n_weeks, n_params. Nelder-Mead is used: robust, and the problem is small."""
    if start is None:
        start = [np.log10(0.003), 1.0, np.log(50.0)] if fixed_slope is None else [np.log10(0.003), np.log(50.0)]
    res = optimize.minimize(neg_loglik, start, args=(d, mult, fixed_slope), method="Nelder-Mead",
                            options={"xatol": 1e-6, "fatol": 1e-8, "maxiter": 20000, "maxfev": 40000})
    if fixed_slope is None:
        a, b, log_k = res.x
    else:
        (a, log_k), b = res.x, fixed_slope
    k_params = 3 if fixed_slope is None else 2
    ll = -res.fun
    return {"a": a, "b": b, "kappa": float(np.exp(log_k)), "loglik": ll,
            "aic": 2 * k_params - 2 * ll, "n_weeks": len(d), "n_params": k_params, "converged": bool(res.success)}


def block_bootstrap(d, mult, fixed_slope, n_boot, block_len, seed):
    """Moving-block bootstrap of (a, b): resample runs of block_len
    consecutive weeks with replacement until the original length is reached,
    refit, repeat. Returns array of shape (n_boot, 2)."""
    rng = np.random.default_rng(seed)
    n = len(d)
    starts = np.arange(n - block_len + 1)
    out = []
    for _ in range(n_boot):
        idx = np.concatenate([np.arange(s, s + block_len) for s in rng.choice(starts, n // block_len + 1)])[:n]
        r = fit(d.iloc[idx].reset_index(drop=True), mult, fixed_slope)
        out.append((r["a"], r["b"]))
    return np.array(out)


def excess_scatter(d, a, b, mult, min_pos=5):
    """How far weekly prevalence scatters around the fitted line beyond pure
    counting noise, in log10 units.

    Uses weeks with at least min_pos positives (where log positivity is well
    defined). Observed variance of log10(observed / predicted positivity)
    minus the variance expected from binomial counting alone
    ((1-p)/(n*p) on the natural-log scale, converted to log10).
    Returns (sd_excess_log10, sd_total_log10, n_weeks_used).
    """
    m = d[d["x"] >= min_pos]
    p_hat = predicted_positivity(a, b, m["w"].values, m["are"].values, mult)
    r = np.log10(m["x"].values / m["n"].values) - np.log10(p_hat)
    binom_var = ((1 - p_hat) / (m["n"].values * p_hat)) / np.log(10) ** 2
    excess = max(np.var(r, ddof=1) - binom_var.mean(), 0.0)
    return float(np.sqrt(excess)), float(np.std(r, ddof=1)), len(m)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bootstrap", type=int, default=500, help="bootstrap replicates for the chosen model")
    args = ap.parse_args()

    mult = json.loads((PROJECT_ROOT / "data" / "prevalence_params.json").read_text())["multiplier_best"]
    ww_params, cal_start_week = frozen_calibration()
    print(f"frozen calibration period starts {ww_params['calibration_period_start']} ({cal_start_week})")
    rows = []

    # --- 1. Main comparison: lags x slope, on a common set of weeks ---------
    tables = {L: build_table(L, "log10_trailing2", "below_loq_trailing2", False, cal_start_week) for L in LAGS}
    common = set.intersection(*(set(t["iso_week"]) for t in tables.values()))
    fits = {}
    for L in LAGS:
        d = tables[L][tables[L]["iso_week"].isin(common)].reset_index(drop=True)
        for slope in ["free", "1"]:
            r = fit(d, mult, None if slope == "free" else 1.0)
            fits[(L, slope)] = r
            rows.append({"variant": "main", "lag": L, "slope": slope, **r})

    # --- 2. Model choice (rule fixed in the module docstring) ---------------
    best_lag = min(LAGS, key=lambda L: fits[(L, "free")]["aic"])
    free, one = fits[(best_lag, "free")], fits[(best_lag, "1")]
    slope_choice = "free" if one["aic"] - free["aic"] > AIC_MARGIN else "1"
    chosen_d = tables[best_lag]   # refit chosen model on all its usable weeks
    chosen = fit(chosen_d, mult, None if slope_choice == "free" else 1.0)
    rows.append({"variant": "chosen_all_weeks", "lag": best_lag, "slope": slope_choice, **chosen})

    # --- 3. Sensitivity runs (chosen lag and slope rule) --------------------
    fixed = None if slope_choice == "free" else 1.0
    for name, col, flag, incl in [("below_loq_included", "log10_trailing2", "below_loq_trailing2", True),
                                  ("unsmoothed_wastewater", "log10_load", "below_loq", False)]:
        d = build_table(best_lag, col, flag, incl, cal_start_week)
        for slope in ["free", "1"]:
            r = fit(d, mult, None if slope == "free" else 1.0)
            rows.append({"variant": name, "lag": best_lag, "slope": slope, **r})

    # --- 4. Per season, slope fixed at 1 and free ---------------------------
    # Also with the slope held at the chosen model's value, so that seasons can
    # differ only in level: this is the "season drift" reported in the doc
    # (methodology review finding D13: previously computed ad hoc).
    for season, d in chosen_d.groupby("season"):
        if len(d) < 10:
            continue
        for slope in ["1", "free", "chosen"]:
            fixed_b = {"1": 1.0, "free": None, "chosen": chosen["b"]}[slope]
            r = fit(d.reset_index(drop=True), mult, fixed_b)
            rows.append({"variant": f"season_{season}", "lag": best_lag, "slope": slope, **r})

    # --- 5. Level = average of the complete seasons (user decision 2026-11-01) --
    # The slope comes from all weeks (best estimated from the full range). The
    # level (a) is the mean, in log10, of each COMPLETE season's level, each
    # refitted with that slope held fixed, so every season counts equally.
    # (The day's first rule used the latest season only; see docstring.)
    # A season is complete once the data reach its KW 39; it needs
    # >= MIN_LEVEL_WEEKS usable weeks. Revisit at every refit (Method step 9).
    latest_week = chosen_d["iso_week"].max()
    complete = sorted(s for s, g in chosen_d.groupby("season")
                      if latest_week >= f"{s[5:]}-W39" and len(g) >= MIN_LEVEL_WEEKS)
    season_fits = {s: fit(chosen_d[chosen_d["season"] == s].reset_index(drop=True), mult, fixed_slope=chosen["b"])
                   for s in complete}
    level_season = "average of " + ", ".join(complete)
    level_d = chosen_d[chosen_d["season"].isin(complete)].reset_index(drop=True)
    level = fit(level_d, mult, fixed_slope=chosen["b"])          # pooled fit, kept for loglik/kappa columns
    level["a"] = float(np.mean([f["a"] for f in season_fits.values()]))
    a_final, b_final = level["a"], chosen["b"]
    rows.append({"variant": "final_level_average_of_seasons", "lag": best_lag, "slope": "chosen", **level})

    comp = pd.DataFrame(rows)
    comp["prev_at_100k_pct"] = 100 * 10 ** comp["a"]
    comp.to_csv(PROJECT_ROOT / "data" / "fit_comparison.csv", index=False, float_format="%.6g")

    # --- 6. Uncertainty and scatter -----------------------------------------
    # Slope: bootstrap of the all-weeks fit. Level: bootstrap of the
    # latest-season fit with the slope held fixed (does not include the
    # slope's own uncertainty).
    boot = block_bootstrap(chosen_d, mult, fixed, args.bootstrap, BLOCK_LEN, SEED)
    b_ci = np.percentile(boot[:, 1], [2.5, 97.5]) if fixed is None else [1.0, 1.0]
    a_pooled_ci = np.percentile(boot[:, 0], [2.5, 97.5])
    # Level uncertainty: block bootstrap within each complete season, refit
    # each season's level, average them (same rule as the point estimate).
    boot_by_season = [block_bootstrap(chosen_d[chosen_d["season"] == s].reset_index(drop=True), mult, b_final,
                                      args.bootstrap, BLOCK_LEN, SEED + i)[:, 0] for i, s in enumerate(complete)]
    a_ci = np.percentile(np.mean(boot_by_season, axis=0), [2.5, 97.5])
    # Week-to-week scatter: estimated on all weeks around the all-weeks line
    # (the single level season has too few weeks with >= 5 positives for a
    # meaningful estimate; its value is kept as a diagnostic only).
    sd_excess, sd_total, n_scatter = excess_scatter(chosen_d, chosen["a"], chosen["b"], mult)
    sd_level_season = excess_scatter(level_d, a_final, b_final, mult)
    season_levels = {s: {"a": f["a"], "prevalence_at_100k_pct": 100 * 10 ** f["a"], "weeks": f["n_weeks"]}
                     for s, f in season_fits.items()}

    # --- 7. Per-week output --------------------------------------------------
    wk = chosen_d.copy()
    wk["prevalence_fitted"] = 10 ** (a_final + b_final * (wk["w"] - W_REF))
    wk["prevalence_observed"] = wk["x"] / wk["n"] * wk["are"] * mult
    wk.to_csv(PROJECT_ROOT / "data" / "fit_weekly.csv", index=False, float_format="%.6g")

    params = {
        "description": "Wastewater -> prevalence conversion (pipeline/fit.py). "
                       "log10(prevalence) = a + b * (w - w_ref), w = log10 Berlin wastewater "
                       "(copies/L, trailing 2-week mean) lag_weeks earlier.",
        "a": a_final, "a_ci95_bootstrap": list(map(float, a_ci)),
        "b": b_final, "b_ci95_bootstrap": list(map(float, b_ci)), "slope_rule": slope_choice,
        "level_rule": f"a = average (log10) of the complete seasons' levels ({', '.join(complete)}), each with b "
                      "fixed; b from all weeks. User decision 2026-11-01 (revised from latest-season-only); "
                      "revisit at each refit.",
        "level_season": level_season,
        "level_seasons": complete,
        "season_levels": season_levels,
        "a_pooled_all_weeks": chosen["a"], "a_pooled_ci95_bootstrap": list(map(float, a_pooled_ci)),
        "w_ref_log10": W_REF, "lag_weeks": best_lag,
        "wastewater_column": "log10_trailing2",
        "prevalence_at_100k_copies_pct": 100 * 10 ** a_final,
        "kappa_betabinomial": chosen["kappa"],
        "residual_scatter_sd_log10": sd_excess, "residual_scatter_weeks": n_scatter,
        "residual_scatter_level_season_diagnostic": {"sd_excess_log10": sd_level_season[0],
                                                     "sd_total_log10": sd_level_season[1],
                                                     "weeks": sd_level_season[2]},
        "n_weeks_slope": chosen["n_weeks"], "n_weeks_level": level["n_weeks"],
        "weeks": [chosen_d["iso_week"].min(), chosen_d["iso_week"].max()],
        "prevalence_multiplier_used": mult,
        # The frozen wastewater calibration this fit belongs to. The app must
        # build its series with exactly these values, and raise the lab-change
        # alarm for any Berlin lab change after calibration_period_start.
        "wastewater_calibration": {k: ww_params[k] for k in
                                   ["calibration_period_start", "plants", "plant_weights_einwohner",
                                    "plant_offsets_log10", "floor_values_raw_copies_per_l"]},
        "bootstrap": {"replicates": args.bootstrap, "block_len_weeks": BLOCK_LEN, "seed": SEED},
    }
    (PROJECT_ROOT / "data" / "fit_params.json").write_text(json.dumps(params, indent=2), encoding="utf-8")

    # --- Report ---------------------------------------------------------------
    pd.set_option("display.width", 160)
    show = comp[["variant", "lag", "slope", "n_weeks", "a", "b", "prev_at_100k_pct", "kappa", "loglik", "aic", "converged"]]
    print(show.round(3).to_string(index=False))
    print(f"\nchosen: lag {best_lag}, slope {slope_choice} (AIC slope=1 minus free: {one['aic'] - free['aic']:+.2f})")
    print(f"  b = {b_final:.3f} (95% CI {b_ci[0]:.3f} .. {b_ci[1]:.3f}), from all {chosen['n_weeks']} weeks")
    print(f"  a (pooled, all weeks) = {chosen['a']:.3f} -> {100 * 10 ** chosen['a']:.3f}% at 100,000 copies/L")
    print("  season levels (b fixed): " + ", ".join(f"{s} {100 * 10 ** f['a']:.3f}% ({f['n_weeks']} wk)"
                                                    for s, f in season_fits.items()))
    print(f"  a (FINAL, {level_season}) = {a_final:.3f} (95% CI {a_ci[0]:.3f} .. "
          f"{a_ci[1]:.3f}) -> {100 * 10 ** a_final:.3f}% at 100,000 copies/L "
          f"({100 * 10 ** a_ci[0]:.3f}-{100 * 10 ** a_ci[1]:.3f}%)")
    print(f"  residual scatter beyond counting noise (all weeks, all-weeks line): SD {sd_excess:.3f} log10 "
          f"(x{10 ** sd_excess:.2f}); total {sd_total:.3f}; {n_scatter} weeks with >=5 positives")
    print(f"  (diagnostic, {level_season} only: excess SD {sd_level_season[0]:.3f}, {sd_level_season[2]} weeks)")


if __name__ == "__main__":
    main()
