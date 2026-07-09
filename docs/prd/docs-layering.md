# Spec: Layered documentation (thin shared core + per-role guides)

> Not a build phase. A documentation reorganization. Sources: grilling session
> 2026-07-08, current `CLAUDE.md` / `AGENTS.md` / `CONTEXT.md` / `docs/glossary.md`,
> `README.md`, `.codex/README.md`. Decision rationale captured in
> `docs/adr/0008-docs-layering.md`.

## Problem Statement

`CLAUDE.md` is a monolith aimed at three different readers at once: an agent that is
*changing the code* (Claude Code, Codex), an agent that is *operating the system*
(Claude Cowork running the coach skill and the sync), and a human reader who just
wants orientation. Everything loads on every session, so an operator pays context for
TDD seams and normalizer purity, while a coder wades through backfill exit codes.

Two further symptoms of the same problem:

- `AGENTS.md` was meant to mirror `CLAUDE.md` but has drifted (it still says phases go
  0->5, calls Phase 5 "Next up" though it is done, and is missing the Phase 5/6 ledger).
- The domain vocabulary lives in two places, `CONTEXT.md` and `docs/glossary.md`, which
  overlap and disagree in wording.

There is no mechanism in the harness to load a different file per mode: `CLAUDE.md` is
always in context. So the reader split cannot be "a file per audience" -- it has to be
"a thin always-loaded core that points to per-role guides read on demand".

## Solution

Split the documentation into layers:

- A **thin shared core** (`CLAUDE.md`, byte-mirrored to `AGENTS.md`) that every session
  loads: what the project is, the golden rule, the onboarding-data cutoff, the command
  block, and pointers to the per-role guides.
- **`docs/DEVELOPMENT.md`** -- read on demand when changing code: workflow, module map,
  conventions, developer gotchas, testing seams, and the deferred backlog.
- **`docs/OPERATIONS.md`** -- read on demand when operating the system: how to run the
  pipeline, exit-code contract, logs, rate-limit handling, and how to generate a coach
  report.
- **`docs/glossary.md`** as the single domain glossary; `CONTEXT.md` is deleted after its
  unique terms are folded in.

Each role reaches its guide through a pointer in the core, so a session pays context only
for what it actually needs. The coach skill (`skills/coach/SKILL.md`) is already the
naturally on-demand operator layer for report writing and is left unchanged.

## User Stories

1. As an operating agent (Cowork), I want the always-loaded core to be short, so that I
   do not spend context on TDD and normalizer rules I will never use.
2. As an operating agent, I want an `OPERATIONS.md` runbook, so that I can run backfill,
   `daily`, and `features` and interpret their exit codes without reading the source.
3. As an operating agent, I want the exit-code contract (ok/0, degraded/1, failed/2)
   documented in one place, so that I know what to do when a run is degraded or failed.
4. As an operating agent, I want the rate-limit (429) guidance in the runbook, so that I
   wait it out instead of hammering the login endpoint.
5. As an operating agent, I want to know that re-running backfill is safe (idempotency),
   so that I can re-run without fear of corrupting core row counts.
6. As an operating agent, I want to know that backfill excludes "today", so that I do not
   treat a missing current-day row as a bug.
7. As an operating agent, I want one paragraph on generating a coach report with a pointer
   to the coach skill, so that I can produce a report without duplicating skill logic.
8. As a coding agent (Claude Code / Codex), I want a `DEVELOPMENT.md`, so that the
   grill -> PRD -> TDD workflow, module map, and conventions live in one place.
9. As a coding agent, I want developer-only gotchas (token API, shape drift, device-keyed
   maps) in the developer guide, so that the core stays lean.
10. As a coding agent, I want the deferred backlog (activity_sets, multi-sport weighting,
    VO2max trend charts, PDF/Notion export) preserved in the developer guide, so that
    nothing that was only recorded in the CLAUDE.md ledger is lost.
11. As a coding agent, I want the testing seams listed in the developer guide, so that I
    know where tests attach before writing them.
12. As Codex, I want `AGENTS.md` to be an exact copy of `CLAUDE.md`, so that I follow the
    same rules as Claude Code with no drift.
13. As a maintainer, I want a test that fails when `AGENTS.md` and `CLAUDE.md` diverge, so
    that the mirror cannot silently drift again.
14. As any agent, I want a single domain glossary, so that a term has exactly one
    definition to trust.
15. As any agent, I want the unique `CONTEXT.md` terms (stream, partial success, daily
    stream, activities range, complete week, weekly rollup, plan adherence, weekly
    plan-vs-actual fact) preserved in the glossary, so that no vocabulary is lost.
16. As a maintainer, I want live code comments that referenced `CONTEXT.md` repointed to
    `docs/glossary.md`, so that no reference dangles after the delete.
