"""
Weekly reference prevalence for New York City and Washington DC, from local
COVID-19 hospital admissions (US counterpart of pipeline/anchor.py +
pipeline/prevalence.py; user decisions 2026-11-01, see
wastewater-threshold-handoff.md, Validation log "US reference for the
threshold readout").

Idea, with the 2025/26 numbers as an example:
  In Berlin, the method's infections (GrippeWeb x sentinel, / symptomatic
  fraction) and Berlin's notified COVID hospitalisations give the share of
  infections that end in hospital (infection hospitalisation risk, IHR), per
  season: 0.240% in 2025/26. It matched England's random-sample survey in
  winter 2023/24 (0.441% vs 0.442%). Decision A: apply Berlin's IHR for the
  same season to US admissions, so all cities are on one scale:
      infections per week = admissions per week / IHR(season)
      prevalence          = infections per week (fraction) * days_infectious / 7
  e.g. 63 admissions per 100k in a season / 0.240% = 26% of people infected.

Admissions sources:
  NYC: New York State HERDS hospital survey, facility level, hospitals in the
       five NYC counties (health.data.ny.gov): daily file jw46-jpb7 to
       2025-10-06 (reports Mon-Fri; Monday's report carries the weekend),
       weekly file vgyq-b7tb from week ending 2025-10-04. Denominator: NYC
       population = sum of the 14 sewershed populations (NYC Open Data).
       Chosen over NHSN New York State because it is local and tracks NYC
       wastewater better (r 0.77-0.79 vs 0.55-0.62, Mar 2024-Sep 2025).
  DC:  NHSN weekly admissions (data.cdc.gov ua7e-t2fy) for DC, Maryland and
       Virginia, each scaled up for non-reporting hospitals and divided by
       its population, then combined with Blue Plains capacity shares (2012
       Intermunicipal Agreement, Table 4-A: DC 152.5, MD 169.7, VA 47.8 of
       370 MGD). The DC share uses the combined MD+VA rate, because DC's own
       hospitals admit many suburban patients (see dc_area_rate). User
       decisions 2026-11-01.
  Both: weeks Sunday-Saturday, labelled by the Saturday (week_end).

IHR seasons: RKI seasons (ISO week 40 to week 39). A US week belongs to the
season of the ISO week containing its Wednesday. Seasons not yet complete use
the latest complete season's IHR.

Outputs (data/):
  ihr_by_season.csv          Berlin-method infections, Berlin and Germany
                             notified hospitalisations, implied IHR per season
  us_reference_weekly.csv    per city and week: admissions, per 100k, IHR used,
                             infections per 100k, prevalence
Raw downloads in data/raw/.

Usage: python3 pipeline/us_reference.py [--refresh]
"""

import argparse
import json
from urllib.parse import urlencode

import numpy as np
import pandas as pd

from common import PROJECT_ROOT, download_cached

RAW = PROJECT_ROOT / "data" / "raw"
NYC_COUNTIES = ["BRONX", "KINGS", "NEW YORK", "QUEENS", "RICHMOND"]
NYS_DAILY = "https://health.data.ny.gov/resource/jw46-jpb7.csv?" + urlencode({
    "$where": "upper(facility_county) in ('" + "','".join(NYC_COUNTIES) + "') AND as_of_date >= '2022-10-01'",
    # total_new_admissions_reported = newly admitted + positive after admission;
    # the weekly file's total_new_covid_19_admissions counts the same thing
    # (overlap week ending 2025-10-04: 177 vs 180). patients_newly_admitted
    # alone (138 that week) leaves out patients who tested positive after
    # admission and created a definition break at the file switch (review
    # finding, 2026-11-01).
    "$select": "as_of_date,facility_pfi,facility_county,patients_newly_admitted,total_new_admissions_reported",
    "$limit": 1000000})
NYS_WEEKLY = "https://health.data.ny.gov/resource/vgyq-b7tb.csv?" + urlencode({
    "$where": "upper(facility_county) in ('" + "','".join(NYC_COUNTIES) + "')",
    "$select": "as_of_date,facility_pfi,facility_county,total_new_covid_19_admissions", "$limit": 1000000})
