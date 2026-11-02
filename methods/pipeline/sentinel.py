"""
Scrape weekly SARS-CoV-2 sentinel counts from RKI's ARE weekly reports.

Source: the "ARE-Wochenbericht" PDFs linked from
https://influenza.rki.de/Wochenberichte.aspx. Each report has a Tab. 1
("Virologische Surveillance im ARE-Praxis-Sentinel (NRZ fuer Influenzaviren)")
with, per calendar week, the number of sentinel samples submitted and the
number positive for each virus. We extract two rows:
  - "Anzahl eingesandter Proben"  -> samples submitted
  - "SARS-CoV-2"                  -> SARS-CoV-2 positives
These are NATIONAL counts (see wastewater-threshold-handoff.md, Data sources §3,
for why regional data is not used).

Each report repeats the previous 3-4 weeks, so every week appears in several
reports and later reports can revise earlier counts. We keep the value from the
MOST RECENT report for each week, and also write every observation to a second
file so revisions can be inspected.

Two table layouts exist (both handled):
  - normal:     [week, week, ..., "Gesamt ab 40. KW"]
  - transition: the first reports of a season (KW 40-42) also show the end of
                the previous season: [prev-season weeks..., "Gesamt <prev
                season>", current-season weeks..., "Gesamt ab 40. KW"]; the
                KW 40 report has no current-season total column.
Transition reports carry the last revisions of the previous season's final
weeks (e.g. KW 39/2024: 87 samples in report 2024-39, 113 in report 2024-41),
so they must not be skipped.

Outputs (paths relative to the project root by default):
  data/sentinel_tab1.csv              one row per ISO week (final values)
  data/sentinel_tab1_all_reports.csv  one row per (week, report) observation
  data/cache/sentinel_text/*.txt      extracted text of each report (cache;
                                      PDFs themselves are not kept)

Self-checks printed at the end:
  - every report parsed, and its last week column equals the report's own week
  - no ISO week missing between --check-from and the latest week
  - per season: the latest "Gesamt" total RKI published for that season vs the
    sum of our final weekly values (differences mean revisions to weeks no
    longer shown in any report, or a parsing problem)

Requires: Python 3.9+, `pdftotext` (Poppler) and `curl` on PATH. Standard
library only.

Usage:
  python3 pipeline/sentinel.py                 # fetch new reports, rebuild CSVs
  python3 pipeline/sentinel.py --refresh       # re-download every report
  python3 pipeline/sentinel.py --help
"""

import argparse
import csv
import re
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from datetime import date
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "https://influenza.rki.de/"
INDEX_URL = BASE_URL + "Wochenberichte.aspx"

# Report links on the index page look like
#   href="Wochenberichte/2025_2026/2026-01.pdf"
# Groups: season folder, report year, report week.
REPORT_LINK_RE = re.compile(r'href="Wochenberichte/(\d{4}_\d{4})/(\d{4})-(\d{2})\.pdf"')

# Project root = parent of this file's folder (pipeline/ -> project root).
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Week header in Tab. 1: "47. KW" (2023 reports also have "41.KW"), optionally
# followed by a year ("40. KW 2025"). With a year it is usually the second
# line of a "Gesamt ab 40. KW 2025" totals header, but in some transition
# reports it is a real week column; classify_columns() decides.
WEEK_HEADER_RE = re.compile(r"\b(\d{1,2})\.\s*KW\b(\s+\d{4})?")

# Totals column header ("Gesamt", "Gesamt ab").
GESAMT_RE = re.compile(r"Gesamt")

# Two header items on different lines whose horizontal centres are within this
# many characters belong to the same column (e.g. "Gesamt ab" above
# "40. KW 2025"). Week columns are >=7 characters apart.
SAME_COLUMN_TOL = 8

# A count in a table row: plain digits or German thousands format ("1.391").
# The lookarounds skip fragments of wrapped header text such as "/2025" that
# pdftotext sometimes places on a data line.
COUNT_RE = re.compile(r"(?<![\d./])(\d{1,3}(?:\.\d{3})+|\d+)(?![\d/])")

