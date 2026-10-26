"""Installation evidence and guarded existing-only database connections."""

import pathlib

import pytest

from garmin_coach.core import installation


def test_missing_database_is_evidence_without_creating_history(athlete_home):
    settings = installation.load(athlete_home)
    evidence = installation.inspect_database(settings)
    assert evidence.schema == "missing"
    assert evidence.data_through is None
    assert evidence.snapshot_computed_at is None
    assert not pathlib.Path(settings.db_path).exists()


def test_guarded_writable_connection_preserves_schema_and_foreign_keys(athlete_home):
    import sqlite3
    from garmin_coach.core import db

    settings = installation.load(athlete_home)
    path = pathlib.Path(settings.db_path)
    path.parent.mkdir()
    conn = db.connect(str(path))
    db.bootstrap(conn)
    conn.execute("CREATE TABLE extra_notes(note TEXT)")
    conn.execute("INSERT INTO extra_notes VALUES ('retained')")
    conn.commit()
    conn.close()
    before = path.read_bytes()

    conn = installation.open_existing_database(settings, writable=True)
    try:
        assert conn.execute("SELECT note FROM extra_notes").fetchone()[0] == "retained"
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO session_rpe(activity_id, rpe) VALUES (999, 8)")
    finally:
        conn.rollback()
        conn.close()
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "daily,snapshot_date",
    [(None, None), ("2026-07-03", None), (None, "2026-07-02"), ("2026-07-03", "2026-07-02")],
)
def test_evidence_preserves_independent_dates_and_personal_policy(
    athlete_home, daily, snapshot_date
):
    from garmin_coach.core import db

    settings = installation.load(athlete_home)
    path = pathlib.Path(settings.db_path)
    path.parent.mkdir()
    conn = db.connect(str(path))
    db.bootstrap(conn)
    conn.execute("ALTER TABLE daily_metrics ADD COLUMN local_note TEXT")
    conn.execute("UPDATE coach_thresholds SET value=2.25 WHERE key='hrv_low_k_sd'")
    if daily:
        db.upsert_daily(conn, "daily_metrics", {"date": daily})
    if snapshot_date:
        conn.execute("INSERT INTO athlete_status(id,computed_at) VALUES (1,?)", (snapshot_date,))
    conn.commit()
    conn.close()
    before = path.read_bytes()

    evidence = installation.inspect_database(settings)
    assert evidence.schema == "compatible"
    assert evidence.data_through == daily
    assert evidence.snapshot_computed_at == snapshot_date
    assert path.read_bytes() == before
    with installation.read_database(settings) as conn:
        assert (
            conn.execute("SELECT value FROM coach_thresholds WHERE key='hrv_low_k_sd'").fetchone()[
                0
            ]
            == 2.25
        )


@pytest.mark.parametrize("writable", [False, True])
def test_existing_only_openers_refuse_old_schema_without_repair(athlete_home, writable):
    import sqlite3

    settings = installation.load(athlete_home)
    path = pathlib.Path(settings.db_path)
    path.parent.mkdir()
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE retained(note TEXT)")
        conn.execute("INSERT INTO retained VALUES ('keep')")
    before = path.read_bytes()
    assert installation.inspect_database(settings).schema == "incompatible"
    with pytest.raises(sqlite3.Error):
        installation.open_existing_database(settings, writable=writable)
    assert path.read_bytes() == before
