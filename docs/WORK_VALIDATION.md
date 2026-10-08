# Local client validation for issue #82

Date: 2026-10-08. Target: local macOS and the existing Poetry checkout.

## Environment observed

- Target application: 26.930.61225, build 13232, bundle identifier `com.openai.codex`.
  The desktop host update tool confirmed this running version/build after the
  user explicitly confirmed both acceptance conversations ran in Work locally
  on the Mac. The application check is version evidence, not tool-call evidence.
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
| Fresh Codex conversation | Passed, user-reported | Desktop fixture chat read guide/router/report/profile, listed 26 coach tools and executed digest/snapshot; expected 2026-07-03 horizon and honest freshness. |
| Fresh Claude Code conversation | Passed for connection/read, user-reported | Version 2.1.284; fixture guide/router/report/profile read, 26 tools reported, two coach calls completed with the expected horizon. Narrative deviations are recorded below. |
| Fresh local Work fixture read | Passed, user-reported | Fresh local Work chat read guidance, router, all three references and the synthetic profile; listed 26 actual tools and executed digest/snapshot with the expected 2026-07-03 horizon. Local Work mode confirmed by the user; desktop host version 26.930.61225, build 13232. |
| Fresh local Work development | Passed, user-reported and diff verified | Isolated checkout; guidance/rules/development guide read; schema mirror test explained; documentation-only diff; relevant offline test passed before and after. Local Work mode confirmed by the user; desktop host version 26.930.61225, build 13232. |
| Fixture plan/profile previews in Work and desktop Codex | Passed, user-reported; files checked | Both report a successful plan_preview, show all seven days and exact profile diffs, describe confirmation/token rails, and stop before writes. |
| Fixture plan/profile preview in Claude Code | Passed on explicit rerun, user-reported | Actual coach call, complete result/table, exact profile diff with final Decyzje section, corrected preview/confirm explanation; stopped before writes. Original deviations remain documented. |
| Fresh local Work personal-data read | Pending | Needs the final locally sourced coaching answer |

Computer Use refused access to the target bundle: "Computer Use is not allowed to
use the app 'com.openai.codex' for safety reasons." The implementation does not work
around that restriction. Complete the prepared application steps through the normal
user interface and record their observed results.

## Work fixture observation (2026-10-08)

The user supplied the fresh local Work transcript at 22:12 Europe/Warsaw. Its
working directory was `/private/tmp/garmin-coach-work-fixture.s0uH1H`. The chat
reported reading `AGENTS.md`, the canonical coach router, `report.md`, `planning.md`,
`authoring.md`, and the synthetic profile. It verified the `.agents/skills/coach`
link and listed 26 actual `mcp__coach__` tools before executing `get_digest` and
`get_snapshot`. This is user-supplied client evidence, independent of the CLI probe.

The answer identified the data and profile as synthetic, the 2026-07-03 horizon
as 97 days old, missing metrics as unknown, and the recommendation as historical.
It preserved the onboarding cutoff, treated recent unconfirmed days as unknown,
and called out different historical-window and current freshness metadata. The
chat reported no Garmin refresh or plan, profile, or workout writes and ended
with the recorded-data disclaimer required by the report reference.

The chat also found the imported `.claude/rules/code-style.md` missing from the
fixture. The fixture generator now copies the complete rules directory, including
linked documentation; the existing test fixture was repaired without altering its
database or profile. A launcher regression reproduces the missing-resource failure
and verifies the repaired generated installation.

The prior instructions for adding a folder via a generic plus menu did not match
the user's UI. The documented desktop route is Work mode, `Cmd+O`, then `Cmd+Shift+G`
in the folder chooser. The user subsequently supplied successful fixture evidence;
that confirms folder access, but does not prove which opening action was used.
The user confirmed local desktop Work for both tests; the desktop host tool
confirmed version 26.930.61225, build 13232. The exact opening/trust clicks were
not supplied. Fixture-root access, canonical skill resources, configured local
coach tools, tool execution and freshness were demonstrated in the supplied chat.

## Work development observation (2026-10-08)

