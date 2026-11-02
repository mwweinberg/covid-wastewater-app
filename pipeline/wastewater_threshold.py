"""
Wastewater threshold readout for the app's data pipeline (Python standard library only).

Drop this file and threshold_config.json into the app repo's pipeline/ folder.
pipeline/build_data.py calls it once per city, with the rows it already downloads:

    import wastewater_threshold as wt
    cfg = wt.load_config()                                     # pipeline/threshold_config.json
    berlin = wt.berlin_series(amelag_rows, cfg)                # rows of amelag_einzelstandorte.tsv
    nyc    = wt.nyc_series(nyc_rows, cdc_ny_rows, cfg)         # NYC Open Data f7dc-2q9f + CDC atcp-73re (NY)
    dc     = wt.dc_series(cdc_dc_rows, cfg)                    # CDC atcp-73re, site ID:300
    block  = wt.readout(berlin, cfg, today)                    # dict to put in the city JSON

What it does NOT do: fit anything. All conversion parameters are frozen in
threshold_config.json, produced by the methods project at each refit
(next routine refit late October 2027). If an alarm fires, the readout is
withheld and a message says why; nothing is refitted automatically.

Conventions
- Series values are log10 of the city's weekly wastewater value:
    Berlin: copies/L of viruslast_normalisiert (ISO weeks, Monday-Sunday)
    NYC:    copies/L (US weeks, Sunday-Saturday, labelled by the Saturday)
    DC:     CDC WVAL mapped onto the frozen snapshot scale (US weeks)
- The readout uses the trailing 2-week mean of those weekly log10 values.
- Every row from the feeds is a dict of strings (as csv.DictReader or the
  Socrata JSON API return them); numbers are parsed here.

Sources of every rule: wastewater-threshold-handoff.md in the methods project
(Method steps, checklists R/F/C/N/D/S, Validation log).
"""

import json
import math
import os
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def load_config(path=None):
    """Read threshold_config.json (next to this file unless a path is given)."""
    with open(path or os.path.join(HERE, "threshold_config.json"), encoding="utf-8") as f:
        return json.load(f)


