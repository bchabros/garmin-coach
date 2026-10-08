# Local client validation for issue #82

Date: 2026-10-08. Target: local macOS and the existing Poetry checkout.

## Environment observed

- Target application: 26.930.61225, build 13232, bundle identifier `com.openai.codex`.
  This identifies the installed bundle, not a completed Work acceptance.
- Existing Poetry runtime: Python 3.13.3.
- Working implementation: project-scoped skill discovery, guarded project launcher,
  printable manual MCP configuration and a synthetic installation.

## Evidence

Offline verification: **1020 passed, 1 skipped** (the absent gitignored personal
profile in the isolated checkout). Ruff formatting, lint, docstrings and mypy
passed. The new project boundary contributes 13 tests; the targeted coach/routing
regression run passed 182 tests with the same profile skip.

| Check | Result | Meaning |
| --- | --- | --- |
| Real stdio initialization, tool listing, digest and snapshot reads | Passed on temporary SQLite | Actual local protocol connection, launched outside the installation folder |
| Codex and Claude configuration output | Passed on temporary installations | Generated entries invoke the same guarded launcher without overwriting settings |
| Missing database/schema, sparse data, missing references and invalid settings | Passed offline | Actionable refusal; no empty database bootstrap or leaked fixture secret |
| Canonical coach discovery | Passed document/resource contract | Both repo skill links resolve to the same router and references |
| Existing confirmation/coach-tool behavior | Passed targeted offline regressions | Existing MCP and fake-publisher seams retain the guards |
| Existing athlete installation local read | Passed through guarded stdio MCP | 26 tools available; digest/snapshot calls completed; database file checksum unchanged. This is not Work acceptance. |
| Fresh Codex conversation | Pending | Needs actual discovery, routing and fixture read in the client |
| Fresh Claude Code conversation | Pending | Needs actual discovery, routing and fixture read in the client |
| Fresh local Work fixture read | Pending | No actual Work result recorded |
| Fresh local Work development | Pending | Needs isolated-checkout investigation, reviewable change and offline test |
| Fresh local Work personal-data read | Pending | Needs the final locally sourced coaching answer |

Computer Use refused access to the target bundle: "Computer Use is not allowed to
use the app 'com.openai.codex' for safety reasons." The implementation does not work
around that restriction. Complete the prepared application steps through the normal
user interface and record their observed results.

## Record application acceptance

For each client, record the date, application/version, product mode, execution
location, primary folder role (fixture/source/personal installation), skill
installation mechanism, coach connection status, references/profile accessed,
tool names called, freshness handling, and success/failure with the recovery action.

For Work development, also record the isolated checkout, inspected files,
reviewable test-only change, command and exit result. Keep personal records,
credentials and profile contents out of this document.

**Completion:** #82 remains open until its fresh Work coaching and development
acceptance and the other client compatibility checks have been completed.
