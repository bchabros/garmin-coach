"""Guard: AGENTS.md (Codex) must not drift from CLAUDE.md (Claude Code).

The thin shared core is authored once in CLAUDE.md and byte-mirrored to AGENTS.md so
both agents follow identical rules. See docs/adr/0008-docs-layering.md.
"""

from __future__ import annotations

import pathlib


def test_agents_md_mirrors_claude_md():
    root = pathlib.Path(__file__).parent.parent
    claude = (root / "CLAUDE.md").read_text()
    agents = (root / "AGENTS.md").read_text()
    assert claude == agents, (
        "CLAUDE.md and AGENTS.md have diverged; AGENTS.md is a byte-for-byte mirror "
        "of CLAUDE.md -- copy CLAUDE.md over AGENTS.md."
    )
