"""Prepare local assistant plugins for one explicitly selected athlete installation."""

from __future__ import annotations

import argparse
import hashlib
import fcntl
import importlib.metadata
import json
import pathlib
import shlex
import shutil
import subprocess
import sys
from typing import Any, Iterator
from contextlib import contextmanager

from . import coach_resources, distribution
from .program_receipt import ProgramReceipt
from .core import installation


_PUBLIC_COACH_RESOURCES = coach_resources.public_names()
# Public resources added after the first generation; an older owned snapshot may lack them.
_ADDED_AFTER_FIRST_GENERATION = frozenset({"references/saved-context.md"})


def integration_root(home: pathlib.Path, runtime_root: pathlib.Path) -> pathlib.Path:
    """Return the generated integration directory for the selected installation."""
    identity = hashlib.sha256(str(_canonical(home)).encode()).hexdigest()[:16]
    return runtime_root / "integrations" / identity


def _canonical(path: pathlib.Path) -> pathlib.Path:
    resolved = path.expanduser().resolve()
    result = pathlib.Path(resolved.anchor)
    for part in resolved.parts[1:]:
        requested = result / part
        if requested.exists() and not any(child.name == part for child in result.iterdir()):
            requested = next(
                child
                for child in result.iterdir()
                if not child.is_symlink() and child.samefile(requested)
            )
        result = requested
    return result


def _host(client: str) -> str:
    return "claude-code" if client == "claude-code" else "openai"


def _runtime_binding(home: pathlib.Path, runtime: pathlib.Path) -> None:
    value = ProgramReceipt(runtime).read()
    if not value:
        for root in (runtime / "integrations").glob("*"):
            receipt = _load(root)
            if (
                receipt.get("selected_clients")
                and _canonical(pathlib.Path(receipt["home"])) != home
            ):
                raise ValueError(
                    "This runtime serves another athlete installation; use a separate runtime."
                )
        return
    selected = value.get("selected_home")
    if selected is not None and _canonical(pathlib.Path(selected)) != home:
        raise ValueError("This runtime serves another athlete installation; use its selected home.")


@contextmanager
def _binding_registry() -> Iterator[tuple[pathlib.Path, dict[str, Any]]]:
    directory = _canonical(pathlib.Path.home()) / "Library/Application Support/Garmin Coach"
    directory.mkdir(parents=True, exist_ok=True)
    lock = _artifact_path(directory, "client-bindings.lock")
    with lock.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        path = _artifact_path(directory, "client-bindings.json")
        value = (
            json.loads(path.read_text()) if path.exists() else {"schema_version": 1, "bindings": {}}
        )
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != 1
            or not isinstance(value.get("bindings"), dict)
        ):
            raise ValueError("Unsupported user-scope binding record; preserve it for recovery.")
        yield path, value


def _native_inventory(
    executable: str, host: str, *, marketplaces: bool = False
) -> list[dict[str, Any]] | None:
    arguments = [executable, "plugin", *(["marketplace"] if marketplaces else []), "list", "--json"]
    try:
        result = subprocess.run(
            arguments, stdin=subprocess.DEVNULL, capture_output=True, timeout=30, check=False
        )
        if result.returncode:
            return None
        if not isinstance(result.stdout, (str, bytes)):
            return None
        value = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    if host == "openai":
        value = (
            value.get("marketplaces" if marketplaces else "installed")
            if isinstance(value, dict)
            else None
        )
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        return None
    key = "name" if marketplaces else ("pluginId" if host == "openai" else "id")
    if any(not isinstance(item.get(key), str) for item in value):
        return None
    return value


def _audit_native_binding(
    home: pathlib.Path,
    runtime: pathlib.Path,
    host: str,
    previous: dict[str, Any],
    *,
    executable: str | None = None,
    allow_other: bool = False,
) -> None:
    executable = executable or shutil.which("codex" if host == "openai" else "claude")
    if not executable:
        return
    inventory = _native_inventory(executable, host)
    if inventory is None:
        raise ValueError(
            "Cannot verify user-scope plugin inventory. Repair the client CLI, inspect "
            "`plugin list --json`, and retry from the original runtime."
        )
    root = integration_root(home, runtime)
    name = "garmin-coach-" + root.name
    expected = f"{name}@{name}"
    key = "pluginId" if host == "openai" else "id"
    plugins = [item for item in inventory if item[key].startswith("garmin-coach-")]
    if not allow_other and any(item[key] != expected for item in plugins):
        raise ValueError(
            "Another Garmin Coach user-scope plugin is installed. Disconnect it through "
            "its original runtime or client UI, then retry; unrelated plugins are preserved."
        )
    own_plugin = any(item[key] == expected for item in plugins)
    registration = previous.get("registrations", {}).get(host, {})
    known_marketplace = registration.get("marketplace_added") or registration.get(
        "marketplace_attempted"
    )
    if not own_plugin and not known_marketplace:
        return
    marketplaces = _native_inventory(executable, host, marketplaces=True)
    if marketplaces is None:
        raise ValueError(
            "Cannot verify user-scope marketplace inventory; repair the client CLI and retry."
        )
    owned = next((item for item in marketplaces if item.get("name") == name), {})
    if not own_plugin and not owned:
        return
    source = owned.get("root") if host == "openai" else owned.get("path")
    if not previous or not isinstance(source, str) or _canonical(pathlib.Path(source)) != root:
        raise ValueError(
            "Existing Garmin Coach user-scope plugin ownership is unverified. Use its "
            "original runtime to remove it, or inspect and disconnect it through the "
            "client UI before retrying."
        )


