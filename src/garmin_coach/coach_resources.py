"""Select the public coach resources every package ships, with the standard library only.

Release tooling, the build backend and the standalone skill packager load this file by
path before application dependencies exist (ADR 0035), so it imports nothing from the
package.
"""

from __future__ import annotations

import json
import pathlib

MANIFEST = pathlib.Path(__file__).with_name("coach_resources.json")


def public_names() -> tuple[str, ...]:
    """Return the public resource names, relative to the coach skill directory."""
    return tuple(json.loads(MANIFEST.read_text()))


def resource_like(path: pathlib.Path) -> bool:
    """Return True for a file that could carry coach instructions: Markdown or a symlink."""
    return path.is_symlink() or path.suffix.lower() == ".md"


def read_public(root: pathlib.Path, *, missing_ok: bool = False) -> dict[str, bytes]:
    """Read every public resource under root as a regular file reached without symlinks.

    Args:
        root: The coach skill directory (``skills/coach`` in a checkout or a package).
        missing_ok: Skip absent resources instead of refusing, for partial synced copies.

    Returns:
        Resource bytes keyed by manifest name, in manifest order.

    Raises:
        ValueError: A name escapes root, the resource or any directory between it and
            root (root included) is a symlink, or a required resource is missing.
    """
    resources = {}
    for name in public_names():
        relative = pathlib.PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Invalid public coach resource path: {name}")
        path = root / relative
        if any(part.is_symlink() for part in (path, *path.parents[: len(relative.parts)])):
            raise ValueError(f"Symlinked public coach resource: {name}")
        if not path.is_file():
            if missing_ok:
                continue
            raise ValueError(f"Missing public coach resource: {name}")
        resources[name] = path.read_bytes()
    return resources
