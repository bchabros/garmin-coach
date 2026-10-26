"""Explicit synthetic observations for package and source connection checks."""

from __future__ import annotations

import pathlib
from contextlib import closing

from .core import db
from .marts import snapshot

SYNTHETIC_DATE = "2026-07-03"


def create_installation(root: pathlib.Path) -> None:
    """Create synthetic data/settings in a new or empty explicit directory.

    Args:
        root: Destination, never selected from the environment or current folder.

    Raises:
        ValueError: Existing content or a symlink would be overwritten.
    """
    if root.is_symlink() or (root.exists() and (not root.is_dir() or any(root.iterdir()))):
        raise ValueError(
            "Fixture requires a new or empty directory; existing records are preserved."
        )
    root.mkdir(parents=True, exist_ok=True)
    config = root / ".env"
    config.write_text("DB_PATH=./data/garmin.db\nDATA_START_DATE=2026-06-08\n")
    config.chmod(0o600)
    (root / "data").mkdir()
    with closing(db.connect(str(root / "data" / "garmin.db"))) as conn:
        db.bootstrap(conn)
        db.upsert_daily(conn, "daily_metrics", {"date": SYNTHETIC_DATE, "hrv": 61, "load_day": 80})
        snapshot.rollup(conn)
        conn.commit()
