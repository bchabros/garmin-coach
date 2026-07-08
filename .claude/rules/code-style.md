# Code style

Match the surrounding code; keep functions small and readable.

- **Short, single-purpose functions.** Favour several small functions over one long
  one; pull logic into a named private helper (`_`-prefixed) rather than nesting, and
  prefer guard clauses / early returns over deep `if` trees.
- **Full type hints, mypy-clean.** Annotate every signature; `from __future__ import
  annotations` + modern generics (`dict[str, Any]`, `X | None`). `task check` runs
  `mypy src` — keep it green.
- **Docstrings.** One module docstring per file (its role in a sentence or two) and a
  Google-style docstring on each public function; a single line is enough when the
  signature already tells the story. English only.
- **Comments say why, not what** — non-obvious intent, gotchas, decisions (often
  pointing at a PRD/ADR). Don't narrate code the reader can already read.
- **No magic literals.** Module-level `UPPER_CASE` constants at the top; coach tunables
  live in the `coach_thresholds` table, not inline.
- **Don't over-generalise (YAGNI).** No parameters, hooks, or abstractions the current
  spec doesn't need; inline until a second real caller appears.
- **Formatting is Ruff's job** — line length ≤ 100, run `task format`; don't hand-format.
