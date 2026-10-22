# Documentation rules

Changed public functions, methods and classes under `src/` use Google-style docstrings.
Describe behavior and non-obvious constraints. Add `Args:`, `Returns:` and `Raises:`
where useful; one line is enough for an obvious operation. Keep examples in this domain.

`task docstrings` checks source with Ruff's Google convention:
`ruff check src --select D --ignore D100,D104,D105,D107`.
`task check` includes that gate with offline tests, lint and mypy.

Keep each rule authoritative in one place and link to it from other guides.
Update `CLAUDE.md` and its byte-identical `AGENTS.md` mirror together.
The packaged `src/garmin_coach/core/schema.sql` is the sole schema source.