# "Stand 16.12.2025" in the table caption: the data cut-off date of the report.
STAND_RE = re.compile(r"Stand\s+(\d{1,2})\.(\d{1,2})\.(\d{4})")

# Pause between downloads, to be polite to RKI's server.
DOWNLOAD_PAUSE_S = 0.5


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def http_get(url, timeout=120):
    """Download url and return the response body as bytes.

    Uses the `curl` command rather than Python's urllib. Reason (found
    2026-11-01): through the Claude Code sandbox proxy, urllib (HTTP/1.1)
    reliably received a truncated index page (851,245 of 887,010 bytes, same
    cut-off every time), while curl (HTTP/2) received it complete.
    curl flags: --fail turns HTTP errors (404 etc.) into a non-zero exit;
    --retry retries transient network failures; -L follows redirects.
    Raises subprocess.CalledProcessError on failure, so a failed download never
    silently produces an empty or partial report.
    """
    result = subprocess.run(
        ["curl", "-sS", "-L", "--fail", "--retry", "3", "--max-time", str(timeout),
         "-A", "wastewater-threshold research script", url],
        check=True, capture_output=True,
    )
    return result.stdout


def list_reports(first_season):
    """Read the RKI index page and return the reports to process.

    Input:  first_season, e.g. "2023_2024"; earlier seasons are skipped.
    Output: list of dicts {season, year, week, url, key}, sorted by
            (year, week). 'key' (e.g. "2025_2026/2026-01") identifies the
            report in the outputs and names its cache file.
    """
    html = http_get(INDEX_URL).decode("utf-8", errors="replace")
    reports = {}
    for season, year, week in REPORT_LINK_RE.findall(html):
        if season < first_season:  # "YYYY_YYYY" strings sort chronologically
            continue
        key = f"{season}/{year}-{week}"
        reports[key] = {
            "season": season,
            "year": int(year),
            "week": int(week),
            "url": f"{BASE_URL}Wochenberichte/{season}/{year}-{week}.pdf",
            "key": key,
        }
    return sorted(reports.values(), key=lambda r: (r["year"], r["week"]))


def report_text(report, cache_dir, refresh):
    """Return the `pdftotext -layout` text of one report, using the cache.

    The PDF is downloaded to a temporary file, converted, and deleted; only
    the text is kept (cache_dir/<season>_<year>-<week>.txt). Published reports
    don't change, so a cached report is not re-downloaded unless refresh=True.
    Returns (text, downloaded) where downloaded tells the caller whether a
    network fetch happened (used to pace downloads).
    """
    cache_file = cache_dir / (report["key"].replace("/", "_") + ".txt")
    if cache_file.exists() and not refresh:
        return cache_file.read_text(encoding="utf-8"), False

    pdf_bytes = http_get(report["url"])
    # Sanity check: a real PDF starts with "%PDF" (an error page would not).
    if not pdf_bytes.startswith(b"%PDF"):
        raise ValueError("download is not a PDF")
    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = Path(tmp) / "report.pdf"
        txt_path = Path(tmp) / "report.txt"
        pdf_path.write_bytes(pdf_bytes)
        # -layout keeps table columns aligned on one line, which the row
        # parser relies on.
        subprocess.run(["pdftotext", "-layout", str(pdf_path), str(txt_path)], check=True)
        text = txt_path.read_text(encoding="utf-8", errors="replace")

    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(text, encoding="utf-8")
    return text, True


# ---------------------------------------------------------------------------
# Season helpers
# ---------------------------------------------------------------------------

def season_of_week(iso_year, iso_week):
    """Season folder name for an ISO week. RKI seasons run KW 40 to KW 39:
    KW 40/2024 .. KW 39/2025 is season "2024_2025"."""
    start = iso_year if iso_week >= 40 else iso_year - 1
    return f"{start}_{start + 1}"


