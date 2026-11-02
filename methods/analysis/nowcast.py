"""
Test simple projections ("nowcasts") that bridge the publication delay:
from the latest available Berlin wastewater week, predict the trailing 2-week
level h weeks later (h = 1, 2, 3), and check how often the predicted category
matches the category actually reached.

Why: the newest Berlin sample is typically ~10 days old when RKI publishes
(weekly, Wednesdays), so on a given day the app's data are ~1.5-2.5 weeks old
(Validation log, "Publication delay").

Methods compared (all on log10 trailing 2-week values, T):
  persistence  T(t+h) = T(t)                       (no change)
  trend        T(t+h) = T(t) + h * g               g = (T(t) - T(t-2)) / 2,
                                                   the weekly growth behind
                                                   the trend rule
  damped       T(t+h) = T(t) + phi_h * g,          phi_h = phi + phi^2 + ...
                                                   (h terms); damping phi in
                                                   {0.5, 0.7, 0.85}
Reported per method and horizon:
  - median and 90th-percentile absolute error (log10 and as a factor)
  - share of weeks where the predicted category equals the realized one
  - share off by two or more bands
Categories: the five bands, converted to wastewater units with the current
fit (data/fit_params.json).

Input: data/berlin_wastewater_weekly.csv, data/fit_params.json
Usage: python3 analysis/nowcast.py
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Method steps 5-6: threshold = 10 cases/100k/day x 7 days infectious x 4;
# "Right around" = threshold x/÷ 1.4 (widened 2026-11-01), "just" bands to ÷2 / x2.
THRESHOLD = 10 / 1e5 * 7 * 4
PREVALENCE_EDGES = [THRESHOLD / 2, THRESHOLD / 1.4, THRESHOLD * 1.4, THRESHOLD * 2]


def wastewater_edges(fit):
    """Band edges in log10 wastewater units: w = w_ref + (log10 p - a) / b."""
    return [fit["w_ref_log10"] + (np.log10(p) - fit["a"]) / fit["b"] for p in PREVALENCE_EDGES]


def category(x, edges):
    """Band index 0..4 (well below .. well above) for log10 values x."""
    return np.digitize(x, edges)


def predict(T, h, method, phi=None):
    """Prediction of T(t+h) made at each week t (Series aligned to t)."""
    g = (T - T.shift(2)) / 2
    if method == "persistence":
        return T
    if method == "trend":
        return T + h * g
    if method == "damped":
        return T + sum(phi ** k for k in range(1, h + 1)) * g
    raise ValueError(method)


def main():
    w = pd.read_csv(PROJECT_ROOT / "data" / "berlin_wastewater_weekly.csv", parse_dates=["week_monday"])
    fit = json.loads((PROJECT_ROOT / "data" / "fit_params.json").read_text())
    edges = wastewater_edges(fit)
    T = w.set_index("week_monday")["log10_trailing2"]
    T = T.reindex(pd.date_range(T.index.min(), T.index.max(), freq="7D"))

    methods = [("persistence", None), ("trend", None)] + [("damped", p) for p in (0.5, 0.7, 0.85)]
    rows = []
    for h in (1, 2, 3):
        actual = T.shift(-h)                        # realized value h weeks later, aligned to t
        for m, phi in methods:
            pred = predict(T, h, m, phi)
            ok = pred.notna() & actual.notna() & T.shift(2).notna()   # same weeks for every method
            err = (pred - actual)[ok]
            c_pred, c_act = category(pred[ok].values, edges), category(actual[ok].values, edges)
            rows.append({
                "h_weeks": h, "method": m if phi is None else f"damped {phi}", "weeks": int(ok.sum()),
                "median_abs_err_x": round(10 ** err.abs().median(), 2),
                "p90_abs_err_x": round(10 ** err.abs().quantile(0.9), 2),
                "category_match_%": round(100 * np.mean(c_pred == c_act)),
                "off_by_2plus_%": round(100 * np.mean(np.abs(c_pred - c_act) >= 2), 1),
            })
    print(f"band edges (log10 copies/L, trailing 2-week): {[round(e, 3) for e in edges]}")
    print(pd.DataFrame(rows).to_string(index=False))

    # How do errors behave when the level is changing fast? (2-week change > x1.25)
    g = (T - T.shift(2)) / 2
    moving = (2 * g).abs() > np.log10(1.25)
    print(f"\nweeks where the 2-week change exceeds x1.25 (trend rule says rising/falling): {int(moving.sum())}")
    for h in (2,):
        actual = T.shift(-h)
        for m, phi in [("persistence", None), ("trend", None), ("damped", 0.7)]:
            pred = predict(T, h, m, phi)
            ok = pred.notna() & actual.notna() & moving
            c_pred, c_act = category(pred[ok].values, edges), category(actual[ok].values, edges)
            print(f"  h={h} {m if phi is None else 'damped ' + str(phi)}: category match {100 * np.mean(c_pred == c_act):.0f}% "
                  f"over {int(ok.sum())} moving weeks")


if __name__ == "__main__":
    main()
