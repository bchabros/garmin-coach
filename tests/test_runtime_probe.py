"""Candidate capability dispatch uses only its isolated runtime's supported API."""

import pathlib
import sqlite3
import subprocess
import sys

import pytest

from garmin_coach import distribution


def _candidate(tmp_path, probe_source=None):
    release = tmp_path / "release"
    package = release / "fake-packages/garmin_coach"
    (package / "mcp").mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "mcp/__init__.py").write_text("")
    if probe_source is not None:
        (package / "mcp/probe.py").write_text(probe_source)
    marker = release / "legacy-called"
    (package / "distribution.py").write_text(
        "import pathlib\nRUNTIME_PROBE = 'legacy contract'\n"
        "def check_runtime_protocol(release, *, launcher=None):\n"
        f"    pathlib.Path({str(marker)!r}).write_text('called')\n"
    )
    python = release / "venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text(
        f"#!{sys.executable}\nimport sys\n"
        "assert sys.argv[1:3] == ['-I', '-c']\n"
        f"sys.path.insert(0, {str(package.parent)!r})\n"
        "script = sys.argv[3]\nsys.argv = ['-c', *sys.argv[4:]]\nexec(script)\n"
    )
    python.chmod(0o755)
    return release, marker


def test_previous_release_uses_its_own_legacy_probe(tmp_path):
    release, marker = _candidate(tmp_path)
    distribution.check_runtime_protocol(release)
    assert marker.read_text() == "called"


def test_supported_candidate_executes_its_new_packaged_probe(tmp_path):
    new_marker = tmp_path / "new-called"
    source = (
        "import pathlib\nPROBE_API_VERSION = 1\n"
        "def main(arguments):\n"
        f"    pathlib.Path({str(new_marker)!r}).write_text(arguments[0])\n"
        "    return 0\n"
    )
    release, legacy = _candidate(tmp_path, source)
    distribution.check_runtime_protocol(release)
    assert new_marker.read_text() == "synthetic"
    assert not legacy.exists()


@pytest.mark.parametrize(
    "source",
    [
        "PROBE_API_VERSION = 999\ndef main(arguments): return 0\n",
        "PROBE_API_VERSION = None\ndef main(arguments): return 0\n",
        "PROBE_API_VERSION = 1\ndef main(arguments): raise RuntimeError('probe failed')\n",
        "import missing_probe_dependency\nPROBE_API_VERSION = 1\n",
    ],
)
def test_failed_or_unsupported_probe_never_uses_legacy_fallback(tmp_path, source):
    release, legacy = _candidate(tmp_path, source)
    with pytest.raises(subprocess.CalledProcessError):
        distribution.check_runtime_protocol(release)
    assert not legacy.exists()


def test_runtime_evidence_reports_missing_history_without_creating_it(tmp_path):
    home = tmp_path / "athlete"
    home.mkdir()
    (home / ".env").write_text("")
    evidence = distribution.inspect_runtime_database(
        pathlib.Path(sys.executable), home, allow_missing=True
    )
    assert evidence == {"schema": "missing", "data_through": None, "snapshot_computed_at": None}
    assert not (home / "data").exists()


def test_previous_release_reads_evidence_with_its_own_storage_opener(tmp_path):
    release, _ = _candidate(tmp_path)
    core = release / "fake-packages/garmin_coach/core"
    core.mkdir()
    (core / "__init__.py").write_text("")
    database = tmp_path / "legacy.sqlite"
    with sqlite3.connect(database) as conn:
        conn.executescript(
            "CREATE TABLE daily_metrics(date TEXT);"
            "INSERT INTO daily_metrics VALUES ('2026-07-03');"
            "CREATE TABLE athlete_status(id INTEGER, computed_at TEXT);"
            "INSERT INTO athlete_status VALUES (1, '2026-07-04');"
        )
    original = database.read_bytes()
    marker = tmp_path / "legacy-storage-called"
    (core / "installation.py").write_text(
        "import pathlib, sqlite3\nfrom types import SimpleNamespace\n"
        f"def activate(home): return SimpleNamespace(db_path={str(database)!r})\n"
        "def read_database(settings):\n"
        f"    pathlib.Path({str(marker)!r}).write_text('validated by candidate')\n"
        "    return sqlite3.connect(pathlib.Path(settings.db_path).as_uri() + '?mode=ro', uri=True)\n"
    )
    evidence = distribution.inspect_runtime_database(
        release / "venv/bin/python", tmp_path, require_data=True
    )
    assert evidence == {
        "schema": "compatible",
        "data_through": "2026-07-03",
        "snapshot_computed_at": "2026-07-04",
    }
    assert marker.read_text() == "validated by candidate"
    assert database.read_bytes() == original


@pytest.mark.parametrize(
    "report",
    [
        {"schema": "compatible"},
        {"schema": "compatible", "data_through": [], "snapshot_computed_at": None},
        {"schema": "missing", "data_through": None, "snapshot_computed_at": 123},
    ],
)
def test_malformed_candidate_evidence_is_refused(tmp_path, report):
    source = (
        "import json\nPROBE_API_VERSION = 1\n"
        f"def main(arguments):\n    print(json.dumps({report!r}))\n    return 0\n"
    )
    release, _ = _candidate(tmp_path, source)
    with pytest.raises(ValueError, match="Packaged database evidence"):
        distribution.inspect_runtime_database(release / "venv/bin/python", tmp_path)
