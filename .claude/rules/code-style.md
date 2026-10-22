# Code style

Use Python 3.13, four-space indentation and the Ruff configuration in `pyproject.toml`.
Keep changes focused and follow the module boundaries in `docs/DEVELOPMENT.md`.

- Use `snake_case` for functions/modules, `CamelCase` for classes and `UPPER_SNAKE_CASE`
  for constants. Keep constants beside the domain logic that owns them.
- Each module uses `logging.getLogger(__name__)`; messages start with a lowercase
  component prefix, such as `sync:` or `daily:`.
- Code, comments, docstrings, logs, errors and CLI help are English. Preserve the
  athlete-authored Polish plan headers and other literal input-format contracts.
- Keep credentials and saved Garmin tokens outside source control. Use the selected
  installation's configuration and the existing transport boundary.
- Write Google-style docstrings as described in [documentation.md](documentation.md).
- Code and command output use plain text without emoji.

Before committing, run `task format`, `task lint` and `task check`. The last command
runs offline tests, lint, the docstring gate and mypy; it does not check formatting.
The pre-commit hook installed by `task install` enforces the full check.
