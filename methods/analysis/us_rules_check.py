"""
Test the trend rule and the projection to today on NYC and DC (both were
tuned on Berlin only; user request 2026-11-01). Same code and scoring as the
Berlin tests, so the three cities are comparable:
  - trend rule: analysis/trend_rule.py (labels from the latest 2 weeks vs the
    2 before, x1.25 cut-off; flip-flops; agreement with hindsight)
  - projection: analysis/nowcast.py's predict() (persistence, full trend,
    damped trend with phi 0.5 / 0.7 / 0.85), horizons 1-4 weeks, scored by
    error and by how often the predicted category equals the one reached.
Categories use each city's own conversion and band edges (data/us_fit_params.json
for NYC/DC, data/fit_params.json for Berlin).

Series: weekly log10 values on a complete weekly index; NYC excludes the
provisional CDC-filled week(s); DC uses the WVAL series mapped onto the
snapshot scale (pipeline/us_wastewater.py).

Usage: python3 analysis/us_rules_check.py
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from trend_rule import evaluate                     # noqa: E402  (same scoring as Berlin)
from nowcast import predict, category                # noqa: E402

DATA = ROOT / "data"
METHODS = [("persistence", None), ("trend", None), ("damped", 0.5), ("damped", 0.7), ("damped", 0.85)]


def series(city):
    """Weekly log10 series and band edges (log10 wastewater units, trailing
    2-week scale) for one city."""
    if city == "berlin":
        w = pd.read_csv(DATA / "berlin_wastewater_weekly.csv", parse_dates=["week_monday"]).set_index("week_monday")
        x = w["log10_load"]
        fit = json.loads((DATA / "fit_params.json").read_text())
        th = 10 / 1e5 * 7 * 4
        edges = [fit["w_ref_log10"] + (np.log10(p) - fit["a"]) / fit["b"] for p in [th / 2, th / 1.4, th * 1.4, th * 2]]
    else:
        w = pd.read_csv(DATA / f"{city}_wastewater_weekly.csv", parse_dates=["week_end"]).set_index("week_end")
        if "provisional" in w:
            w = w[~w["provisional"].astype("boolean").fillna(False).astype(bool)]
        x = w["log10_weekly"]
        edges = json.loads((DATA / "us_fit_params.json").read_text())[city]["band_edges_wastewater"]
    x = x.dropna()
    return x.reindex(pd.date_range(x.index.min(), x.index.max(), freq="7D")), edges


def projection_table(x, edges):
    """Score each projection method at horizons 1-4 on the trailing 2-week
    series T. Same weeks for every method within a horizon."""
    T = x.rolling(2).mean()
    rows = []
    for h in (1, 2, 3, 4):
        actual = T.shift(-h)
        for m, phi in METHODS:
            pred = predict(T, h, m, phi)
            ok = pred.notna() & actual.notna() & T.shift(2).notna()
            err = (pred - actual)[ok]
            cp, ca = category(pred[ok].values, edges), category(actual[ok].values, edges)
            rows.append({"h": h, "method": m if phi is None else f"damped {phi}", "weeks": int(ok.sum()),
                         "median_err_x": round(10 ** err.abs().median(), 2), "p90_err_x": round(10 ** err.abs().quantile(0.9), 2),
                         "category_match_%": round(100 * np.mean(cp == ca)),
                         "off_by_2plus_%": round(100 * np.mean(np.abs(cp - ca) >= 2), 1)})
    return pd.DataFrame(rows)


def main():
    pd.set_option("display.width", 160)
    trend_rows = []
    for city in ["berlin", "nyc", "dc"]:
        x, edges = series(city)
        # Week-to-week noise: SD of the change in the weekly log10 value.
        noise = x.diff().std()
        # Every rule is scored against the same hindsight target (3-week span),
        # so windows are comparable (review finding 2026-11-01).
        trend_rows += [{"city": city, "weekly_change_sd_x": round(10 ** noise, 2), "hindsight_span": 3,
                        **evaluate(x, win, f, hind_span=3)} for win in (2, 3) for f in (1.25, 1.5)]
        print(f"\n=== projection, {city.upper()} ({x.notna().sum()} weeks) ===")
        print(projection_table(x, edges).to_string(index=False))
    print("\n=== trend rule (window = weeks per side; factor = cut-off) ===")
    print(pd.DataFrame(trend_rows).to_string(index=False))


if __name__ == "__main__":
    main()
