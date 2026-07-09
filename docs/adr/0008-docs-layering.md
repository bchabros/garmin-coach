# ADR 0008 - Layered documentation (thin shared core + per-role guides)

## Status

Accepted

## Context

`CLAUDE.md` had grown into a monolith serving three readers at once: an agent
*changing the code* (Claude Code, Codex), an agent *operating the system* (Claude
Cowork running sync and the coach skill), and a human wanting orientation. Because the
harness always loads `CLAUDE.md` in full, an operator paid context for TDD seams and
normalizer-purity rules, while a coder waded through backfill exit codes and rate-limit
advice.

Two secondary problems compounded it:

- `AGENTS.md` was meant to mirror `CLAUDE.md` but had **drifted** -- it still said
  phases run 0->5, called Phase 5 "Next up" though it was done, and lacked the Phase 5/6
  ledger.
- Domain vocabulary lived in **two** places, `CONTEXT.md` and `docs/glossary.md`, which
  overlapped and disagreed in wording.

There is no harness mechanism to load a different instruction file per mode: `CLAUDE.md`
is always in context. So the reader split cannot be "one file per audience" -- it must be
"a thin always-loaded core that points to per-role guides read on demand".

## Decision

- **Thin shared core.** `CLAUDE.md` keeps only what every session needs: what the project
  is, the golden rule, the onboarding-data cutoff, the command block, pointers to the
  per-role guides, and the `.claude/rules/*` imports. Everything audience-specific moves
  out.

- **`AGENTS.md` is a byte-for-byte mirror of `CLAUDE.md`,** guarded by
  `tests/test_agents_mirror.py` (modeled on `tests/test_schema_sync.py`). The core is
  written audience-neutrally so the identical file reads correctly for both agents. This
  kills the drift by construction: the mirror cannot silently diverge again.

- **Per-role guides, read on demand.** `docs/DEVELOPMENT.md` (workflow, module map,
  conventions, developer gotchas, testing seams, deferred backlog) and
  `docs/OPERATIONS.md` (running the pipeline, exit-code contract, logs, rate limits,
  generating a coach report). The coach skill (`skills/coach/SKILL.md`) is already the
  naturally on-demand operator layer for report writing and is unchanged.

- **Single glossary.** `docs/glossary.md` is the sole domain glossary. `CONTEXT.md`'s
  unique terms (stream, partial success, daily stream, activities range, complete week,
  weekly rollup, planned/actual intent, plan adherence, weekly plan-vs-actual fact,
  monotony/strain, deload) were folded in; on duplicates the existing glossary wording
  won, preserving any unique factual nuance. `CONTEXT.md` is deleted.

- **Phase history is not duplicated.** The "done" phase ledger in `CLAUDE.md` was removed
  (the `README.md` status table plus each phase's PRD and ADR already record it); only the
  genuinely-not-done deferred items moved, into `docs/DEVELOPMENT.md`.

- **References.** Live code comments in `weekly.py` repoint from `CONTEXT.md` to
  `docs/glossary.md`. Historical PRDs (phase-5, phase-6) keep their `CONTEXT.md` references
  untouched -- they are dated records, not living docs. `README.md` and this ADR reflect
  the new layout.

## Consequences

- The always-loaded core shrinks from ~135 lines to a thin skeleton; each session pays
  context only for the guide its role needs.
- A new test (`test_agents_mirror.py`) must pass: after editing `CLAUDE.md`, copy it over
  `AGENTS.md` or the suite fails.
- New docs to keep current: `docs/DEVELOPMENT.md`, `docs/OPERATIONS.md`. Contributors add
  developer facts to the former, operator facts to the latter, and the core stays lean.
- The mirror makes `CLAUDE.md` and `AGENTS.md` identical in body **and** title; the H1 is
  audience-neutral (`Agent guide`) rather than the filename.
