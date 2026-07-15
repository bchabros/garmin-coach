# Issue tracker: GitHub issues (with a PRD-file history)

New work is tracked as **GitHub issues** on `bchabros/garmin-coach`, titled by the
capability gap they close (not by roadmap phase numbers). The phased build that got
the system here lives on in `docs/prd/` as history - those files are pinned by
README, tests, and ADRs and are **not** migrated.

The transition was decided after Phase 11 shipped (2026-07-15); the first issues in
the new convention are #13 (race-day pacing), #16 (strength/HIIT authoring), and
epic #18 (the coach MCP server).

## Conventions

- **One issue per work item**, titled by what is missing or being built
  (e.g. "Strength/HIIT workout authoring + push"), not "Phase N".
- **The spec lives in the issue body** - problem, solution, decisions, testing
  notes - written by `/to-spec` when the pipeline runs.
- **Tickets are a task-list checklist** inside the issue (`- [ ] T1 ...`); separate
  child issues only when a ticket needs its own conversation.
- **The PR closes the issue** (`Closes #N` in the description); squash merge.
- Architecture decisions still go to `docs/adr/` in the repo - the tracker holds
  work, the repo holds decisions.

## When a skill says "publish to the issue tracker"

Create a GitHub issue with `gh issue create` (title = the capability gap, body = the
spec + ticket checklist).

## When a skill says "fetch the relevant ticket"

`gh issue view <number>` (the user will normally pass the number or URL).

## Legacy: PRD-scoped Markdown (docs/prd/)

Everything below applies only to the existing `docs/prd/` folders; do not create
new ones.

- A PRD is a flat file (`phase-N.md`) or a folder (`docs/prd/<feature>/` with
  `PRD.md`, `issues/NN-<slug>.md`, optional `map.md`).
- Triage state is a `Status:` line near the top of an issue file (vocabulary in
  `triage-labels.md`); comments append under a `## Comments` heading.
- Wayfinding (`/wayfinder`) operates on these folders: the map is
  `docs/prd/<feature>/map.md`, children are `issues/NN-<slug>.md` with `Type:` /
  `Status:` / `Blocked by:` lines; claim by setting `Status: claimed`, resolve by
  appending `## Answer` and updating the map's Decisions-so-far.