def previous_season(season):
    """"2024_2025" -> "2023_2024"."""
    a, b = (int(x) for x in season.split("_"))
    return f"{a - 1}_{b - 1}"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_count(token):
    """Convert a table token like "1.391" (German thousands dot) to 1391."""
    return int(token.replace(".", ""))


def find_tab1(lines):
    """Return the lines of Tab. 1 (caption up to the first footnote line).

    The caption starts with "Tab. 1:" at the beginning of a line; prose
    mentions such as "(Tab. 1)" don't match. To avoid picking up an unrelated
    table, the caption (its first two lines) must mention "Atemwegsviren".
    The table ends at the first footnote line (starting with "*"), or after
    40 lines as a safety stop.
    Returns None if no matching table is found.
    """
    for i, line in enumerate(lines):
        if not line.strip().startswith("Tab. 1:"):
            continue
        caption = " ".join(lines[i:i + 2])
        if "Atemwegsviren" not in caption:
            continue
        block = [lines[i]]
        for nxt in lines[i + 1:i + 40]:
            if nxt.strip().startswith("*"):
                break
            block.append(nxt)
        return block
    return None


def classify_columns(header_lines):
    """Build the ordered column list of Tab. 1 from its header lines.

    Input:  the lines between the caption and the first data row.
    Output: list of ("week", week_number) or ("total", None), left to right.

    Method: collect every "Gesamt" and every "NN. KW" with its horizontal
    centre and line index. A "NN. KW" item that sits on a different line from
    a "Gesamt" item but in the same horizontal position (within
    SAME_COLUMN_TOL) is part of that totals header (e.g. "Gesamt ab" over
    "40. KW 2025"); every other "NN. KW" item is a week column. Columns are
    then ordered by horizontal position. Data rows are later matched to this
    list by ORDER, not position, because in some reports the header is
    printed more tightly than the numbers below it.
    """
    gesamt, weeks = [], []
    for li, line in enumerate(header_lines):
        for m in GESAMT_RE.finditer(line):
            gesamt.append((li, (m.start() + m.end()) / 2))
        for m in WEEK_HEADER_RE.finditer(line):
            weeks.append((li, (m.start() + m.end()) / 2, int(m.group(1))))

    columns = [(x, ("total", None)) for _, x in gesamt]
    for li, x, wk in weeks:
        part_of_total = any(gli != li and abs(gx - x) <= SAME_COLUMN_TOL for gli, gx in gesamt)
        if not part_of_total:
            columns.append((x, ("week", wk)))
    return [c for _, c in sorted(columns, key=lambda t: t[0])]


def is_samples_label(lines, i):
    """True if line i starts the 'Anzahl eingesandter Proben' row.
    In some reports the label wraps ("Anzahl" / "eingesandter" / "Proben"),
    so a line starting with "Anzahl" followed by one starting with
    "eingesandter" also counts."""
    s = lines[i].strip()
    if s.startswith("Anzahl eingesandter Proben"):
        return True
    if s.startswith("Anzahl") and i + 1 < len(lines) and lines[i + 1].strip().startswith("eingesandter"):
        return True
    return False


def row_numbers(lines, i, label_re, n_expected):
    """Read the counts of the data row starting at line i.

    The label is removed first (the SARS-CoV-2 label contains a digit).
    If the line holds fewer than n_expected numbers, the next line is also
    read when it contains no letters: in one layout pdftotext moves the last
    two numbers of the row onto their own line.
    Returns the list of ints (may differ in length from n_expected; the
    caller decides whether that's an error).
    """
    text = label_re.sub("", lines[i].strip(), count=1)
    tokens = COUNT_RE.findall(text)
    if len(tokens) < n_expected and i + 1 < len(lines):
        nxt = lines[i + 1]
        if nxt.strip() and not re.search(r"[A-Za-z]", nxt):
            tokens += COUNT_RE.findall(nxt)
    return [parse_count(t) for t in tokens]


