"""Versioned private runtime preparation, terminal setup and opt-in scheduling."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from contextlib import closing
from typing import Any


SCHEMA_VERSION = 1


def default_runtime_root() -> pathlib.Path:
    """Return the macOS user's private program directory."""
    return pathlib.Path.home() / "Library" / "Application Support" / "Garmin Coach"


def read_state(root: pathlib.Path) -> dict[str, Any]:
    """Read the setup receipt without inferring ownership from its location."""
    path = root / "install-state.json"
    if not path.exists():
        return {}
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported install receipt; preserve it and use a compatible installer.")
    return value


def write_state(root: pathlib.Path, state: dict[str, Any]) -> None:
    """Atomically persist non-personal setup observations."""
    root.mkdir(parents=True, exist_ok=True)
    record = dict(state, schema_version=SCHEMA_VERSION)
    with tempfile.NamedTemporaryFile(mode="w", dir=root, delete=False) as stream:
        json.dump(record, stream, indent=2)
        stream.write("\n")
        name = stream.name
    os.replace(name, root / "install-state.json")


def _download(asset: dict[str, Any], destination: pathlib.Path) -> None:
    url = str(asset["url"])
    if not url.startswith(("https://", "file://")):
        raise ValueError("Release assets must use HTTPS or a local file URL.")
    expected = str(asset["sha256"])
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("Release asset checksum is missing or invalid.")
    with urllib.request.urlopen(url, timeout=60) as response, destination.open("wb") as target:
        shutil.copyfileobj(response, target)
    if hashlib.sha256(destination.read_bytes()).hexdigest() != expected:
        raise ValueError(f"Release asset checksum mismatch: {destination.name}")


def _manifest(path: pathlib.Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if value.get("schema_version") != SCHEMA_VERSION or value.get("platform") != "darwin":
        raise ValueError("Unsupported release manifest.")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[a-zA-Z0-9.-]+)?", value.get("version", "")):
        raise ValueError("Invalid release version.")
    if value.get("architecture") not in ("arm64", "x86_64"):
        raise ValueError("Unsupported release architecture.")
    if not re.fullmatch(r"[0-9a-f]{40}", value.get("source_commit", "")):
        raise ValueError("Invalid release source commit.")
    return value


