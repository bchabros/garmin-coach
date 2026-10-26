"""Standard-library ownership and durable progress of one managed program.

The v1 JSON remains compatible with earlier installers, including extension fields.
Purpose operations reload the receipt before writing, retaining nested progress that
another step may have recorded. No native commands or athlete-data access belong here.
"""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import re
import pathlib
import tempfile
from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = 1
_UNSUPPORTED = "Unsupported install receipt; preserve it and use a compatible installer."


class ReceiptError(ValueError):
    """A program-ownership or journal refusal safe to display without receipt contents."""


def read_state(root: pathlib.Path) -> dict[str, Any]:
    """Read the setup receipt without inferring ownership from its location."""
    path = root / "install-state.json"
    if not path.exists():
        return {}
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise ReceiptError(
            "Unsupported install receipt; preserve it and use a compatible installer."
        )
    _validate_state(value)
    schedule = value.get("scheduler") or {}
    if schedule:
        job_path = schedule.get("path") or schedule.get("plist_path")
        if job_path is not None:
            schedule.setdefault("path", job_path)
            schedule.setdefault("plist_path", job_path)
        schedule.setdefault("activation_status", schedule.get("status", "unknown"))
        schedule.setdefault("status", schedule["activation_status"])
    return value


def _validate_state(value: dict[str, Any]) -> None:
    for key in (
        "builds",
        "managed_releases",
        "managed_release_inventories",
        "owned_launchers",
        "pending_activation",
        "operation",
        "scheduler",
        "stages",
    ):
        if key not in value or (key == "operation" and value[key] is None):
            continue
        if not isinstance(value[key], dict):
            raise ReceiptError(_UNSUPPORTED)
    for key in (
        "selected_home",
        "release",
        "source_commit",
        "release_dir",
        "manifest_sha256",
        "managed_by",
    ):
        if value.get(key) is not None and not isinstance(value[key], str):
            raise ReceiptError(_UNSUPPORTED)
    selected = value.get("selected_clients", [])
    if not isinstance(selected, list) or any(not isinstance(name, str) for name in selected):
        raise ReceiptError(_UNSUPPORTED)
    _validate_ownership(value)
    _validate_record_fields(value)


def _validate_ownership(value: dict[str, Any]) -> None:
    for key in ("managed_releases", "managed_release_inventories", "owned_launchers"):
        if any(not isinstance(digest, str) for digest in value.get(key, {}).values()):
            raise ReceiptError(_UNSUPPORTED)
    if any(not isinstance(record, dict) for record in value.get("builds", {}).values()):
        raise ReceiptError(_UNSUPPORTED)
    launchers = value.get("pending_activation", {}).get("launchers", {})
    if not isinstance(launchers, dict):
        raise ReceiptError(_UNSUPPORTED)
    for entry in launchers.values():
        if not isinstance(entry, dict):
            raise ReceiptError(_UNSUPPORTED)
        if any(
            entry.get(key) is not None and not isinstance(entry[key], str)
            for key in ("sha256", "previous_sha256")
        ):
            raise ReceiptError(_UNSUPPORTED)


def _validate_record_fields(value: dict[str, Any]) -> None:
    for key in ("operation", "scheduler", "pending_activation"):
        record = value.get(key) or {}
        for field in (
            "path",
            "plist_path",
            "label",
            "status",
            "activation_status",
            "selected_home",
            "phase",
            "kind",
            "backup",
            "home",
            "previous",
            "candidate",
            "manifest",
            "release_dir",
            "previous_release_dir",
        ):
            if record.get(field) is not None and not isinstance(record[field], str):
                raise ReceiptError(_UNSUPPORTED)
    command = value.get("scheduler", {}).get("command")
    if command is not None and (
        not isinstance(command, list)
        or not command
        or any(not isinstance(arg, str) for arg in command)
    ):
        raise ReceiptError(_UNSUPPORTED)