def parse_report(text, report):
    """Extract the Tab. 1 counts from one report's text.

    Output: dict with
      stand       data cut-off date (datetime.date) or None if not found
      weeks       list of (iso_year, iso_week) for the week columns
      samples     list of ints, one per week column
      sars_cov_2  list of ints, one per week column
      totals      {season: (samples_total, sars_cov_2_total)} for each
                  "Gesamt" column (current and, in transition reports,
                  previous season)
      warnings    list of strings (non-fatal oddities worth reporting)
    Raises ValueError if the table or a required row can't be read.
    """
    block = find_tab1(text.splitlines())
    if block is None:
        raise ValueError("Tab. 1 not found")
    warnings = []

    # Caption: ends at the line with "Stand <date>" (1-2 lines). The caption
    # itself contains "40. KW 2025" etc., so it must not be read as header.
    caption_end = next((k for k in range(min(3, len(block))) if STAND_RE.search(block[k])), None)
    if caption_end is None:
        warnings.append("no 'Stand' date in caption")
        caption_end = 1
        stand = None
    else:
        m = STAND_RE.search(block[caption_end])
        stand = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))

    # Header = lines between the caption and the samples row.
    samples_i = next((k for k in range(caption_end + 1, len(block)) if is_samples_label(block, k)), None)
    if samples_i is None:
        raise ValueError("row 'samples' not found")
    columns = classify_columns(block[caption_end + 1:samples_i])
    week_numbers = [wk for kind, wk in columns if kind == "week"]
    if not week_numbers:
        raise ValueError("no week columns in header")
    if week_numbers[-1] != report["week"]:
        warnings.append(f"last week column is KW {week_numbers[-1]}, report is KW {report['week']}")

    # Data rows, matched to the columns by order.
    sc2_i = next((k for k in range(samples_i + 1, len(block)) if block[k].strip().startswith("SARS-CoV-2")), None)
    if sc2_i is None:
        raise ValueError("row 'sars_cov_2' not found")
    rows = {
        "samples": row_numbers(block, samples_i, re.compile(r"^\D*"), len(columns)),
        "sars_cov_2": row_numbers(block, sc2_i, re.compile(r"^SARS-CoV-2\*?"), len(columns)),
    }
    for name, vals in rows.items():
        if len(vals) != len(columns):
            raise ValueError(f"row '{name}': {len(vals)} numbers for {len(columns)} columns "
                             f"({sum(k == 'week' for k, _ in columns)} weeks + "
                             f"{sum(k == 'total' for k, _ in columns)} totals)")

    # Split values into week columns and totals columns.
    weeks = assign_years(week_numbers, report["year"], report["week"])
    samples = [v for (kind, _), v in zip(columns, rows["samples"]) if kind == "week"]
    sc2 = [v for (kind, _), v in zip(columns, rows["sars_cov_2"]) if kind == "week"]

    # Which season each "Gesamt" column belongs to: a total placed before the
    # first current-season week (KW >= 40 in an early-season report) is the
    # previous season's; otherwise it is the report's own season.
    totals = {}
    seen_current_week = False
    for (kind, wk), s, p in zip(columns, rows["samples"], rows["sars_cov_2"]):
        if kind == "week":
            # In a transition report, current-season weeks start at KW 40.
            if not (report["week"] >= 40 and wk < 40):
                seen_current_week = True
        else:
            season = report["season"] if seen_current_week else previous_season(report["season"])
            totals[season] = (s, p)

    # Plausibility: a season total can't be smaller than the weeks of that
    # season shown in the same report. Catches wrapped header text read as a
    # number (seen in report 2023-50: "2023" from "40. KW 2023" landed on the
    # data line). A bad total only affects the season-total self-check, not
    # the weekly values, so warn rather than fail.
    for season, (ts, tp) in totals.items():
        shown = [(s, p) for (y, w), s, p in zip(weeks, samples, sc2) if season_of_week(y, w) == season]
        if ts < sum(s for s, _ in shown) or tp < sum(p for _, p in shown):
            warnings.append(f"implausible Gesamt for {season}: {ts} samples / {tp} SARS-CoV-2 "
                            f"is below the sum of the weeks shown")

    return {"stand": stand, "weeks": weeks, "samples": samples, "sars_cov_2": sc2,
            "totals": totals, "warnings": warnings}