NHSN = "https://data.cdc.gov/resource/ua7e-t2fy.csv?" + urlencode({
    "$where": "jurisdiction in ('DC','MD','VA','NY')",
    "$select": "weekendingdate,jurisdiction,totalconfc19newadm,totalconfc19newadmperchosprep", "$limit": 100000})
RKI_HOSP = ("https://raw.githubusercontent.com/robert-koch-institut/COVID-19-Hospitalisierungen_in_Deutschland/"
            "main/Aktuell_Deutschland_COVID-19-Hospitalisierungen.csv")

# Blue Plains allocated flow capacity (MGD), 2012 IMA Table 4-A. DC's own
# allocation only (its non-party users in VA/MD are counted under VA/MD).
BLUE_PLAINS_MGD = {"DC": 152.5, "MD": 169.6 + 0.07 + 0.03, "VA": 31.0 + 13.8 + 1.5 + 1.5}
# Resident populations, approximate (US Census Bureau Vintage 2024 estimates;
# not re-verified in this project). Only per-capita rates depend on them.
STATE_POP = {"DC": 702_250, "MD": 6_263_220, "VA": 8_811_195}
MIN_REPORTING_PCT = 50   # NHSN weeks with fewer hospitals reporting are dropped


def season_of_date(d):
    """RKI season label ("2025_2026") of the ISO week containing date d."""
    y, w, _ = d.isocalendar()
    start = y if w >= 40 else y - 1
    return f"{start}_{start + 1}"


def fetch(refresh):
    """Download or reuse all inputs. Returns dict name -> path."""
    files = {"nys_daily": (NYS_DAILY, RAW / "nys_herds_daily_nyc.csv"),
             "nys_weekly": (NYS_WEEKLY, RAW / "nys_herds_weekly_nyc.csv"),
             "nhsn": (NHSN, RAW / "nhsn_hrd_dc_md_va_ny.csv"),
             "rki_hosp": (RKI_HOSP, RAW / "rki_covid19_hospitalisierungen.csv")}
    for name, (url, path) in files.items():
        print(f"{name}: {'downloaded' if download_cached(url, path, refresh) else 'cached'} ({path.name})")
    return {k: v[1] for k, v in files.items()}


# ---------------------------------------------------------------------------
# Berlin infection hospitalisation risk per season
# ---------------------------------------------------------------------------

def berlin_ihr(rki_path):
    """IHR per RKI season = notified hospitalisations / Berlin-method infections.

    Hospitalisations: RKI 7-day sums by report date, all ages; the Sunday value
    covers that ISO week (Mon-Sun). Infections: symptomatic incidence from
    data/anchor_weekly.csv divided by the symptomatic fraction
    (data/prevalence_params.json). Germany-wide values are kept for comparison.
    Returns DataFrame indexed by season with weeks, complete flag, sums, IHRs.
    """
    pp = json.loads((PROJECT_ROOT / "data" / "prevalence_params.json").read_text())
    a = pd.read_csv(PROJECT_ROOT / "data" / "anchor_weekly.csv", parse_dates=["week_monday"])
    a["infections_per100k"] = a["incidence_per100k"] / pp["symptomatic_fraction"]
    h = pd.read_csv(rki_path, parse_dates=["Datum"], dtype={"Bundesland_Id": str})
    h = h[(h["Altersgruppe"] == "00+") & (h["Datum"].dt.weekday == 6)]
    h = h.assign(week_monday=h["Datum"] - pd.Timedelta(days=6))
    hw = h.pivot_table(index="week_monday", columns="Bundesland", values="7T_Hospitalisierung_Inzidenz")
    t = a.set_index("week_monday")[["iso_week", "infections_per100k"]].join(
        hw[["Berlin", "Bundesgebiet"]].rename(columns={"Berlin": "hosp_berlin_per100k", "Bundesgebiet": "hosp_germany_per100k"}),
        how="inner").dropna()
    t["season"] = [season_of_date(d) for d in t.index]
    s = t.groupby("season").agg(weeks=("iso_week", "size"), last_week=("iso_week", "max"),
                                infections_per100k=("infections_per100k", "sum"),
                                hosp_berlin_per100k=("hosp_berlin_per100k", "sum"),
                                hosp_germany_per100k=("hosp_germany_per100k", "sum"))
    s["ihr_berlin"] = s["hosp_berlin_per100k"] / s["infections_per100k"]
    s["ihr_germany"] = s["hosp_germany_per100k"] / s["infections_per100k"]
    # Complete = data reach the season's ISO week 39 and cover most of it.
    s["complete"] = [(r.last_week >= f"{idx[5:]}-W39") and r.weeks >= 50 for idx, r in s.iterrows()]
    return s, pp["days_infectious"]