17. As a reader, I want `README.md`'s file-tree and workflow pointer to reflect the new
    layout, so that the repo map is accurate.
18. As a maintainer, I want the rationale for the layering recorded in an ADR, so that a
    future reader understands why the split exists, not just that it does.
19. As any agent, I want the core to keep the onboarding-data cutoff (2026-06-08), so that
    every number is interpreted against the right data-start.
20. As any agent, I want the golden rule (separate transport from intelligence) to remain
    in the always-loaded core, so that the most important invariant is never missed.

## Implementation Decisions

### Documents

- **`CLAUDE.md` (rewritten, thin, English)** keeps only: title + "what this is" (2-3
  sentences with brief/PRD pointers), the golden rule, a one-line onboarding cutoff, the
  full bash command block, a "where to go next" pointer list (DEVELOPMENT / OPERATIONS /
  glossary), and the `@.claude/rules/*` import block plus one sentence telling Codex to
  read those rule paths directly.
- **`AGENTS.md`** becomes a byte-for-byte copy of `CLAUDE.md`.
- **`docs/DEVELOPMENT.md` (new, English)** receives: the Workflow section
  (grill -> PRD -> TDD, seams, `task check`), the module map, the Conventions section
  (Poetry, docstrings, schema source-of-truth + sync, normalizer purity/scalars, fixture
  anonymization), the developer gotchas (garminconnect token API, shape drift, device-keyed
  maps), the Testing Seams list (four points lifted from `CONTEXT.md`), and a **Deferred**
  section (activity_sets, multi-sport/discipline weighting, VO2max/threshold trend charts,
  PDF/Notion export).
- **`docs/OPERATIONS.md` (new, English)** receives: how to run backfill / daily / features
  and when; the exit-code contract (ok/0, degraded/1, failed/2) with what to do for each;
  where logs are (`LOG_PATH`, rotation) and what `degraded` means (isolated stream
  failure); 429 / rate-limit handling; backfill-excludes-today; idempotency ("re-run is
  safe"); one paragraph on generating a coach report with a pointer to
  `skills/coach/SKILL.md`; and a pointer to `docs/prd/phase-4.md` for deep detail.
- **`docs/glossary.md`** gains the unique `CONTEXT.md` terms folded into the right
  sections; on duplicate terms the existing glossary wording wins, but any unique factual
  nuance (e.g. "trained without watch reads as rest") is preserved.
- **`CONTEXT.md`** is deleted.
- **`docs/adr/0008-docs-layering.md` (new)** records context, decision (layered docs +
  mirror mechanism + single glossary + pointer-not-per-mode), and consequences.

### Content-routing decisions (gotchas)

- Onboarding cutoff -> one line in core + `glossary` (`data_start`).
- Backfill-excludes-today, 429 rate limits, idempotency -> `OPERATIONS.md`.
- garminconnect token API, shape drift, device-keyed maps -> `DEVELOPMENT.md`.

### Phase history

- The "done" phase ledger in `CLAUDE.md` is deleted (redundant with the `README.md` status
  table + the PRDs + the ADRs).
- Only the genuinely-not-done "deferred" items move, into `DEVELOPMENT.md`.

### References

- `src/garmin_coach/weekly.py` comments (module docstring line ~5 and the classifier
  helper ~30) repoint from `CONTEXT.md` to `docs/glossary.md`.
- The phase-5 and phase-6 PRDs keep their `CONTEXT.md` references untouched -- they are
  dated historical records.
- `README.md` file-tree (lines ~51-54) drops `CONTEXT.md`, adds `docs/DEVELOPMENT.md` and
  `docs/OPERATIONS.md`, and relabels `CLAUDE.md`/`AGENTS.md` as the thin shared core; the
  workflow reference (line ~297) points at `docs/DEVELOPMENT.md`.
- `.codex/README.md` is verified; line 8 ("mirrored from `CLAUDE.md`") stays true under the
  copy + test approach, so no edit is expected.

### Language

All documentation files are written in English (repo convention). Chat may be Polish.

## Testing Decisions

- **Seam:** the single automated seam is a new `tests/test_agents_mirror.py` that asserts
  `AGENTS.md` is byte-for-byte identical to `CLAUDE.md`, modeled on the existing
  `tests/test_schema_sync.py`. It is the highest available seam and reuses an established
  pattern rather than inventing a new one.
- **No other automated tests.** The remaining work is prose; correctness is verified by
  review against this spec, `ruff`/`mypy` staying green (the `weekly.py` comment edits must
  not break lint), and the full suite passing once at the end.
- **Manual verification:** `grep` the repo for surviving `CONTEXT.md` references outside the
  two historical PRDs; confirm no dangling links in `README.md`.