def _num(x):
    """Parse a feed value to float; None for missing/NA/empty/non-numeric."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def _day(s):
    """Date from 'YYYY-MM-DD' or 'YYYY-MM-DDThh:mm:ss.sss' (Socrata)."""
    return date.fromisoformat(str(s)[:10])


def iso_monday(d):
    """Monday of the ISO week containing d (Berlin weeks)."""
    return d - timedelta(days=d.weekday())


def us_saturday(d):
    """Saturday ending the Sunday-Saturday week containing d (US weeks)."""
    return d + timedelta(days=(5 - d.weekday()) % 7)


def _mean(xs):
    return sum(xs) / len(xs)


def threshold_and_edges(cfg):
    """Reference threshold (prevalence, fraction) and the four band edges.
    threshold = cases/100k/day x days infectious x unreported multiplier
    (= 0.28% with 10, 7, 4); edges = threshold x band factors."""
    t = cfg["threshold"]
    th = t["cases_per_100k_per_day"] / 1e5 * t["days_infectious"] * t["unreported_multiplier"]
    return th, [th * f for f in t["band_factors"]]


def band_index(prev, edges):
    """0..4 = Well below .. Well above (value exactly on an edge goes up)."""
    return sum(1 for e in edges if prev >= e)


def one_in_n(prev, cfg):
    """'About 1 in N' text: N = 1/prevalence rounded to 2 significant figures,
    with 'fewer than 1 in 10,000' / 'more than 1 in 20' at the extremes."""
    o = cfg["one_in_n"]
    n = 1 / prev
    if n > o["max_n"]:
        return f"fewer than 1 in {o['max_n']:,}"
    if n < o["min_n"]:
        return f"more than 1 in {o['min_n']:,}"
    digits = o["significant_figures"] - int(math.floor(math.log10(n))) - 1
    return f"about 1 in {int(round(n, digits)):,}"


def restatement_map(snapshot, current, rcfg):
    """How CDC rescaled a site's WVAL history since the frozen snapshot.

    WVAL = exp((ln c - baseline) / sd), so a new baseline/sd changes log WVAL
    by a straight line: log10 new = alpha + beta * log10 old. Fitted by least
    squares over the weeks present in both {week: log10} dicts, ignoring the
    top `drop_top_share` of snapshot values (CDC caps extremes).
    Returns dict alpha, beta, resid_sd, n_weeks, restated, ok."""
    weeks = sorted(set(snapshot) & set(current))
    pairs = [(snapshot[w], current[w]) for w in weeks]
    if len(pairs) < 10:
        return {"alpha": 0.0, "beta": 1.0, "resid_sd": 0.0, "n_weeks": len(pairs), "restated": False, "ok": False}
    cut = sorted(p[0] for p in pairs)[int((1 - rcfg["drop_top_share"]) * (len(pairs) - 1))]
    pairs = [p for p in pairs if p[0] <= cut]
    mx, my = _mean([p[0] for p in pairs]), _mean([p[1] for p in pairs])
    sxx = sum((p[0] - mx) ** 2 for p in pairs)
    beta = sum((p[0] - mx) * (p[1] - my) for p in pairs) / sxx
    alpha = my - beta * mx
    res = [p[1] - alpha - beta * p[0] for p in pairs]
    sd = math.sqrt(sum(r * r for r in res) / max(len(res) - 2, 1))
    restated = abs(alpha) > rcfg["tolerance"] or abs(beta - 1) > rcfg["tolerance"]
    return {"alpha": alpha, "beta": beta, "resid_sd": sd, "n_weeks": len(pairs), "restated": restated,
            "ok": sd <= rcfg["max_resid_sd_log10"]}


# ---------------------------------------------------------------------------
# City series
# ---------------------------------------------------------------------------
# Each builder returns a dict:
#   city, status ("ok" / "LAB_CHANGE_ALARM" / "METHOD_CHECK" / "RESTATEMENT_CHECK"),
#   message (why, if not ok), week_step_days (7), weeks: {week_date: {...}},
#   newest_sample (date), comparable_since (date)
# with per-week fields log10 (weekly), below_loq (Berlin), provisional (NYC).

def berlin_series(amelag_rows, cfg):
    """Berlin weekly SARS-CoV-2 series (checklist R1, R2, R4, R5).

    Steps: keep SARS-CoV-2 rows of the three plants from the frozen
    comparable_since date; LAB CHANGE ALARM if any laborwechsel == "ja" after
    that date; a sample is below LOQ if unter_bg == "ja" or raw viruslast is a
    known floor value (1,000 copies/L), and a week is flagged if any sample is;
    per plant and ISO week: mean of log10 viruslast_normalisiert; city value =
    population-weighted mean over reporting plants of (plant value - frozen
    plant offset)."""
    c = cfg["cities"]["berlin"]
    s = c["series"]
    start = date.fromisoformat(c["comparable_since"])
    plants = set(s["plants"])
    floors = set(s["floor_values_raw_copies_per_l"])
    out = {"city": "berlin", "status": "ok", "message": None, "comparable_since": start, "weeks": {}}
    rows = [r for r in amelag_rows if r.get("typ") == "SARS-CoV-2" and r.get("standort") in plants]
    changes = sorted({(r["standort"], r["datum"]) for r in rows
                      if r.get("laborwechsel") == "ja" and _day(r["datum"]) > start})
    if changes:
        out["status"] = "LAB_CHANGE_ALARM"
        out["message"] = ("Lab change in Berlin wastewater testing: " + "; ".join(f"{p} on {d}" for p, d in changes)
                          + f". Values since then are not comparable with the calibration (from {start}). "
                            "Threshold readout withheld until the conversion is refitted.")
        return out
    acc = {}                                   # (plant, monday) -> [log10 values], below-LOQ flag
    newest = None
    for r in rows:
        d = _day(r["datum"])
        v = _num(r.get(s["value_column"]))
        if d < start or v is None or v <= 0:
            continue
        below = r.get("unter_bg") == "ja" or _num(r.get("viruslast")) in floors
        key = (r["standort"], iso_monday(d))
        e = acc.setdefault(key, {"x": [], "below": False})
        e["x"].append(math.log10(v))
        e["below"] = e["below"] or below
        newest = d if newest is None or d > newest else newest
    by_week = {}
    for (plant, wk), e in acc.items():
        by_week.setdefault(wk, []).append((plant, _mean(e["x"]), e["below"]))
    for wk, items in by_week.items():
        wsum = sum(s["plant_weights"][p] for p, _, _ in items)
        val = sum(s["plant_weights"][p] * (x - s["plant_offsets_log10"][p]) for p, x, _ in items) / wsum
        out["weeks"][wk] = {"log10": val, "below_loq": any(b for _, _, b in items), "n_plants": len(items),
                            "provisional": False}
    out["newest_sample"] = newest
    return out


def _cdc_by_plant(cdc_rows, site_map):
    """CDC SARS-CoV-2 WVAL rows -> {plant: {saturday: log10 WVAL}}."""
    out = {}
    for r in cdc_rows:
        if r.get("pathogen_target") != "SARS-CoV-2" or r.get("site") not in site_map:
            continue
        v = _num(r.get("site_wval"))
        if v is None or v <= 0:
            continue
        out.setdefault(site_map[r["site"]], {})[_day(r["week_end"])] = math.log10(v)
    return out


def nyc_series(nyc_rows, cdc_ny_rows, cfg):
    """NYC weekly SARS-CoV-2 series (checklist N-a, N-a2, N-c, N-d).

    NYC Open Data rows from comparable_since (digital PCR only); LAB CHANGE
    ALARM on an unknown `technology`; one row per plant and sample date
    (preferred method first); per plant and US week: mean of log10 copies/L.
    Weeks after NYC's newest published week are filled from CDC's WVAL for the
    same plant, mapped onto the frozen snapshot scale (restatements), then
    translated: log10 copies/L = slope * log10 WVAL + intercept, intercept from
    the last `intercept_weeks` weeks where both exist; these weeks are
    provisional. METHOD_CHECK if NYC's values moved against CDC's by more than
    x1.5 since the freeze, or if CDC's history changed beyond a rescaling.
    City value = population-weighted mean over reporting plants of
    (plant value - frozen plant offset)."""
    c = cfg["cities"]["nyc"]
    s = c["series"]
    rc = cfg["cdc_restatement"]
    start = date.fromisoformat(c["comparable_since"])
    out = {"city": "nyc", "status": "ok", "message": None, "comparable_since": start, "weeks": {}}
    methods = s["methods_in_preference_order"]
    rows = []
    for r in nyc_rows:
        v = _num(r.get("copies_l"))
        if v is None or v <= 0:
            continue
        d = _day(r["sample_date"])
        if us_saturday(d) < start:
            continue
        rows.append((r["wrrf_name"], d, r.get("technology"), math.log10(v)))
    unknown = sorted({t for _, _, t, _ in rows if t not in methods})
    if unknown:
        first = min(d for _, d, t, _ in rows if t in unknown)
        out["status"] = "LAB_CHANGE_ALARM"
        out["message"] = (f"New lab method in NYC wastewater data ({', '.join(unknown)}), first sample {first}. "
                          "Values are not comparable with the calibration. Threshold readout withheld until refit.")
        return out
    best = {}                                  # (plant, day) -> (method rank, log10)
    for plant, d, t, x in rows:
        k = (plant, d)
        if k not in best or methods.index(t) < best[k][0]:
            best[k] = (methods.index(t), x)
    own = {}                                   # plant -> {saturday: [log10]}
    for (plant, d), (_, x) in best.items():
        own.setdefault(plant, {}).setdefault(us_saturday(d), []).append(x)
    own = {p: {w: _mean(v) for w, v in wk.items()} for p, wk in own.items()}
    newest = max(d for _, d in best)
    last_week = max(w for wk in own.values() for w in wk)

    # CDC values, mapped onto the snapshot scale per plant.
    cdc = _cdc_by_plant(cdc_ny_rows, s["cdc_site_map"])
    mapped, problems = {}, []
    for plant, cur in cdc.items():
        snap = {date.fromisoformat(k): v for k, v in s["cdc_snapshot_log10_wval"].get(plant, {}).items()}
        m = restatement_map(snap, cur, rc)
        if not m["ok"]:
            problems.append(f"{plant}: CDC history changed beyond a rescaling (residual SD {m['resid_sd']:.3f})")
        mapped[plant] = {w: (v - m["alpha"]) / m["beta"] for w, v in cur.items()}

    # Translation intercepts and the silent-method-change check.
    intercepts, fill = {}, {}
    for plant, slope in s["cdc_translation_slopes"].items():
        both = sorted(w for w in own.get(plant, {}) if w in mapped.get(plant, {}))[-s["intercept_weeks"]:]
        if not both:
            continue
        intercepts[plant] = _mean([own[plant][w] - slope * mapped[plant][w] for w in both])
        for w, lw in mapped.get(plant, {}).items():
            if w > last_week:
                fill.setdefault(plant, {})[w] = slope * lw + intercepts[plant]
    wts = s["plant_weights"]
    shift = (sum((intercepts[p] - s["cdc_intercepts_at_freeze"][p]) * wts[p] for p in intercepts)
             / sum(wts[p] for p in intercepts)) if intercepts else 0.0
    if abs(shift) > s["method_drift_max_log10"]:
        problems.append(f"NYC values moved x{10 ** shift:.2f} against CDC's index for the same plants since the "
                        "calibration, without a lab-method label change (possible silent method change)")
    if problems:
        out["status"] = "METHOD_CHECK"
        out["message"] = "; ".join(problems) + ". Threshold readout withheld until checked or refitted."
        return out

    weeks = sorted({w for wk in own.values() for w in wk} | {w for wk in fill.values() for w in wk})
    for w in weeks:
        items = [(p, own[p][w], False) for p in own if w in own[p]] + \
                [(p, fill[p][w], True) for p in fill if w in fill[p]]
        wsum = sum(wts[p] for p, _, _ in items)
        val = sum(wts[p] * (x - s["plant_offsets_log10"][p]) for p, x, _ in items) / wsum
        out["weeks"][w] = {"log10": val, "below_loq": False, "n_plants": len(items),
                           "provisional": any(f for _, _, f in items)}
    out["newest_sample"] = max(newest, max((w for wk in fill.values() for w in wk), default=newest))
    out["nyc_vs_cdc_shift"] = 10 ** shift
    return out


def dc_series(cdc_dc_rows, cfg):
    """DC weekly series: CDC WVAL for Blue Plains from comparable_since, mapped
    onto the frozen snapshot scale (checklist D-a, D-d, D-e). LAB CHANGE ALARM
    if CDC's `source` (testing lab) is not the frozen one; RESTATEMENT_CHECK if
    CDC's history changed beyond a rescaling."""
    c = cfg["cities"]["dc"]
    s = c["series"]
    start = date.fromisoformat(c["comparable_since"])
    out = {"city": "dc", "status": "ok", "message": None, "comparable_since": start, "weeks": {}}
    rows = [r for r in cdc_dc_rows if r.get("site") == s["site"] and r.get("pathogen_target") == "SARS-CoV-2"
            and _day(r["week_end"]) >= start]
    new_src = sorted({r.get("source") for r in rows if r.get("source") and r.get("source") not in s["sources"]})
    if new_src:
        first = min(_day(r["week_end"]) for r in rows if r.get("source") in new_src)
        out["status"] = "LAB_CHANGE_ALARM"
        out["message"] = (f"New testing lab for DC (Blue Plains) wastewater: {', '.join(new_src)}, from week ending "
                          f"{first}. Values are not comparable with the calibration. Threshold readout withheld until refit.")
        return out
    cur = {}
    for r in rows:
        v = _num(r.get("site_wval"))
        if v is not None and v > 0:
            cur[_day(r["week_end"])] = math.log10(v)
    snap = {date.fromisoformat(k): v for k, v in s["cdc_snapshot_log10_wval"].items()}
    m = restatement_map(snap, cur, cfg["cdc_restatement"])
    if not m["ok"]:
        out["status"] = "RESTATEMENT_CHECK"
        out["message"] = (f"CDC changed DC's WVAL history in a way that is not a simple rescaling (residual SD "
                          f"{m['resid_sd']:.3f}). Threshold readout withheld until checked or refitted.")
        return out
    for w, v in cur.items():
        out["weeks"][w] = {"log10": (v - m["alpha"]) / m["beta"], "below_loq": False, "n_plants": 1,
                           "provisional": False}
    out["newest_sample"] = max(cur) if cur else None
    out["cdc_restatement"] = m
    return out