def ihr_lookup(seasons, ihr):
    """IHR to use for each US season label: the season's own Berlin IHR if that
    season is complete, else the latest complete season's."""
    done = ihr[ihr["complete"]]
    latest = done.index.max()
    return [done.loc[s, "ihr_berlin"] if s in done.index else done.loc[latest, "ihr_berlin"] for s in seasons], \
           [s if s in done.index else latest for s in seasons]


# ---------------------------------------------------------------------------
# US admissions
# ---------------------------------------------------------------------------

def nyc_admissions(daily_path, weekly_path):
    """Weekly NYC hospital admissions (HERDS), one definition throughout:
    patients NEWLY ADMITTED with COVID (user decision 2026-11-01), i.e.
    excluding patients who only tested positive after admission. Reason: on
    the wider "all admissions" definition, NYC's 2023/24 season implies 109%
    of people infected (impossible); on "newly admitted", 76%.

    Daily file: patients_newly_admitted, summed per Sunday-Saturday week
    (weeks with < 4 report days dropped). Weekly file (from its first week):
    it only has all admissions (total_new_covid_19_admissions), so it is
    scaled by the share "newly admitted / all admissions" measured in the
    daily file over the last 52 complete weeks before the switch.
    Returns (Series indexed by week_end, the scaling share used)."""
    d = pd.read_csv(daily_path, parse_dates=["as_of_date"])
    d["week_end"] = d["as_of_date"] + pd.to_timedelta((5 - d["as_of_date"].dt.weekday) % 7, unit="D")
    dw = d.groupby("week_end").agg(adm=("patients_newly_admitted", "sum"),
                                   total=("total_new_admissions_reported", "sum"), days=("as_of_date", "nunique"))
    w = pd.read_csv(weekly_path, parse_dates=["as_of_date"])
    ww = w.groupby("as_of_date")["total_new_covid_19_admissions"].sum()
    daily_part = dw.loc[(dw.index < ww.index.min()) & (dw["days"] >= 4)]
    last_year = daily_part.tail(52)
    share = float(last_year["adm"].sum() / last_year["total"].sum())
    return pd.concat([daily_part["adm"], ww * share]).sort_index().rename("admissions"), share


