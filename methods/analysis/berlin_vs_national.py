"""
Read-only check: does Berlin track the national curve closely enough that
national sentinel SARS-CoV-2 positivity can stand in for Berlin's?

Two comparisons, both since the April 2024 Berlin lab change:
  1. Berlin wastewater vs. a national wastewater curve that EXCLUDES Berlin
     (the published aggregate includes Berlin at ~19% of its weight, which
     would inflate agreement). The ex-Berlin curve re-implements RKI's
     documented aggregation (AMELAG Readme, "Aggregation of the WWTP values").
  2. GrippeWeb ARE incidence, region "Osten" (contains Berlin) vs "Bundesweit".

Outputs, per comparison:
  - cross-correlation at lags -4..+4 weeks, on levels and on week-over-week
    changes (changes are more sensitive to timing offsets than levels)
  - slope of Berlin on national (amplitude: >1 means Berlin swings harder)
  - spread of the log ratio over time (is any level offset stable?)
  - peak weeks of each wave, side by side

Standard library only (written before the global venv was readable from the
Claude Code sandbox; kept dependency-free so it runs with any Python 3.9+).

Usage: python3 berlin_vs_national.py <amelag_einzelstandorte.tsv> <grippeweb.tsv>
"""

import csv
import math
import sys
from collections import defaultdict
from datetime import date, timedelta

# Calibration period starts at the latest Berlin lab change (Ruhleben, 2024-04-03).
START = date(2024, 4, 3)
BERLIN_PREFIX = "Berlin "
# RKI only publishes an aggregate week if at least this many sites reported.
MIN_SITES = 10


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def week_monday(d):
    """Return the Monday of the ISO week containing date d.
    Used as the weekly key so that 'one week later' is simply +7 days."""
    return d - timedelta(days=d.weekday())


def mean(xs):
    """Arithmetic mean of a non-empty list."""
    return sum(xs) / len(xs)


