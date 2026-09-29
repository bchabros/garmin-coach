# Consolidation pass (agent notes)

The monthly, unattended pass over the **agent notes**: Claude Code's own per-project memory
for this repo, kept outside it under `~/.claude/projects/<key>/memory/` (one Markdown file per
fact plus the `MEMORY.md` index loaded at the start of every session). Terms are in
`docs/glossary.md` ("agent notes", "consolidation pass"). Decided in issue #56.

This file is the source of truth for the pass. The Claude Desktop scheduled task that runs it
(`consolidate-agent-notes`, 1st of the month, 08:00) only points here, so a change to the rules
is a change to this file, reviewed like any other.

## Scope

- **In:** the agent-notes directory printed by `task claude:memory-backup`. Only that one.
- **Out, never touched:** the athlete profile and everything else under the repo's `memory/`
  folder (its consolidation is consent-gated, ADR 0025), any other project's notes under
  `~/.claude/projects/`, and every tracked file in the repo. The pass commits nothing, switches
  no branch, and writes nothing inside the repo.

## Steps

Run from the repo root, `/Users/Chabi/garmin-coach`.

1. **Back up.** Run `task claude:memory-backup`. It prints the notes directory, the new backup
   folder (`memory-backups/<date>T<HHMM>/` beside the notes), and any index drift. **If it
   fails, stop here and report why; touch nothing.** Use the directory it printed, not a path
   guessed from the session's own memory settings: a scheduled session may start somewhere
   else.
2. **Take stock.** Read `MEMORY.md` and every note file.
3. **Check each note against the repo.** For every concrete claim a note makes about the repo
   (a file, command, task, flag, module, layout, convention, issue or PR state), look it up.
   Check against `main` (`git show main:<path>`, `git grep <pattern> main`, `gh issue view`),
   not the working tree: the main checkout may have a feature branch checked out. A claim
   about the machine (installed tools, paths under the home directory) is checked on the
   machine.
4. **Consolidate**, following the `consolidate-memory` skill and the note format in the
   session's auto-memory instructions:
   - **Merge duplicates** into the richer file; move the other file's unique content in, then
     delete it.
   - **Correct** a note whose claim is now false but whose lesson still holds.
   - **Drop** a note only when it is false against the repo or the machine and has no lesson
     left. **Age alone never drops a note;** it only flags the note for step 3.
   - **Fix relative dates** ("last week", "yesterday") to absolute ones.
   - Do not invent new notes; the pass tidies what exists.
5. **Tidy the index.** One line per note, `- [Title](file.md) — hook`, each under ~150
   characters; under 200 lines in total. Remove lines for dropped notes; add one for any
   orphan note kept.
6. **Verify.** Run `task claude:memory-check`. It must pass. If it does not, fix the index and
   run it again.
7. **Record.** Write `SUMMARY.md` into the backup folder created in step 1: the date, then one
   line per note that was merged, corrected or dropped, with the reason (for a correction or a
   drop, the repo fact that contradicted it), and a count of notes kept unchanged.
8. **Report.** End with a short message: the backup folder, what changed (same content as the
   summary), and the result of step 6.

## Restoring

Every backup folder is a complete copy of the notes directory as it was before that pass. To
undo a pass, copy the folder's note files back over the notes directory (leave `SUMMARY.md`
behind). Backups are never pruned automatically.

## Running it by hand

Nothing here depends on the schedule. Ask any Claude Code session in this repo to "run the
consolidation pass from `docs/agents/consolidation-pass.md`", or take a snapshot alone before
a risky session with `task claude:memory-backup`. Backup folders are named to the minute, so
several on one day are normal.
