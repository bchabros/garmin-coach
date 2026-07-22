"""Coach report orchestrator (out of seam).

Loads coach thresholds from the DB, builds the digest, renders the two charts, and
writes the ``reports/{date}/`` artifacts. Reads only the finished DB - never Garmin.
The Markdown narrative (``report.md``) is written by the coach skill, not here.
"""

from __future__ import annotations

import datetime as _dt
import json
import pathlib
import sqlite3

from . import charts, digest, thresholds as _thresholds
from ..marts import snapshot


def read_thresholds(conn: sqlite3.Connection) -> dict[str, float]:
    """Read effective coach thresholds from defaults plus DB seed rows."""
    return _thresholds.read(conn)


def generate_report(
    conn: sqlite3.Connection,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    reports_dir: str = "./reports",
) -> pathlib.Path:
    """Build the digest + charts + snapshot and write them to ``reports/{today}/``.

    Emits ``digest.json``, the two charts, and (when the ``athlete_status`` mart is
    populated) ``snapshot.json`` - the current standing, read-only from the mart.

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

    rows = (
        digest.enrich_hrv_band(
            digest.read_mart(conn, dg["window"]["from"], dg["window"]["to"]), thresholds
        )
        if dg["window"]["from"]
        else []
    )

    out = pathlib.Path(reports_dir) / _dt.date.today().isoformat()
    out.mkdir(parents=True, exist_ok=True)

    charts.render_hrv_band(rows, out / "hrv_band.png")
    charts.render_acwr(rows, out / "acwr.png", thresholds)
    (out / "digest.json").write_text(json.dumps(dg, indent=2))

    # Emit the current standing beside the digest (read-only; features owns the mart).
    # It is a singleton, so it carries whether it belongs to this report's horizon.
    status = snapshot.read(conn)
    if status is not None:
        status["matches_horizon"] = digest.matches_horizon(
            status.get("computed_at"), dg["window"]["to"]
        )
        snapshot.write_json(status, out)
    return out
