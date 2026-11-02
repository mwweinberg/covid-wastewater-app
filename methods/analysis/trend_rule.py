"""
Test candidate trend rules ("rising" / "falling" / "steady") on the Berlin
wastewater series, to see how often each label would have flip-flopped.

Rule family tested:
    change = level(t) / level(t - gap)
    where level = trailing k-week geometric mean of the weekly wastewater
    values (k = window weeks), and gap = k, so the two windows don't overlap.
    rising if change > factor, falling if change < 1/factor, else steady.
Candidate tested first: window 2, factor 1.25 (proposed 2026-11-01).

What is reported per rule:
  - share of weeks labelled rising / falling / steady
  - label changes per year
  - "flip-flops": a rising label followed by falling within 2 weeks or the
    reverse (the confusing case), and short-lived labels (a label lasting
    only 1 week)
  - agreement with hindsight: compare the label with the direction of a
    smoother centered 5-week series over the same span (which uses future
    weeks, so the app can't compute it, but it shows what the real
    direction was). Weeks where the hindsight change is small (< x1.1)
    count as "steady" in hindsight.

Input: data/berlin_wastewater_weekly.csv (pipeline/wastewater.py)
Usage: python3 analysis/trend_rule.py
"""

from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def labels_for(x, window, factor):
    """Trend label per week for the weekly log10 series x (pd.Series on a
    complete weekly index). Returns a Series of 'rising'/'falling'/'steady'
    (NaN where the windows aren't complete)."""
    level = x.rolling(window).mean()               # log10 of the geometric mean
    change = level - level.shift(window)           # log10 of the ratio
    lab = pd.Series(np.where(change > np.log10(factor), "rising",
                             np.where(change < -np.log10(factor), "falling", "steady")), index=x.index)
    return lab.where(change.notna()), change


def hindsight_labels(x, window, steady_factor=1.1):
    """Direction in hindsight: change of a centered 5-week mean over the same
    span the rule looks at (from the middle of the earlier window to the
    middle of the later one). Uses future weeks; for evaluation only."""
    smooth = x.rolling(5, center=True).mean()
    change = smooth - smooth.shift(window)
    lab = pd.Series(np.where(change > np.log10(steady_factor), "rising",
                             np.where(change < -np.log10(steady_factor), "falling", "steady")), index=x.index)
    return lab.where(change.notna())


def evaluate(x, window, factor, hind_span=None):
    """Summary statistics for one rule (see module docstring).
    hind_span: span (weeks) of the hindsight direction the rule is scored
    against. Default = window (original behaviour). Pass one fixed value when
    comparing windows, otherwise each window is scored against a different
    target (longer spans make agreement easier; review finding 2026-11-01)."""
    lab, _ = labels_for(x, window, factor)
    hind = hindsight_labels(x, window if hind_span is None else hind_span)
    lab = lab.dropna()
    years = len(lab) / 52.18
    changes = (lab != lab.shift()).iloc[1:].sum()
    # Flip-flops: opposite direction within the next 2 weeks.
    vals = lab.values
    flips = sum(1 for i in range(len(vals)) for j in (1, 2)
                if i + j < len(vals) and {vals[i], vals[i + j]} == {"rising", "falling"})
    # Labels that last exactly one week.
    runs = (lab != lab.shift()).cumsum()
    run_len = lab.groupby(runs).size()
    one_week = int((run_len == 1).sum())
    both = pd.concat([lab, hind], axis=1, keys=["rule", "hind"]).dropna()
    agree = (both["rule"] == both["hind"]).mean()
    opposite = (((both["rule"] == "rising") & (both["hind"] == "falling")) |
                ((both["rule"] == "falling") & (both["hind"] == "rising"))).mean()
    share = lab.value_counts(normalize=True)
    return {
        "window": window, "factor": factor, "weeks": len(lab),
        "rising_%": round(100 * share.get("rising", 0)), "steady_%": round(100 * share.get("steady", 0)),
        "falling_%": round(100 * share.get("falling", 0)),
        "changes_per_year": round(changes / years, 1), "flip_flops": flips, "one_week_labels": one_week,
        "agree_hindsight_%": round(100 * agree), "opposite_hindsight_%": round(100 * opposite, 1),
    }


def main():
    w = pd.read_csv(PROJECT_ROOT / "data" / "berlin_wastewater_weekly.csv", parse_dates=["week_monday"])
    x = w.set_index("week_monday")["log10_load"]
    x = x.reindex(pd.date_range(x.index.min(), x.index.max(), freq="7D"))

    rows = [evaluate(x, win, f) for win in (2, 3) for f in (1.25, 1.5)]
    print(pd.DataFrame(rows).to_string(index=False))

    # Detail for the proposed rule: the last 16 weeks.
    lab, change = labels_for(x, 2, 1.25)
    tail = pd.DataFrame({"week": [d.date() for d in x.index], "load": (10 ** x).round(-2),
                         "change_x": (10 ** change).round(2), "label": lab.values}).tail(16)
    print("\nproposed rule (window 2, x1.25), last 16 weeks:")
    print(tail.to_string(index=False))


if __name__ == "__main__":
    main()
