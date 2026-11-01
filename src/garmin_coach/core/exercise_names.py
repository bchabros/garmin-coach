"""The shared spelling contract for captured/manual exercise identifiers."""

from __future__ import annotations

import re
from typing import Any

_NAME = re.compile(r"^[A-Z0-9_]+$")


def normalize(raw: Any, *, label: str = "exercise") -> str:
    """Normalize spaces/hyphens and case, rejecting empty or unsupported spellings."""
    name = re.sub(r"[\s\-]+", "_", str(raw).strip().upper())
    if not name:
        raise ValueError(f"{label}: empty name")
    if not _NAME.match(name):
        raise ValueError(
            f"{label}: a name may use letters, digits and underscores only (got {raw!r})"
        )
    return name
