# Testing Workflow

When adding or changing tests, keep them focused and run them with `task test`
or targeted `poetry run pytest ...` commands.

## Flow (TDD)

1. Define expected behavior
2. Write tests (they should fail)
3. Verify they fail for the right reason
4. Implement
5. Run tests — all must pass

## Rules

- **Mock external dependencies in unit tests** — Azure Storage Queue, Azure Document
  Intelligence, ADLS Gen2, and the Spark/Delta layer. Unit tests must not hit live Azure
  or Databricks.
- **Use fixtures for payload validation** — `fixtures/MSG-2026-TEST12345.json`
  is the current valid `ClaimPayload` fixture for the bronze `MSG` contract.
  Prefer fixtures over hand-rolled payloads for integration-style validation tests.
- **Payload validation** — exercise both VALID and quarantined payloads. Cover a
  good payload, unsupported `schema_version`, invalid `message_id`, invalid
  file operation/classes combinations, and failing embedded-file integrity
  checks (bad base64, size mismatch, checksum mismatch).

## Commands

```bash
task test       # poetry run pytest
task test-cov   # pytest with coverage (term-missing)
```

Pytest config lives in `pyproject.toml` (`[tool.pytest.ini_options]`): tests are
discovered from `tests/`, files named `test_*.py`.