The user supplied a second Work result from the disposable local checkout
`/private/tmp/garmin-coach-work-development.7_5a2kla`, cloned at
`2247a0c6afea3a2b6cc0900be04cc22ea9985f6d`. The checkout reused the existing
Poetry runtime; no personal configuration, database or athlete profile was copied.

The chat reported reading `AGENTS.md`, the project rules and `docs/DEVELOPMENT.md`.
It inspected `tests/test_schema_sync.py` and correctly explained that the test
compares the package resource `src/garmin_coach/core/schema.sql` with the docs
snapshot as text; it neither validates SQL syntax nor opens the database.

It ran `PYTHONPATH=src poetry run pytest tests/test_schema_sync.py -q`, added a
short command example beside the schema convention in `docs/DEVELOPMENT.md`, and
reran the test. Both reported runs passed: `1 passed in 0.01s`. The coordinating
agent independently inspected the disposable checkout: only the stated
six-line documentation addition was modified, `git diff --check` passed, and HEAD
remained unchanged. The development example stays in the disposable checkout.

The chat reported no Garmin access, personal-data use, commit or push. Together
with the observed reviewable diff and offline results, this demonstrates Work's
development path separately from the fixture coaching conversation. Application
version 26.930.61225/build 13232 is identified by the desktop host tool; the user
explicitly confirmed Work mode and local Mac execution for both conversations.

## Codex Desktop fixture observation (2026-10-08)

The user supplied the desktop Codex answer from the requested fixture chat at
22:23 Europe/Warsaw. It reported reading `AGENTS.md`, the coach router, reporting
reference and synthetic profile; all 26 actual coach tools were listed and
`get_digest({})` / `get_snapshot({})` succeeded. The desktop host previously
identified version 26.930.61225, build 13232; this transcript does not independently
supply a version or the folder/trust clicks.

The answer correctly identified the 2026-07-03 horizon as 97 days old, the profile
as synthetic, the recommendation as historical, template intent as unagreed, null
metrics as unknown and pre-cutoff dates as onboarding gaps. It distinguished the
historical window's unconfirmed-day list from the current freshness envelope and
reported no refresh or state writes. File-reading shell use was explicitly
separated from real MCP data reads. The pasted answer omitted the report reference's
required final verbatim disclaimer; connection/read success does not prove full
narrative adherence.

## Claude Code fixture observation (2026-10-08)

The attached transcript identifies Claude Code **2.1.284**, the fixture root
`/private/tmp/garmin-coach-work-fixture.s0uH1H`, and two actual coach calls. The
answer reports reading the guide (including its byte-for-byte mirror), coach router,
reporting reference, synthetic profile and `memory/README.md`, listing 26 tools,
and loading the schemas of the two read tools used. Unrelated MCP authentication
notices did not prevent the coach calls.

The answer identifies the expected 2026-07-03 horizon, the stale synthetic profile,
the template plan, unknown recent-day execution and onboarding gaps. It reports no
Garmin refresh or writes. Two narrative deviations remain visible: the required
final disclaimer is absent, and the claim that no terminal command ran contradicts
the transcript's three shell commands. The transcript does not show shell
substitution for the coach data calls; it shows two actual coach calls after file
inspection. Record these limitations instead of treating tool connectivity as proof
of every response rule. Sparse fixture differences between digest and snapshot
were discussed; no formula or schema change is introduced for this setup issue.

## Fixture preview observations (2026-10-08)

The user supplied follow-up answers from desktop Codex and local Work on the same
synthetic fixture. Both explicitly report `plan_preview` returning `error: null`
for the week beginning 2026-07-06 and show all seven days: rest on Monday,
Wednesday, Friday and Sunday; easy 30-minute runs on Tuesday, Thursday and
Saturday. Both correctly distinguish the preview's `plan_week` source from a
persisted week. These are user-supplied client results; the coordinating chat has
no registered coach tools and does not substitute a shell call for this acceptance.

Both answers report reading the coach instructions, planning and authoring
references, and `memory/README.md`. They show exact proposed profile diffs adding
only an explicitly synthetic standing preference and an empty final `Decyzje`
section. The profile date remains aligned with the previously read 2026-07-03
horizon. Each answer distinguishes approval of the plan from approval of the
profile diff and stops before applying either.