def _claim_bindings(
    path: pathlib.Path,
    registry: dict[str, Any],
    home: pathlib.Path,
    runtime: pathlib.Path,
    selected: list[str],
    previous: dict[str, Any],
) -> None:
    owner = {"home": str(home), "runtime_root": str(runtime)}
    for host in dict.fromkeys(map(_host, selected)):
        claim = registry["bindings"].get(host)
        if claim is not None and claim != owner:
            raise ValueError(
                f"A Garmin Coach user-scope binding already owns {host}: {claim}. "
                "Remove that binding through its original athlete installation and runtime "
                "before configuring another. Preserve unrelated client plugins."
            )
        if claim is None:
            _audit_native_binding(home, runtime, host, previous)
    for host in dict.fromkeys(map(_host, selected)):
        registry["bindings"][host] = owner
    distribution.atomic_write(path, _json(registry))


def _release_bindings(
    path: pathlib.Path,
    registry: dict[str, Any],
    home: pathlib.Path,
    runtime: pathlib.Path,
    remaining: list[str],
) -> None:
    owner = {"home": str(home), "runtime_root": str(runtime)}
    for host, claim in list(registry["bindings"].items()):
        if claim == owner and host not in set(map(_host, remaining)):
            del registry["bindings"][host]
    distribution.atomic_write(path, _json(registry))


def _read_resources(root: pathlib.Path) -> dict[str, bytes]:
    try:
        return coach_resources.read_public(root)
    except ValueError as error:
        raise ValueError(
            "Coach resources missing or unsafe; reinstall the complete package."
        ) from error


def _resources() -> dict[str, bytes]:
    source = pathlib.Path(__file__).resolve().parents[2] / "skills/coach"
    if source.is_dir():
        return _read_resources(source)
    distribution = importlib.metadata.distribution("garmin-coach")
    prefix = "skills/coach/"
    available = {str(path) for path in distribution.files or []}
    if any(prefix + name not in available for name in _PUBLIC_COACH_RESOURCES):
        raise ValueError("Coach resources missing; reinstall the complete Garmin Coach package.")
    return _read_resources(pathlib.Path(str(distribution.locate_file(prefix))))


def _json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def _managed_resources(
    resources: dict[str, bytes], home: pathlib.Path, runtime: pathlib.Path
) -> dict[str, bytes]:
    # The router's own CLI section stays canonical; the binding section names the launcher.
    command = shlex.join([str(runtime / "bin/garmin-coach"), "--home", str(home)])
    return {
        key: value.replace(b"poetry run garmin-coach", command.encode())
        for key, value in resources.items()
    }


def _package(home: pathlib.Path, runtime: pathlib.Path, name: str) -> dict[str, bytes]:
    version = importlib.metadata.version("garmin-coach")
    resources = _managed_resources(_resources(), home, runtime)
    binding = (
        "\n\n## Selected athlete installation\n\n"
        f"This plugin serves `{home}`. Resolve memory, plans, reports and other athlete "
        "files against this absolute folder, even when the plugin is installed in a cache. "
        "Resolve `references/` against this skill's own folder. Select the athlete installation "
        "as the primary local conversation folder for file access. This is a coaching "
        "installation; source development requires a separately selected source checkout. "
        "For an allowed local CLI fallback, use "
        f"`{shlex.join([str(runtime / 'bin/garmin-coach'), '--home', str(home)])}` "
        "and the requested command. This managed executable is the supported runtime "
        "for this installation. If it is unavailable, report the "
        "runtime prerequisite and have the operator repair the managed installation.\n"
    )
    frontmatter, separator, body = resources["SKILL.md"].partition(b"\n---\n")
    if not separator:
        raise ValueError("Coach router frontmatter missing; reinstall the complete package.")
    resources["SKILL.md"] = frontmatter + separator + binding.encode() + body
    files = {f"plugin/skills/coach/{key}": value for key, value in resources.items()}
    files.update(_plugin_configuration(home, runtime, name, version))
    files.update(_marketplace_configuration(name))
    return files


