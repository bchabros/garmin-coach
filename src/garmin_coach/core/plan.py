"""Weekly plan of record: parse, cache, and resolve per-week plan overrides (issue #21).

The athlete authors one ``plans/<monday>_week.md`` per week; its table carries the
engine's intent vocabulary. This module ingests those files into ``plan_week`` (the
DB is a derived cache - the Markdown stays the source of record) and resolves every
planned-intent read: the authored week when present, else the static
``plan_template`` fallback. Parse errors raise ``PlanParseError`` - a silently wrong
plan is exactly the failure this module exists to prevent.
"""

from __future__ import annotations

import datetime as _dt
import pathlib
import re
import sqlite3

INTENTS = ("rest", "easy", "tempo", "strength", "hyrox", "crossfit", "quality")

# Planned intents name the session the athlete meant; the mart can only ever
# observe what the load says. ``_actual_intent`` classifies a finished day into
# the four MEASURABLE classes below - it reads load numbers, so it cannot tell a
# crossfit session from a hyrox one. Adherence therefore compares classes, not
# raw intents: a planned crossfit day executed hard is a match, not a divergence.
# Without this, every intent outside {rest, easy, quality} would score as a
# permanent mismatch. See docs/glossary.md and docs/adr/0015.
_INTENT_CLASS = {
    "rest": "rest",
    "easy": "easy",
    "tempo": "quality",
    "strength": "strength",
    "hyrox": "quality",
    "crossfit": "quality",
    "quality": "quality",
}

_FILENAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_week\.md$")
_DAY_ABBREVS = ("Pon", "Wt", "Śr", "Czw", "Pt", "Sob", "Nd")


def intent_class(intent: str | None) -> str | None:
    """Collapse a planned intent to the measurable class adherence compares on.

    Args:
        intent: Any value from ``INTENTS``, or None.

    Returns:
        ``rest`` | ``easy`` | ``quality`` | ``strength``, or None for None/unknown.
    """
    if intent is None:
        return None
    return _INTENT_CLASS.get(intent)


class PlanParseError(ValueError):
    """A plan file violates the contract; the import must fail loudly."""


def parse_week(text: str, week_start: str) -> list[dict]:
    """Parse one plan file's table into ``plan_week`` rows.

    Args:
        text: The full Markdown text of a ``plans/<monday>_week.md`` file.
        week_start: The Monday named by the filename (YYYY-MM-DD).

    Returns:
        Seven row dicts (``week_start``, ``dow``, ``planned``, ``intent``) in
        Monday-Sunday order.

    Raises:
        PlanParseError: On a non-Monday week, a missing or short table, a date that does
            not match the filename's week, or an intent outside the vocabulary.
    """
    monday = _monday_or_raise(week_start)
    cells = _table_rows(text)
    if len(cells) != 7:
        raise PlanParseError(f"expected 7 plan rows for week {week_start}, found {len(cells)}")

    rows: list[dict] = []
    for dow, row in enumerate(cells):
        expected = (monday + _dt.timedelta(days=dow)).strftime("%d.%m")
        date_cell = _strip_emphasis(row[1])
        if date_cell != expected:
            raise PlanParseError(
                f"week {week_start} row {dow}: date {date_cell!r} does not match "
                f"expected {expected!r}"
            )
        intent = _strip_emphasis(row[3]).lower()
        if intent not in INTENTS:
            raise PlanParseError(
                f"week {week_start} row {dow}: intent {intent!r} not in {'|'.join(INTENTS)}"
            )
        rows.append(
            {
                "week_start": week_start,
                "dow": dow,
                "planned": _strip_emphasis(row[2]),
                "intent": intent,
            }
        )
    return rows


