# Documentation Rules

Every public function, method, and class under `src/` and `config/` must have a
**Google-style docstring**. Notebooks (`notebooks/`), sandbox scripts (`scratch/`),
tests (`tests/`), and `__init__.py` files are exempt.

## Google style

Use `Args:`, `Returns:`, and `Raises:` sections — but only where the logic isn't
self-evident. Do not add docstrings to code you didn't change.

```python
def extract(self, payload: str | dict[str, Any]) -> ExtractionResult:
    """Extract files and metadata from a claim JSON payload.

    Args:
        payload: Raw claim JSON as a string, or an already-parsed dict.

    Returns:
        An ExtractionResult with decoded files and any validation errors.

    Raises:
        ValueError: If the payload is not valid schema v2.0.
    """
```

One-line docstrings are fine when the behavior is obvious:

```python
def is_success(self) -> bool:
    """Return True if no errors were recorded."""
```

## Enforcement

Backed by ruff pydocstyle (`convention = "google"`) — see `pyproject.toml`.
The enabled rules are the "missing docstring" checks (`D101`/`D102`/`D103`) plus
the Args-section check (`D417`).

```bash
task docstrings   # ruff check --select D101,D102,D103,D417 src/ config/
```

`task check` runs this alongside lint, format-check, and typecheck.
