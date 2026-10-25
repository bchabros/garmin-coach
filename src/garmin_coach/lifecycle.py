"""Diagnose and maintain a program while preserving its athlete installation."""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import fcntl
import importlib.metadata
import json
import os
import pathlib
import shutil
import shlex
import sqlite3
import subprocess
import sys
import tarfile
import uuid
from contextlib import closing, contextmanager
from collections.abc import Iterator
from typing import Any

from . import clients
from .program_receipt import ProgramReceipt, ReceiptError, verify_release_inventory
from .core import installation
from .core.config import Settings


class LifecycleError(ValueError):
    """A maintenance refusal whose message is safe to show without transport output."""


def _root(value: str | None) -> pathlib.Path:
    from .distribution import default_runtime_root

    return pathlib.Path(value).expanduser().resolve() if value else default_runtime_root().resolve()


def _check(state: str, recovery: str = "") -> dict[str, str]:
    return {"state": state, "recovery": recovery}


def _storage_check(home: pathlib.Path, root: pathlib.Path, settings: Settings) -> dict[str, str]:
    personal = [home.resolve()]
    personal.extend(
        (home / name).resolve()
        for name in ("backups", "data", "memory", installation.REPORTS_FOLDER)
    )
    personal.extend(
        pathlib.Path(getattr(settings, name)).resolve() for name in installation.PATH_SETTINGS
    )
    if any(path.is_relative_to(root) or root.is_relative_to(path) for path in personal):
        return _check(
            "conflict",
            "Move configured personal records outside the program root before update or removal.",
        )
    return _check("ready")


def _database(settings: Settings) -> dict[str, str]:
    path = pathlib.Path(settings.db_path)
    if not path.is_file():
        return _check("missing", "Run setup to import the selected athlete's history.")
    try:
        with closing(installation.read_database(settings)) as conn:
            horizon = conn.execute("SELECT MAX(date) FROM daily_metrics").fetchone()[0]
            snapshot = conn.execute(
                "SELECT computed_at FROM athlete_status WHERE id = 1"
            ).fetchone()
    except sqlite3.Error:
        return _check("incompatible", "Back up the database, then run offline features recovery.")
    if horizon is None or not snapshot or snapshot[0] is None:
        return _check("data_missing", "Import history, then run the offline features command.")
    return _check("ready")


def _read_state(root: pathlib.Path) -> dict[str, Any]:
    try:
        return ProgramReceipt(root).read()
    except (OSError, ValueError):
        raise LifecycleError(
            "Program receipt unreadable; preserve it and recover using the pinned bootstrap."
        ) from None


def _runtime(root: pathlib.Path, state: dict[str, Any]) -> dict[str, str]:
    if not state:
        return _check(
            "unmanaged", "Use the managed bootstrap, or retain the source checkout workflow."
        )
    if not (root / "current" / "venv" / "bin" / "python").is_file():
        return _check(
            "missing", "Rerun the pinned bootstrap command to repair the managed runtime."
        )
    if (root / "current").resolve().parent != root / "releases":
        return _check("changed", "Restore the managed current-release pointer.")
    release = (root / "current").resolve()
    inventory = state.get("managed_release_inventories", {}).get(str(release))
    if not inventory:
        return _check(
            "unverified",
            "Preserve this runtime; use a new managed root with an ownership inventory.",
        )
    try:
        verify_release_inventory(release, inventory)
    except (OSError, ValueError):
        return _check(
            "changed", "Preserve unknown or edited runtime files before repair or removal."
        )
    return _runtime_import(root)


