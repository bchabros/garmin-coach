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
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import uuid
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


def atomic_write(path: pathlib.Path, content: bytes, *, mode: int = 0o600) -> None:
    """Replace a regular file atomically, cleaning the private temporary on failure.

    Kept here so the standalone installer and packaged callers share the same
    standard-library-only implementation. Symlink activation remains separate.

    Args:
        path: Destination whose parent directory is created when needed.
        content: Complete replacement bytes.
        mode: Permissions of the replacement, private by default.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = pathlib.Path(stream.name)
        try:
            stream.write(content)
            stream.close()
            temporary.chmod(mode)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def write_state(root: pathlib.Path, state: dict[str, Any]) -> None:
    """Atomically persist non-personal setup observations."""
    record = dict(state, schema_version=SCHEMA_VERSION)
    atomic_write(root / "install-state.json", (json.dumps(record, indent=2) + "\n").encode())


def _download(asset: dict[str, Any], destination: pathlib.Path) -> None:
    url = str(asset["url"])
    if not url.startswith(("https://", "file://")):
        raise ValueError("Release assets must use HTTPS or a local file URL.")
    expected = str(asset["sha256"])
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("Release asset checksum is missing or invalid.")
    cache = os.environ.get("GARMIN_COACH_ASSET_CACHE")
    cached = pathlib.Path(cache) / destination.name if cache else None
    if cached is not None and cached.is_file():
        shutil.copyfile(cached, destination)
    else:
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
    if platform.machine() == "x86_64":
        translated = subprocess.run(
            ["/usr/sbin/sysctl", "-in", "sysctl.proc_translated"],
            capture_output=True,
            text=True,
            check=False,
        )
        if translated.returncode == 0 and translated.stdout.strip() == "1":
            raise ValueError(
                "Rosetta Terminal detected; open a native Apple Silicon Terminal and "
                "run the arm64 installer."
            )
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


def _build_checkpoint(
    root: pathlib.Path, release: pathlib.Path, metadata: dict[str, Any], stage: str
) -> None:
    state = read_state(root)
    state.setdefault("builds", {})[str(release)] = {
        "stage": stage,
        "manifest": metadata,
        "owned_entries": _inventory_entries(release) if release.exists() else {},
        "action": "Repeat the same installer; incomplete build files are preserved.",
    }
    write_state(root, state)


def _release_destination(root: pathlib.Path, metadata: dict[str, Any]) -> pathlib.Path:
    if (root / "releases").is_symlink():
        raise ValueError("Program release directory is a symlink; preserve it.")
    canonical = root / "releases" / f"{metadata['version']}-{metadata['architecture']}"
    candidates = [canonical]
    builds = read_state(root).get("builds", {})
    candidates += [pathlib.Path(name) for name in builds if pathlib.Path(name) != canonical]
    for release in candidates:
        if release.parent != root / "releases" or not release.exists():
            continue
        if release.is_symlink():
            raise ValueError("Release directory is a symlink; preserve it.")
        try:
            observed = json.loads((release / "manifest.json").read_text())
        except (OSError, ValueError):
            observed = None
        if observed != metadata:
            pending = builds.get(str(release), {})
            if pending.get("manifest") == metadata and not (release / "ready.json").exists():
                continue
            if release == canonical:
                raise ValueError(
                    "Existing release has different or unverified contents; preserve it."
                )
            continue
        if (release / "ready.json").exists():
            expected = read_state(root).get("managed_release_inventories", {}).get(str(release))
            verify_release_inventory(release, expected)
            if _prepared_release(release, metadata):
                return release
    if not canonical.exists():
        return canonical
    return canonical.with_name(canonical.name + "-retry-" + uuid.uuid4().hex)


def _build_release(
    manifest: pathlib.Path, root: pathlib.Path, release: pathlib.Path, metadata: dict[str, Any]
) -> None:
    with tempfile.TemporaryDirectory(prefix="download-", dir=root) as temporary:
        folder = pathlib.Path(temporary)
        python_archive, bundle_archive = folder / "python.tar.gz", folder / "bundle.tar.gz"
        _download(metadata["python"], python_archive)
        _download(metadata["bundle"], bundle_archive)
        # Journal ownership and intended work before the first program-directory write.
        _build_checkpoint(root, release, metadata, "creating")
        release.mkdir(parents=True)
        shutil.copyfile(manifest, release / "manifest.json")
        for archive, target, stage in (
            (python_archive, release, "extracting_python"),
            (bundle_archive, release / "bundle", "extracting_bundle"),
        ):
            _build_checkpoint(root, release, metadata, stage)
            _unpack(archive, target)
        _build_checkpoint(root, release, metadata, "installing_environment")
        _install_environment(release)
        _build_checkpoint(root, release, metadata, "checking_protocol")
        _runtime_check(release, metadata)
        _write_inventory(release, metadata)
        _build_checkpoint(root, release, metadata, "ready")


def prepare_release(manifest: pathlib.Path, root: pathlib.Path) -> pathlib.Path:
    """Prepare a verified runtime beside existing releases, preserving interrupted builds.

    Args:
        manifest: Local per-architecture release manifest with HTTPS or file asset URLs.
        root: Managed runtime root, separate from the athlete installation.

    Returns:
        A ready release directory. Repairs return a new sibling and never alter the
        active runtime. Unfinished siblings remain preserved for inspection/recovery.
    """
    metadata = _manifest(manifest)
    validate_release_platform(metadata)
    root = root.expanduser().resolve()
    release = _release_destination(root, metadata)
    if (release / "ready.json").exists():
        return release
    root.mkdir(parents=True, exist_ok=True)
    _build_release(manifest, root, release, metadata)
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
    params = StdioServerParameters(
        command=server_command[0], args=server_command[1:] + ['--home', str(home)]
    )
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
        "DATA_START_DATE",
        "WEEKLY_TEMPLATE",
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
            env={**_probe_environment(), "HOME": str(folder)},
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
    with tempfile.TemporaryDirectory(prefix="coach-import-probe-") as temporary:
        subprocess.run(
            [str(release / "venv" / "bin" / "python"), "-I", "-c", script],
            env={**_probe_environment(), "HOME": temporary},
            check=True,
            timeout=60,
        )
    check_runtime_protocol(release)


def _guard_active_pointer(root: pathlib.Path) -> None:
    current = root / "current"
    if not current.exists() and not current.is_symlink():
        return
    state = read_state(root)
    pending = state.get("pending_activation", {})
    expected = [
        state.get("release_dir"),
        pending.get("release_dir"),
        pending.get("previous_release_dir"),
    ]
    if not current.is_symlink() or not any(
        path is not None and current.resolve() == pathlib.Path(path).resolve() for path in expected
    ):
        raise ValueError("Active runtime pointer is not owned or has changed; preserve it.")


def _launcher_content(module: str) -> bytes:
    return (
        "#!/bin/sh\nset -eu\n"
        'runtime_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)\n'
        'python="$runtime_root/current/venv/bin/python"\n'
        'if [ ! -x "$python" ]; then\n'
        '  echo "Garmin Coach runtime missing. Re-run your versioned installer to repair it; '
        'keep your athlete installation." >&2\n  exit 2\nfi\n'
        f'exec "$python" -I -m garmin_coach.{module} "$@"\n'
    ).encode()


def verify_activation_launchers(root: pathlib.Path) -> None:
    """Refuse edited launcher bytes, including either journaled side of an activation.

    Args:
        root: Managed runtime root whose receipt records the current or pending ownership.

    Raises:
        ValueError: A launcher/directory is a symlink or its bytes have no owned hash.
    """
    state = read_state(root)
    if (root / "bin").is_symlink():
        raise ValueError("Stable launcher directory is a symlink; preserve it.")
    for name in ("garmin-coach", "garmin-coach-mcp"):
        launcher = root / "bin" / name
        pending = state.get("pending_activation", {}).get("launchers", {}).get(str(launcher), {})
        expected = {
            state.get("owned_launchers", {}).get(str(launcher)),
            pending.get("previous_sha256"),
            pending.get("sha256"),
        }
        if launcher.is_symlink() or (
            launcher.exists() and hashlib.sha256(launcher.read_bytes()).hexdigest() not in expected
        ):
            raise ValueError("Stable launcher is not owned or was edited; preserve it.")


def _activation_plan(root: pathlib.Path, release: pathlib.Path) -> dict[str, Any]:
    verify_activation_launchers(root)
    state = read_state(root)
    pending = state.get("pending_activation", {})
    if pending and pending.get("release_dir") != str(release):
        if state.get("release_dir") != str(release):
            raise ValueError("Another activation is pending; repeat its installer first.")
        pending = {}
    current = root / "current"
    previous_pointer = str(current.resolve()) if current.is_symlink() else None
    plan = pending or {
        "release_dir": str(release),
        "previous_release_dir": previous_pointer,
        "launchers": {},
    }
    for name, module in (("garmin-coach", "cli"), ("garmin-coach-mcp", "mcp.server")):
        launcher = root / "bin" / name
        expected = hashlib.sha256(_launcher_content(module)).hexdigest()
        previous = hashlib.sha256(launcher.read_bytes()).hexdigest() if launcher.exists() else None
        entry = plan["launchers"].setdefault(
            str(launcher), {"previous_sha256": previous, "sha256": expected}
        )
        if entry["sha256"] != expected:
            raise ValueError("Pending launcher belongs to a different installer; preserve it.")
    state["pending_activation"] = plan
    write_state(root, state)
    return state


def _activate_files(root: pathlib.Path, release: pathlib.Path) -> None:
    (root / "bin").mkdir(exist_ok=True)
    for name, module in (("garmin-coach", "cli"), ("garmin-coach-mcp", "mcp.server")):
        launcher = root / "bin" / name
        atomic_write(launcher, _launcher_content(module), mode=0o755)
    with tempfile.NamedTemporaryFile(dir=root, delete=False) as stream:
        pointer = pathlib.Path(stream.name)
    pointer.unlink()
    pointer.symlink_to(release.relative_to(root))
    os.replace(pointer, root / "current")


def _record_activation(root: pathlib.Path, release: pathlib.Path, metadata: dict[str, Any]) -> None:
    state = read_state(root)
    ready = json.loads((release / "ready.json").read_text())
    manifest_hash = hashlib.sha256((release / "manifest.json").read_bytes()).hexdigest()
    state.setdefault("managed_release_inventories", {})[str(release)] = ready["inventory_sha256"]
    state.setdefault("managed_releases", {})[str(release)] = manifest_hash
    state["owned_launchers"] = {
        path: entry["sha256"] for path, entry in state["pending_activation"]["launchers"].items()
    }
    state.update(
        managed_by="garmin-coach",
        release=metadata["version"],
        source_commit=metadata["source_commit"],
        release_dir=str(release),
        manifest_sha256=manifest_hash,
    )
    state.pop("pending_activation", None)
    write_state(root, state)


def activate_release(release: pathlib.Path, root: pathlib.Path) -> None:
    """Activate a checked release using a resumable pre-write ownership journal."""
    root, release = root.expanduser().resolve(), release.resolve()
    if release.parent != root / "releases" or not (release / "ready.json").is_file():
        raise ValueError("Only a prepared owned release can be activated.")
    _guard_active_pointer(root)
    metadata = _manifest(release / "manifest.json")
    verify_release_inventory(release)
    _runtime_check(release, metadata)
    _activation_plan(root, release)
    _activate_files(root, release)
    _record_activation(root, release, metadata)


def _execute(arguments: list[str]) -> None:
    subprocess.run(arguments, check=True)


def _schedule_observation(previous: dict[str, Any], path: pathlib.Path) -> dict[str, Any]:
    activation = previous.get("activation_status", previous.get("status", "unknown"))
    result = {
        **previous,
        "status": activation,
        "activation_status": activation,
        "choice": previous.get("choice", "enabled"),
        "actions": previous.get("actions", []),
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
    path: pathlib.Path, root: pathlib.Path, state: dict[str, Any], when: str, execute: Any
) -> dict[str, Any] | None:
    previous = state.get("scheduler", {})
    if path.is_symlink():
        raise ValueError("Scheduler job is a symlink; preserve it.")
    if path.exists():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if previous.get("path") != str(path) or previous.get("sha256") != digest:
            raise ValueError("Existing scheduler job is not owned or has changed; preserve it.")
        if previous.get("time") == when:
            return _bootstrap_schedule(root=root, record=previous, state=state, execute=execute)
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


def _printed_schedule_matches(record: dict[str, Any], output: str) -> bool:
    # launchctl documents this as diagnostic output, not a stable machine API.
    # Accept only a complete known identity; changed/missing formats fail closed.
    target = f"gui/{os.getuid()}/{record['label']} = {{"
    if not output.splitlines() or output.splitlines()[0].strip() != target:
        return False
    fields = {
        "path": record["path"],
        "program": record["command"][0],
        "working directory": record["selected_home"],
        "stdout path": record["stdout_path"],
        "stderr path": record["stderr_path"],
    }
    for key, expected in fields.items():
        found = re.findall(r"(?m)^[ \t]*" + re.escape(key) + r" = (.*)$", output)
        if found != [expected]:
            return False
    blocks = re.findall(r"(?ms)^[ \t]*arguments = \{\n(.*?)^[ \t]*\}", output)
    if len(blocks) != 1 or [line.strip() for line in blocks[0].splitlines()] != record["command"]:
        return False
    return _printed_schedule_time(record, output)


def _printed_schedule_time(record: dict[str, Any], output: str) -> bool:
    streams = re.findall(r"(?m)^[ \t]*stream = (.*)$", output)
    descriptors = re.findall(r"(?ms)^[ \t]*descriptor = \{\n(.*?)^[ \t]*\}", output)
    if streams != ["com.apple.launchd.calendarinterval"] or len(descriptors) != 1:
        return False
    lines = [line.strip() for line in descriptors[0].splitlines() if line.strip()]
    matches = [re.fullmatch(r'"([A-Za-z]+)" => ([0-9]+)', line) for line in lines]
    if len(matches) != 2 or any(match is None for match in matches):
        return False
    pairs = [(match.group(1), match.group(2)) for match in matches if match is not None]
    hour, minute = map(int, record["time"].split(":"))
    return dict(pairs) == {"Hour": str(hour), "Minute": str(minute)}


def _owned_schedule_is_loaded(record: dict[str, Any]) -> bool:
    result = subprocess.run(
        ["/bin/launchctl", "print", f"gui/{os.getuid()}/{record['label']}"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        if re.search(r"Could not find service|No such (?:process|service)", result.stderr or ""):
            return False
        raise ValueError(
            "Could not inspect the daily job; preserve it and check launchd before retrying."
        )
    if not _printed_schedule_matches(record, result.stdout):
        raise ValueError(
            "Loaded daily job identity could not be verified; preserve it and inspect its "
            "path, arguments and schedule before retrying."
        )
    return True


def _bootstrap_schedule(
    root: pathlib.Path, record: dict[str, Any], state: dict[str, Any], execute: Any
) -> dict[str, Any]:
    try:
        if not _owned_schedule_is_loaded(record):
            execute(["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", record["path"]])
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        record.update(
            status="prepared",
            activation_status="failed",
            actions=[str(exc)]
            if isinstance(exc, ValueError)
            else [
                "Daily refresh activation failed; the saved job may load at the next login. "
                "Repeat setup to retry, or explicitly remove the owned scheduler job."
            ],
        )
        state["scheduler"] = record
        write_state(root, state)
        raise
    record.update(status="enabled", activation_status="enabled", actions=[])
    state["scheduler"] = record
    write_state(root, state)
    return record


def _guard_new_schedule(record: dict[str, Any], state: dict[str, Any], content: bytes) -> None:
    if not _owned_schedule_is_loaded(record):
        return
    previous = state.get("scheduler", {})
    if (
        previous.get("path") != record["path"]
        or previous.get("sha256") != hashlib.sha256(content).hexdigest()
    ):
        raise ValueError(
            "Loaded daily job has no matching ownership receipt; preserve it and inspect "
            "launchd before creating another job."
        )


def _enable_schedule(
    home: pathlib.Path,
    root: pathlib.Path,
    record: dict[str, Any],
    state: dict[str, Any],
    execute: Any,
) -> dict[str, Any]:
    path = pathlib.Path(record["path"])
    command, label, when = record["command"], record["label"], record["time"]
    content = _schedule_content(home, command, label, when)
    _guard_new_schedule(record, state, content)
    record.update(
        status="preparing",
        activation_status="not_run",
        choice="enabled",
        sha256=hashlib.sha256(content).hexdigest(),
        actions=["Repeat setup to finish installing and activating the owned daily job."],
    )
    state["scheduler"] = record
    write_state(root, state)
    path.parent.mkdir(parents=True, exist_ok=True)
    (home / "logs").mkdir(parents=True, exist_ok=True)
    atomic_write(path, content)
    record.update(status="prepared", activation_status="not_run")
    write_state(root, state)
    return _bootstrap_schedule(root, record, state, execute)


def _legacy_schedule(agents: pathlib.Path, *, inspect_loaded: bool) -> list[str]:
    legacy = agents / "com.garmincoach.daily.plist"
    present = legacy.exists() or legacy.is_symlink()
    if not present and inspect_loaded:
        try:
            result = subprocess.run(
                ["/bin/launchctl", "print", f"gui/{os.getuid()}/com.garmincoach.daily"],
                check=False,
                capture_output=True,
                timeout=10,
            )
            present = result.returncode == 0
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError(
                "Could not inspect the legacy daily job; check launchd before enabling."
            ) from exc
    if present:
        return [
            "An existing legacy daily job com.garmincoach.daily is present. "
            "Review and explicitly disable that job before enabling another daily refresh."
        ]
    return []


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
    warnings = _legacy_schedule(agents, inspect_loaded=enabled and agents_dir is None)
    if warnings:
        record["warnings"] = warnings
        if enabled:
            raise ValueError(warnings[0])
    state = read_state(root)
    previous = state.get("scheduler", {})
    if not enabled:
        if previous.get("choice") == "enabled" or previous.get("status") in ("enabled", "prepared"):
            record = _schedule_observation(previous, path)
            state["scheduler"] = record
            write_state(root, state)
        return record
    existing = _existing_schedule(path, root, state, when, execute)
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


def _continue_onboarding(
    home: pathlib.Path,
    command: list[str],
    arguments: list[str] | None,
    execute: Any,
    previous: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    readiness = _readiness(home)
    if not arguments and previous.get("status") == "complete" and readiness == "ready":
        return {**previous, "reused": True}, readiness
    before = _onboarding_files(home)
    code = execute(command + ["setup", *(arguments or [])])
    readiness = _readiness(home)
    return _onboarding_result(home, before, code, readiness), readiness


def _setup_next(result: dict[str, Any]) -> str:
    if result["onboarding"]["status"] != "complete":
        return "Repeat this versioned installer with the same athlete installation to resume setup."
    if result["clients"]["status"] == "incomplete":
        return "Repair the listed client registration problems and repeat setup."
    return (
        "Open a fresh chat in each selected client, complete its trust/activation steps, "
        "and use the first-read prompt."
    )


def _recover_setup_operation(
    home: pathlib.Path, root: pathlib.Path, state: dict[str, Any], execute: Any
) -> dict[str, Any]:
    if not state.get("operation"):
        return state
    release = pathlib.Path(state["release_dir"]).resolve()
    if release.parent != root / "releases" or not (release / "ready.json").is_file():
        raise ValueError(
            "Interrupted update needs a checked corrective runtime. Preserve the journal/backups "
            "and repeat the corrective versioned installer."
        )
    command = [
        str(release / "venv" / "bin" / "python"),
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
        str(release / "manifest.json"),
    ]
    recovery = (
        "Interrupted update recovery is incomplete. Preserve the journal and backups; "
        "retry with the checked candidate engine: " + shlex.join(command)
    )
    try:
        code = execute(command)
        observed = read_state(root)
    except (OSError, ValueError, subprocess.SubprocessError, KeyboardInterrupt) as exc:
        raise ValueError(recovery) from exc
    if code != 0 or observed.get("operation"):
        raise ValueError(recovery)
    return observed


def _prepare_setup(
    home: pathlib.Path, root: pathlib.Path, clients: str, execute: Any
) -> tuple[pathlib.Path, pathlib.Path, list[str], dict[str, Any]]:
    selected = clients.split(",")
    if not selected or any(name not in ("work", "codex", "claude-code") for name in selected):
        raise ValueError("Choose clients from work,codex,claude-code.")
    home, root = home.expanduser().resolve(), root.expanduser().resolve()
    _separate_roots(home, root)
    state = read_state(root)
    if state.get("selected_home") not in (None, str(home)):
        raise ValueError("This runtime already selects another athlete installation; preserve it.")
    state = _recover_setup_operation(home, root, state, execute)
    selected = list(dict.fromkeys([*state.get("selected_clients", []), *selected]))
    state.update({"selected_home": str(home), "selected_clients": selected})
    write_state(root, state)
    return home, root, selected, state


def _prepare_setup_clients(
    command: list[str],
    root: pathlib.Path,
    selected: list[str],
    onboarding: dict[str, Any],
    execute: Any,
) -> dict[str, Any]:
    if onboarding["status"] != "complete":
        return {"status": "not_run"}
    arguments = [
        "clients",
        "configure",
        "--clients",
        ",".join(selected),
        "--runtime-root",
        str(root),
    ]
    return _client_result(command, root, selected, execute(command + arguments))


def finish_setup(
    home: pathlib.Path,
    root: pathlib.Path,
    *,
    clients: str,
    setup_arguments: list[str] | None = None,
    execute: Any = _run_stage,
) -> dict[str, Any]:
    """Resume athlete/client setup using completed local onboarding when available.

    Args:
        home: Explicit selected athlete installation.
        root: Managed runtime root containing the active stable launchers.
        clients: Selected client identifiers; earlier choices are retained.
        setup_arguments: Explicit arguments requesting another athlete onboarding run.
        execute: Terminal subprocess boundary for offline tests.

    Returns:
        Stage observations, preserving incomplete setup for a repeated run.
    """
    home, root, selected, state = _prepare_setup(home, root, clients, execute)
    command = [str(root / "bin" / "garmin-coach"), "--home", str(home)]
    onboarding, readiness = _continue_onboarding(
        home,
        command,
        setup_arguments,
        execute,
        state.get("stages", {}).get("onboarding", {}),
    )
    result = {
        "onboarding": onboarding,
        "coach_read": readiness,
        "clients": _prepare_setup_clients(command, root, selected, onboarding, execute),
        "client_acceptance": "not_run",
    }
    result["next"] = _setup_next(result)
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
    saved = read_state(args.runtime_root).get("scheduler", {})
    enabled, when = args.daily is not None, args.daily or saved.get("time", "06:00")
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
    try:
        result["scheduler"] = _offer_schedule(args, result)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        saved = read_state(args.runtime_root).get("scheduler", {})
        result["scheduler"] = {
            **saved,
            "status": "incomplete",
            "reason": str(exc),
            "actions": saved.get("actions") or [str(exc)],
        }
    state = read_state(args.runtime_root)
    state["stages"] = result
    write_state(args.runtime_root, state)
    print(json.dumps(result, indent=2))
    print(
        "First-read prompt: Read the coach skill, reporting reference and my profile. "
        "Use only get_digest and get_snapshot. Explain the evidence dates, missing data "
        "and freshness. Do not refresh Garmin or write anything. End with the digest disclaimer."
    )
    complete = result["onboarding"]["status"] == "complete"
    clients_ready = result["clients"]["status"] in ("ready", "needs_activation")
    schedule_ready = result["scheduler"]["status"] in ("enabled", "disabled")
    return 0 if complete and clients_ready and schedule_ready else 1


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
