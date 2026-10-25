"""The shared system-command seam preserves subprocess policy at safe programs."""

import sys
import subprocess

import pytest


def test_live_runner_preserves_bytes_streams_and_nonzero_return_code():
    from garmin_coach import system_commands

    result = system_commands.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read()); sys.stderr.buffer.write(b'failure'); sys.exit(7)",
        ],
        input=b"payload",
        capture_output=True,
        check=False,
    )
    assert result.returncode == 7
    assert result.stdout == b"payload"
    assert result.stderr == b"failure"


def test_selected_fake_records_options_refuses_unknown_and_restores_live_runner():
    from garmin_coach import system_commands
    from tests.command_runner import RecordingRunner

    expected = ["fake-command", "inspect"]

    def handler(arguments, **options):
        if arguments == expected:
            return subprocess.CompletedProcess(arguments, 0, "ready")

    fake = RecordingRunner(handler)
    with system_commands.use_runner(fake):
        assert system_commands.run(expected, timeout=3, text=True).stdout == "ready"
        with pytest.raises(AssertionError, match="Unexpected system command"):
            system_commands.run(["unplanned-command"])
    assert fake.calls == [(expected, {"timeout": 3, "text": True}), (["unplanned-command"], {})]
    assert system_commands.run([sys.executable, "-c", "pass"], check=True).returncode == 0


def test_live_runner_preserves_text_environment_cwd_and_checked_failure(tmp_path):
    from garmin_coach import system_commands

    script = "import os,sys; print(os.getcwd()); print(os.environ['RUN_MARKER']); sys.exit(4)"
    with pytest.raises(subprocess.CalledProcessError) as failure:
        system_commands.run(
            [sys.executable, "-c", script],
            cwd=tmp_path,
            env={"RUN_MARKER": "selected"},
            capture_output=True,
            text=True,
            check=True,
        )
    assert failure.value.returncode == 4
    assert failure.value.stdout == f"{tmp_path}\nselected\n"
    assert failure.value.stderr == ""


def test_live_runner_keeps_the_callers_timeout():
    from garmin_coach import system_commands

    with pytest.raises(subprocess.TimeoutExpired) as failure:
        system_commands.run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.05)
    assert 0 < failure.value.timeout <= 0.05


def test_injected_interrupt_propagates_and_releases_the_runner_scope():
    from garmin_coach import system_commands
    from tests.command_runner import RecordingRunner

    interrupt = KeyboardInterrupt()

    def fail(arguments, **options):
        raise interrupt

    with pytest.raises(KeyboardInterrupt) as caught:
        with system_commands.use_runner(RecordingRunner(fail)):
            system_commands.run(["interrupted-command"])
    assert caught.value is interrupt
    assert system_commands.run([sys.executable, "-c", "pass"]).returncode == 0


def test_candidate_evidence_keeps_isolation_options_and_credential_scrubbing(
    tmp_path, monkeypatch, command_runner
):
    from garmin_coach import distribution

    monkeypatch.setenv("GARMIN_PASSWORD", "must-not-reach-candidate")
    python = tmp_path / "candidate/venv/bin/python"
    home = tmp_path / "athlete"
    report = (
        '{"schema":"compatible","data_through":"2026-07-03","snapshot_computed_at":"2026-07-03"}'
    )

    def evidence(arguments, **options):
        if arguments[:3] == [str(python), "-I", "-c"]:
            return subprocess.CompletedProcess(arguments, 0, report)

    recording = command_runner(evidence)
    assert distribution.inspect_runtime_database(python, home)["data_through"] == "2026-07-03"
    assert len(recording.calls) == 1
    _, options = recording.calls[0]
    assert options["timeout"] == 60 and options["check"] is True
    assert options["capture_output"] is True and options["text"] is True
    assert options["stdin"] == subprocess.DEVNULL
    assert options["env"]["HOME"] == options["cwd"]
    assert "GARMIN_PASSWORD" not in options["env"]
    assert "GARMIN_COACH_HOME" not in options["env"]
