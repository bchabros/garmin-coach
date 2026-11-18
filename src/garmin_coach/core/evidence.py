"""Stable identities for complete reviewed JSON evidence."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def fingerprint(value: Any) -> str:
    """Identify complete reviewed evidence, independent of JSON key ordering."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()
