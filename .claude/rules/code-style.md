# Code Style

## Before committing

Run `task format && task lint` — non-negotiable. `task check` additionally runs
format-check, typecheck, and the docstring gate (see [documentation.md](documentation.md)).

## Formatting

4-space indentation, 100-character line length (enforced by ruff — see `pyproject.toml`).

## Architecture

Follow SOLID. Keep functions short and at a single abstraction level. A function
should fit on one screen without scrolling — if it doesn't, refactor it into
focused private helpers.

## Project-specific conventions not enforced by Ruff

**Logging** — each module gets its own logger via the standard library:
```python
import logging

logger = logging.getLogger(__name__)

logger.info("[NB_02] Processing %d attachments for batch %s", count, batch_id)
```
Prefix messages with a `[Component]` tag. See the pipeline notebooks
(`notebooks/NB_00`–`NB_02`) for the established pattern.

**Docstrings** — Google-style with `Args:` and `Returns:` sections, only where the
logic isn't self-evident. Don't add docstrings to code you didn't change. Full rules
in [documentation.md](documentation.md).

**Naming**
- `snake_case` — functions and modules
- `CamelCase` — classes
- `UPPER_SNAKE_CASE` — constants (e.g. `ALLOWED_PROCESS_TYPES` in `src/etl_pipeline/models.py`, `MIRA_DEV_CLUSTER_ID` in `src/consts/databricks.py`)

**Constants** — module-level domain constants belong in `src/consts/`, grouped by domain
(e.g. `databricks.py`, `llms.py`). Schema-contract constants live alongside the Pydantic
models in `src/etl_pipeline/models.py`. Exception: frozen dataclass configs
in `config/config.py` are env-specific configuration objects, not constants — they stay
there. Private compiled-regex caches (underscore-prefixed) may stay in the module that
uses them.

**Secrets** — never commit secrets. Azure Document Intelligence credentials come from the
Databricks secret scope `mira-secrets`; local/dev runs fall back to environment variables
(`DOC_INTELLIGENCE_ENDPOINT` / `DOC_INTELLIGENCE_API_KEY`). Never hard-code credentials.

**Language** — all code, comments, docstrings, log messages, argparse help strings,
error messages, and print statements must be in English. Exceptions: LLM prompt
strings and Pydantic field descriptions may be in Polish when the downstream model
or user-facing output requires it.

**Emojis** — never use emojis anywhere in code, comments, docstrings, log messages,
or print statements.
