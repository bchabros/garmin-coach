"""Phase 3 coach report orchestrator (out of seam).

Loads coach thresholds from the DB, builds the digest, renders the two charts, and
writes the ``reports/{date}/`` artifacts. Reads only the finished DB - never Garmin.
The Markdown narrative (``report.md``) is written by the coach skill, not here.
"""

from __future__ import annotations

import datetime as _dt
import json
import pathlib
import sqlite3

from . import charts, digest


def read_thresholds(conn: sqlite3.Connection) -> dict[str, float]:
    """Read coach_thresholds into a dict (authoritative over code defaults)."""
    return {key: value for key, value in conn.execute("SELECT key, value FROM coach_thresholds")}


def generate_report(
    conn: sqlite3.Connection,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    reports_dir: str = "./reports",
) -> pathlib.Path:
    """Build the digest + charts and write them to ``reports/{today}/``.

    Args:
        conn: Open SQLite connection with the mart populated.
        from_date: Window start (default: trailing 28 days).
        to_date: Window end (default: latest mart day).
        reports_dir: Root directory for dated report folders.

    Returns:
        The path to the created report folder.
    """
    thresholds = read_thresholds(conn)
    dg = digest.build_digest(conn, from_date=from_date, to_date=to_date, thresholds=thresholds)

    thr = digest.merge_thresholds(thresholds)
    rows = (
        digest.enrich_hrv_band(digest.read_mart(conn, dg["window"]["from"], dg["window"]["to"]), thr)
        if dg["window"]["from"]
        else []
    )

    out = pathlib.Path(reports_dir) / _dt.date.today().isoformat()
    out.mkdir(parents=True, exist_ok=True)

    charts.render_hrv_band(rows, out / "hrv_band.png")
    charts.render_acwr(rows, out / "acwr.png", thr)
    (out / "digest.json").write_text(json.dumps(dg, indent=2))
    return out