def upsert_week(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """Idempotently upsert one parsed week into ``plan_week``."""
    conn.executemany(
        "INSERT INTO plan_week(week_start, dow, planned, intent) VALUES (?,?,?,?) "
        "ON CONFLICT(week_start, dow) DO UPDATE SET "
        "planned=excluded.planned, intent=excluded.intent",
        [(r["week_start"], r["dow"], r["planned"], r["intent"]) for r in rows],
    )
    conn.commit()


def import_dir(
    conn: sqlite3.Connection, plans_dir: str | pathlib.Path, week: str | None = None
) -> list[str]:
    """Import every ``<monday>_week.md`` in ``plans_dir`` (or one week) into the cache.

    Args:
        conn: Open connection with the schema bootstrapped.
        plans_dir: Directory holding the plan files; missing directory imports nothing.
        week: Only import the file for this Monday when given.

    Returns:
        The week_start of every imported week, sorted.

    Raises:
        PlanParseError: The first file that violates the contract aborts the import.
    """
    directory = pathlib.Path(plans_dir)
    if not directory.is_dir():
        return []
    imported: list[str] = []
    for path in sorted(directory.glob("*_week.md")):
        match = _FILENAME_RE.match(path.name)
        if match is None:
            raise PlanParseError(f"{path.name}: plan filename must be <YYYY-MM-DD>_week.md")
        week_start = match.group(1)
        _monday_or_raise(week_start, context=path.name)
        if week is not None and week_start != week:
            continue
        upsert_week(conn, parse_week(path.read_text(encoding="utf-8"), week_start))
        imported.append(week_start)
    return sorted(imported)


def resolve_day(conn: sqlite3.Connection, date: str) -> dict | None:
    """Resolve the planned intent for one date: authored week first, template fallback.

    Returns:
        ``{date, dow, planned, intent, source}`` with source ``plan_week`` |
        ``plan_template``, or None when neither table has the day.
    """
    day = _dt.date.fromisoformat(date)
    week_start = (day - _dt.timedelta(days=day.weekday())).isoformat()
    row = conn.execute(
        "SELECT planned, intent FROM plan_week WHERE week_start = ? AND dow = ?",
        (week_start, day.weekday()),
    ).fetchone()
    source = "plan_week"
    if row is None:
        row = conn.execute(
            "SELECT planned, intent FROM plan_template WHERE dow = ?", (day.weekday(),)
        ).fetchone()
        source = "plan_template"
    if row is None:
        return None
    return {
        "date": date,
        "dow": day.weekday(),
        "planned": row[0],
        "intent": row[1],
        "source": source,
    }


def resolve_week(conn: sqlite3.Connection, week_start: str) -> list[dict]:
    """Resolve all seven days of the week starting on ``week_start`` (a Monday)."""
    monday = _dt.date.fromisoformat(week_start)
    days = []
    for dow in range(7):
        date = (monday + _dt.timedelta(days=dow)).isoformat()
        days.append(resolve_day(conn, date) or {"date": date, "dow": dow})
    return days


def has_override(conn: sqlite3.Connection, week_start: str) -> bool:
    """Return True when the week has authored ``plan_week`` rows."""
    row = conn.execute(
        "SELECT 1 FROM plan_week WHERE week_start = ? LIMIT 1", (week_start,)
    ).fetchone()
    return row is not None


def write_week_file(
    plans_dir: str | pathlib.Path, week_start: str, days: list[dict]
) -> pathlib.Path:
    """Write a coach-proposed week as a normal plan file; never overwrites.

    Args:
        plans_dir: Destination directory (created if missing).
        week_start: The plan's Monday (YYYY-MM-DD).
        days: Seven ``{planned, intent}`` dicts in Monday-Sunday order.

    Returns:
        The path of the written ``<monday>_week.md``.

    Raises:
        FileExistsError: A plan of record for that week already exists; revisions
            stay a manual edit + re-import.
    """
    monday = _monday_or_raise(week_start)
    directory = pathlib.Path(plans_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{week_start}_week.md"
    if path.exists():
        raise FileExistsError(f"{path} already exists; revise it manually and re-import")

    sunday = monday + _dt.timedelta(days=6)
    lines = [
        f"# Plan tygodnia — {monday.strftime('%d.%m')}–{sunday.strftime('%d.%m.%Y')}",
        "",
        f"Zaproponowany przez coacha, zatwierdzony {_dt.date.today().isoformat()} "
        "(plan_confirm).",
        "",
        "| Dzień | Data | Plan | Zamiar (dla silnika) | Status |",
        "|------|------|------|----------------------|--------|",
    ]
    for dow, day in enumerate(days):
        date = (monday + _dt.timedelta(days=dow)).strftime("%d.%m")
        lines.append(
            f"| {_DAY_ABBREVS[dow]} | {date} | {day['planned']} | {day['intent']} | plan |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _monday_or_raise(week_start: str, context: str | None = None) -> _dt.date:
    where = f"{context}: " if context else ""
    try:
        day = _dt.date.fromisoformat(week_start)
    except ValueError as exc:
        raise PlanParseError(f"{where}invalid week_start {week_start!r}") from exc
    if day.weekday() != 0:
        raise PlanParseError(f"{where}week_start {week_start} is not a Monday")
    return day


def _table_rows(text: str) -> list[list[str]]:
    """Extract the plan table's data rows as lists of five stripped cells."""
    rows: list[list[str]] = []
    in_table = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            if in_table:
                break
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not in_table:
            if len(cells) >= 4 and "Zamiar" in stripped:
                in_table = True
            continue
        if set(cells[0]) <= {"-", ":", " "}:  # header separator row
            continue
        if len(cells) < 4:
            raise PlanParseError(f"malformed table row: {stripped!r}")
        rows.append(cells)
    if not in_table:
        raise PlanParseError("no plan table found (header with 'Zamiar (dla silnika)')")
    return rows


def _strip_emphasis(cell: str) -> str:
    return cell.replace("**", "").replace("*", "").strip()