def _plugin_configuration(
    home: pathlib.Path, runtime: pathlib.Path, name: str, version: str
) -> dict[str, bytes]:
    files = {}
    files["plugin/.mcp.json"] = _json(
        {
            "mcpServers": {
                "coach": {
                    "type": "stdio",
                    "command": str(runtime / "bin/garmin-coach-mcp"),
                    "args": ["--home", str(home)],
                }
            }
        }
    )
    files["plugin/.codex-plugin/plugin.json"] = _json(
        {
            "name": name,
            "version": version,
            "description": "Local deterministic Garmin coaching",
            "skills": "./skills/",
            "mcpServers": "./.mcp.json",
            "requires_local_executor": True,
        }
    )
    files["plugin/.claude-plugin/plugin.json"] = _json(
        {
            "name": name,
            "version": version,
            "description": "Local deterministic Garmin coaching",
            "author": {"name": "Garmin Coach maintainers"},
        }
    )
    return files


def _marketplace_configuration(name: str) -> dict[str, bytes]:
    files = {}
    files[".agents/plugins/marketplace.json"] = _json(
        {
            "name": name,
            "interface": {"displayName": "Garmin Coach"},
            "plugins": [
                {
                    "name": name,
                    "source": {"source": "local", "path": "./plugin"},
                    "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                    "category": "Productivity",
                }
            ],
        }
    )
    files[".claude-plugin/marketplace.json"] = _json(
        {
            "name": name,
            "owner": {"name": "Garmin Coach"},
            "description": "Local athlete-specific Garmin Coach integration",
            "plugins": [{"name": name, "source": "./plugin"}],
        }
    )
    return files


def _artifact_path(root: pathlib.Path, relative: str) -> pathlib.Path:
    path = pathlib.Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Invalid integration artifact path; refusing to change registration.")
    target = root / path
    for parent in (target, *target.parents):
        if parent.is_symlink():
            raise ValueError("Integration artifact changed to a symlink; restore it before setup.")
        if parent == root.parent:
            break
    return target


def _load(root: pathlib.Path) -> dict[str, Any]:
    path = _artifact_path(root, "registration.json")
    if not path.is_file():
        return {}
    receipt = json.loads(path.read_text())
    if not isinstance(receipt, dict) or receipt.get("schema_version") != 1:
        raise ValueError("Unsupported integration receipt; restore it before setup.")
    return receipt


def recorded_selection(home: pathlib.Path, runtime_root: pathlib.Path) -> list[str] | None:
    """Read saved client choices without native probes; None means no registration receipt."""
    receipt = _load(integration_root(home, runtime_root))
    if not receipt:
        return None
    selected = receipt.get("selected_clients", [])
    if not isinstance(selected, list) or any(not isinstance(name, str) for name in selected):
        raise ValueError("Unsupported integration selection; preserve its receipt for recovery.")
    return list(selected)


def _save(root: pathlib.Path, receipt: dict[str, Any]) -> None:
    distribution.atomic_write(root / "registration.json", _json(receipt))


