# ADR 0035 - Public coach context and consistent resource exports

## Status

Accepted

## Context

Public coach packages must support saved-context lookup without exporting private race records or stale private skill copies.

## Decision

- Saved-context guidance contains generic lookup rules. Race evidence/profile stays
  in the selected installation; current metrics use sanctioned reads. Cite historical
  evidence and label race scenarios with their arithmetic assumptions.
- One standard-library manifest/module selects regular non-symlink public files.
  Native plugins, legacy archive, release resources and wheel includes must agree.
  Historical context.md remains excluded because older versions could contain private data.
- Current generation is router plus four references. An older owned router-plus-three
  generation may restore its original public bytes; do not invent a new reference or
  restore private extras. Configure upgrades generations transactionally.
- Read-only chat uses existing artifacts/MCP. Saved report/chart generation is an
  explicitly requested operation using a supported runtime. No plan means unknown;
  existing confirmation gates remain.
- Keep Poetry's standard backend and explicit includes. Release builder checks
  manifest/include agreement before build; hand-run Poetry build skips this check.
  Do not add forced network/isolated-build dependencies to make a broader claim.

## Consequences

Private source context cannot slip into client artifacts. Package/protocol success
is separate from a fresh client's saved-context, reporting and confirmation acceptance.