def corr(xs, ys):
    """Pearson correlation of two equal-length lists. Returns NaN if either
    series has zero variance or fewer than 3 points."""
    n = len(xs)
    if n < 3:
        return float("nan")
    mx, my = mean(xs), mean(ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return float("nan")
    return sxy / math.sqrt(sxx * syy)


def ols_slope(xs, ys):
    """Slope of the least-squares line y = a + b*x. Returns b."""
    mx, my = mean(xs), mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx


def smooth3(series):
    """Centered 3-week moving average over a {monday: value} dict.
    A week is only smoothed if both neighbours exist; otherwise it is dropped.
    This is the 'light smoothing' from the handoff, step 1."""
    out = {}
    for w, v in series.items():
        a, b = series.get(w - timedelta(7)), series.get(w + timedelta(7))
        if a is not None and b is not None:
            out[w] = (a + v + b) / 3
    return out


# ---------------------------------------------------------------------------
# Wastewater: RKI-style aggregation
# ---------------------------------------------------------------------------

def load_site_weeks(path):
    """Read AMELAG site-level data and return SARS-CoV-2 weekly means.

    Returns:
      weekly: {(site, segment): {monday: mean log10(viruslast_normalisiert)}}
      pop:    {site: einwohner}
      floor_weeks: set of (site, monday) with at least one raw value == 1000
                   (the suspected unflagged below-LOQ floor seen in Berlin)

    'segment' counts laborwechsel == 'ja' flags seen so far for that site, so
    each site-laboratory combination gets its own offset, as RKI does.
    Segment numbering uses the full history, but only weeks >= START are kept.
    """
    rows = defaultdict(list)
    pop = {}
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            if r["typ"] != "SARS-CoV-2":
                continue
            rows[r["standort"]].append(r)
            pop[r["standort"]] = float(r["einwohner"])

    weekly_lists = defaultdict(lambda: defaultdict(list))
    floor_weeks = set()
    for site, rs in rows.items():
        rs.sort(key=lambda r: r["datum"])
        seg = 0
        for r in rs:
            # A 'ja' row is the first sample from the new lab: bump first.
            if r["laborwechsel"] == "ja":
                seg += 1
            d = date.fromisoformat(r["datum"])
            v = r["viruslast_normalisiert"]
            if d < START or v == "NA" or float(v) <= 0:
                continue
            w = week_monday(d)
            weekly_lists[(site, seg)][w].append(math.log10(float(v)))
            if r["viruslast"] != "NA" and float(r["viruslast"]) == 1000:
                floor_weeks.add((site, w))

    weekly = {k: {w: mean(v) for w, v in d.items()} for k, d in weekly_lists.items()}
    return weekly, pop, floor_weeks


def aggregate(weekly, pop, min_sites):
    """Re-implementation of RKI's aggregation on log10 weekly values.

    1. For each week, mean across all site-segments reporting that week.
    2. Each site-segment's offset = its mean deviation from those weekly means.
    3. Subtract the offset (removes plant/lab level differences).
    4. Population-weighted mean of the adjusted values, per week, only for
       weeks with at least min_sites sites reporting.

    Input/Output: weekly as from load_site_weeks (already filtered to the
    desired sites); returns {monday: aggregated log10 value}.
    """
    by_week = defaultdict(list)
    for k, d in weekly.items():
        for w, x in d.items():
            by_week[w].append(x)
    wk_mean = {w: mean(xs) for w, xs in by_week.items()}

    offset = {k: mean([x - wk_mean[w] for w, x in d.items()]) for k, d in weekly.items()}

    num, den, cnt = defaultdict(float), defaultdict(float), defaultdict(int)
    for (site, seg), d in weekly.items():
        for w, x in d.items():
            num[w] += pop[site] * (x - offset[(site, seg)])
            den[w] += pop[site]
            cnt[w] += 1
    return {w: num[w] / den[w] for w in num if cnt[w] >= min_sites}


# ---------------------------------------------------------------------------
# GrippeWeb
# ---------------------------------------------------------------------------

def load_grippeweb(path, region):
    """Return {monday: log10(ARE incidence per 100k)} for all ages (00+) in
    the given region, for weeks from START on. Kalenderwoche is 'YYYY-Www'."""
    out = {}
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            if r["Erkrankung"] != "ARE" or r["Altersgruppe"] != "00+" or r["Region"] != region:
                continue
            y, wk = r["Kalenderwoche"].split("-W")
            monday = date.fromisocalendar(int(y), int(wk), 1)
            if monday >= week_monday(START) and r["Inzidenz"] not in ("", "NA"):
                out[monday] = math.log10(float(r["Inzidenz"]))
    return out


# ---------------------------------------------------------------------------
# Comparison report
# ---------------------------------------------------------------------------

def peaks(series, min_rise=0.3):
    """Find wave peaks in a smoothed {monday: log10} series.
    A peak is a week that is the maximum within +/- 4 weeks AND stands at
    least min_rise log10 above the lowest point in the preceding 12 weeks
    (filters out small wiggles). Returns a sorted list of mondays."""
    ws = sorted(series)
    out = []
    for w in ws:
        win = [series[x] for x in ws if abs((x - w).days) <= 28]
        before = [series[x] for x in ws if 0 < (w - x).days <= 84]
        if series[w] == max(win) and before and series[w] - min(before) >= min_rise:
            out.append(w)
    return out


def compare(name, berlin, national):
    """Print the timing / amplitude / offset comparison of two weekly log10
    series. Positive lag k means the national series is k weeks LATER than
    Berlin (i.e. Berlin leads)."""
    print(f"\n=== {name} ===")
    common = sorted(set(berlin) & set(national))
    print(f"weeks in common: {len(common)}  ({common[0]} .. {common[-1]})")

    # Cross-correlation on levels and on week-over-week changes.
    db = {w: berlin[w] - berlin[w - timedelta(7)] for w in berlin if w - timedelta(7) in berlin}
    dn = {w: national[w] - national[w - timedelta(7)] for w in national if w - timedelta(7) in national}
    print("lag(wk)  r_levels  r_changes   (lag>0: Berlin leads)")
    for k in range(-4, 5):
        sh = timedelta(7 * k)
        pl = [(berlin[w], national[w + sh]) for w in berlin if w + sh in national]
        pc = [(db[w], dn[w + sh]) for w in db if w + sh in dn]
        rl = corr([a for a, _ in pl], [b for _, b in pl])
        rc = corr([a for a, _ in pc], [b for _, b in pc])
        print(f"  {k:+d}     {rl:6.3f}    {rc:6.3f}")

    # Amplitude and offset stability at lag 0.
    b = [berlin[w] for w in common]
    n = [national[w] for w in common]
    slope = ols_slope(n, b)
    ratio = [x - y for x, y in zip(b, n)]
    mr = mean(ratio)
    sd = math.sqrt(mean([(r - mr) ** 2 for r in ratio]))
    srt = sorted(ratio)
    p10, p90 = srt[int(0.1 * len(srt))], srt[int(0.9 * len(srt)) - 1]
    print(f"slope Berlin~national (log-log): {slope:.2f}")
    print(f"log10 ratio Berlin-national: SD {sd:.3f} (x{10**sd:.2f}), "
          f"10-90% spread {p90 - p10:.3f} (x{10**(p90 - p10):.2f})")

    # Per-half-year mean of the ratio, to see whether the offset drifts.
    halves = defaultdict(list)
    for w, r in zip(common, ratio):
        halves[f"{w.year}-H{1 if w.month <= 6 else 2}"].append(r - mr)
    print("ratio drift by half-year (log10, relative to overall mean): " +
          ", ".join(f"{h} {mean(v):+.2f}" for h, v in sorted(halves.items())))

    # Wave peaks side by side (smoothed series).
    pb, pn = peaks(smooth3(berlin)), peaks(smooth3(national))
    print("peaks Berlin:  ", [str(p) for p in pb])
    print("peaks national:", [str(p) for p in pn])


def main():
    amelag_path, gw_path = sys.argv[1], sys.argv[2]

    weekly, pop, floor_weeks = load_site_weeks(amelag_path)
    berlin_w = {k: v for k, v in weekly.items() if k[0].startswith(BERLIN_PREFIX)}
    other_w = {k: v for k, v in weekly.items() if not k[0].startswith(BERLIN_PREFIX)}

    # Berlin: same aggregation over its 3 plants (offsets handle a plant
    # missing a week); min_sites=1 since there are only three.
    berlin = aggregate(berlin_w, pop, min_sites=1)
    national = aggregate(other_w, pop, min_sites=MIN_SITES)
    n_sites = len({k[0] for k in other_w})
    print(f"ex-Berlin national curve built from {n_sites} sites, "
          f"{len(other_w)} site-lab segments since {START}")

    compare("Wastewater: Berlin vs national ex-Berlin (raw weekly)", berlin, national)
    compare("Wastewater: Berlin vs national ex-Berlin (3-wk smoothed)",
            smooth3(berlin), smooth3(national))

    # Sensitivity: drop Berlin weeks that contain the suspected 1000-copy floor.
    fw = {w for (_, w) in floor_weeks if _.startswith(BERLIN_PREFIX)}
    berlin_nf = {w: v for w, v in berlin.items() if w not in fw}
    print(f"\n(Berlin weeks containing a raw==1000 floor value: {len(fw)})")
    compare("Wastewater: Berlin (floor weeks dropped) vs national ex-Berlin", berlin_nf, national)

    osten = load_grippeweb(gw_path, "Osten")
    bund = load_grippeweb(gw_path, "Bundesweit")
    compare("GrippeWeb ARE 00+: Osten vs Bundesweit (raw weekly)", osten, bund)


if __name__ == "__main__":
    main()
