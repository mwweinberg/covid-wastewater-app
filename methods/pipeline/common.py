"""
Shared helpers for the pipeline scripts.
"""

import subprocess
import time
from pathlib import Path

# Project root = parent of the pipeline/ folder.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def download_cached(url, path, refresh=False, max_age_h=24):
    """Download url to path unless a copy younger than max_age_h exists.

    Inputs:
      url        source URL
      path       local file (pathlib.Path); parent folders are created
      refresh    True = always download
      max_age_h  cached copy is reused if younger than this many hours
    Output: True if a download happened, False if the cached copy was used.

    Uses curl rather than Python's urllib: through the Claude Code sandbox
    proxy, urllib (HTTP/1.1) received truncated responses (see
    wastewater-threshold-handoff.md, Environment). --fail makes HTTP errors
    fatal; curl writes to a temporary ".part" file that replaces the cached
    copy only on success, so a failed download never leaves a broken file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = path.exists() and (time.time() - path.stat().st_mtime) < max_age_h * 3600
    if fresh and not refresh:
        return False
    tmp = path.with_suffix(path.suffix + ".part")
    subprocess.run(["curl", "-sS", "-L", "--fail", "--retry", "3", "-o", str(tmp), url], check=True)
    tmp.replace(path)
    return True


def iso_week_label(ts):
    """Format a date/Timestamp as its ISO week, e.g. "2026-W01"
    (same format as GrippeWeb's Kalenderwoche and data/sentinel_tab1.csv)."""
    y, w, _ = ts.isocalendar()
    return f"{y}-W{w:02d}"