def _command(arguments: list[str]) -> bool:
    try:
        result = subprocess.run(
            arguments, stdin=subprocess.DEVNULL, capture_output=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _checkpoint_registration(root: pathlib.Path, host: str, registration: dict[str, Any]) -> None:
    receipt = _load(root)
    receipt["registrations"][host] = registration
    receipt["clients"] = _client_states(receipt)
    _save(root, receipt)


def _marketplace_was_added(root: pathlib.Path, host: str, registration: dict[str, Any]) -> bool:
    if not registration.get("marketplace_attempted"):
        return False
    inventory = _native_inventory(registration["executable"], host, marketplaces=True)
    name = "garmin-coach-" + root.name
    if inventory is None:
        raise ValueError("Cannot verify attempted marketplace registration; repair the client CLI.")
    matches = [item for item in inventory if item["name"] == name]
    if not matches:
        return False
    source = matches[0].get("root" if host == "openai" else "path")
    if not isinstance(source, str) or _canonical(pathlib.Path(source)) != root:
        raise ValueError("Native marketplace source changed; preserve it and inspect ownership.")
    return True


def _plugin_generation(receipt: dict[str, Any]) -> str:
    content = json.dumps(receipt["artifacts"], sort_keys=True).encode()
    return hashlib.sha256(content).hexdigest()


def _installed_plugin(root: pathlib.Path, host: str, executable: str) -> dict[str, Any] | None:
    inventory = _native_inventory(executable, host)
    if inventory is None:
        raise ValueError("Cannot verify native plugin inventory; repair the client CLI and retry.")
    name = "garmin-coach-" + root.name
    key = "pluginId" if host == "openai" else "id"
    return next(
        (
            item
            for item in inventory
            if item[key] == f"{name}@{name}" and (host == "openai" or item.get("scope") == "user")
        ),
        None,
    )


def _resume_committed_plugin(
    root: pathlib.Path, host: str, registration: dict[str, Any], receipt: dict[str, Any]
) -> bool:
    if not (
        registration.get("state") == "preparing"
        and registration.get("stage") == "plugin"
        and registration.get("plugin_absent_before_attempt") is True
        and registration.get("attempted_generation") == _plugin_generation(receipt)
        and (not registration.get("needs_clean_install") or registration.get("refresh_removed"))
    ):
        return False
    installed = _installed_plugin(root, host, registration["executable"])
    if installed is None or installed.get("errors"):
        return False
    if installed.get("version", receipt["package_version"]) != receipt["package_version"]:
        return False
    registration.update(state="registered", stage="plugin")
    registration.pop("needs_refresh", None)
    registration.pop("refresh_removed", None)
    registration.pop("needs_clean_install", None)
    _checkpoint_registration(root, host, registration)
    return True


def _prepare_marketplace(root: pathlib.Path, host: str, registration: dict[str, Any]) -> bool:
    executable = registration["executable"]
    if not registration.get("marketplace_added") and _marketplace_was_added(
        root, host, registration
    ):
        registration["marketplace_added"] = True
        _checkpoint_registration(root, host, registration)
    if not registration.get("marketplace_added"):
        registration.update(state="preparing", stage="marketplace", marketplace_attempted=True)
        _checkpoint_registration(root, host, registration)
        if not _command([executable, "plugin", "marketplace", "add", str(root)]):
            return False
        registration["marketplace_added"] = True
        _checkpoint_registration(root, host, registration)
    return True


def _prepare_refresh(root: pathlib.Path, host: str, registration: dict[str, Any]) -> bool:
    executable = registration["executable"]
    name = "garmin-coach-" + root.name
    refreshing = bool(registration.get("needs_refresh"))
    clean_install = bool(registration.get("needs_clean_install"))
    if (
        refreshing
        and host == "claude-code"
        and not clean_install
        and _installed_plugin(root, host, executable) is None
    ):
        registration.pop("needs_refresh", None)
        _checkpoint_registration(root, host, registration)
        return True
    if (
        refreshing
        and (host == "openai" or clean_install)
        and not registration.get("refresh_removed")
    ):
        registration.update(state="preparing", stage="refresh_remove")
        _checkpoint_registration(root, host, registration)
        operation = "remove" if host == "openai" else "uninstall"
        command = [executable, "plugin", operation, f"{name}@{name}"]
        if host == "claude-code":
            command.extend(["--scope", "user"])
        if not _command(command) and _installed_plugin(root, host, executable) is not None:
            return False
        registration["refresh_removed"] = True
        _checkpoint_registration(root, host, registration)
    return True


def _register(root: pathlib.Path, host: str, previous: dict[str, Any]) -> dict[str, Any]:
    name = "garmin-coach-" + root.name
    if previous.get("state") == "registered" and not previous.get("needs_refresh"):
        return previous
    executable = shutil.which("codex" if host == "openai" else "claude")
    if not executable:
        return {**previous, "state": "client_missing", "plugin_id": f"{name}@{name}"}
    registration = {
        **previous,
        "executable": executable,
        "plugin_id": f"{name}@{name}",
        "marketplace_id": name,
        "marketplace_root": str(root),
    }
    receipt = _load(root)
    _audit_native_binding(
        pathlib.Path(receipt["home"]), pathlib.Path(receipt["runtime_root"]), host, receipt
    )
    if not _prepare_marketplace(root, host, registration):
        return {**registration, "state": "failed", "stage": "marketplace"}
    if _resume_committed_plugin(root, host, registration, receipt):
        return registration
    if not _prepare_refresh(root, host, registration):
        return {**registration, "state": "failed"}
    refreshing = bool(registration.get("needs_refresh"))
    operation = (
        "add"
        if host == "openai"
        else ("update" if refreshing and not registration.get("needs_clean_install") else "install")
    )
    command = [executable, "plugin", operation, f"{name}@{name}"]
    if host != "openai":
        command.extend(["--scope", "user"])
    registration.update(
        state="preparing",
        stage="plugin",
        plugin_attempted=True,
        attempted_generation=_plugin_generation(receipt),
        plugin_absent_before_attempt=_installed_plugin(root, host, executable) is None,
    )
    _checkpoint_registration(root, host, registration)
    succeeded = _command(command)
    if succeeded:
        registration.pop("needs_refresh", None)
        registration.pop("refresh_removed", None)
        registration.pop("needs_clean_install", None)
    return {**registration, "state": "registered" if succeeded else "failed", "stage": "plugin"}


def _client_action(state: str) -> str:
    if state == "needs_activation":
        return (
            "Open the selected athlete installation as a local conversation folder. "
            "Restart the client, enable the plugin if requested, and verify its coach skill "
            "and MCP tools in a fresh conversation. Record client acceptance separately."
        )
    if state == "preparing":
        return "Registration is incomplete. Rerun client configuration to finish or resume setup."
    return (
        "Install or repair the selected client, then rerun configuration. "
        "Client command output is withheld because it may contain private settings."
    )


def _client_states(receipt: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for client in receipt["selected_clients"]:
        host = _host(client)
        registration = receipt.get("registrations", {}).get(host, {})
        state = (
            "preparing"
            if receipt.get("pending_artifacts")
            else registration.get("state", "preparing")
        )
        if state == "registered":
            state = "preparing" if registration.get("needs_refresh") else "needs_activation"
        actions = [_client_action(state)]
        if registration.get("needs_clean_install"):
            actions.insert(
                0,
                "The previous native plugin cache has not been verified clean. Install or "
                "repair the original client CLI and rerun configuration, or disconnect the "
                "Garmin Coach plugin in the client UI before using it.",
            )
        result[client] = {
            "state": state,
            "actions": actions,
            "client_acceptance": "not_run",
        }
    return result


def _public_skill_resource(relative: str) -> bool:
    prefix = "plugin/skills/coach/"
    return relative.startswith(prefix) and relative[len(prefix) :] in _PUBLIC_COACH_RESOURCES


def _unknown_skill_artifacts(root: pathlib.Path, known: set[str]) -> list[str]:
    skill_root = _artifact_path(root, "plugin/skills")
    return [
        str(path.relative_to(root))
        for path in skill_root.rglob("*")
        if coach_resources.resource_like(path) and str(path.relative_to(root)) not in known
    ]


def _obsolete_artifacts(receipt: dict[str, Any], desired: dict[str, bytes]) -> dict[str, str]:
    return {
        name: digest for name, digest in receipt.get("artifacts", {}).items() if name not in desired
    }


def _remove_obsolete_files(root: pathlib.Path, obsolete: dict[str, str]) -> None:
    for relative, expected in obsolete.items():
        path = _artifact_path(root, relative)
        if not path.exists():
            continue
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(
                f"Integration artifact changed: {relative}; preserve it for inspection."
            )
        path.unlink()


def _validate_files(
    root: pathlib.Path, files: dict[str, bytes], previous: dict[str, Any]
) -> dict[str, str]:
    observed = {}
    known = {**previous.get("artifacts", {}), **previous.get("pending_artifacts", {})}
    unknown = _unknown_skill_artifacts(root, set(known) | set(files))
    if unknown:
        raise ValueError(
            "Unowned coach resource in generated plugin. Run clients remove --confirm with "
            "this installation and runtime selected, inspect and move private resources "
            "outside the generated skill, then rerun configuration."
        )
    for relative in dict.fromkeys([*files, *known]):
        target = _artifact_path(root, relative)
        fallback = (
            hashlib.sha256(files[relative]).hexdigest() if relative in files else known[relative]
        )
        expected = previous.get("artifacts", {}).get(relative, fallback)
        allowed = {expected, previous.get("pending_artifacts", {}).get(relative, expected)}
        if target.exists():
            if not target.is_file():
                raise ValueError(
                    f"Integration artifact changed: {relative}; preserve it for inspection."
                )
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
            if actual not in allowed:
                raise ValueError(
                    f"Integration artifact changed: {relative}; inspect and preserve it. "
                    "Move private resources outside the generated skill before retrying."
                )
            observed[relative] = actual
        elif relative not in files:
            observed[relative] = expected
    return observed


def _mark_clean_install(receipt: dict[str, Any]) -> None:
    for host in set(map(_host, receipt["selected_clients"])):
        receipt["registrations"].setdefault(host, {}).update(
            needs_clean_install=True,
            needs_refresh=True,
        )


def _prepare_files(root: pathlib.Path, files: dict[str, bytes], receipt: dict[str, Any]) -> None:
    obsolete = _obsolete_artifacts(receipt, files)
    private_cleanup = any(name.startswith("plugin/skills/") for name in obsolete)
    if private_cleanup:
        _mark_clean_install(receipt)
    receipt["pending_artifacts"] = {
        relative: hashlib.sha256(content).hexdigest() for relative, content in files.items()
    }
    changed = receipt["pending_artifacts"] != receipt.get("artifacts", {})
    if changed:
        for registration in receipt["registrations"].values():
            if registration.get("state") == "registered" or registration.get("plugin_attempted"):
                registration["needs_refresh"] = True
                registration.pop("refresh_removed", None)
    receipt["clients"] = _client_states(receipt)
    _save(root, receipt)
    for relative, content in files.items():
        distribution.atomic_write(_artifact_path(root, relative), content)
    _remove_obsolete_files(root, obsolete)
    receipt["artifacts"] = receipt.pop("pending_artifacts")
    _save(root, receipt)


def _ownership_status(
    home: pathlib.Path, runtime: pathlib.Path, receipt: dict[str, Any]
) -> dict[str, Any]:
    path = _artifact_path(
        _canonical(pathlib.Path.home()) / "Library/Application Support/Garmin Coach",
        "client-bindings.json",
    )
    registry = json.loads(path.read_text()) if path.exists() else {}
    if not isinstance(registry, dict) or (registry and registry.get("schema_version") != 1):
        raise ValueError("Unsupported user-scope binding record; preserve it for recovery.")
    bindings = registry.get("bindings", {})
    if not isinstance(bindings, dict):
        raise ValueError("Invalid user-scope binding record; preserve it for recovery.")
    owner = {"home": str(home), "runtime_root": str(runtime)}
    claims = [bindings.get(host) for host in set(map(_host, receipt["selected_clients"]))]
    if any(claim is not None and claim != owner for claim in claims):
        return {
            "state": "conflict",
            "actions": [
                "Another runtime owns this user-scope binding. Use that runtime to inspect "
                "and remove it; this stale runtime must not mutate clients."
            ],
        }
    if any(claim is None for claim in claims):
        return {
            "state": "unverified",
            "actions": [
                "Run clients configure or clients remove --confirm from this original athlete "
                "installation and runtime. Native plugin inventory will verify ownership "
                "before any mutation; repair the client CLI if inventory is unavailable."
            ],
        }
    return {"state": "verified", "actions": []}


def status(home: pathlib.Path, runtime_root: pathlib.Path) -> dict[str, Any]:
    """Inspect owned resources and recorded registration without changing client settings."""
    home, runtime_root = _canonical(home), _canonical(runtime_root)
    root = integration_root(home, runtime_root)
    receipt = _load(root)
    if not receipt:
        return {
            "home": str(home),
            "selected_clients": [],
            "integrity": "not_configured",
            "client_acceptance": "not_run",
        }
    changed = []
    expected_files = {**receipt.get("artifacts", {}), **receipt.get("pending_artifacts", {})}
    for relative, expected in expected_files.items():
        target = _artifact_path(root, relative)
        allowed = {expected, receipt.get("artifacts", {}).get(relative, expected)}
        if not target.exists() and receipt.get("pending_artifacts"):
            continue
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() not in allowed:
            changed.append(relative)
    unowned = _unknown_skill_artifacts(root, set(expected_files))
    changed.extend(unowned)
    source = _package(home, runtime_root, "garmin-coach-" + root.name)
    source_matches = all(
        receipt["artifacts"].get(relative) == hashlib.sha256(content).hexdigest()
        for relative, content in source.items()
    )
    return {
        **receipt,
        "clients": _client_states(receipt),
        "ownership": _ownership_status(home, runtime_root, receipt),
        "integrity": "changed"
        if changed
        else ("preparing" if receipt.get("pending_artifacts") else "verified"),
        "changed_artifacts": changed,
        "unowned_artifacts": unowned,
        "source_version": importlib.metadata.version("garmin-coach"),
        "resources_match_source": source_matches,
        "version_matches": receipt["package_version"] == importlib.metadata.version("garmin-coach"),
    }


def _unregister(root: pathlib.Path, host: str, receipt: dict[str, Any]) -> None:
    registration = receipt["registrations"][host]
    name = "garmin-coach-" + root.name
    expected = f"{name}@{name}"
    if registration.get("plugin_id") != expected:
        raise ValueError("Client registration changed; refusing to remove an unrelated plugin.")
    executable = registration.get("executable")
    if not executable:
        return
    if pathlib.Path(executable).name != ("codex" if host == "openai" else "claude"):
        raise ValueError("Client executable changed; restore registration before removal.")
    _audit_native_binding(
        pathlib.Path(receipt["home"]),
        pathlib.Path(receipt["runtime_root"]),
        host,
        receipt,
        executable=executable,
        allow_other=True,
    )
    may_be_installed = (
        registration.get("plugin_attempted")
        or registration.get("state") == "registered"
        or registration.get("stage") == "plugin"
    )
    if registration.get("state") != "plugin_removed" and may_be_installed:
        operation = "remove" if host == "openai" else "uninstall"
        command = [executable, "plugin", operation, expected]
        if host != "openai":
            command.extend(["--scope", "user"])
        if not _command(command):
            inventory = _native_inventory(executable, host)
            key = "pluginId" if host == "openai" else "id"
            if inventory is None or any(item[key] == expected for item in inventory):
                raise ValueError(
                    "Client plugin removal failed; repair the client and retry removal."
                )
        registration["state"] = "plugin_removed"
        _save(root, receipt)
    if registration.get("marketplace_added") or registration.get("marketplace_attempted"):
        if not _command([executable, "plugin", "marketplace", "remove", name]):
            marketplaces = _native_inventory(executable, host, marketplaces=True)
            if marketplaces is None or any(item["name"] == name for item in marketplaces):
                raise ValueError("Client marketplace removal failed; retry removal to finish.")
        registration["marketplace_added"] = False
        registration["marketplace_attempted"] = False
        _save(root, receipt)


def _remove(
    home: pathlib.Path,
    runtime_root: pathlib.Path,
    selected: list[str] | None = None,
    *,
    confirm: bool = False,
) -> dict[str, Any]:
    """Preview or remove owned client registrations, preserving shared OpenAI selections.

    Args:
        home: Athlete installation whose assistants are being disconnected.
        runtime_root: Managed program directory.
        selected: Assistants to remove, or all configured assistants when omitted.
        confirm: Whether to execute the previewed removal.

    Returns:
        Remaining selections and whether removal was executed.

    Raises:
        ValueError: Owned resources changed, or a client refused removal.
    """
    receipt = status(home, runtime_root)
    if receipt["integrity"] == "changed" and set(receipt["changed_artifacts"]) - set(
        receipt.get("unowned_artifacts", [])
    ):
        raise ValueError("Integration artifacts changed; restore them before removal.")
    removed = selected if selected is not None else receipt["selected_clients"]
    remaining = [client for client in receipt["selected_clients"] if client not in removed]
    if not confirm or not receipt["selected_clients"]:
        return {
            **receipt,
            "confirmed": confirm,
            "remove_clients": removed,
            "remaining_clients": remaining,
        }
    root = integration_root(home, runtime_root)
    retained_hosts = {_host(c) for c in remaining}
    for host in list(receipt["registrations"]):
        if host not in retained_hosts:
            _unregister(root, host, receipt)
            del receipt["registrations"][host]
            _save(root, receipt)
    receipt["selected_clients"] = remaining
    receipt["clients"] = _client_states(receipt)
    _save(root, receipt)
    return {**receipt, "confirmed": True, "remove_clients": removed}


def _configure(
    home: pathlib.Path,
    runtime_root: pathlib.Path,
    selected: list[str],
    *,
    resources: dict[str, bytes] | None = None,
    version: str | None = None,
    clean_install: bool = False,
) -> dict[str, Any]:
    """Apply one validated owned generation and resume native registration."""
    home, runtime_root = _canonical(home), _canonical(runtime_root)
    if (not selected and resources is None) or any(
        client not in ("work", "codex", "claude-code") for client in selected
    ):
        raise ValueError("Select work, codex, claude-code, or a combination.")
    if runtime_root.is_relative_to(home) or home.is_relative_to(runtime_root):
        raise ValueError("The program runtime and athlete installation must be separate folders.")
    _runtime_binding(home, runtime_root)
    installation.activate(home)
    root = integration_root(home, runtime_root)
    previous = _load(root)
    selected = list(dict.fromkeys([*previous.get("selected_clients", []), *selected]))
    name = "garmin-coach-" + root.name
    files = resources if resources is not None else _package(home, runtime_root, name)
    observed = _validate_files(root, files, previous)
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "home": str(home),
        "runtime_root": str(runtime_root),
        "integration_root": str(root),
        "package_version": version
        if version is not None
        else importlib.metadata.version("garmin-coach"),
        "selected_clients": selected,
        "registrations": previous.get("registrations", {}),
        "client_acceptance": "not_run",
        "protocol_check": "not_run",
        "runtime_available": (runtime_root / "bin/garmin-coach-mcp").is_file(),
        "artifacts": observed,
    }
    if clean_install:
        _mark_clean_install(receipt)
    receipt["clients"] = _client_states(receipt)
    _prepare_files(root, files, receipt)
    _register_clients(root, receipt)
    return receipt


def _register_clients(root: pathlib.Path, receipt: dict[str, Any]) -> None:
    selected = receipt["selected_clients"]
    for host in dict.fromkeys(_host(c) for c in selected):
        receipt["registrations"][host] = _register(
            root, host, receipt["registrations"].get(host, {})
        )
        receipt["clients"] = _client_states(receipt)
        _save(root, receipt)


def configure(
    home: pathlib.Path, runtime_root: pathlib.Path, selected: list[str]
) -> dict[str, Any]:
    """Prepare selected assistants, enforcing exclusive user-scope athlete bindings."""
    home, runtime_root = _canonical(home), _canonical(runtime_root)
    _runtime_binding(home, runtime_root)
    if runtime_root.is_relative_to(home) or home.is_relative_to(runtime_root):
        raise ValueError("The program runtime and athlete installation must be separate folders.")
    if not selected or any(client not in ("work", "codex", "claude-code") for client in selected):
        raise ValueError("Select work, codex, claude-code, or a combination.")
    installation.activate(home)
    previous = _load(integration_root(home, runtime_root))
    combined = list(dict.fromkeys([*previous.get("selected_clients", []), *selected]))
    with _binding_registry() as (path, registry):
        _claim_bindings(path, registry, home, runtime_root, combined, previous)
        return _configure(home, runtime_root, selected)


def remove(
    home: pathlib.Path,
    runtime_root: pathlib.Path,
    selected: list[str] | None = None,
    *,
    confirm: bool = False,
) -> dict[str, Any]:
    """Preview or disconnect only this runtime's exclusively owned assistant bindings."""
    home, runtime_root = _canonical(home), _canonical(runtime_root)
    if not confirm:
        return _remove(home, runtime_root, selected, confirm=False)
    _runtime_binding(home, runtime_root)
    previous = _load(integration_root(home, runtime_root))
    with _binding_registry() as (path, registry):
        _claim_bindings(
            path, registry, home, runtime_root, previous.get("selected_clients", []), previous
        )
        result = _remove(home, runtime_root, selected, confirm=True)
        _release_bindings(path, registry, home, runtime_root, result["selected_clients"])
        return result


def _snapshot_resources(
    backup_integration: pathlib.Path, snapshot: dict[str, Any]
) -> dict[str, bytes]:
    files = {}
    if _unknown_skill_artifacts(backup_integration, set(snapshot["artifacts"])):
        raise ValueError("Unowned coach resource in recovery snapshot; preserve it for inspection.")
    for relative, expected in snapshot["artifacts"].items():
        path = _artifact_path(backup_integration, relative)
        private = relative.startswith("plugin/skills/") and not _public_skill_resource(relative)
        if private and not path.exists():
            continue
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("Recovery integration snapshot changed; preserve it for inspection.")
        if private:
            continue
        files[relative] = path.read_bytes()
    required = {
        "plugin/skills/coach/" + name
        for name in _PUBLIC_COACH_RESOURCES
        if name not in _ADDED_AFTER_FIRST_GENERATION
    }
    if not required <= files.keys():
        raise ValueError(
            "Recovery snapshot lacks public coach resources; preserve it for inspection."
        )
    return files


def restore(
    home: pathlib.Path, runtime_root: pathlib.Path, backup_integration: pathlib.Path
) -> dict[str, Any]:
    """Restore a verified prior plugin snapshot through the resumable generation transaction.

    Args:
        home: Selected athlete installation, unchanged by program rollback.
        runtime_root: Managed runtime owning the current assistant bindings.
        backup_integration: Snapshot containing the prior registration receipt and owned files.

    Returns:
        Restored generation receipt with independent client preparation outcomes.

    Raises:
        ValueError: Snapshot, current files, or user-scope ownership cannot be verified.
    """
    home, runtime_root = _canonical(home), _canonical(runtime_root)
    _runtime_binding(home, runtime_root)
    root = integration_root(home, runtime_root)
    snapshot = _load(backup_integration)
    previous = _load(root)
    if not snapshot or snapshot.get("pending_artifacts"):
        raise ValueError("Recovery integration snapshot is missing or incomplete; preserve it.")
    if snapshot.get("home") != str(home) or snapshot.get("runtime_root") != str(runtime_root):
        raise ValueError("Recovery integration snapshot serves another athlete or runtime.")
    selected = snapshot.get("selected_clients", [])
    if previous.get("selected_clients") and set(selected) != set(previous["selected_clients"]):
        raise ValueError(
            "Client selection changed since recovery snapshot; reconcile selection before rollback."
        )
    files = _snapshot_resources(backup_integration, snapshot)
    private_snapshot = any(
        name.startswith("plugin/skills/") and not _public_skill_resource(name)
        for name in snapshot["artifacts"]
    )
    _validate_files(root, files, previous)
    with _binding_registry() as (path, registry):
        _claim_bindings(path, registry, home, runtime_root, selected, previous)
        return _configure(
            home,
            runtime_root,
            selected,
            resources=files,
            version=snapshot["package_version"],
            clean_install=private_snapshot,
        )


def _handle_cli(args: argparse.Namespace) -> int:
    try:
        home = installation.select(args.home)
        if args.operation == "configure":
            result = configure(
                home, args.runtime_root, list(dict.fromkeys(args.clients.split(",")))
            )
        elif args.operation == "remove":
            selected = list(dict.fromkeys(args.clients.split(","))) if args.clients else None
            result = remove(home, args.runtime_root, selected, confirm=args.confirm)
        else:
            result = status(home, args.runtime_root)
    except (ValueError, OSError, installation.InstallationError) as exc:
        print(f"clients: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


def add_parser(subparsers: Any) -> None:
    """Add the assistant operations to the application's existing CLI parser."""
    group = subparsers.add_parser(
        "clients", help="Prepare and inspect local assistant integration."
    )
    operations = group.add_subparsers(dest="operation", required=True)
    for name in ("configure", "status", "remove"):
        operation = operations.add_parser(name)
        operation.add_argument(
            "--runtime-root", type=pathlib.Path, default=distribution.default_runtime_root()
        )
        operation.add_argument("--clients", default="", required=name == "configure")
        operation.add_argument("--confirm", action="store_true")
        operation.set_defaults(func=_handle_cli)


def main(argv: list[str] | None = None) -> int:
    """Run standalone client setup; the main CLI exposes the same operations."""
    parser = argparse.ArgumentParser(description=__doc__)
    installation.add_home_argument(parser)
    parser.add_argument(
        "--runtime-root", type=pathlib.Path, default=distribution.default_runtime_root()
    )
    parser.add_argument("operation", choices=("configure", "status", "remove"))
    parser.add_argument("--clients", default="")
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args(argv)
    return _handle_cli(args)


if __name__ == "__main__":
    raise SystemExit(main())
