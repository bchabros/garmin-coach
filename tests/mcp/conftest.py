"""Coach MCP tests run inside a temporary installation, as the server always does."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _selected_installation(athlete_home):
    """Every coach tool reads the selected installation's settings (ADR 0031)."""
    return athlete_home
