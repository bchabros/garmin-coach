"""Report retention through the operator's CLI, using a disposable report tree."""

import datetime as dt
from pathlib import Path

import pytest

from garmin_coach.cli import main


def test_prune_defaults_to_preview_of_reports_strictly_older_than_90_days(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    today = dt.date.today()
    old = tmp_path / "reports" / (today - dt.timedelta(days=91)).isoformat()
    boundary = tmp_path / "reports" / (today - dt.timedelta(days=90)).isoformat()
    for folder in (old, boundary):
        folder.mkdir(parents=True)
        (folder / "report.md").write_text("report")
        (folder / "hrv_band.png").write_bytes(b"chart")
        (folder / "digest.json").write_text("{}")

    assert main(["reports", "prune"]) == 0

    output = capsys.readouterr().out
    assert "dry-run" in output
    assert f"{old.name}: age=91 days files=2 bytes=11" in output
    assert boundary.name not in output
    assert (old / "report.md").read_text() == "report"
    assert (old / "hrv_band.png").read_bytes() == b"chart"
    assert (old / "digest.json").read_text() == "{}"


def test_confirm_prunes_only_report_files_and_keeps_records(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    old = tmp_path / "reports" / (dt.date.today() - dt.timedelta(days=91)).isoformat()
    old.mkdir(parents=True)
    retained = ("digest.json", "snapshot.json", "workout.json", "push.json", "notes.md")
    for name in (*retained, "report.md", "hrv_band.png", "acwr.png"):
        (old / name).write_text(name)
    plans = tmp_path / "plans"
    plans.mkdir()
    (plans / "2026-06-08_week.md").write_text("plan of record")

    assert main(["reports", "prune", "--confirm"]) == 0

    assert {p.name for p in old.iterdir()} == set(retained)
    for name in retained:
        assert (old / name).read_text() == name
    assert (plans / "2026-06-08_week.md").read_text() == "plan of record"
    assert "deleted_files=3" in capsys.readouterr().out


def test_custom_age_and_digest_opt_out_still_keep_workout_history(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    today = dt.date.today()
    old = tmp_path / "reports" / (today - dt.timedelta(days=31)).isoformat()
    boundary = tmp_path / "reports" / (today - dt.timedelta(days=30)).isoformat()
    for folder in (old, boundary):
        folder.mkdir(parents=True)
        for name in ("report.md", "digest.json", "snapshot.json", "push.json", "workout.json"):
            (folder / name).write_text(name)

    assert main(["reports", "prune", "--older-than", "30", "--no-keep-digest", "--confirm"]) == 0

    assert {p.name for p in old.iterdir()} == {"push.json", "workout.json"}
    assert len(list(boundary.iterdir())) == 5


@pytest.mark.parametrize("age", ["-1", "1.5", "invalid"])
def test_invalid_age_is_refused_without_deleting_anything(tmp_path, monkeypatch, capsys, age):
    monkeypatch.chdir(tmp_path)
    folder = tmp_path / "reports" / dt.date.today().isoformat()
    folder.mkdir(parents=True)
    (folder / "report.md").write_text("keep")

    with pytest.raises(SystemExit) as exc:
        main(["reports", "prune", "--older-than", age, "--confirm"])

    assert exc.value.code == 2
    assert "--older-than" in capsys.readouterr().err
    assert (folder / "report.md").read_text() == "keep"


def test_non_dated_entries_and_nested_artifacts_are_never_pruned(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "gh-issue-workout-name.md").write_text("issue draft")
    for name in ("20260101", "2026-02-30", "archive", "2026-01-01"):
        folder = reports / name
        folder.mkdir()
        (folder / "report.md").write_text("narrative")
    nested = reports / "2026-01-01" / "archive"
    nested.mkdir()
    (nested / "report.md").write_text("nested")

    assert main(["reports", "prune", "--confirm"]) == 0

    assert not (reports / "2026-01-01" / "report.md").exists()
    assert (nested / "report.md").read_text() == "nested"
    for name in ("20260101", "2026-02-30", "archive"):
        assert (reports / name / "report.md").read_text() == "narrative"
    assert (reports / "gh-issue-workout-name.md").read_text() == "issue draft"


@pytest.mark.parametrize("link_kind", ["root", "day", "file"])
def test_symlinks_cannot_redirect_retention_into_plans(tmp_path, monkeypatch, link_kind):
    monkeypatch.chdir(tmp_path)
    reports = tmp_path / "reports"
    plans = tmp_path / "plans"
    day = (dt.date.today() - dt.timedelta(days=91)).isoformat()
    source = plans / day
    source.mkdir(parents=True)
    (source / "report.md").write_text("irreplaceable")
    if link_kind == "root":
        reports.symlink_to(plans, target_is_directory=True)
        link = reports
    elif link_kind == "day":
        reports.mkdir()
        link = reports / day
        link.symlink_to(source, target_is_directory=True)
    else:
        (reports / day).mkdir(parents=True)
        link = reports / day / "report.md"
        link.symlink_to(source / "report.md")

    assert main(["reports", "prune", "--confirm"]) == (2 if link_kind == "root" else 0)

    assert (source / "report.md").read_text() == "irreplaceable"
    assert link.is_symlink()


def test_missing_reports_is_a_noop_without_creating_directories(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    assert main(["reports", "prune", "--confirm"]) == 0

    assert "folders=0 deleted_files=0" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("all_fail,expected_code", [(False, 1), (True, 2)])
def test_deletion_failures_are_reported_and_other_files_still_prune(
    tmp_path, monkeypatch, capsys, all_fail, expected_code
):
    monkeypatch.chdir(tmp_path)
    folder = tmp_path / "reports" / (dt.date.today() - dt.timedelta(days=91)).isoformat()
    folder.mkdir(parents=True)
    for name in ("report.md", "acwr.png"):
        (folder / name).write_text(name)
    unlink = Path.unlink

    def fail_selected(path, *args, **kwargs):
        if all_fail or path.name == "report.md":
            raise PermissionError("test deletion denied")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_selected)

    assert main(["reports", "prune", "--confirm"]) == expected_code

    output = capsys.readouterr().out
    assert "test deletion denied" in output
    assert "report.md" in output
    assert (folder / "report.md").read_text() == "report.md"
    assert (folder / "acwr.png").exists() == all_fail


def test_empty_report_folder_is_removed_only_after_confirmed_deletion(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    folder = tmp_path / "reports" / (dt.date.today() - dt.timedelta(days=91)).isoformat()
    folder.mkdir(parents=True)
    (folder / "digest.json").write_text("{}")
    (folder / "snapshot.json").write_text("{}")

    assert main(["reports", "prune", "--no-keep-digest"]) == 0
    assert folder.is_dir()
    assert main(["reports", "prune", "--no-keep-digest", "--confirm"]) == 0
    assert not folder.exists()


def test_unreadable_report_is_reported_without_aborting_other_folders(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    reports = tmp_path / "reports"
    today = dt.date.today()
    older = reports / (today - dt.timedelta(days=92)).isoformat()
    newer = reports / (today - dt.timedelta(days=91)).isoformat()
    for folder in (older, newer):
        folder.mkdir(parents=True)
        (folder / "report.md").write_text("report")
    stat = Path.stat

    def fail_stat(path, *args, **kwargs):
        if path.name == "report.md" and path.parent.name == older.name:
            raise PermissionError("test inspection denied")
        return stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fail_stat)

    assert main(["reports", "prune", "--confirm"]) == 1

    assert "test inspection denied" in capsys.readouterr().out
    assert (older / "report.md").read_text() == "report"
    assert not newer.exists()