def assign_years(week_numbers, report_year, report_week):
    """Turn Tab. 1 week numbers into (iso_year, iso_week) pairs.

    The columns run oldest -> newest and end at the report's own week. Walking
    backwards from the report week, a jump UP in week number means we crossed
    into the previous year (e.g. columns 50, 51, 52, 1 in report 2026-01).
    """
    out = []
    year = report_year
    prev = None
    for wk in reversed(week_numbers):
        if prev is not None and wk > prev:
            year -= 1
        out.append((year, wk))
        prev = wk
    return list(reversed(out))


# ---------------------------------------------------------------------------
# Combining reports
# ---------------------------------------------------------------------------

def iso_week_label(year, week):
    """Format (2026, 1) as "2026-W01" (same format as GrippeWeb's Kalenderwoche)."""
    return f"{year}-W{week:02d}"


def combine(parsed):
    """Merge per-report observations into one final value per week.

    Input:  list of (report, parse_result) sorted oldest report first.
    Output: (final, all_obs)
      all_obs  list of dicts, one per (week, report) observation
      final    {(year, week): dict} using the most recent report's value, plus
               n_reports (how many reports showed the week) and revised
               (True if the counts differed between reports)
    """
    all_obs = []
    by_week = defaultdict(list)
    for report, res in parsed:
        for (y, w), s, p in zip(res["weeks"], res["samples"], res["sars_cov_2"]):
            obs = {
                "iso_week": iso_week_label(y, w),
                "week_monday": date.fromisocalendar(y, w, 1).isoformat(),
                "season": season_of_week(y, w),
                "samples": s,
                "sars_cov_2_positive": p,
                "source_report": report["key"],
                "report_stand": res["stand"].isoformat() if res["stand"] else "",
            }
            all_obs.append(obs)
            by_week[(y, w)].append(obs)

    final = {}
    for yw, obs_list in by_week.items():
        latest = obs_list[-1]  # reports were processed oldest first
        distinct = {(o["samples"], o["sars_cov_2_positive"]) for o in obs_list}
        final[yw] = dict(latest, n_reports=len(obs_list), revised=len(distinct) > 1)
    return final, all_obs