def dc_area_rate(nhsn_path):
    """Blue Plains-weighted admission rate per 100k: for DC, MD and VA,
    admissions / (share of hospitals reporting) / population, combined with
    capacity shares. Returns DataFrame indexed by week_end.

    DC's own hospital rate is NOT used for the DC share (user decision
    2026-11-01): DC hospitals also admit many Maryland and Virginia residents,
    so admissions per DC resident run 1.4-1.6x the neighbours' and would
    inflate the area rate by ~20%. The DC share uses the combined MD+VA rate
    (admissions summed over both states / their combined population) instead.
    DC's own rate is kept as a column for reference."""
    n = pd.read_csv(nhsn_path, parse_dates=["weekendingdate"])
    n = n[n["jurisdiction"].isin(STATE_POP)]
    n = n[n["totalconfc19newadmperchosprep"] >= MIN_REPORTING_PCT]
    n["adj_adm"] = n["totalconfc19newadm"] / (n["totalconfc19newadmperchosprep"] / 100)
    n["rate"] = n["adj_adm"] / n["jurisdiction"].map(STATE_POP) * 1e5
    wide = n.pivot(index="weekendingdate", columns="jurisdiction", values="rate").dropna()
    adm = n.pivot(index="weekendingdate", columns="jurisdiction", values="adj_adm").reindex(wide.index)
    total = sum(BLUE_PLAINS_MGD.values())
    shares = {k: v / total for k, v in BLUE_PLAINS_MGD.items()}
    out = pd.DataFrame({f"rate_{k}": wide[k] for k in STATE_POP})
    out["rate_MD_VA"] = (adm["MD"] + adm["VA"]) / (STATE_POP["MD"] + STATE_POP["VA"]) * 1e5
    out["admissions_per100k"] = (shares["DC"] * out["rate_MD_VA"] + shares["MD"] * wide["MD"]
                                 + shares["VA"] * wide["VA"])
    out.index.name = "week_end"
    return out, shares


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    paths = fetch(args.refresh)

    # 1. Berlin IHR per season.
    ihr, days_inf = berlin_ihr(paths["rki_hosp"])
    ihr.to_csv(PROJECT_ROOT / "data" / "ihr_by_season.csv", float_format="%.6g")
    print("Berlin-method IHR by season:")
    print((ihr.assign(ihr_berlin_pct=100 * ihr["ihr_berlin"], ihr_germany_pct=100 * ihr["ihr_germany"])
           [["weeks", "complete", "infections_per100k", "hosp_berlin_per100k", "ihr_berlin_pct", "ihr_germany_pct"]]
           .round(3).to_string()))

    # 2. Local admissions per 100k.
    nyc_pop = float(sum(json.loads((PROJECT_ROOT / "data" / "us_wastewater_params.json").read_text())
                        ["nyc"]["plant_weights_population"].values()))
    nyc, newly_share = nyc_admissions(paths["nys_daily"], paths["nys_weekly"])
    nyc = nyc.to_frame()
    print(f"NYC: weekly HERDS file scaled by {newly_share:.3f} (newly admitted / all admissions, daily file, last 52 weeks)")
    nyc["admissions_per100k"] = nyc["admissions"] / nyc_pop * 1e5
    dc, shares = dc_area_rate(paths["nhsn"])
    print(f"NYC population (sum of sewersheds): {nyc_pop:,.0f}; DC-area capacity shares: "
          + ", ".join(f"{k} {v:.1%}" for k, v in shares.items()))

    # 3. Infections and prevalence.
    frames = []
    for city, t in [("nyc", nyc), ("dc", dc)]:
        # Only weeks from the first complete IHR season on (2023/24): earlier
        # seasons have no Berlin IHR of their own, and borrowing a later one
        # gives meaningless levels. Both cities' comparable wastewater periods
        # start later anyway (NYC 2023-04, DC 2024-06).
        first = ihr[ihr["complete"]].index.min()
        t = t[[season_of_date(d - pd.Timedelta(days=3)) >= first for d in t.index]].copy()
        t["season"] = [season_of_date(d - pd.Timedelta(days=3)) for d in t.index]   # Wednesday of the week
        t["ihr"], t["ihr_season_used"] = ihr_lookup(t["season"], ihr)
        t["infections_per100k"] = t["admissions_per100k"] / t["ihr"]
        t["prevalence"] = t["infections_per100k"] / 1e5 * days_inf / 7
        t.insert(0, "city", city)
        frames.append(t.reset_index().rename(columns={"index": "week_end"}))
        by = t.groupby("season").agg(weeks=("prevalence", "size"), adm_per100k=("admissions_per100k", "sum"),
                                     infected_pct=("infections_per100k", lambda x: x.sum() / 1000),
                                     peak_prev_pct=("prevalence", lambda x: 100 * x.max()))
        print(f"{city.upper()} by season:\n{by.round(2).to_string()}")
    out = pd.concat(frames, ignore_index=True)
    out.to_csv(PROJECT_ROOT / "data" / "us_reference_weekly.csv", index=False, float_format="%.6g")
    print(f"wrote ihr_by_season.csv and us_reference_weekly.csv ({len(out)} rows)")


if __name__ == "__main__":
    main()