def _unpack(archive: pathlib.Path, destination: pathlib.Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as content:
        content.extractall(destination, filter="data")


def validate_release_platform(metadata: dict[str, Any]) -> None:
    """Refuse unsupported operating systems, architectures and minimum macOS versions."""
    if platform.system() != "Darwin" or platform.machine() != metadata["architecture"]:
        raise ValueError("Release does not match this macOS architecture.")
    actual = tuple(map(int, platform.mac_ver()[0].split(".")[:2]))
    minimum = tuple(map(int, metadata["min_macos"].split(".")[:2]))
    if actual < minimum:
        raise ValueError(f"This release requires macOS {metadata['min_macos']} or newer.")


def _inventory_entries(release: pathlib.Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for path in sorted(release.rglob("*")):
        relative = path.relative_to(release).as_posix()
        if relative in ("inventory.json", "ready.json"):
            continue
        if path.is_symlink():
            result[relative] = {"kind": "symlink", "target": os.readlink(path)}
        elif path.is_dir():
            result[relative] = {"kind": "directory"}
        elif path.is_file():
            result[relative] = {
                "kind": "file",
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        else:
            raise ValueError(f"Unowned special runtime entry: {relative}")
    return result


def _runtime_cache(relative: str, entry: dict[str, Any], expected: dict[str, Any]) -> bool:
    path = pathlib.PurePosixPath(relative)
    if entry["kind"] == "directory" and path.name == "__pycache__":
        return any(
            pathlib.PurePosixPath(name).parent == path.parent and name.endswith(".py")
            for name in expected
        )
    if entry["kind"] != "file" or path.parent.name != "__pycache__":
        return False
    match = re.fullmatch(r"(.+)\.cpython-313(?:\.opt-[12])?\.pyc", path.name)
    return bool(match and str(path.parent.parent / (match[1] + ".py")) in expected)


def _write_inventory(release: pathlib.Path, metadata: dict[str, Any]) -> None:
    entries = _inventory_entries(release)
    entries = {
        name: entry for name, entry in entries.items() if not _runtime_cache(name, entry, entries)
    }
    inventory = release / "inventory.json"
    inventory.write_text(json.dumps({"schema_version": 1, "entries": entries}, indent=2) + "\n")
    ready = {
        "version": metadata["version"],
        "managed_by": "garmin-coach",
        "manifest_sha256": hashlib.sha256((release / "manifest.json").read_bytes()).hexdigest(),
        "inventory_sha256": hashlib.sha256(inventory.read_bytes()).hexdigest(),
    }
    pending = release / ".ready-next"
    pending.write_text(json.dumps(ready, indent=2) + "\n")
    os.replace(pending, release / "ready.json")


def verify_release_inventory(
    release: pathlib.Path, expected_inventory_hash: str | None = None
) -> None:
    """Verify ownership of every present runtime entry before destructive repair/removal.

    Missing inventoried program files are allowed so a missing private runtime can be
    repaired. Added or edited entries are refused; only interpreter caches tied to
    inventoried Python sources may vary.

    Args:
        release: Program release directory to verify without modifying its contents.
        expected_inventory_hash: Optional inventory digest from the outer setup receipt.
    """
    try:
        ready = json.loads((release / "ready.json").read_text())
        inventory_bytes = (release / "inventory.json").read_bytes()
        inventory = json.loads(inventory_bytes)
    except (OSError, ValueError) as exc:
        raise ValueError(
            "Runtime ownership inventory is unavailable; preserve it and choose a new runtime root."
        ) from exc
    digest = hashlib.sha256(inventory_bytes).hexdigest()
    if (
        ready.get("managed_by") != "garmin-coach"
        or ready.get("inventory_sha256") != digest
        or expected_inventory_hash not in (None, digest)
        or inventory.get("schema_version") != 1
    ):
        raise ValueError("Runtime ownership inventory changed; preserve its contents.")
    if (
        ready.get("manifest_sha256")
        != hashlib.sha256((release / "manifest.json").read_bytes()).hexdigest()
    ):
        raise ValueError("Runtime manifest changed; preserve its contents.")
    expected = inventory["entries"]
    for name, entry in _inventory_entries(release).items():
        if not _runtime_cache(name, entry, expected) and expected.get(name) != entry:
            raise ValueError(f"Unknown or edited runtime inventory entry: {name}; preserve it.")


def _install_environment(release: pathlib.Path) -> None:
    python = release / "python" / "bin" / "python3"
    subprocess.run([str(python), "-I", "-m", "venv", str(release / "venv")], check=True)
    subprocess.run(
        [
            str(release / "venv" / "bin" / "python"),
            "-I",
            "-m",
            "pip",
            "--isolated",
            "install",
            "--no-index",
            "--find-links",
            str(release / "bundle" / "wheels"),
            "--require-hashes",
            "-r",
            str(release / "bundle" / "requirements.txt"),
        ],
        check=True,
    )


def _prepared_release(release: pathlib.Path, metadata: dict[str, Any]) -> bool:
    if (release / "ready.json").exists():
        if json.loads((release / "manifest.json").read_text()) != metadata:
            raise ValueError("An existing release has different contents; choose another version.")
        try:
            _runtime_check(release, metadata)
            return True
        except (OSError, subprocess.SubprocessError):
            marker = json.loads((release / "ready.json").read_text())
            if marker.get("managed_by") != "garmin-coach":
                raise ValueError("Broken runtime is not owned; preserve it.") from None
    return False


def prepare_release(manifest: pathlib.Path, root: pathlib.Path) -> pathlib.Path:
    """Prepare a verified private runtime without activating it or touching athlete data.

    Args:
        manifest: Local per-architecture release manifest with HTTPS or file asset URLs.
        root: Managed runtime root, separate from the athlete installation.

    Returns:
        The version-addressed release directory after successful package health checks.
    """
    metadata = _manifest(manifest)
    validate_release_platform(metadata)
    root = root.expanduser().resolve()
    release = root / "releases" / f"{metadata['version']}-{metadata['architecture']}"
    if _prepared_release(release, metadata):
        return release
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="download-", dir=root) as temporary:
        folder = pathlib.Path(temporary)
        python_archive, bundle_archive = folder / "python.tar.gz", folder / "bundle.tar.gz"
        _download(metadata["python"], python_archive)
        _download(metadata["bundle"], bundle_archive)
        if release.exists():
            if release.is_symlink() or not (release / "manifest.json").is_file():
                raise ValueError("Incomplete runtime is not owned by this installer; preserve it.")
            if json.loads((release / "manifest.json").read_text()) != metadata:
                raise ValueError("Incomplete runtime belongs to another release; preserve it.")
            expected = read_state(root).get("managed_release_inventories", {}).get(str(release))
            verify_release_inventory(release, expected)
            shutil.rmtree(release)
        release.mkdir(parents=True)
        (release / "manifest.json").write_text(json.dumps(metadata, indent=2) + "\n")
        _unpack(python_archive, release)
        _unpack(bundle_archive, release / "bundle")
        _install_environment(release)
        _runtime_check(release, metadata)
        _write_inventory(release, metadata)
    return release


RUNTIME_PROBE = """
import asyncio
import pathlib
import json
import sys
from garmin_coach.core import db
from garmin_coach.marts import snapshot
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
home = pathlib.Path(sys.argv[1])
server_command = json.loads(sys.argv[2])
home.mkdir()
(home / '.env').write_text('DB_PATH=./data/garmin.db\\nDATA_START_DATE=2026-06-08\\n')
(home / 'data').mkdir()
conn = db.connect(str(home / 'data' / 'garmin.db'))
db.bootstrap(conn)
db.upsert_daily(conn, 'daily_metrics', {'date': '2026-07-03', 'hrv': 61, 'load_day': 80})
snapshot.rollup(conn)
conn.commit()
conn.close()
async def probe():
    params = StdioServerParameters(command=server_command[0], args=server_command[1:] + ['--home', str(home)])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for tool in ('get_digest', 'get_snapshot'):
                result = await session.call_tool(tool, {})
                assert not result.isError, result
                assert result.structuredContent['freshness']['data_through'] == '2026-07-03'
asyncio.run(asyncio.wait_for(probe(), timeout=45))
print('packaged stdio digest and snapshot: passed; client_acceptance: not_run')
"""


def _probe_environment() -> dict[str, str]:
    excluded = {
        "PYTHONPATH",
        "VIRTUAL_ENV",
        "GARMIN_COACH_HOME",
        "DB_PATH",
        "PLANS_DIR",
        "LOG_PATH",
        "GARMINTOKENS",
        "GARMIN_EMAIL",
        "GARMIN_PASSWORD",
    }
    result = {key: value for key, value in os.environ.items() if key not in excluded}
    return {**result, "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}


def check_runtime_protocol(release: pathlib.Path, *, launcher: pathlib.Path | None = None) -> None:
    """Read synthetic digest/snapshot through the candidate's real stdio MCP server.

    Args:
        release: Candidate runtime; no personal installation is read or changed.
        launcher: Optional stable server launcher for the post-activation release smoke.
    """
    python = str(release / "venv" / "bin" / "python")
    command = [str(launcher)] if launcher else [python, "-I", "-m", "garmin_coach.mcp.server"]
    with tempfile.TemporaryDirectory(prefix="coach-candidate-probe-") as temporary:
        folder = pathlib.Path(temporary)
        subprocess.run(
            [python, "-I", "-c", RUNTIME_PROBE, str(folder / "athlete"), json.dumps(command)],
            cwd=folder,
            env=_probe_environment(),
            check=True,
            timeout=60,
        )


def _runtime_check(release: pathlib.Path, metadata: dict[str, Any]) -> None:
    script = (
        "import importlib.metadata as m; from importlib.resources import files; "
        "import garmin_coach.cli, garmin_coach.mcp.server; "
        f"assert m.version('garmin-coach') == {metadata['version']!r}; "
        "assert files('garmin_coach.core').joinpath('schema.sql').is_file()"
    )
    subprocess.run([str(release / "venv" / "bin" / "python"), "-I", "-c", script], check=True)
    check_runtime_protocol(release)


def _guard_active_pointer(root: pathlib.Path) -> None:
    current = root / "current"
    if not current.exists() and not current.is_symlink():
        return
    expected = read_state(root).get("release_dir")
    if (
        not current.is_symlink()
        or expected is None
        or current.resolve() != pathlib.Path(expected).resolve()
    ):
        raise ValueError("Active runtime pointer is not owned or has changed; preserve it.")


def activate_release(release: pathlib.Path, root: pathlib.Path) -> None:
    """Activate an already checked release and install stable CLI/MCP launchers."""
    root, release = root.expanduser().resolve(), release.resolve()
    if release.parent != root / "releases" or not (release / "ready.json").is_file():
        raise ValueError("Only a prepared owned release can be activated.")
    _guard_active_pointer(root)
    metadata = _manifest(release / "manifest.json")
    _runtime_check(release, metadata)
    (root / "bin").mkdir(exist_ok=True)
    for name, module in (("garmin-coach", "cli"), ("garmin-coach-mcp", "mcp.server")):
        launcher = root / "bin" / name
        if launcher.exists():
            owned = read_state(root).get("owned_launchers", {})
            if owned.get(str(launcher)) != hashlib.sha256(launcher.read_bytes()).hexdigest():
                raise ValueError("Stable launcher is not owned or was edited; preserve it.")
        launcher.write_text(
            "#!/bin/sh\nset -eu\n"
            'runtime_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)\n'
            'python="$runtime_root/current/venv/bin/python"\n'
            'if [ ! -x "$python" ]; then\n'
            '  echo "Garmin Coach runtime missing. Re-run your versioned installer to repair it; '
            'keep your athlete installation." >&2\n  exit 2\nfi\n'
            f'exec "$python" -I -m garmin_coach.{module} "$@"\n'
        )
        launcher.chmod(0o755)
    pointer = root / ".current-next"
    pointer.unlink(missing_ok=True)
    pointer.symlink_to(release.relative_to(root))
    os.replace(pointer, root / "current")
    state = read_state(root)
    ready = json.loads((release / "ready.json").read_text())
    state.setdefault("managed_release_inventories", {})[str(release)] = ready["inventory_sha256"]
    state.setdefault("managed_releases", {})[str(release)] = hashlib.sha256(
        (release / "manifest.json").read_bytes()
    ).hexdigest()
    state["owned_launchers"] = {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (root / "bin").iterdir()
        if path.name in ("garmin-coach", "garmin-coach-mcp")
    }
    state.update(
        {
            "managed_by": "garmin-coach",
            "release": metadata["version"],
            "source_commit": metadata["source_commit"],
            "release_dir": str(release),
            "manifest_sha256": hashlib.sha256((release / "manifest.json").read_bytes()).hexdigest(),
        }
    )
    write_state(root, state)


def _execute(arguments: list[str]) -> None:
    subprocess.run(arguments, check=True)


def _schedule_observation(previous: dict[str, Any], path: pathlib.Path) -> dict[str, Any]:
    activation = previous.get("activation_status", previous.get("status", "unknown"))
    result = {
        **previous,
        "status": activation,
        "activation_status": activation,
        "choice": previous.get("choice", "enabled"),
        "actions": [],
    }
    if not path.is_file():
        result.update(
            status="missing",
            actions=["Re-enable daily refresh explicitly to recreate its missing job."],
        )
    elif (
        previous.get("path") != str(path)
        or previous.get("sha256") != hashlib.sha256(path.read_bytes()).hexdigest()
    ):
        result.update(
            status="changed", actions=["Review the changed scheduler job; it has been preserved."]
        )
    return result


def _existing_schedule(
    path: pathlib.Path, state: dict[str, Any], when: str, execute: Any
) -> dict[str, Any] | None:
    previous = state.get("scheduler", {})
    if path.exists():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if previous.get("path") != str(path) or previous.get("sha256") != digest:
            raise ValueError("Existing scheduler job is not owned or has changed; preserve it.")
        if previous.get("time") == when:
            if previous.get("status") != "enabled":
                execute(["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)])
                previous.update(status="enabled", activation_status="enabled")
                state["scheduler"] = previous
            return previous
        raise ValueError("Schedule already enabled at another time; remove it explicitly first.")
    return None


def _schedule_content(home: pathlib.Path, command: list[str], label: str, when: str) -> bytes:
    hour, minute = map(int, when.split(":"))
    return plistlib.dumps(
        {
            "Label": label,
            "ProgramArguments": command,
            "WorkingDirectory": str(home),
            "StartCalendarInterval": {"Hour": hour, "Minute": minute},
            "EnvironmentVariables": {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
            "StandardOutPath": str(home / "logs" / "launchd.out.log"),
            "StandardErrorPath": str(home / "logs" / "launchd.err.log"),
        }
    )


def _schedule_record(
    home: pathlib.Path, command: list[str], label: str, path: pathlib.Path, when: str
) -> dict[str, Any]:
    return {
        "status": "disabled",
        "time": when,
        "timezone": "local",
        "label": label,
        "path": str(path),
        "plist_path": str(path),
        "selected_home": str(home),
        "command": command,
        "stdout_path": str(home / "logs" / "launchd.out.log"),
        "stderr_path": str(home / "logs" / "launchd.err.log"),
    }


def _enable_schedule(
    home: pathlib.Path,
    root: pathlib.Path,
    record: dict[str, Any],
    state: dict[str, Any],
    execute: Any,
) -> dict[str, Any]:
    path = pathlib.Path(record["path"])
    command, label, when = record["command"], record["label"], record["time"]
    path.parent.mkdir(parents=True, exist_ok=True)
    (home / "logs").mkdir(parents=True, exist_ok=True)
    content = _schedule_content(home, command, label, when)
    path.write_bytes(content)
    record.update(
        {"status": "prepared", "choice": "enabled", "sha256": hashlib.sha256(content).hexdigest()}
    )
    state["scheduler"] = record
    write_state(root, state)
    execute(["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)])
    record["status"] = "enabled"
    state["scheduler"] = record
    write_state(root, state)
    return record


def configure_schedule(
    home: pathlib.Path,
    root: pathlib.Path,
    when: str,
    *,
    enabled: bool,
    agents_dir: pathlib.Path | None = None,
    execute: Any = _execute,
) -> dict[str, Any]:
    """Preview or enable one owned per-installation daily user LaunchAgent.

    Args:
        home: Explicit selected athlete installation.
        root: Managed runtime root containing the stable CLI launcher.
        when: Daily local time in HH:MM format.
        enabled: Whether the user explicitly opted in.
        agents_dir: Optional temporary LaunchAgents directory for offline validation.
        execute: Scheduler boundary; tests inject a fake launchctl executor.

    Returns:
        The disabled/enabled state, preview and exact ownership receipt.
    """
    if not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", when):
        raise ValueError("Schedule time must be HH:MM in local time.")
    home, root = home.expanduser().resolve(), root.expanduser().resolve()
    label = "com.garmincoach.daily." + hashlib.sha256(str(home).encode()).hexdigest()[:12]
    agents = agents_dir or pathlib.Path.home() / "Library" / "LaunchAgents"
    path = agents / f"{label}.plist"
    command = [str(root / "bin" / "garmin-coach"), "--home", str(home), "daily"]
    record = _schedule_record(home, command, label, path, when)
    state = read_state(root)
    previous = state.get("scheduler", {})
    if not enabled:
        if previous.get("choice") == "enabled" or previous.get("status") in ("enabled", "prepared"):
            record = _schedule_observation(previous, path)
            state["scheduler"] = record
            write_state(root, state)
        return record
    existing = _existing_schedule(path, state, when, execute)
    if existing is not None:
        state["scheduler"] = existing
        write_state(root, state)
        return existing
    return _enable_schedule(home, root, record, state, execute)


def _run_stage(arguments: list[str]) -> int:
    return subprocess.run(arguments, check=False).returncode


def _readiness(home: pathlib.Path) -> str:
    import sqlite3

    from garmin_coach.core import installation

    try:
        settings = installation.activate(home)
        with closing(installation.read_database(settings)) as connection:
            horizon = connection.execute("SELECT MAX(date) FROM daily_metrics").fetchone()[0]
        return "ready" if horizon else "missing_data"
    except (ValueError, OSError, sqlite3.Error):
        return "not_ready"


def _separate_roots(home: pathlib.Path, root: pathlib.Path) -> None:
    if home.is_relative_to(root) or root.is_relative_to(home):
        raise ValueError("Program and athlete directories must be separate, without overlap.")


def _onboarding_files(home: pathlib.Path) -> set[pathlib.Path]:
    reports = home / "reports"
    return {
        path
        for pattern in ("*/setup-*/digest.json", "*/setup-*/status.json")
        for path in reports.glob(pattern)
        if path.is_file()
    }


def _onboarding_result(
    home: pathlib.Path, before: set[pathlib.Path], code: int, readiness: str
) -> dict[str, Any]:
    fresh = _onboarding_files(home) - before
    metadata: dict[str, Any] = {"import_complete": None, "failure_kind": None}
    if fresh:
        latest = max(fresh, key=lambda path: path.stat().st_mtime_ns)
        try:
            observed = json.loads(latest.read_text())["onboarding"]
            metadata = {
                key: observed.get(key)
                for key in (
                    "import_complete",
                    "read_ready",
                    "failure_kind",
                    "completed_through",
                    "history_source",
                )
            }
        except (OSError, ValueError, KeyError):
            pass
    if code != 0 or metadata.get("import_complete") is False:
        status = "partial" if metadata.get("completed_through") else "incomplete"
    elif not metadata.get("read_ready"):
        status = "configured"
    else:
        status = "incomplete" if readiness == "not_ready" else "complete"
    return {"status": status, "exit_code": code, **metadata}


def _client_result(
    command: list[str], root: pathlib.Path, selected: list[str], code: int
) -> dict[str, Any]:
    result: dict[str, Any] = {"status": "incomplete", "exit_code": code, "by_client": {}}
    try:
        observed = subprocess.run(
            command + ["clients", "status", "--runtime-root", str(root)],
            check=True,
            capture_output=True,
            text=True,
        )
        receipt = json.loads(observed.stdout)
        result["by_client"] = {
            name: receipt.get("clients", {}).get(
                name,
                {
                    "state": "unknown",
                    "actions": ["Repeat client configuration and inspect its status."],
                },
            )
            for name in selected
        }
        states = [record["state"] for record in result["by_client"].values()]
        healthy = (
            code == 0
            and receipt.get("integrity") == "verified"
            and receipt.get("runtime_available")
        )
        if healthy and all(state in ("ready", "needs_activation") for state in states):
            result["status"] = "needs_activation" if "needs_activation" in states else "ready"
        result["integrity"] = receipt.get("integrity", "unknown")
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        result["by_client"] = {
            name: {
                "state": "unknown",
                "actions": ["Repeat client configuration; a verified status was unavailable."],
            }
            for name in selected
        }
    return result


def finish_setup(
    home: pathlib.Path,
    root: pathlib.Path,
    *,
    clients: str,
    setup_arguments: list[str] | None = None,
    execute: Any = _run_stage,
) -> dict[str, Any]:
    """Run the existing interactive athlete and client setup with visible terminal prompts.

    Args:
        home: Explicit selected athlete installation.
        root: Managed runtime root containing the active stable launchers.
        clients: Comma-separated supported client identifiers.
        setup_arguments: Optional first-run arguments forwarded to athlete onboarding.
        execute: Terminal subprocess boundary, replaceable for offline tests.

    Returns:
        Honest stage observations, preserving incomplete setup for a repeated run.
    """
    selected = clients.split(",")
    if not selected or any(name not in ("work", "codex", "claude-code") for name in selected):
        raise ValueError("Choose clients from work,codex,claude-code.")
    home, root = home.expanduser().resolve(), root.expanduser().resolve()
    _separate_roots(home, root)
    state = read_state(root)
    if state.get("selected_home") not in (None, str(home)):
        raise ValueError("This runtime already selects another athlete installation; preserve it.")
    state.update({"selected_home": str(home), "selected_clients": selected})
    write_state(root, state)
    command = [str(root / "bin" / "garmin-coach"), "--home", str(home)]
    before = _onboarding_files(home)
    code = execute(command + ["setup", *(setup_arguments or [])])
    readiness = _readiness(home)
    onboarding = _onboarding_result(home, before, code, readiness)
    outcome = onboarding["status"]
    result: dict[str, Any] = {
        "onboarding": onboarding,
        "coach_read": readiness,
        "clients": {"status": "not_run"},
        "client_acceptance": "not_run",
    }
    if outcome == "complete":
        client_code = execute(
            command + ["clients", "configure", "--clients", clients, "--runtime-root", str(root)]
        )
        result["clients"] = _client_result(command, root, selected, client_code)
    result["next"] = (
        "Repair the listed client registration problems and repeat setup."
        if outcome == "complete" and result["clients"]["status"] == "incomplete"
        else "Open a fresh chat in each selected client, complete its trust/activation steps, and use the first-read prompt."
        if outcome == "complete"
        else "Repeat this versioned installer with the same athlete installation to resume setup."
    )
    state = read_state(root)
    state["stages"] = result
    write_state(root, state)
    return result


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["install"])
    parser.add_argument("--manifest", type=pathlib.Path, required=True)
    parser.add_argument("--runtime-root", type=pathlib.Path, default=default_runtime_root())
    parser.add_argument("--home", type=pathlib.Path)
    parser.add_argument("--clients")
    parser.add_argument("--to", dest="to_date")
    parser.add_argument("--configure-only", action="store_true")
    schedule = parser.add_mutually_exclusive_group()
    schedule.add_argument("--daily", metavar="HH:MM")
    schedule.add_argument("--no-schedule", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    return parser.parse_args(argv)


def _client_choice(args: argparse.Namespace) -> str:
    if args.clients is not None:
        return str(args.clients)
    saved = read_state(args.runtime_root).get("selected_clients", [])
    if saved:
        return ",".join(saved)
    if not sys.stdin.isatty():
        raise ValueError("Interactive setup needs a terminal; supply --clients to select clients.")
    return input("Select clients (work,codex,claude-code; comma-separated): ").strip()


def _offer_schedule(args: argparse.Namespace, result: dict[str, Any]) -> dict[str, Any]:
    enabled, when = args.daily is not None, args.daily or "06:00"
    preview = configure_schedule(args.home, args.runtime_root, when, enabled=False)
    print("Daily refresh preview: " + json.dumps(preview))
    if not args.no_schedule and args.daily is None and sys.stdin.isatty():
        enabled = input("Enable this daily refresh? [y/N]: ").strip().lower() in ("y", "yes")
    if enabled and result["onboarding"]["status"] != "complete":
        raise ValueError("Complete interactive onboarding before enabling unattended refresh.")
    return configure_schedule(args.home, args.runtime_root, when, enabled=enabled)


def _complete_setup(args: argparse.Namespace) -> int:
    clients = _client_choice(args)
    print(f"Athlete installation: {args.home.expanduser().resolve()}")
    forwarded = ["--to", args.to_date] if args.to_date is not None else []
    if args.configure_only:
        forwarded.append("--configure-only")
    result = finish_setup(args.home, args.runtime_root, clients=clients, setup_arguments=forwarded)
    result["scheduler"] = _offer_schedule(args, result)
    print(json.dumps(result, indent=2))
    print(
        "First-read prompt: Read the coach skill, reporting reference and my profile. "
        "Use only get_digest and get_snapshot. Explain the evidence dates, missing data "
        "and freshness. Do not refresh Garmin or write anything. End with the digest disclaimer."
    )
    complete = result["onboarding"]["status"] == "complete"
    return 0 if complete and result["clients"]["status"] in ("ready", "needs_activation") else 1


def _validate_existing_installation(release: pathlib.Path, home: pathlib.Path) -> None:
    if not (home / ".env").is_file():
        return
    script = (
        "import pathlib, sys; from garmin_coach.core import installation; "
        "settings = installation.activate(sys.argv[1]); "
        "path = pathlib.Path(settings.db_path); "
        "connection = installation.read_database(settings) if path.exists() else None; "
        "connection.close() if connection is not None else None"
    )
    subprocess.run(
        [str(release / "venv" / "bin" / "python"), "-I", "-c", script, str(home)],
        check=True,
        timeout=60,
    )


def _install(args: argparse.Namespace, argv: list[str] | None) -> int:
    args.runtime_root = args.runtime_root.expanduser().resolve()
    state = read_state(args.runtime_root)
    args.home = args.home or pathlib.Path(
        state.get("selected_home", pathlib.Path.home() / "GarminCoach")
    )
    home = args.home.expanduser().resolve()
    _separate_roots(home, args.runtime_root.expanduser().resolve())
    if state.get("selected_home") not in (None, str(home)):
        raise ValueError("This runtime already selects another athlete installation; preserve it.")
    release = prepare_release(args.manifest, args.runtime_root)
    _validate_existing_installation(release, home)
    activate_release(release, args.runtime_root)
    if args.prepare_only:
        print(json.dumps({"runtime": "ready", "release": str(release), "onboarding": "not_run"}))
        return 0
    target_python = release / "venv" / "bin" / "python"
    if pathlib.Path(sys.executable).resolve() != target_python.resolve():
        os.execv(
            str(target_python),
            [
                str(target_python),
                "-I",
                "-m",
                "garmin_coach.distribution",
                *(argv if argv is not None else sys.argv[1:]),
            ],
        )
    return _complete_setup(args)


def main(argv: list[str] | None = None) -> int:
    """Run versioned private-runtime installation from a verified release manifest."""
    args = _arguments(argv)
    try:
        return _install(args, argv)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, tarfile.TarError) as exc:
        print(f"installer: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
