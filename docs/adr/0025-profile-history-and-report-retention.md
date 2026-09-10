# ADR 0025 - Preserve coaching history before expiring report artifacts

## Status

Accepted

## Context

Issues [#53](https://github.com/bchabros/garmin-coach/issues/53) and
[#54](https://github.com/bchabros/garmin-coach/issues/54) describe one lifecycle:
keep lasting lessons about the athlete, then expire old report artifacts. The athlete
profile is private and gitignored, so overwriting a decision without recording its
history would erase the only copy. Appending every correction instead leaves
contradictory statements in the context the coach reads.

The original #54 description calls everything under `reports/` reproducible. That is
too broad. The report command writes the digest, snapshot and charts, but the coach
writes `report.md`; reproducing the inputs does not reproduce its exact judgement.
The same dated folders also hold `workout.json` and `push.json`. Those preserve the
authored session and its push history (ADRs 0013 and 0021), not a cache of DB facts.

## Decision

- The profile has current-state sections and a final dated `Decyzje` history. An
  update replaces an existing fact; its superseded version moves to the history when
  it still has coaching value. Temporary constraints and open threads carry dates.
  About 150 lines, including history and blank lines, is a consolidation trigger,
  not an enforced cap. Every edit, including initial restructuring, needs the
  athlete's approval of the exact diff. A refusal leaves content and date unchanged.
- The editing contract lives in `memory/README.md`; the coach router reads it before
  proposing an amendment. No deterministic module parses the profile or judges
  which lessons matter. The profile and its restructuring diff remain private.
- `garmin-coach reports prune` operates on `./reports` from the working directory,
  with no option to widen that root. It selects only direct folders whose names are
  exactly `YYYY-MM-DD`, strictly older than 90 days by local calendar date. The
  nonnegative `--older-than` option changes the threshold; filesystem modification
  times do not affect age. A missing root is a no-op.
- The deletion allow-list is exactly `report.md`, `hrv_band.png`, and `acwr.png`.
  `--no-keep-digest` also selects `digest.json` and `snapshot.json`. Workout specs,
  push receipts, other images, drafts, unknown files and nested directories are
  never selected. A symbolic-link root is refused; symbolic-link date folders and
  files are skipped. No recursive deletion is used. A dated folder is removed only
  if it is empty after confirmed file deletion.
- Dry-run is the default and shows the selected folders, their age, file count and
  bytes. Only `--confirm` deletes. Before confirmation, the operator reviews the
  selected narratives and preserves lasting lessons, with source report dates, via
  the profile approval loop. If a promotion is pending or declined, keep its source
  narrative; do not confirm a selection containing it. This prerequisite belongs
  to the operating workflow, not to a model in the delete path.
- Retention remains manual (`task prune-reports`); the nightly pipeline, the DB,
  Garmin and the agent's own memory (#56) are outside this change.

## Consequences

The profile keeps current advice separate from why earlier decisions changed.
Ordinary profile updates do not grow a second copy of every fact, and useful history
does not depend on putting private prose in Git.

Deleting derived report files cannot remove the local evidence needed to identify
or reconcile a pushed workout. Keeping digest and snapshot by default retains a
compact trace, although it does not bound their long-term growth. Rebuilding an
expired report uses the current code and stored data; it is not promised to reproduce
the original narrative or an artifact from an older algorithm byte for byte.

Filesystem deletion is not atomic: individual failures are reported, successful
deletions are counted, and the command returns nonzero so the operator can retry.
Automated tests exercise this policy through the CLI on temporary folders. Profile
tests guard the document route; conversational acceptance checks the editing rules.
The initial private profile amendment and first real prune remain explicit operator
acceptance steps, and a PR merge alone does not prove them complete.
