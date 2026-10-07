"""Complete plan import outcomes shared by terminal, chat, and nightly runs.

Markdown remains the plan of record. Accepted weeks survive a later file error;
their conflicts are reported and their marts are rebuilt immediately unless the
nightly orchestrator defers that pass until after synchronization.
"""

from __future__ import annotations

import pathlib
import sqlite3
from dataclasses import dataclass
from typing import Any

from .core import plan
from .marts import features
from .workouts import publish


@dataclass(frozen=True)
class PlanImportResult:
    """Accepted weeks, pushed workouts they invalidate, and the first file error."""

    weeks: list[str]
    invalidated_pushes: list[dict[str, Any]]
    error: str | None

    def as_data(self) -> dict[str, Any]:
        """Return the common plan-import response fields."""
        return {
            "weeks": self.weeks,
            "invalidated_pushes": self.invalidated_pushes,
            "error": self.error,
        }


def import_plans(
    conn: sqlite3.Connection,
    *,
    plans_dir: str | pathlib.Path,
    reports_dir: str | pathlib.Path = "reports",
    week: str | None = None,
    data_start_date: str,
    rebuild: bool = True,
) -> PlanImportResult:
    """Import plans and finish the consequences for every accepted week.

    Args:
        conn: Open SQLite connection with the schema bootstrapped.
        plans_dir: Directory of authored weekly Markdown plans.
        reports_dir: Root of the dated push receipts.
        week: Only import this Monday's plan when supplied.
        data_start_date: First real-data day for the mart rebuild.
        rebuild: Rebuild accepted weeks' marts immediately; the nightly orchestrator
            passes False because its single mart pass follows synchronization.

    Returns:
        Accepted weeks and their conflicts, even when a later file failed. A missing
        directory or no matching file is an empty outcome without a file error.

    Raises:
        Exception: A database write or mart rebuild failed; file errors are returned.
    """
    error = None
    try:
        weeks = plan.import_dir(conn, plans_dir, week=week)
    except plan.PlanParseError as exc:
        weeks, error = exc.imported_weeks, str(exc)
    conflicts = [
        conflict
        for imported_week in weeks
        for conflict in publish.invalidated_pushes(
            reports_dir, plan.planned_by_date(conn, imported_week)
        )
    ]
    if weeks and rebuild:
        features.rebuild_marts(conn, data_start_date=data_start_date)
    return PlanImportResult(weeks=weeks, invalidated_pushes=conflicts, error=error)