def atomic_write(path: pathlib.Path, content: bytes, *, mode: int = 0o600) -> None:
    """Replace a regular file atomically, cleaning the private temporary on failure.

    The standalone installer and packaged callers share the same
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


def inventory_entries(release: pathlib.Path) -> dict[str, dict[str, Any]]:
    """Observe program files, links and directories without inferring ownership."""
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
            raise ReceiptError(f"Unowned special runtime entry: {relative}")
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


def write_inventory(release: pathlib.Path, metadata: dict[str, Any]) -> None:
    """Publish verified file ownership before marking a prepared release ready."""
    entries = inventory_entries(release)
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
        raise ReceiptError(
            "Runtime ownership inventory is unavailable; preserve it and choose a new runtime root."
        ) from exc
    digest = hashlib.sha256(inventory_bytes).hexdigest()
    if (
        ready.get("managed_by") != "garmin-coach"
        or ready.get("inventory_sha256") != digest
        or expected_inventory_hash not in (None, digest)
        or inventory.get("schema_version") != 1
    ):
        raise ReceiptError("Runtime ownership inventory changed; preserve its contents.")
    if (
        ready.get("manifest_sha256")
        != hashlib.sha256((release / "manifest.json").read_bytes()).hexdigest()
    ):
        raise ReceiptError("Runtime manifest changed; preserve its contents.")
    expected = inventory["entries"]
    for name, entry in inventory_entries(release).items():
        if not _runtime_cache(name, entry, expected) and expected.get(name) != entry:
            raise ReceiptError(f"Unknown or edited runtime inventory entry: {name}; preserve it.")


@dataclass(frozen=True)
class ProgramReceipt:
    """Purpose operations over one program root, retaining compatible v1 evidence."""

    root: pathlib.Path

    def read(self) -> dict[str, Any]:
        """Read a fresh detached snapshot; absence differs from unreadable state."""
        return read_state(self.root)

    def backup_to(self, directory: pathlib.Path) -> None:
        """Preserve receipt bytes privately, including unreadable state needed for recovery."""
        source = self.root / "install-state.json"
        if source.is_file():
            atomic_write(directory / "install-state.json", source.read_bytes())

    def record_build(
        self,
        release: pathlib.Path,
        metadata: dict[str, Any],
        stage: str,
        owned_entries: dict[str, Any],
    ) -> None:
        """Checkpoint intended work and observed ownership before the next build step."""
        state = self.read()
        state.setdefault("builds", {})[str(release)] = {
            "stage": stage,
            "manifest": metadata,
            "owned_entries": owned_entries,
            "action": "Repeat the same installer; incomplete build files are preserved.",
        }
        write_state(self.root, state)

    def guard_active_pointer(self) -> None:
        """Refuse an active pointer outside recorded current or pending ownership."""
        root = self.root
        current = root / "current"
        if not current.exists() and not current.is_symlink():
            return
        state = self.read()
        pending = state.get("pending_activation", {})
        expected = [
            state.get("release_dir"),
            pending.get("release_dir"),
            pending.get("previous_release_dir"),
        ]
        if not current.is_symlink() or not any(
            path is not None and current.resolve() == pathlib.Path(path).resolve()
            for path in expected
        ):
            raise ReceiptError("Active runtime pointer is not owned or has changed; preserve it.")

    def verify_activation_launchers(self) -> None:
        """Refuse edited launcher bytes, including either journaled side of an activation.

        Raises:
            ValueError: A launcher/directory is a symlink or its bytes have no owned hash.
        """
        root, state = self.root, self.read()
        if (root / "bin").is_symlink():
            raise ReceiptError("Stable launcher directory is a symlink; preserve it.")
        for name in ("garmin-coach", "garmin-coach-mcp"):
            launcher = root / "bin" / name
            pending = (
                state.get("pending_activation", {}).get("launchers", {}).get(str(launcher), {})
            )
            expected = {
                state.get("owned_launchers", {}).get(str(launcher)),
                pending.get("previous_sha256"),
                pending.get("sha256"),
            }
            if launcher.is_symlink() or (
                launcher.exists()
                and hashlib.sha256(launcher.read_bytes()).hexdigest() not in expected
            ):
                raise ReceiptError("Stable launcher is not owned or was edited; preserve it.")

    def verified_launchers(self) -> list[pathlib.Path]:
        """Verify every recorded launcher before maintenance or removal."""
        root, state = self.root, self.read()
        paths = []
        for name, expected in state.get("owned_launchers", {}).items():
            path = pathlib.Path(name)
            if path.parent != root / "bin" or path.is_symlink() or (root / "bin").is_symlink():
                raise ReceiptError("Launcher ownership is inconsistent; preserve the program.")
            if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ReceiptError(
                    "A launcher was modified; preserve it and resolve ownership first."
                )
            paths.append(path)
        return paths

    def verified_releases(self, snapshot: dict[str, Any] | None = None) -> list[pathlib.Path]:
        """Verify release ownership from current state or a preserved recovery snapshot."""
        root = self.root
        state = self.read() if snapshot is None else snapshot
        paths = []
        for name, expected in state.get("managed_releases", {}).items():
            path = pathlib.Path(name)
            if (
                path.parent != root / "releases"
                or path.is_symlink()
                or (root / "releases").is_symlink()
            ):
                raise ReceiptError("Release ownership is inconsistent; preserve the program.")
            if path.exists():
                manifest = path / "manifest.json"
                if (
                    not manifest.is_file()
                    or hashlib.sha256(manifest.read_bytes()).hexdigest() != expected
                ):
                    raise ReceiptError(
                        "A release manifest changed; preserve it and resolve ownership first."
                    )
                inventory = state.get("managed_release_inventories", {}).get(name)
                if not inventory:
                    raise ReceiptError(
                        "Release inventory is unverified; preserve it and choose a new runtime root."
                    )
                verify_release_inventory(path, inventory)
            paths.append(path)
        return paths

    def owned_schedule(self, home: pathlib.Path) -> pathlib.Path | None:
        """Verify scheduler identity and bytes, including a verifiable missing job."""
        root, state = self.root, self.read()
        schedule = state.get("scheduler") or {}
        if not schedule.get("path") and not schedule.get("plist_path"):
            if schedule.get("label"):
                raise ReceiptError(
                    "Daily job ownership has no recorded plist path; preserve its label."
                )
            return None
        path = pathlib.Path(schedule.get("plist_path") or schedule["path"])
        if path.is_symlink():
            raise ReceiptError(
                "Scheduled job is a symlink; preserve it and resolve ownership first."
            )
        if not path.exists():
            label = schedule_label(home)
            if schedule.get("label") != label or path.name != label + ".plist":
                raise ReceiptError(
                    "Missing daily job has no verifiable installation-specific label."
                )
            return None
        if hashlib.sha256(path.read_bytes()).hexdigest() != schedule.get("sha256"):
            raise ReceiptError("Scheduled job changed; preserve it and resolve ownership first.")
        job = plistlib.loads(path.read_bytes())
        expected = [str(root / "bin" / "garmin-coach"), "--home", str(home), "daily"]
        if job.get("Label") != schedule.get("label") or job.get("ProgramArguments") != expected:
            raise ReceiptError("Scheduled job does not address the selected athlete installation.")
        return path

    def begin_activation(self, release: pathlib.Path, launchers: dict[str, bytes]) -> None:
        """Record both sides of activation before replacing any owned launcher or pointer."""
        self.verify_activation_launchers()
        state = self.read()
        pending = state.get("pending_activation", {})
        if pending and pending.get("release_dir") != str(release):
            if state.get("release_dir") != str(release):
                raise ReceiptError("Another activation is pending; repeat its installer first.")
            pending = {}
        current = self.root / "current"
        plan = pending or {
            "release_dir": str(release),
            "previous_release_dir": str(current.resolve()) if current.is_symlink() else None,
            "launchers": {},
        }
        for name, content in launchers.items():
            path = self.root / "bin" / name
            digest = hashlib.sha256(content).hexdigest()
            previous = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
            entry = plan["launchers"].setdefault(
                str(path), {"previous_sha256": previous, "sha256": digest}
            )
            if entry["sha256"] != digest:
                raise ReceiptError(
                    "Pending launcher belongs to a different installer; preserve it."
                )
        state["pending_activation"] = plan
        write_state(self.root, state)

    def finish_activation(self, release: pathlib.Path, metadata: dict[str, Any]) -> None:
        """Publish the activated release without discarding update or build progress."""
        state = self.read()
        ready = json.loads((release / "ready.json").read_text())
        digest = hashlib.sha256((release / "manifest.json").read_bytes()).hexdigest()
        state.setdefault("managed_release_inventories", {})[str(release)] = ready[
            "inventory_sha256"
        ]
        state.setdefault("managed_releases", {})[str(release)] = digest
        state["owned_launchers"] = {
            path: entry["sha256"]
            for path, entry in state["pending_activation"]["launchers"].items()
        }
        state.update(
            managed_by="garmin-coach",
            release=metadata["version"],
            source_commit=metadata["source_commit"],
            release_dir=str(release),
            manifest_sha256=digest,
        )
        state.pop("pending_activation", None)
        write_state(self.root, state)

    def record_scheduler(self, record: dict[str, Any]) -> None:
        """Save observed job progress separately from other installation progress."""
        state = self.read()
        state["scheduler"] = record
        write_state(self.root, state)

    def select_installation(self, home: pathlib.Path, selected: list[str]) -> list[str]:
        """Bind one installation and retain previously selected clients in order."""
        state = self.read()
        if state.get("selected_home") not in (None, str(home)):
            raise ReceiptError(
                "This runtime already selects another athlete installation; preserve it."
            )
        selected = list(dict.fromkeys([*state.get("selected_clients", []), *selected]))
        state.update(selected_home=str(home), selected_clients=selected)
        write_state(self.root, state)
        return selected

    def record_stages(self, result: dict[str, Any]) -> None:
        """Record setup observations, retaining independent client-stage outcomes."""
        state = self.read()
        state["stages"] = result
        write_state(self.root, state)

    def begin_update(
        self,
        home: pathlib.Path,
        backup: pathlib.Path,
        previous: pathlib.Path,
        manifest: pathlib.Path,
    ) -> None:
        """Journal update intent before preparing a candidate or changing registrations."""
        state = self.read()
        state["operation"] = {
            "schema_version": 1,
            "kind": "update",
            "phase": "preparing",
            "backup": str(backup),
            "home": str(home),
            "previous": str(previous),
            "manifest": str(manifest.resolve()),
        }
        write_state(self.root, state)

    def checkpoint_update(self, phase: str, *, candidate: pathlib.Path | None = None) -> None:
        """Record the next update step while retaining its recovery evidence."""
        state = self.read()
        state["operation"]["phase"] = phase
        if candidate is not None:
            state["operation"]["candidate"] = str(candidate)
        write_state(self.root, state)

    def interrupted_update(self) -> UpdateJournal | None:
        """Read a typed recovery intent, refusing unsupported or malformed journals."""
        value = self.read().get("operation")
        return UpdateJournal.from_record(value) if value else None

    def finish_update(self) -> None:
        """Clear the successful update journal after all its consequences complete."""
        state = self.read()
        state.pop("operation", None)
        write_state(self.root, state)

    def require_recovery(
        self,
        home: pathlib.Path,
        previous: pathlib.Path,
        backup: pathlib.Path,
    ) -> None:
        """Retain recovery intent when a rollback itself fails."""
        state = self.read()
        state["operation"] = {
            **state.get("operation", {}),
            "kind": "update",
            "phase": "recovery_required",
            "backup": str(backup),
            "home": str(home),
            "previous": str(previous),
            "candidate": state.get("operation", {}).get("candidate"),
        }
        write_state(self.root, state)

    def restore(self, prior: dict[str, Any]) -> None:
        """Restore prior activation, preserving ownership learned during this attempt."""
        current = self.read()
        restored = dict(current, **prior)
        for key in ("builds", "managed_releases", "managed_release_inventories"):
            restored[key] = {**current.get(key, {}), **prior.get(key, {})}
        restored["owned_launchers"] = current.get(
            "owned_launchers", prior.get("owned_launchers", {})
        )
        restored.pop("operation", None)
        restored.pop("pending_activation", None)
        write_state(self.root, restored)

    def retain_clients(self, selected: list[str]) -> None:
        """Record the remaining selection after an explicit client removal."""
        state = self.read()
        state["selected_clients"] = selected
        write_state(self.root, state)

    def mark_removed(self) -> None:
        """Record completed removal while preserving retained personal/recovery evidence."""
        state = self.read()
        state.update(
            removed=True,
            owned_launchers={},
            managed_releases={},
            managed_release_inventories={},
            scheduler={},
            selected_clients=[],
        )
        write_state(self.root, state)


def schedule_label(home: pathlib.Path) -> str:
    """Return the stable installation-specific daily-job identity."""
    return "com.garmincoach.daily." + hashlib.sha256(str(home).encode()).hexdigest()[:12]


@dataclass(frozen=True)
class UpdateJournal:
    """Read-only recovery intent, including legacy pre-versioned mutation semantics."""

    phase: str
    backup: pathlib.Path
    home: str | None
    previous: pathlib.Path | None
    may_have_changed_clients: bool

    @classmethod
    def from_record(cls, value: dict[str, Any]) -> UpdateJournal:
        """Interpret v1 and v0.1.0 intent without guessing unsupported operation kinds."""
        if value.get("kind") != "update" or value.get("schema_version") not in (None, 1):
            raise ReceiptError("Unknown interrupted operation; preserve its receipt and backup.")
        if not isinstance(value.get("backup"), str) or not value["backup"]:
            raise ReceiptError(
                "Interrupted operation has no recovery backup; preserve its receipt."
            )
        for key in ("home", "previous"):
            if key in value and (not isinstance(value[key], str) or not value[key]):
                raise ReceiptError(
                    "Interrupted operation identity is invalid; preserve its receipt."
                )
        phase = value.get("phase", "unknown")
        if not isinstance(phase, str):
            raise ReceiptError("Interrupted operation phase is invalid; preserve its receipt.")
        return cls(
            phase=phase,
            backup=pathlib.Path(value["backup"]),
            home=value.get("home"),
            previous=pathlib.Path(value["previous"]) if value.get("previous") else None,
            may_have_changed_clients=value.get("schema_version") is None
            or phase not in {"preparing", "validating_candidate"},
        )