# ---------------------------------------------------------------------------
# Readout
# ---------------------------------------------------------------------------

def _trailing2(weeks, w):
    """Trailing 2-week mean at week w (needs w and the week before); else None."""
    prev = w - timedelta(days=7)
    if w in weeks and prev in weeks:
        return (weeks[w]["log10"] + weeks[prev]["log10"]) / 2
    return None


def _trend(weeks, latest, cfg):
    """Trend label from MEASURED weekly values: mean of the latest 3 weeks vs
    the 3 before (log10), x1.25 cut-off. None if any of the 6 weeks is missing
    (gap), below LOQ (Berlin) or provisional (NYC, CDC-filled)."""
    t = cfg["trend"]
    k = t["window_weeks"]
    span = [latest - timedelta(days=7 * i) for i in range(2 * k)]
    if any(w not in weeks or weeks[w]["below_loq"] or weeks[w]["provisional"] for w in span):
        return None
    recent = _mean([weeks[w]["log10"] for w in span[:k]])
    before = _mean([weeks[w]["log10"] for w in span[k:]])
    ch = recent - before
    lim = math.log10(t["factor"])
    return t["labels"]["rising" if ch > lim else "falling" if ch < -lim else "steady"]


def readout(series, cfg, today=None):
    """Threshold readout for one city (checklists F1-F6, N-e, D-b, D-f).

    Value used: the trailing 2-week mean of the newest week. If the week before
    is missing, the single newest week is used and the readout says so (no
    trend, no projection). Berlin: projected to the current week with a damped
    trend (phi 0.7), unless the data are more than 3 weeks old ("data delayed",
    measured value shown). NYC/DC: last value carried forward, "data delayed"
    if more than 3 weeks old. One number drives the category, "1 in N", the
    percentile and the alert level condition.

    Returns a dict ready for the city JSON (see the handoff's display spec)."""
    today = today or date.today()
    city = series["city"]
    c = cfg["cities"][city]
    if series["status"] != "ok":
        return {"status": series["status"], "message": series["message"], "available": False}
    weeks = series["weeks"]
    th, edges = threshold_and_edges(cfg)
    conv = c["conversion"]

    def prev_of(x):
        return 10 ** (conv["a"] + conv["b"] * (x - conv["w_ref"]))

    latest = max(weeks)
    t2 = _trailing2(weeks, latest)
    gap_weeks = 0
    if t2 is None:                                 # previous week missing: single week (D-b)
        x = weeks[latest]["log10"]
        earlier = [w for w in weeks if w < latest]
        gap_weeks = ((latest - max(earlier)).days // 7 - 1) if earlier else None
    else:
        x = t2
    # Weeks between the newest data week and the current week.
    current = iso_monday(today) if c["week"] == "iso_monday_to_sunday" else us_saturday(today)
    h = (current - latest).days // 7
    delayed = h > c["projection"].get("max_weeks", c["projection"].get("delayed_after_weeks", 3))

    # Projection (Berlin only): T + (phi + ... + phi^h) * g, g = half the latest 2-week change
    # of the trailing series; g = 0 if a below-LOQ week is involved; none if delayed or single week.
    projected, x_now = False, x
    g = 0.0
    if c["projection"]["method"] == "damped_trend" and t2 is not None:
        t_back = _trailing2(weeks, latest - timedelta(days=14))
        span = [latest - timedelta(days=7 * i) for i in range(4)]
        if t_back is not None and not any(weeks.get(w, {}).get("below_loq", True) for w in span):
            g = (t2 - t_back) / 2
        if not delayed and h > 0:
            phi = c["projection"]["phi"]
            x_now = x + sum(phi ** k for k in range(1, h + 1)) * g
            projected = True
    p_now = prev_of(x_now)
    band = band_index(p_now, edges)

    # Alert level condition (F5 / N-e): at or above "Right around" now; Berlin also if the
    # damped projection reaches it within 3 more weeks. The app combines this with its
    # momentum (wave-starting) rule in Analytics.kt.
    p_future = p_now
    if projected or (c["projection"]["method"] == "damped_trend" and t2 is not None and not delayed):
        phi = c["projection"]["phi"]
        p_future = max(prev_of(x + sum(phi ** k for k in range(1, h + j + 1)) * g) for j in range(0, 4))
    alert_level_ok = max(p_now, p_future) >= edges[cfg["threshold"]["alert_band_index"] - 1]

    # Percentile among comparable-period trailing 2-week values (strictly below), and its start month.
    hist = [v for v in (_trailing2(weeks, w) for w in weeks) if v is not None]
    pct = 100.0 * sum(1 for v in hist if v < x_now) / len(hist) if hist else None
    since = series["comparable_since"]
    trend = None if t2 is None else _trend(weeks, latest, cfg)
    # App adaptation (2026-11-02): when the newest weeks are CDC fill-ins (NYC), the strict rule
    # would almost never show a trend. Fall back to the newest measured week and say which.
    trend_as_of = trend_as_of_label = None
    if trend is None and t2 is not None and weeks[latest]["provisional"]:
        measured = [w for w in weeks if not weeks[w]["provisional"]]
        if measured:
            m = max(measured)
            trend = _trend(weeks, m, cfg)
            if trend is not None:
                trend_as_of = str(m if c["week"] == "us_sunday_to_saturday" else m + timedelta(days=6))
                trend_as_of_label = date.fromisoformat(trend_as_of).strftime("%b ") + str(date.fromisoformat(trend_as_of).day)

    week_label = latest if c["week"] == "us_sunday_to_saturday" else latest + timedelta(days=6)
    return {
        "available": True, "status": "ok", "city": city,
        "category_index": band,
        "main_line": cfg["threshold"]["labels"][band] + (" (estimated for today)" if projected else ""),
        "short_label": cfg["threshold"]["short_labels"][band],
        "confidence_label": c["confidence_label"],
        "prevalence": p_now, "one_in_n": one_in_n(p_now, cfg),
        "trend": trend, "trend_as_of": trend_as_of,
        "percentile": None if pct is None else round(pct),
        "percentile_since": since.strftime("%B %Y"),
        "second_line": " · ".join(x for x in [one_in_n(p_now, cfg)[0].upper() + one_in_n(p_now, cfg)[1:]
                                              + " people infectious",
                                              trend + (f" (to {trend_as_of_label})" if trend and trend_as_of else "") if trend else None,
                                              None if pct is None else
                                              f"Higher than {round(pct)}% of weeks since {since.strftime('%B %Y')}"]
                                  if x),
        "as_of": str(series["newest_sample"]), "newest_week_end": str(week_label),
        "projected": projected, "weeks_behind": h, "data_delayed": delayed,
        "single_week_after_gap": t2 is None, "gap_weeks": gap_weeks,
        "provisional": weeks[latest]["provisional"],
        "alert_level_ok": alert_level_ok,
        "threshold_prevalence": th, "band_edges_prevalence": edges,
        "band_edges_wastewater": [conv["w_ref"] + (math.log10(e) - conv["a"]) / conv["b"] for e in edges],
    }


def chart_series(series, cfg):
    """Weekly points for the app's charts (F3/F4): week date, trailing 2-week
    wastewater value (linear units; the app's WeekPoint.value), that week's own
    value (WeekPoint.raw, the dots) and best-guess prevalence, comparable
    period only. Weeks without a trailing value are skipped."""
    conv = cfg["cities"][series["city"]]["conversion"]
    pts = []
    for w in sorted(series["weeks"]):
        t2 = _trailing2(series["weeks"], w)
        if t2 is None:
            continue
        pts.append({"week": str(w), "value": 10 ** t2, "raw": 10 ** series["weeks"][w]["log10"],
                    "prevalence": 10 ** (conv["a"] + conv["b"] * (t2 - conv["w_ref"])),
                    "provisional": series["weeks"][w]["provisional"]})
    return pts


def cdc_category_nyc(cdc_ny_rows, cfg, pathogen="covid", min_plants=10):
    """CDC category for NYC (checklist S2, N-b): median site WVAL of the 14
    plants in the latest CDC week with at least `min_plants` reporting, with
    CDC's current cut-offs from the config. Returns (category, week, n)."""
    target = {"covid": "SARS-CoV-2", "fluA": "Influenza A virus", "rsv": "RSV"}[pathogen]
    site_map = cfg["cities"]["nyc"]["series"]["cdc_site_map"]
    by_week = {}
    for r in cdc_ny_rows:
        v = _num(r.get("site_wval"))
        if r.get("pathogen_target") == target and r.get("site") in site_map and v is not None:
            by_week.setdefault(_day(r["week_end"]), []).append(v)
    ok = [w for w, v in by_week.items() if len(v) >= min_plants]
    if not ok:
        return None, None, 0
    w = max(ok)
    vals = sorted(by_week[w])
    n = len(vals)
    med = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2
    cuts = cfg["cdc_category_cutoffs_2026_08"][pathogen]
    names = ["Very Low", "Low", "Moderate", "High", "Very High"]
    return names[sum(1 for cut in cuts if med > cut)], str(w), n
