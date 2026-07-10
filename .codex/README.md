# Codex workspace notes

This directory is Codex's local companion space for this repository, analogous to
the existing `.claude` and `.junie` directories.

The canonical Codex-facing instructions live in the repository root:

- `AGENTS.md` - primary instructions for Codex, mirrored from `CLAUDE.md`
- `CLAUDE.md` - Claude Code source context
- `docs/PROJECT.md` - full project brief and roadmap
- `docs/prd/` - phase-specific PRDs

## Codex operating rules

- Treat `AGENTS.md` as the first file to read before changing code.
- Keep transport separate from intelligence: ETL may call Garmin via
  `garminconnect`, but metrics and coach layers read only the finished SQLite DB.
- Use `mcp__garmin__*` tools only for ad-hoc exploration or fixture building, never
  in the deterministic pipeline.
- Build phase by phase and do not advance until the current phase Definition of
  Done is met.
- Work test-first at the established seams: `models.py`, `db.py`, and `sync.py`
  with an injected fake client.
- Use Poetry for dependency and command workflows.
- Write new and changed public docstrings in Google style.
- Before committing, run:

```bash
task check
```

## Local Codex notes

Keep any Codex-only planning, scratch references, or future automation notes in
this folder. Do not put project source of truth here; update the root docs when a
decision should be shared by all agents.