def write_csv(path, rows, fields):
    """Write a list of dicts to CSV with the given column order."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


# ---------------------------------------------------------------------------
# Self-checks
# ---------------------------------------------------------------------------

def check_completeness(final, check_from):
    """Return the ISO weeks missing between check_from ("YYYY-Www") and the
    latest week we have."""
    y0, w0 = check_from.split("-W")
    start = date.fromisocalendar(int(y0), int(w0), 1)
    latest = max(date.fromisocalendar(y, w, 1) for (y, w) in final)
    have = {date.fromisocalendar(y, w, 1) for (y, w) in final}
    missing = []
    d = start
    while d <= latest:
        if d not in have:
            iy, iw, _ = d.isocalendar()
            missing.append(iso_week_label(iy, iw))
        d = date.fromordinal(d.toordinal() + 7)
    return missing


def check_season_totals(parsed, final):
    """Compare, per season, the latest "Gesamt" totals RKI published for that
    season (from whichever report printed it last, including transition
    reports) with the sum of our final weekly values for that season.

    Returns a list of (season, report_key, gesamt_samples, our_samples,
    gesamt_sc2, our_sc2, complete) where complete is False if our data does
    not start at KW 40 of that season (sum not comparable).
    """
    latest_total = {}
    for report, res in parsed:
        for season, tot in res["totals"].items():
            latest_total[season] = (report["key"], tot)  # later reports overwrite
    sums = defaultdict(lambda: [0, 0])
    weeks_by_season = defaultdict(set)
    for (y, w), v in final.items():
        sums[v["season"]][0] += v["samples"]
        sums[v["season"]][1] += v["sars_cov_2_positive"]
        weeks_by_season[v["season"]].add(w)
    out = []
    for season, (key, (gs, gp)) in sorted(latest_total.items()):
        complete = 40 in weeks_by_season[season]
        out.append((season, key, gs, sums[season][0], gp, sums[season][1], complete))
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--first-season", default="2023_2024",
                    help="first season folder to include (default 2023_2024, which covers April 2024)")
    ap.add_argument("--check-from", default="2024-W14",
                    help="first ISO week that must be present (default 2024-W14, start of the current Berlin lab period)")
    ap.add_argument("--out", default=str(PROJECT_ROOT / "data" / "sentinel_tab1.csv"))
    ap.add_argument("--out-all", default=str(PROJECT_ROOT / "data" / "sentinel_tab1_all_reports.csv"))
    ap.add_argument("--cache", default=str(PROJECT_ROOT / "data" / "cache" / "sentinel_text"))
    ap.add_argument("--refresh", action="store_true", help="re-download all reports, ignoring the cache")
    args = ap.parse_args()

    cache_dir = Path(args.cache)

    # 1. Which reports exist.
    reports = list_reports(args.first_season)
    print(f"{len(reports)} reports listed from season {args.first_season} on "
          f"({reports[0]['key']} .. {reports[-1]['key']})")

    # 2. Fetch (or read from cache) and parse each report.
    parsed, failures, n_downloaded = [], [], 0
    for report in reports:
        try:
            text, downloaded = report_text(report, cache_dir, args.refresh)
            if downloaded:
                n_downloaded += 1
                time.sleep(DOWNLOAD_PAUSE_S)
            res = parse_report(text, report)
        except Exception as e:  # report and continue; failures are listed at the end
            failures.append((report["key"], str(e)))
            continue
        for w in res["warnings"]:
            print(f"  warning {report['key']}: {w}")
        parsed.append((report, res))
    print(f"downloaded {n_downloaded}, parsed {len(parsed)}, failed {len(failures)}")
    for key, err in failures:
        print(f"  FAILED {key}: {err}")
    if not parsed:
        sys.exit("nothing parsed")

    # 3. Merge into one value per week and write outputs.
    final, all_obs = combine(parsed)
    final_rows = [final[k] for k in sorted(final)]
    write_csv(Path(args.out), final_rows,
              ["iso_week", "week_monday", "season", "samples", "sars_cov_2_positive",
               "source_report", "report_stand", "n_reports", "revised"])
    write_csv(Path(args.out_all), all_obs,
              ["iso_week", "week_monday", "season", "samples", "sars_cov_2_positive",
               "source_report", "report_stand"])
    print(f"wrote {len(final_rows)} weeks ({final_rows[0]['iso_week']} .. {final_rows[-1]['iso_week']}) "
          f"to {args.out}")

    # 4. Self-checks.
    missing = check_completeness(final, args.check_from)
    print(f"missing weeks since {args.check_from}: {missing if missing else 'none'}")

    revised = [r for r in final_rows if r["revised"]]
    print(f"weeks revised between reports: {len(revised)} of {len(final_rows)}")

    print("season totals (latest published Gesamt vs sum of final weekly values):")
    print("  season     from report          samples Gesamt/ours   SARS-CoV-2 Gesamt/ours")
    for season, key, gs, os_, gp, op, complete in check_season_totals(parsed, final):
        if not complete:
            flag = "   (our data starts after KW 40; not comparable)"
        elif gs == os_ and gp == op:
            flag = ""
        else:
            flag = "   <- differs"
        print(f"  {season}  {key:20s} {gs:7d} / {os_:<7d}        {gp:5d} / {op:<5d}{flag}")


if __name__ == "__main__":
    main()