def _runtime_import(root: pathlib.Path) -> dict[str, str]:
    try:
        subprocess.run(
            [
                str(root / "current/venv/bin/python"),
                "-I",
                "-c",
                "import garmin_coach.cli, garmin_coach.mcp.server",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return _check("unusable", "Rerun the pinned bootstrap to repair the managed runtime.")
    return _check("ready")


def _protocol_check(
    home: pathlib.Path, root: pathlib.Path, runtime: dict[str, str]
) -> dict[str, str]:
    if runtime["state"] != "ready":
        return _check("not_run", "Repair the verified runtime before a stdio probe.")
    from .mcp import probe

    try:
        asyncio.run(probe.check(root / "current" / "venv" / "bin" / "python", home))
    except Exception:
        return _check(
            "failed", "Run the selected installation's MCP server and inspect local setup."
        )
    return _check("ready")


def _skill_check() -> dict[str, str]:
    folder = pathlib.Path(__file__).resolve().parents[2] / "skills" / "coach"
    required = {
        "SKILL.md",
        "references/report.md",
        "references/planning.md",
        "references/authoring.md",
    }
    packaged = {str(name) for name in importlib.metadata.files("garmin-coach") or []}
    available = all((folder / name).is_file() for name in required) or all(
        f"skills/coach/{name}" in packaged for name in required
    )
    return _check("ready" if available else "missing", "Reinstall complete coach skill assets.")


def _integration_checks(
    home: pathlib.Path, root: pathlib.Path
) -> tuple[dict[str, str], dict[str, str], dict[str, Any]]:
    try:
        if clients.recorded_selection(home, root) is None:
            return (
                _check("not_selected", "Run clients configure to select an assistant."),
                _skill_check(),
                {},
            )
        status = clients.status(home, root)
    except (OSError, ValueError):
        return _check("changed", "Repair the integration receipt."), _check("unverified"), {}
    observations = _client_observations(status)
    if status["integrity"] == "preparing":
        return (
            _check("preparing", "Retry clients configure to finish the owned generation."),
            _check("preparing"),
            observations,
        )
    ownership = status.get("ownership", {})
    if ownership.get("state") in {"conflict", "unverified"}:
        return (
            _check(ownership["state"], " ".join(ownership.get("actions", []))),
            _check("unverified"),
            observations,
        )
    if (
        status["integrity"] == "changed"
        or not status.get("version_matches", True)
        or not status.get("resources_match_source", True)
    ):
        return (
            _check("stale", "Repair or update the owned client integration."),
            _check("stale"),
            observations,
        )
    states = [value["state"] for value in observations.values()]
    state = next(
        (
            value
            for value in (
                "failed",
                "preparing",
                "client_missing",
                "plugin_removed",
                "unverified",
                "needs_activation",
            )
            if value in states
        ),
        "ready" if states else "not_selected",
    )
    return (
        _check(state, "Run clients status and complete fresh-chat acceptance."),
        _check("ready"),
        observations,
    )


def _client_observations(status: dict[str, Any]) -> dict[str, Any]:
    known = {"ready", "failed", "client_missing", "preparing", "needs_activation", "plugin_removed"}
    observations = {}
    for name in ("work", "codex", "claude-code"):
        value = status.get("clients", {}).get(name)
        if value is None:
            continue
        state = value.get("state", "preparing")
        observations[name] = (
            {"state": state, "actions": value["actions"]}
            if state in known
            else {
                "state": "unverified",
                "actions": ["Rerun client configuration to verify registration."],
            }
        )
    return observations


def _schedule_check(
    home: pathlib.Path, root: pathlib.Path, state: dict[str, Any]
) -> dict[str, str]:
    legacy = pathlib.Path.home() / "Library/LaunchAgents/com.garmincoach.daily.plist"
    if legacy.exists():
        return _check(
            "legacy_conflict", "Inspect the legacy daily job before enabling another schedule."
        )
    if not state.get("scheduler"):
        return _check(
            "not_scheduled", "Existing manually configured jobs are not adopted automatically."
        )
    try:
        path = ProgramReceipt(root).owned_schedule(home)
    except (OSError, ValueError):
        return _check("changed", "Restore the owned daily job before modifying its registration.")
    if path is None:
        return _check("missing", "Rerun bootstrap scheduling to restore the owned daily job.")
    if state["scheduler"].get("status") != "enabled":
        return _check("prepared", "Finish the scheduler activation from bootstrap.")
    try:
        result = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{state['scheduler']['label']}"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return _check("unverified", "Inspect the owned job using launchctl on the supported Mac.")
    return _check(
        "ready" if result.returncode == 0 else "not_loaded", "Rerun bootstrap scheduling if needed."
    )


def diagnose(home: pathlib.Path, root: pathlib.Path) -> dict[str, Any]:
    """Inspect local readiness without transport, migrations or athlete record output.

    Args:
        home: Explicit selected athlete installation.
        root: Separate managed program root.

    Returns:
        Redacted readiness checks and manual acceptance status.
    """
    settings = installation.load(home)
    receipt_error = None
    try:
        state = _read_state(root)
    except LifecycleError as exc:
        state, receipt_error = {}, str(exc)
    tokens = pathlib.Path(settings.garmintokens)
    saved = tokens.is_dir() and any(tokens.iterdir())
    client_check, skill_check, observations = _integration_checks(home, root)
    runtime = (
        _check("receipt_unreadable", receipt_error) if receipt_error else _runtime(root, state)
    )
    return {
        "installation": str(home),
        "runtime_root": str(root),
        "clients": observations,
        "versions": {
            "python": sys.version.split()[0],
            "package": importlib.metadata.version("garmin-coach"),
            "release": state.get("release", "unmanaged"),
            "source_commit": state.get("source_commit", "unmanaged"),
        },
        "checks": {
            "runtime": runtime,
            "protocol": _protocol_check(home, root, runtime),
            "database": _database(settings),
            "storage": _storage_check(home, root, settings),
            "login": _check(
                "unverified" if saved else "missing", "Run doctor --live-auth to check login."
            ),
            "clients": client_check,
            "skill": skill_check,
            "scheduling": _schedule_check(home, root, state),
            "operation": _check(
                "interrupted" if state.get("operation") else "ready",
                "Inspect the recorded recovery backup before another update.",
            ),
        },
        "client_acceptance": "not_run",
    }


def _doctor(args: argparse.Namespace) -> int:
    home = installation.select(args.home)
    result = diagnose(home, _root(args.runtime_root))
    if args.live_auth:
        result["checks"]["login"] = _live_auth(home)
    if not args.live_auth:
        result["checks"]["login"]["recovery"] = (
            "Use --live-auth to check login; local reads do not need it."
        )
    print(json.dumps(result, indent=2))
    accepted = {"ready", "not_selected", "not_scheduled"}
    return (
        0
        if all(
            check["state"] in accepted
            for name, check in result["checks"].items()
            if name != "login" or args.live_auth
        )
        else 1
    )


def _live_auth(home: pathlib.Path) -> dict[str, str]:
    tokens = pathlib.Path(installation.load(home).garmintokens)
    if not tokens.is_dir() or not any(tokens.iterdir()):
        return _check("missing", "Run setup in a terminal to save this installation's login.")
    script = (
        "import sys; from garmin_coach.core import installation; "
        "from garmin_coach.etl.client import login_api; "
        "settings = installation.load(installation.select(sys.argv[1])); "
        "login_api(settings.model_copy(update={'garmin_email': '', 'garmin_password': ''}))"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-c", script, str(home)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=45,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return _check(
            "unverified", "Check the network, then renew login using setup in a terminal."
        )
    if result.returncode:
        return _check(
            "unverified", "Check the network, then renew login using setup in a terminal."
        )
    return _check("ready")


def _backup(home: pathlib.Path, root: pathlib.Path) -> pathlib.Path:
    settings = installation.load(home)
    installation.require_database(settings)
    parent = home / "backups" / "program"
    if not parent.resolve().is_relative_to(home):
        raise LifecycleError("Recovery backup must stay inside the athlete installation.")
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = parent / f"{stamp}-{uuid.uuid4().hex[:8]}"
    target.mkdir(mode=0o700)
    shutil.copyfile(home / ".env", target / "settings.env")
    (target / "settings.env").chmod(0o600)
    database = target / "database.sqlite"
    source = pathlib.Path(settings.db_path)
    installation.backup_database(settings, database)
    ProgramReceipt(root).backup_to(target)
    integrations = clients.integration_root(home, root)
    if integrations.exists():
        shutil.copytree(integrations, target / "integration")
    if (root / "bin").is_dir():
        shutil.copytree(root / "bin", target / "launchers")
    record = {"database": str(source), "home": str(home), "created_at": stamp}
    (target / "recovery.json").write_text(json.dumps(record, indent=2) + "\n")
    return target


def _program_backup(args: argparse.Namespace) -> int:
    home = installation.select(args.home)
    target = _backup(home, _root(args.runtime_root))
    print(json.dumps({"backup": str(target), "state": "complete"}, indent=2))
    return 0


def _managed(home: pathlib.Path, root: pathlib.Path) -> dict[str, Any]:
    if _storage_check(home, root, installation.load(home))["state"] != "ready":
        raise LifecycleError(
            "Personal records overlap the program root; move them before maintenance."
        )
    state = _read_state(root)
    if state.get("managed_by") != "garmin-coach":
        raise LifecycleError("Unmanaged program; retain the source checkout workflow.")
    if state.get("selected_home") and pathlib.Path(state["selected_home"]).resolve() != home:
        raise LifecycleError("The managed runtime belongs to a different athlete installation.")
    return state


def _read_candidate(release: pathlib.Path, home: pathlib.Path) -> None:
    script = (
        "import sys; from garmin_coach.core import installation; "
        "settings = installation.load(installation.select(sys.argv[1])); "
        "conn = installation.read_database(settings); "
        "assert conn.execute('SELECT MAX(date) FROM daily_metrics').fetchone()[0]; "
        "assert conn.execute('SELECT computed_at FROM athlete_status WHERE id=1').fetchone()[0]; "
        "conn.close()"
    )
    subprocess.run(
        [str(release / "venv" / "bin" / "python"), "-I", "-c", script, str(home)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
        check=True,
    )


def _refresh_clients(
    home: pathlib.Path, root: pathlib.Path, release: pathlib.Path, selected: list[str] | None = None
) -> None:
    recorded = clients.recorded_selection(home, root)
    if recorded is None:
        return
    selected = selected if selected is not None else recorded
    if not selected:
        return
    completed = subprocess.run(
        [
            str(release / "venv" / "bin" / "python"),
            "-I",
            "-m",
            "garmin_coach.clients",
            "--home",
            str(home),
            "--runtime-root",
            str(root),
            "configure",
            "--clients",
            ",".join(selected),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=90,
        check=True,
    )
    _require_client_preparation(completed.stdout, selected)


def _require_client_preparation(output: str, selected: list[str]) -> None:
    try:
        result = json.loads(output)
    except (ValueError, TypeError):
        raise LifecycleError("Client registration returned an unreadable result.") from None
    details = result.get("clients") if isinstance(result, dict) else None
    if not isinstance(details, dict) or set(details) != set(selected):
        raise LifecycleError("Client registration did not report every selected assistant.")
    if any(
        not isinstance(value, dict)
        or value.get("state") not in {"ready", "needs_activation", "client_missing"}
        for value in details.values()
    ):
        raise LifecycleError("Client registration is incomplete; restore the prior integration.")


def _restore(
    root: pathlib.Path,
    home: pathlib.Path,
    previous: pathlib.Path,
    state: dict[str, Any],
    backup: pathlib.Path,
) -> None:
    from . import distribution

    distribution.activate_release(previous, root)
    if (backup / "integration").exists():
        restored = clients.restore(home, root, backup / "integration")
        _require_client_preparation(json.dumps(restored), restored["selected_clients"])
    ProgramReceipt(root).restore(state)


def _prepare_update(
    home: pathlib.Path,
    root: pathlib.Path,
    backup: pathlib.Path,
    manifest: pathlib.Path,
) -> pathlib.Path:
    from . import distribution

    receipt = ProgramReceipt(root)
    receipt.begin_update(home, backup, (root / "current").resolve(), manifest)
    candidate = distribution.prepare_release(manifest, root)
    receipt.checkpoint_update("validating_candidate", candidate=candidate)
    _read_candidate(candidate, home)
    return candidate


def _activate_update(home: pathlib.Path, root: pathlib.Path, candidate: pathlib.Path) -> None:
    from . import distribution

    selected = clients.recorded_selection(home, root) or []
    receipt = ProgramReceipt(root)
    receipt.checkpoint_update("activating")
    distribution.activate_release(candidate, root)
    receipt.checkpoint_update("validating")
    _read_candidate(root / "current", home)
    receipt.checkpoint_update("configuring_clients")
    _refresh_clients(home, root, root / "current", selected)
    receipt.finish_update()


def _recover_update(
    home: pathlib.Path,
    root: pathlib.Path,
    previous: pathlib.Path,
    state: dict[str, Any],
    backup: pathlib.Path,
    touched: bool,
) -> bool:
    try:
        if touched:
            _restore(root, home, previous, state, backup)
        else:
            ProgramReceipt(root).restore(state)
        return True
    except (OSError, ValueError, subprocess.SubprocessError):
        ProgramReceipt(root).require_recovery(home, previous, backup)
        return False


def _recover_interrupted(
    home: pathlib.Path, root: pathlib.Path, state: dict[str, Any]
) -> dict[str, Any]:
    receipt = ProgramReceipt(root)
    operation = receipt.interrupted_update()
    if operation is None:
        return state
    backup = operation.backup.resolve()
    if not backup.is_relative_to(home / "backups" / "program"):
        raise LifecycleError("Recovery backup does not belong to this athlete installation.")
    record = json.loads((backup / "recovery.json").read_text())
    prior = ProgramReceipt(backup).read()
    if record.get("home") != str(home) or operation.home not in (None, str(home)):
        raise LifecycleError("Recovery journal belongs to another athlete installation.")
    receipt.verified_releases(prior)
    previous = operation.previous or pathlib.Path(prior["release_dir"])
    if str(previous) not in prior.get("managed_releases", {}):
        raise LifecycleError("Prior runtime is not in the saved ownership inventory.")
    # v0.1.0 could detach clients while its journal still reported preparing.
    if not _recover_update(
        home,
        root,
        previous,
        prior,
        backup,
        operation.may_have_changed_clients,
    ):
        raise LifecycleError("Recovery incomplete; preserve the backup and retry program update.")
    return receipt.read()


def _program_update(args: argparse.Namespace) -> int:
    home, root = installation.select(args.home), _root(args.runtime_root)
    manifest = pathlib.Path(args.manifest).expanduser().resolve()
    state = _recover_interrupted(home, root, _managed(home, root))
    ProgramReceipt(root).verified_launchers()
    ProgramReceipt(root).verified_releases()
    previous = (root / "current").resolve()
    if previous.parent != root / "releases" or not (previous / "ready.json").is_file():
        raise LifecycleError(
            "The prior release is unavailable; repair the runtime before updating."
        )
    if json.loads(manifest.read_text()) == json.loads((previous / "manifest.json").read_text()):
        print(json.dumps({"state": "unchanged", "client_acceptance": "not_run"}))
        return 0
    backup, touched = _backup(home, root), False
    try:
        candidate = _prepare_update(home, root, backup, manifest)
        touched = True
        _activate_update(home, root, candidate)
    except (OSError, ValueError, subprocess.SubprocessError, tarfile.TarError, KeyboardInterrupt):
        phase = _read_state(root).get("operation", {}).get("phase", "preparing")
        recovered = _recover_update(home, root, previous, state, backup, touched)
        retry = shlex.join(
            [
                sys.executable,
                "-I",
                "-m",
                "garmin_coach.lifecycle",
                "--home",
                str(home),
                "program",
                "update",
                "--runtime-root",
                str(root),
                "--manifest",
                str(manifest),
            ]
        )
        _report_update_failure(recovered, backup, phase, retry)
        return 2
    print(json.dumps({"state": "updated", "backup": str(backup), "client_acceptance": "not_run"}))
    return 0


def _report_update_failure(recovered: bool, backup: pathlib.Path, phase: str, retry: str) -> None:
    actions = {
        "preparing": "Check the release assets and runtime prerequisites.",
        "validating_candidate": "Check candidate compatibility with the existing database.",
        "activating": "Run doctor and inspect runtime and launcher ownership.",
        "validating": "Check the activated candidate's local database read.",
        "configuring_clients": "Run clients status and repair the affected native client CLI.",
    }
    result = {
        "state": "rolled_back" if recovered else "recovery_required",
        "backup": str(backup),
        "client_acceptance": "not_run",
        "failed_phase": phase,
        "recovery": (
            ("Prior runtime restored. " if recovered else "Preserve both runtimes. ")
            + actions.get(phase, "Inspect the recorded operation and client status.")
            + " Keep the recovery backup, then retry using the checked engine."
        ),
        "retry_command": retry,
    }
    print(json.dumps(result))


@contextmanager
def _program_lock(root: pathlib.Path) -> Iterator[None]:
    descriptor = os.open(root / ".lifecycle.lock", os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def _dispatch(args: argparse.Namespace) -> int:
    try:
        if args.command == "doctor":
            return _doctor(args)
        if args.program_command == "backup":
            return _program_backup(args)
        root = _root(args.runtime_root)
        _managed(installation.select(args.home), root)
        with _program_lock(root):
            return (
                _program_update(args) if args.program_command == "update" else _program_remove(args)
            )
    except (LifecycleError, ReceiptError) as exc:
        print(f"lifecycle: {exc}", file=sys.stderr)
        return 2
    except (
        ValueError,
        OSError,
        KeyError,
        TypeError,
        sqlite3.Error,
        subprocess.SubprocessError,
        tarfile.TarError,
    ):
        print(
            "lifecycle: operation refused; inspect doctor, ownership receipts "
            "and any recovery backup.",
            file=sys.stderr,
        )
        return 2


def _remove_schedule(state: dict[str, Any], path: pathlib.Path | None) -> None:
    if not state.get("scheduler", {}).get("label"):
        return
    label = state["scheduler"]["label"]
    result = subprocess.run(
        ["launchctl", "bootout", f"gui/{os.getuid()}/{label}"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=20,
        check=False,
    )
    if result.returncode not in (0, 3, 113):
        raise LifecycleError("Could not stop the owned daily job; preserve the runtime and retry.")
    if path is not None:
        path.unlink(missing_ok=True)


def _remove_clients(
    home: pathlib.Path, root: pathlib.Path, selected: list[str] | None, confirm: bool
) -> dict[str, Any]:
    if clients.recorded_selection(home, root) is None:
        return {"state": "not_selected"}

    result = clients.remove(home, root, selected, confirm=confirm)
    if result.get("state") in ("failed", "conflict"):
        raise LifecycleError(
            "Client removal failed; preserve the runtime and recover client ownership."
        )
    return result


def _remove_runtime(
    home: pathlib.Path, root: pathlib.Path, state: dict[str, Any], confirm: bool
) -> dict[str, Any]:
    launchers = ProgramReceipt(root).verified_launchers()
    releases = ProgramReceipt(root).verified_releases()
    schedule = ProgramReceipt(root).owned_schedule(home)
    selection = _remove_clients(home, root, None, confirm)
    resources = {
        "launchers": [str(path) for path in launchers],
        "releases": [str(path) for path in releases],
        "scheduler": state.get("scheduler", {}),
        "clients": selection.get("remove_clients", []),
    }
    if confirm:
        _remove_schedule(state, schedule)
        for path in launchers:
            path.unlink(missing_ok=True)
        pointer = root / "current"
        if pointer.is_symlink() and pointer.resolve() in releases:
            pointer.unlink()
        for path in releases:
            if path.exists():
                shutil.rmtree(path)
        ProgramReceipt(root).mark_removed()

    return {"state": "removed" if confirm else "preview", "resources": resources}


def _retained_paths(home: pathlib.Path) -> list[str]:
    settings = installation.load(home)
    paths = {home, home / "backups", home / "memory", home / "reports"}
    paths.update(pathlib.Path(getattr(settings, key)) for key in installation.PATH_SETTINGS)
    return sorted(str(path) for path in paths)


def _program_remove(args: argparse.Namespace) -> int:
    home, root = installation.select(args.home), _root(args.runtime_root)
    state = _managed(home, root)
    if state.get("operation"):
        raise LifecycleError(
            "Retry program update to recover the interrupted operation before removal."
        )
    if args.client:
        selection = _remove_clients(home, root, [args.client], args.confirm)
        if args.confirm:
            ProgramReceipt(root).retain_clients(clients.recorded_selection(home, root) or [])
        result = {
            "state": "removed" if args.confirm else "preview",
            "client": args.client,
            "resources": {"clients": selection.get("remove_clients", [args.client])},
        }
    else:
        result = _remove_runtime(home, root, state, args.confirm)
    result["retained_installation"] = str(home)
    result["retained_paths"] = _retained_paths(home)
    print(json.dumps(result, indent=2))
    return 0


def add_commands(sub: Any) -> None:
    """Add lifecycle command adapters to the application's argument parser.

    Args:
        sub: Argument parser subcommands collection.
    """
    doctor = sub.add_parser("doctor", help="Inspect local readiness without contacting Garmin.")
    doctor.add_argument("--runtime-root", default=None)
    doctor.add_argument(
        "--live-auth", action="store_true", help="Explicitly resume the saved Garmin login."
    )
    doctor.set_defaults(func=_dispatch)
    program = sub.add_parser(
        "program", help="Maintain the program while retaining athlete records."
    )
    operations = program.add_subparsers(dest="program_command", required=True)
    backup = operations.add_parser("backup", help="Save a private SQLite recovery snapshot.")
    backup.add_argument("--runtime-root", default=None)
    backup.set_defaults(func=_dispatch)
    update = operations.add_parser(
        "update", help="Check a candidate release before switching runtimes."
    )
    update.add_argument("--runtime-root", default=None)
    update.add_argument("--manifest", required=True)
    update.set_defaults(func=_dispatch)
    remove = operations.add_parser("remove", help="Preview removal; retain all athlete records.")
    remove.add_argument("--runtime-root", default=None)
    remove.add_argument("--client", choices=("work", "codex", "claude-code"))
    remove.add_argument("--confirm", action="store_true")
    remove.set_defaults(func=_dispatch)


def main(argv: list[str] | None = None) -> int:
    """Run standalone lifecycle commands using the normal installation selection rules.

    Args:
        argv: Optional command-line arguments.

    Returns:
        Zero for success, one for unhealthy diagnosis, or two for a refused operation.
    """
    parser = argparse.ArgumentParser(prog="garmin-coach")
    installation.add_home_argument(parser)
    add_commands(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(argv)
    try:
        installation.activate(args.home)
        return args.func(args)
    except (ValueError, OSError):
        print(
            "lifecycle: operation refused; check the selected installation and program receipt.",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
