"""Prepare local assistant plugins for one explicitly selected athlete installation."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import pathlib
import shlex
import shutil
import subprocess
import sys
import tempfile
from typing import Any

from .core import installation


def default_runtime_root() -> pathlib.Path:
    """Return the managed macOS runtime location, separate from athlete records."""
    return pathlib.Path.home() / "Library/Application Support/Garmin Coach"


def integration_root(home: pathlib.Path, runtime_root: pathlib.Path) -> pathlib.Path:
    """Return the generated integration directory for the selected installation."""
    identity = hashlib.sha256(str(home.resolve()).encode()).hexdigest()[:16]
    return runtime_root / "integrations" / identity


def _resources() -> dict[str, bytes]:
    source = pathlib.Path(__file__).resolve().parents[2] / "skills/coach"
    if source.is_dir():
        return {str(p.relative_to(source)): p.read_bytes() for p in source.rglob("*.md")}
    distribution = importlib.metadata.distribution("garmin-coach")
    prefix = "skills/coach/"
    return {
        str(p)[len(prefix) :]: pathlib.Path(str(distribution.locate_file(p))).read_bytes()
        for p in distribution.files or []
        if str(p).startswith(prefix) and str(p).endswith(".md")
    }


def _json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def _package(home: pathlib.Path, runtime: pathlib.Path, name: str) -> dict[str, bytes]:
    version = importlib.metadata.version("garmin-coach")
    resources = _resources()
    required = {"SKILL.md", *(f"references/{n}.md" for n in ("report", "planning", "authoring"))}
    if not required <= resources.keys():
        raise ValueError("Coach resources missing; reinstall the complete Garmin Coach package.")
    binding = (
        "\n\n## Selected athlete installation\n\n"
        f"This plugin serves `{home}`. Resolve memory, plans, reports and other athlete "
        "files against this absolute folder, even when the plugin is installed in a cache. "
        "Resolve `references/` against this skill's own folder. Select the athlete installation "
        "as the primary local conversation folder for file access. This is a coaching "
        "installation; source development requires a separately selected source checkout. "
        "For an allowed local CLI fallback, use "
        f"`{shlex.join([str(runtime / 'bin/garmin-coach'), '--home', str(home)])}` "
        "and the requested command. This managed executable replaces the "
        "source-checkout Poetry/PYTHONPATH fallback below. If it is unavailable, report the "
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


def _atomic_write(path: pathlib.Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = pathlib.Path(stream.name)
        stream.write(content)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


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


def _save(root: pathlib.Path, receipt: dict[str, Any]) -> None:
    _atomic_write(root / "registration.json", _json(receipt))


def _command(arguments: list[str]) -> bool:
    try:
        result = subprocess.run(arguments, capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _register(root: pathlib.Path, host: str, previous: dict[str, Any]) -> dict[str, Any]:
    name = "garmin-coach-" + root.name
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
    if registration.get("state") == "registered":
        return registration
    if not registration.get("marketplace_added"):
        if not _command([executable, "plugin", "marketplace", "add", str(root)]):
            return {**registration, "state": "failed", "stage": "marketplace"}
        registration["marketplace_added"] = True
        receipt = _load(root)
        receipt["registrations"][host] = {**registration, "state": "preparing"}
        _save(root, receipt)
    operation = "add" if host == "openai" else "install"
    command = [executable, "plugin", operation, f"{name}@{name}"]
    if host != "openai":
        command.extend(["--scope", "user"])
    succeeded = _command(command)
    return {**registration, "state": "registered" if succeeded else "failed", "stage": "plugin"}


def _client_states(receipt: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for client in receipt["selected_clients"]:
        registration = receipt["registrations"].get(
            "claude-code" if client == "claude-code" else "openai", {"state": "failed"}
        )
        state = registration["state"]
        if state == "registered":
            state = "needs_activation"
        result[client] = {
            "state": state,
            "actions": [
                "Open the selected athlete installation as a local conversation folder. "
                "Restart the client, enable the plugin if requested, and verify its coach skill "
                "and MCP tools in a fresh conversation. Record client acceptance separately."
                if state == "needs_activation"
                else "Install or repair the selected client, then rerun configuration. "
                "Client command output is withheld because it may contain private settings."
            ],
        }
    return result


def _prepare_files(root: pathlib.Path, files: dict[str, bytes], previous: dict[str, Any]) -> None:
    for relative, content in files.items():
        target = _artifact_path(root, relative)
        expected = previous.get("artifacts", {}).get(relative, hashlib.sha256(content).hexdigest())
        if target.exists() and (
            not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != expected
        ):
            raise ValueError(f"Integration artifact changed: {relative}; restore it before setup.")
    for relative, content in files.items():
        _atomic_write(_artifact_path(root, relative), content)


def status(home: pathlib.Path, runtime_root: pathlib.Path) -> dict[str, Any]:
    """Inspect owned resources and recorded registration without changing client settings."""
    home, runtime_root = home.expanduser().resolve(), runtime_root.expanduser().resolve()
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
    for relative, expected in receipt["artifacts"].items():
        target = _artifact_path(root, relative)
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            changed.append(relative)
    source = _package(home, runtime_root, "garmin-coach-" + root.name)
    source_matches = all(
        receipt["artifacts"].get(relative) == hashlib.sha256(content).hexdigest()
        for relative, content in source.items()
    )
    return {
        **receipt,
        "integrity": "changed" if changed else "verified",
        "changed_artifacts": changed,
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
    if registration.get("state") != "plugin_removed":
        operation = "remove" if host == "openai" else "uninstall"
        command = [executable, "plugin", operation, expected]
        if host != "openai":
            command.extend(["--scope", "user"])
        if not _command(command):
            raise ValueError("Client plugin removal failed; repair the client and retry removal.")
        registration["state"] = "plugin_removed"
        _save(root, receipt)
    if registration.get("marketplace_added"):
        if not _command([executable, "plugin", "marketplace", "remove", name]):
            raise ValueError("Client marketplace removal failed; retry removal to finish.")
        registration["marketplace_added"] = False
        _save(root, receipt)


def remove(
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
    if receipt["integrity"] == "changed":
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
    retained_hosts = {"claude-code" if c == "claude-code" else "openai" for c in remaining}
    for host in list(receipt["registrations"]):
        if host not in retained_hosts:
            _unregister(root, host, receipt)
            del receipt["registrations"][host]
            _save(root, receipt)
    receipt["selected_clients"] = remaining
    receipt["clients"] = _client_states(receipt)
    _save(root, receipt)
    return {**receipt, "confirmed": True, "remove_clients": removed}


def configure(
    home: pathlib.Path, runtime_root: pathlib.Path, selected: list[str]
) -> dict[str, Any]:
    """Prepare complete assistant packages without logging into Garmin.

    Args:
        home: Explicit athlete installation.
        runtime_root: Managed program directory, outside athlete records.
        selected: Local assistant surfaces to add.

    Returns:
        An integration receipt distinguishing registration from client acceptance.
    """
    home, runtime_root = home.expanduser().resolve(), runtime_root.expanduser().resolve()
    if not selected or any(client not in ("work", "codex", "claude-code") for client in selected):
        raise ValueError("Select work, codex, claude-code, or a combination.")
    if runtime_root.is_relative_to(home) or home.is_relative_to(runtime_root):
        raise ValueError("The program runtime and athlete installation must be separate folders.")
    installation.activate(home)
    root = integration_root(home, runtime_root)
    previous = _load(root)
    selected = list(dict.fromkeys([*previous.get("selected_clients", []), *selected]))
    name = "garmin-coach-" + root.name
    files = _package(home, runtime_root, name)
    _prepare_files(root, files, previous)
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "home": str(home),
        "runtime_root": str(runtime_root),
        "integration_root": str(root),
        "package_version": importlib.metadata.version("garmin-coach"),
        "selected_clients": selected,
        "registrations": previous.get("registrations", {}),
        "client_acceptance": "not_run",
        "protocol_check": "not_run",
        "runtime_available": (runtime_root / "bin/garmin-coach-mcp").is_file(),
        "artifacts": {path: hashlib.sha256(content).hexdigest() for path, content in files.items()},
    }
    _save(root, receipt)
    _register_clients(root, receipt)
    return receipt


def _register_clients(root: pathlib.Path, receipt: dict[str, Any]) -> None:
    selected = receipt["selected_clients"]
    for host in dict.fromkeys("claude-code" if c == "claude-code" else "openai" for c in selected):
        receipt["registrations"][host] = _register(
            root, host, receipt["registrations"].get(host, {})
        )
        receipt["clients"] = _client_states(receipt)
        _save(root, receipt)


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
        operation.add_argument("--runtime-root", type=pathlib.Path, default=default_runtime_root())
        operation.add_argument("--clients", default="", required=name == "configure")
        operation.add_argument("--confirm", action="store_true")
        operation.set_defaults(func=_handle_cli)


def main(argv: list[str] | None = None) -> int:
    """Run standalone client setup; the main CLI exposes the same operations."""
    parser = argparse.ArgumentParser(description=__doc__)
    installation.add_home_argument(parser)
    parser.add_argument("--runtime-root", type=pathlib.Path, default=default_runtime_root())
    parser.add_argument("operation", choices=("configure", "status", "remove"))
    parser.add_argument("--clients", default="")
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args(argv)
    return _handle_cli(args)


if __name__ == "__main__":
    raise SystemExit(main())