Their descriptions preserve push/removal previews, explicit confirmation with the
returned token, plan intensity guards, token invalidation after relevant changes,
and removal of only the selected day's calendar entries while retaining the
workout in the library. Both acknowledge that account previews read Garmin and
report no account-tool call, authoring or confirmation. The coordinating agent
subsequently checked the actual fixture: the profile still matches its original
six lines exactly and `plans/2026-07-06_week.md` does not exist. This verifies those
file outcomes, not the absence of every possible database or account operation.

The supplied Claude Code follow-up identifies the same fixture and version
2.1.284, but the pasted text is truncated between the announced planning read and
the later explanation. It does not preserve the actual tool result or seven-day
table, so that original preview was not marked passed. The visible profile proposal omits the
README's required final `Decyzje` section, and the text incorrectly attributes
rescheduling to `push_preview` alone; the documented operation requires the
subsequent approved `push_confirm` with the preview token.

The user then supplied an explicit Claude Code rerun with a visible `Called coach`
entry, full `plan_preview` JSON (`data.error: null`), and a complete seven-day
table matching the requested 2026-07-06 week. The freshness envelope still identifies
the 2026-07-03 horizon and treats recent unconfirmed days as unknown. The exact
proposed profile diff now includes the empty final `Decyzje` section, preserves the
date and original content, and remains pending approval. Claude explicitly corrects
its prior explanation: `push_preview` only shows an operation; rescheduling needs
the approved `push_confirm` with the returned token/date and still obeys the plan
guard. It reports no write or Garmin contact and inspected the confirmation tool's
description without calling it. A subsequent fixture file check again found the
original profile unchanged and no proposed week file. T3's preview/profile stop
behavior is now observed in all three clients; accepted/rejected account-write
behavior remains verified through the existing offline fake-publisher tests.

## Remaining workflow acceptance

Project setup and fixture read have now been demonstrated in Work, desktop Codex
and Claude Code, complementing the guarded-launch and preservation tests (T2).
Work and desktop Codex now demonstrate seven-day plan previews and exact profile
diffs stopping before confirmation; Claude's explicit rerun supplies the missing
evidence and corrects its explanation. T3 is complete. Keep
push/removal transport and accepted/rejected token behavior at the existing offline
fake-publisher seam; no real Garmin preview or account write is required. The final
personal-installation read in fresh local Work (T5) remains pending.

## Personal-installation acceptance preparation

The initial guarded stdio check addressed `/Users/Chabi/garmin-coach` through the
implementation worktree's launcher. It succeeded with 26 tools and unchanged
database/profile checksums, but the primary checkout was still on `main`. The user
correctly noted that this would combine branch runtime code with main's guidance,
so that arrangement was not treated as a complete branch acceptance.

At the user's request, the existing implementation worktree was detached at
`20ed959fcb58bc0a662e8f130509533dd87c7c2d`, then the original checkout was switched
to `feat/work-coach-integration`. The user's pre-existing `docs/glossary.md` edit
was preserved byte-for-byte, as were the personal database and profile. Temporary
personal-data links/settings prepared in the worktree were removed; fixture client
configurations can still use its preserved runtime and source. No personal data
was copied to a new installation.

The original checkout's local `.codex/config.toml` now invokes its own guarded
`scripts/coach_project.py` with `/Users/Chabi/garmin-coach` as the selected project.
Thus branch code, primary guidance, canonical skills and existing personal data
are all addressed from the same checkout. Machine-specific configuration stays
untracked; no previous client entry was overwritten. The user's glossary change
is excluded from acceptance documentation commits.

Start a fresh local Work conversation with `/Users/Chabi/garmin-coach` primary,
verify `feat/work-coach-integration`, read the canonical router/report reference
and existing profile, and call only `get_digest` / `get_snapshot`. T5 remains
pending until that actual client result is supplied. This original checkout holds
personal data; do not use it as an isolated fixture development environment.

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
