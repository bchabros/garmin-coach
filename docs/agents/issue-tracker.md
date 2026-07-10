# Issue tracker: PRD-scoped Markdown

This repo has no external issue tracker (no GitHub Issues, Jira, etc.). Work is
scoped to a PRD and lives beside it under `docs/prd/`, versioned in git. This
follows the repo's house rule that the PRD -- not an external tracker -- is the
source of work (`docs/PROJECT.md`, `docs/prd/phase-6.md`).

## Layout

A PRD is a flat file by default and grows into a folder only when it needs a task
breakdown or a wayfinder map:

```
docs/prd/
├── phase-6.md                  <- flat file; no breakdown needed
└── phase-7-<slug>/             <- folder once the phase gains children
    ├── PRD.md                  <- the spec (to-spec writes here)
    ├── issues/NN-<slug>.md     <- task breakdown, numbered from 01 (to-tickets)
    └── map.md                  <- wayfinder map, if used
```

- Existing flat PRDs (`phase-0.md`..`phase-6.md`) stay flat -- they are pinned by
  README, tests, ADRs, and `src/`. Do not migrate them.
- `<feature>` is normally the phase slug (`phase-7-<name>`).
- Everything is committed to git; there is no separate scratch area.

## Conventions

- One feature per directory: `docs/prd/<feature>/`
- The spec is `docs/prd/<feature>/PRD.md`
- Implementation issues are `docs/prd/<feature>/issues/<NN>-<slug>.md`, numbered
  from `01`
- Triage state is recorded as a `Status:` line near the top of each issue file
  (see `triage-labels.md` for the role strings)
- Comments and conversation history append to the bottom of the file under a
  `## Comments` heading

## When a skill says "publish to the issue tracker"

Create a new file under `docs/prd/<feature>/` (creating the directory, and an
`issues/` subdirectory, if needed).

## When a skill says "fetch the relevant ticket"

Read the file at the referenced path. The user will normally pass the path or the
issue number directly.

## Wayfinding operations

Used by `/wayfinder`. The **map** is a file with one **child** file per ticket.

- **Map**: `docs/prd/<feature>/map.md` -- the Notes / Decisions-so-far / Fog body.
- **Child ticket**: `docs/prd/<feature>/issues/NN-<slug>.md`, numbered from `01`,
  with the question in the body. A `Type:` line records the ticket type
  (`research`/`prototype`/`grilling`/`task`); a `Status:` line records
  `claimed`/`resolved`.
- **Blocking**: a `Blocked by: NN, NN` line near the top. A ticket is unblocked
  when every file it lists is `resolved`.
- **Frontier**: scan `docs/prd/<feature>/issues/` for files that are open,
  unblocked, and unclaimed; first by number wins.
- **Claim**: set `Status: claimed` and save before any work.
- **Resolve**: append the answer under an `## Answer` heading, set
  `Status: resolved`, then append a context pointer (gist + link) to the map's
  Decisions-so-far in `map.md`.
