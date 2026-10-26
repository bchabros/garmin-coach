# Operations runbook

For agents and humans **operating the system** (Claude Cowork pointed at the DB,
plus scheduled runs). Changing the code instead? See `docs/DEVELOPMENT.md`. Term
definitions live in `docs/glossary.md`; nightly design decisions are in
[ADR 0004](adr/0004-phase-4-automation.md).

The golden rule still holds here: this layer reads the finished DB. Only `backfill`,
`sync`, `refresh-today`, and `daily` talk to Garmin (plus the out-of-seam `push`);
interactive `setup` invokes the same backfill after explicit confirmation.
`features` and `report` never do. Local `doctor` is also offline; its explicit
`--live-auth` option only attempts the saved login. See [program maintenance](LIFECYCLE.md)
for diagnosis, backups, staged updates and removal while preserving athlete records.

## Directory map (what lives where)

- `reports/{date}/` -- derived `digest.json`, `snapshot.json`, `hrv_band.png`, and
  `acwr.png`, plus the coach-written narrative `report.md`. These folders can also
  hold `workout.json` (authored session) and `push.json` (push history): preserve both.
- `plans/` -- weekly training plans as Markdown (e.g. `2026-07-06_week.md`): the athlete's
  **plan of record**, with session detail the DB does not store, plus notes and the
  plan-vs-actual follow-up date. The `Zamiar (dla silnika)` column is ingested into
  `plan_week` and drives every planned-intent read (issue #21, ADR 0015); the rest stays
  prose for the human. Gitignored personal data.
- `memory/` -- long-term athlete context (goals, physiology, tendencies, coaching decisions,
  open threads). Qualitative context that does not belong in the DB; numbers there only
  summarize the DB. Reading it is the coach skill's own rail, not an operator chore: the
  "The athlete profile" section of `skills/coach/SKILL.md` has when it is read, how its age
  is judged, and how an amendment is proposed (issue #52). Gitignored personal data, except
  `memory/README.md`, which carries the conventions.
- `docs/` -- system docs (this runbook, `DEVELOPMENT.md`, `glossary.md` and ADRs). `skills/coach/` -- the coach skill (narrative layer over the digest).
- `data/garmin.db` -- SQLite system-of-record. `logs/` -- nightly run logs.

## Guided first-run setup (macOS)

The runtime must already be available. From a source checkout, install its dependencies
with Poetry, then run in an interactive terminal:

```bash
poetry run garmin-coach --home ~/GarminCoach setup
```

`--home` goes before `setup`. Unlike ordinary commands, setup requires `--home` or
`GARMIN_COACH_HOME` explicitly, including when launched from a source checkout. A new
destination must be absent or empty. It is created atomically, with private folder
permissions, `data/`, `plans/`, `reports/`, `logs/`, and an empty `memory/athlete-profile.md`
plus the profile editing rules. No existing athlete's files or database rows are copied.

1. Enter the first date with your real Garmin history; there is no suggested owner date.
2. Enter seven comma-separated intents, Monday through Sunday, from
   `rest,easy,tempo,strength,hyrox,crossfit,quality`, or enter `none` for no agreed template.
   For example, `rest,easy,strength,easy,rest,tempo,easy` is a possible explicit choice,
   never a recommendation or an automatic default.
3. Review the destination and choices, then type `yes` to create the installation.
4. Review the inclusive import range, then type `yes` to start. Enter your own Garmin
   email, password (hidden terminal input), and MFA code if requested. Login uses the
   existing transport and saves tokens inside this installation's `.garminconnect/`.
5. Setup imports the range, rebuilds marts, prints observation counts and a first local
   read, and writes `reports/<end>/setup-<unique>/digest.json` plus `snapshot.json` when
   available. Every run gets its own directory, so prior reports and receipts survive.

The default end is yesterday; today's history is incomplete. `setup --to YYYY-MM-DD`
chooses an earlier end. `setup --configure-only` stops before login and creates no DB.
Declining the import leaves valid configuration for a later run. Exit 0 means the
requested stage completed (including an empty response); exit 2 means refusal, failure
or interruption. An empty response is not proof of an empty training history.

**Recovery and resumption.** Re-run setup with the same `--home` and `--to` shown in
the failure message. A checkpoint in `data/setup-import.json` advances only after every
daily stream for that date has committed. The first incomplete day is fetched again;
completed days and core rows remain intact. Checkpoints are bound to the history start
and database identity; extending the end date (including running a day later) resumes
after the last committed day. Missing, invalid or mismatched checkpoints cause a safe
repeat of the full range. Nightly sync watermarks are never written by setup. Once a
range is complete, repeating it rebuilds the local read without logging in again; use
the normal `sync`/`backfill` commands for a fresh fetch. After a rate limit, wait before
retrying; setup stops at the failed request rather than automatically retrying it.
Diagnostics report the failed stage, exception class, failure category and last complete
day without logging exception messages, credentials or token contents. Rate-limit,
authentication, connection and interruption outcomes are distinguished from unknown failures.

**Existing installations.** A `.env` identifies adoption: setup loads the selected
installation's configuration and uses its existing paths. Without a setup checkpoint,
an existing history is inspected read-only: setup shows the observed coverage and offers
`yes` for a local read or `import` to explicitly request a history fetch. Local adoption
does not log in, and `onboarding.import_complete` is null rather than inferring complete
history from a maximum date. The local read ends at the latest actual observation within
the requested window; date-only rows do not extend it. An ongoing setup import instead
resumes its checkpoint.
Setup does not
rewrite the `.env`, profile, profile rules, authored plans, reports, receipts or existing
DB overrides/templates. Schema bootstrap retains its existing non-destructive migration
behavior. `WEEKLY_TEMPLATE` is a first-DB choice, not a way to revise an existing plan;
authored weekly files retain precedence through the normal plan resolver/import path.
Clear contradictory exported path/account settings before selecting another installation.
For a new athlete, also unset inherited `DATA_START_DATE`, `WEEKLY_TEMPLATE` and
`GARMIN_PASSWORD`; the guided choices and terminal login establish their own identity.
If a manual `.env` has no database and omits the history start or weekly choice, setup
asks for those missing choices and previews the lines to append. Other configuration
bytes and paths are preserved, and declining leaves the file unchanged. An existing
database retains its compatibility defaults and historical settings.

**Honest missing data.** Fresh schemas contain algorithm policy defaults and shared
exercise mappings, without the owner's HRV baseline/SD or training week. Existing
personal rows retain their historical meaning. Date-only payload rows and negative
availability flags are not counted as measurements. The first digest records import
completion, missing streams and days with observations under `onboarding`; sparse or
partial evidence is named in the terminal. An incomplete import writes a distinct
`setup-<unique>/status.json`, leaves existing marts untouched and produces no coaching
digest, snapshot or recommendation until resumption completes. No agreed plan means unknown adherence and
no invented missed sessions. The legacy `DATA_START_DATE` default remains for adopted
installations; new setup always records the athlete's explicit choice.

**Separate user-operated live acceptance.** Automated checks use temporary storage and
fake Garmin transport. To validate a real first login on supported macOS, the operator
chooses a new empty installation, runs setup in Terminal, verifies their own login/MFA,
selected history, first read and same-range re-run, then records the OS/runtime versions
and result without credentials or personal records. For adoption, verify the existing
configuration and personal files remain unchanged. This live check is not performed by
development tests. Client registration is described in [CLIENT_SETUP.md](CLIENT_SETUP.md), and managed
bootstrap/scheduling in [DISTRIBUTION.md](DISTRIBUTION.md). Their real-client and clean-Mac
acceptance remain separate from offline setup verification.

## The athlete installation (where every run reads and writes)

Each command and coach server captures its installation configuration at startup.
After editing `.env` or changing inherited settings, start a new command or restart
the coach MCP server (reconnect/restart its client if needed). The running server keeps
its original database path, folders and recheck window. New records, plan/workout files
and saved tokens at those captured paths remain visible immediately; no restart is
needed for ordinary training changes.

Every command, the nightly run and the coach server act on one **athlete installation**:
the folder holding one athlete's personal state - its `.env`, `data/garmin.db`, `plans/`,
`reports/`, `logs/`, `memory/`, and by default the saved Garmin login (ADR 0031). The
program itself never lives there. The directory map above is the inside of an
installation; the owner's source checkout doubles as one, adopted in place.

**Selecting it**, in this order:

1. `--home <folder>` on `garmin-coach` (before the subcommand) or on `garmin-coach-mcp`;
2. `GARMIN_COACH_HOME=<folder>`;
3. otherwise the folder the run starts in, **only if it holds a `.env`**.

```bash
garmin-coach --home ~/GarminCoach report
GARMIN_COACH_HOME=~/GarminCoach garmin-coach daily
garmin-coach-mcp --home ~/GarminCoach     # what an assistant client's entry runs
poetry run garmin-coach report             # from the checkout: unchanged
```

`scripts/daily.sh`, the repo `.mcp.json`, the Claude Desktop entry (`task claude:register`)
and the guarded launcher (`scripts/coach_project.py --project`) all start inside the
checkout, so none of them changes.

**Paths.** Relative paths in the installation's `.env` resolve against the installation,
never against the starting folder; paths outside it are fine when written there. Reports,
workout specs and push receipts always live in `<installation>/reports/`. The
`--reports-dir` and `--plans-dir` flags stay per-command overrides.

| Setting | Default, inside the installation |
|---|---|
| `DB_PATH` | `data/garmin.db` |
| `PLANS_DIR` | `plans/` |
| `LOG_PATH` | `logs/daily.log` |
| `GARMINTOKENS` | `.garminconnect/` |

The saved Garmin login defaults to the installation so two installations never share an
account; the owner's `.env` names `~/.garminconnect` explicitly and keeps using it.

**Only an import creates the database.** `backfill` and `sync` may create a missing
database, as may `setup` after its confirmed initial backfill; other commands, `daily`
and the coach server refuse.

**Refusals** stop before any data is read or written; commands exit 2 and the server exits
before serving:

| Message starts with | Cause | What to do |
|---|---|---|
| `No athlete installation selected` | no `--home`, no `GARMIN_COACH_HOME`, and the starting folder has no `.env` | name the installation, or start inside it |
| `Not an athlete installation` | the named folder has no `.env` (often a typo) | name the right folder; for a new athlete run `garmin-coach --home <empty-folder> setup` |
| `Installation conflict: --home and GARMIN_COACH_HOME` | the two name different folders | drop one, or make them agree |
| `Installation conflict: DB_PATH` (or `PLANS_DIR`, `LOG_PATH`, `GARMINTOKENS`, `GARMIN_EMAIL`) | an environment value disagrees with the installation's `.env` | unset it, or write the intended value into that `.env` |
| `No database at ...` | the installation has never been imported | `garmin-coach backfill --from YYYY-MM-DD` (or `sync`) |
| `Invalid runtime settings in ...` | the `.env` does not parse | fix that file |

A relative path in an environment variable is read from the folder the run starts in, as
the shell would; an absolute path or a symlink to the same file always agrees.
The guarded launcher preserves that starting folder for its diagnostic server as well.
Environment names are case-insensitive; every spelling must agree with the installation,
so conflicting `GARMIN_EMAIL` and `garmin_email` values are refused in either order.
Settings that do not choose a location or an account (e.g. `SYNC_RECHECK_DAYS`) still
take an environment value over the `.env`.

## Running the pipeline

```bash
poetry run garmin-coach backfill --from 2026-06-08   # [--to YYYY-MM-DD]  one-off history fill
poetry run garmin-coach sync                          # pull missing data since watermarks
poetry run garmin-coach refresh-today                 # opt-in same-day pull + features (partial!)
poetry run garmin-coach features                      # recompute marts (daily + weekly + zones)
poetry run garmin-coach report [--to YYYY-MM-DD]      # build digest.json + charts for a date
poetry run garmin-coach plan import [--week YYYY-MM-DD]  # cache plans/*.md into plan_week
poetry run garmin-coach daily  [--to YYYY-MM-DD]      # nightly: plans -> sync -> features -> alerts
scripts/daily.sh [--to YYYY-MM-DD]                    # thin wrapper for cron / launchd
```

- **First time / gaps:** `backfill --from 2026-06-08`. Idempotent (see below), so safe to
  re-run over an already-filled range.
- **Routine catch-up:** `sync` pulls each stream from its watermark and always pulls the
  re-check window again (the last 3 days ending yesterday, see Gotchas); then run `features`
  to rebuild the marts. Backfill likewise imports core without rebuilding marts.
- **After editing a plan:** `plan import` caches `plans/*.md` immediately (idempotent;
  re-importing a mid-week revision just overwrites). The nightly run does the same scan,
  so an edit takes effect by morning at the latest. A revision that leaves an
  already-pushed workout harder than the new plan prints a `conflict:` line naming that
  date (issue #22) -- the import still succeeds; re-author and re-push that day. It reads
  the receipts under `--reports-dir` (default: the installation's `reports/`), as does
  the nightly scan.
  If a later file is malformed, earlier accepted weeks stay saved and their metrics
  are rebuilt; the command names those weeks and the failing file and exits 1.
- **Nightly:** `daily` (or `scripts/daily.sh`) chains plans -> sync -> features -> alerts.
  Alerts are the digest's `warn`/`alert` signals, logged; **no charts** on the nightly path.
  A malformed plan file **degrades** the run (exit 1) and names the file -- it never falls
  back silently to the template. Accepted weeks remain visible in `plans_imported`.
  Plan conflicts appear in `invalidated_pushes` and warning logs; they leave an otherwise
  successful run `ok` (exit 0) and never modify Garmin. Use `daily --reports-dir` for
  receipts outside the default report directory. The marts are rebuilt once, after sync.
- **Logging a circuit's stations:** a Hyrox / group-HIIT session reaches the DB as one
  nameless `UNKNOWN` set, so the movement-overlap read cannot see what it loaded.
  `poetry run garmin-coach log-sets --activity <id> SLED_PULL SANDBAG_CARRY ...` writes
  the stations (Garmin names, in order; any case, spaces or hyphens tolerated) to the
  core overlay `manual_activity_sets` and recomputes the marts from that day (ADR 0022).
  A re-log replaces the list; the ETL never touches these rows. Names the movement map
  does not know are printed as drift, not refused - add them to `exercise_pattern`.
  Same shape as `log-rpe --activity <id> --rpe N`, the other transport-free writer.
- Scheduling is documented, not auto-installed: see
  `scripts/com.garmincoach.daily.plist.example` for a launchd template.

## Local athlete changes and failed recomputation

RPE, manual circuit stations and goal-event changes save the athlete's core fact before
recomputing derived data. Niggles need no mart pass. Successful CLI writes exit 0;
refused/failed writes and saved-but-unrefreshed results exit 2. Partial results name the
saved activity/event and print recovery instructions; repeating event add is unnecessary.
MCP exposes `saved`, `recomputed` and `error` alongside the existing evidence/freshness.
`saved=true, recomputed=false` means the fact survived but the old mart generation remains.
`recomputed=null` is normal for niggles. A saved goal event is reported separately from
the old block; its `block_after` stays unknown until recomputation succeeds.

After correcting the reported failure, run the existing offline command for the
**same selected installation**, for example:

```sh
garmin-coach --home /absolute/path/to/athlete features
```

Then repeat the cached read. This does not contact Garmin. There is no automatic retry
queue in the local-write flow. Event add/update also displays the current block before
and after a successful rebuild, with its anchor race date; missing blocks stay explicit.

## Exit-code contract

`daily` (and `scripts/daily.sh`, which passes it through) exits with:

| Status | Exit | Meaning | What to do |
|--------|------|---------|------------|
| `ok` | 0 | All streams synced, features + alerts ran. | Nothing. |
| `degraded` | 1 | An isolated stream failed but the run continued. | Check the log for the failing stream; often self-heals next run. Re-run `sync` if it persists. |
| `failed` | 2 | A stage crashed or the whole sync was down. | Read the log; treat as a real outage (network, auth, Garmin down, or a 429 -- see below). |

A crash in the `features` stage rolls the **whole** mart pass back: daily metrics,
plan blocks, weekly rollups, zones, overlap and the snapshot either all advance
together or none of them do. After a `failed` run the marts still hold the last
good generation, so a report read against them is stale but never mixed.

## Report retention

The command only walks the selected installation's `reports/`; there is no
`--reports-dir` option that could point it at `plans/` or the athlete profile.
Both preview and confirmed pruning require an existing installation database, like all
non-import commands. This check only verifies the file exists; pruning never opens or
migrates the database. If it is missing, the command refuses before scanning or deleting
reports and tells you to import the athlete's history first.

```bash
poetry run garmin-coach reports prune                  # dry-run, default age > 90 days
poetry run garmin-coach reports prune --older-than 120 # another nonnegative day threshold
task prune-reports                                    # same default preview
poetry run garmin-coach reports prune --confirm        # only after the review below
```

The preview lists selected dated folders with age, file count and selected bytes.
Age comes from the folder's `YYYY-MM-DD` name and the local calendar date, not its
modification time. A folder exactly 90 days old stays at the default threshold.

Only `report.md`, `hrv_band.png` and `acwr.png` are eligible by default.
`digest.json` and `snapshot.json` stay as a compact trace; `--no-keep-digest` adds
both to the selection, including in a dry-run. `workout.json`, `push.json`, unknown
files, non-dated entries, nested directories and symbolic links are never selected.
The root itself must not be a symbolic link. Empty dated folders are removed only
after confirmed deletion of their selected files; there is no recursive cleanup.

**Before the first confirmed run:** finish the profile editing rules and initial
profile review (#53, building on #52). Review the narratives selected by the preview
and move lasting lessons into the profile, with the source report date, through its
exact-diff approval loop. A pending or declined promotion means keep its source
narrative and do not confirm that selection. The command does not read the profile,
consult a model, or decide whether a lesson matters. Re-run the preview if the
selection or day has changed before confirmation.

`--confirm` is the explicit deletion step; the Task equivalent is
`task prune-reports -- --confirm`. Retention is manual, never part of `daily`.
A missing reports directory succeeds without creating one. Exit 0 means the run
completed; exit 1 means some deletions succeeded and others failed; exit 2 means
invalid input, a failed scan, or no successful deletion in a run with errors.
Failures name the affected paths, and the summary reports actual deleted files.
There is no rollback for files already deleted; retry after fixing the reported cause.

The digest, snapshot and charts can be rebuilt from SQLite. The exact coach narrative
cannot: preserve its useful judgements before expiry. Rebuilding with newer code or
corrected data can also change derived results. `plans/`, workout specs and push
receipts are records, never disposable report output. See
[ADR 0025](adr/0025-profile-history-and-report-retention.md).

## Logs

In-process `RotatingFileHandler`, configured by `daily` itself (not the shell wrapper).
Config keys (`config.py`, set in the installation's `.env`; a `LOG_PATH` from the
environment must agree with it):

- `LOG_PATH` (default `logs/daily.log` inside the installation)
- `LOG_MAX_BYTES` (default `1_000_000`)
- `LOG_BACKUP_COUNT` (default `5`)

`degraded` runs log at WARNING, `failed` at ERROR.

## Gotchas (operational)

- **Login rate limits (429).** Garmin returns 429 (IP-level) on repeated login attempts.
  Once tokens are cached in the saved login (`GARMINTOKENS`), resume avoids the login
  endpoint -- don't
  hammer it, **wait it out**. A 429 typically surfaces as a `failed` run.
- **A scheduled run never asks a question.** The nightly run stands on the saved login (`GARMINTOKENS`) alone; no password is stored anywhere, by choice (a
  stored password
  would turn every passing network failure at 06:00 into an unattended full login against
  the rate-limited endpoint above, and an account with two-step verification would stop at
  the code anyway). When the saved login does not resume, it is tried three times over
  about twenty seconds, because the network often needs a moment after the machine wakes.
  Each failed try logs `client: saved login did not resume: <why>`, including the reason
  `garminconnect` reports only at debug level: that line tells a network hiccup from an
  expired login. If it still fails and no terminal is attached, the run is `failed` (exit
  `2`) with one line, `daily: login failed: ... no terminal is attached ...`, and no
  traceback. **Fix:** run any Garmin command once from a terminal (`poetry run garmin-coach
  sync`) to renew the saved login. The same message comes back from `refresh_today` and
  the workout push over MCP, where no prompt can ever be answered. The re-check window
  (below) makes up the lost night on its own.
- **The re-check window: the last 3 days are never taken on Garmin's first word.** A run
  can still be sitting on the watch when the nightly run asks, and Garmin itself fills in
  HRV and revises readiness after the fact, so every `sync` pulls the 3 days ending
  yesterday again, for every stream ([ADR 0026](adr/0026-recheck-window.md)). A day is
  pulled on the three nightly runs after it and becomes final on the third; the watermark
  never passes the first day of the window. A run that reaches Garmin Connect up to three
  days late therefore lands on its own, and so does a night that failed or never ran.
  Cost: about 23 Garmin calls a night instead of about 9, a few seconds longer, and
  `raw_payloads` grows by the window each night (core row counts never change). One
  INFO line per run, `sync: no activity yet for <dates> (re-check window ...)`, names the
  window days that hold no activity: on a rest day it is routine, and when a run is
  missing it confirms the gap. It never changes the run's status. Width is
  `SYNC_RECHECK_DAYS` (default `3`; `1` is the old behaviour). **`backfill` is still
  needed** for a run uploaded more than three days late.
- **The load split is tuned to Garmin's balance, and the log says how well it fits.**
  Every cardio session's load is shared between easy, hard and anaerobic work by a rule
  tuned to reproduce Garmin's own 28-day load balance
  ([ADR 0027](adr/0027-load-split-tuned-to-garmin-balance.md)). After the marts are
  rebuilt the nightly run logs `daily: load split differs from Garmin's 28-day balance
  by N pp` (mean difference of the three shares, in percentage points; 2.7 on average over
  summer 2026, never above 5.1). A sustained value above about 5 means the rule has
  stopped fitting this athlete's training and the comparison in the ADR should be re-run.
  **Changing the rule's constants requires a full recompute**, `poetry run garmin-coach
  features`: it never contacts Garmin, rewrites `daily_metrics` and `weekly_metrics` since
  `data_start`, and so changes past weeks too.
- **Backfill / sync exclude "today".** HRV and sleep only land after the night, so the
  pipeline only pulls through **yesterday**. A missing current-day row is expected, not a
  bug. To see *this morning's* HRV/readiness for a same-day call, opt in explicitly with
  `refresh-today`: it pulls today (partial), rebuilds the mart through today, and **never
  advances sync watermarks**, so the nightly run re-pulls the day complete. Morning
  streams (sleep, HRV, readiness) are reliable after wake; intraday fields (today's
  load/TE, steps, RHR) are partial until the nightly run - never read them as final.
  Same exit-code contract as `daily` (0 ok / 1 degraded / 2 failed).
- **Re-running is safe (idempotency).** Re-running `backfill`/`sync` must not change
  **core** row counts (upsert by PK). Only `raw_payloads` grows (append-only, keyed by
  `fetched_at` plus the payload hash). Re-run freely to recover from a degraded run.
- **Enrichment gaps.** Per-activity weather and exercise sets are best-effort: a failed
  fetch leaves the enrichment absent and never aborts the run, so it does **not** make
  the run `degraded`. It is logged as `daily: enrichment gap: ...` and counted in the
  sync stage's `enrichment_gaps=`. Grep the log for these before concluding that Garmin
  simply had no data; a re-run repairs them.
- **Onboarding gap.** Real data starts 2026-06-08 (`data_start`, defined in
  `docs/glossary.md`); earlier dates are explicit gaps, not zero training.

## Generating a coach report

`garmin-coach report [--to YYYY-MM-DD]` builds the deterministic artifacts under
`reports/{date}/`: `digest.json` plus two charts (`hrv_band.png`, `acwr.png`). It does
**not** write the narrative. The coach skill reads the digest and writes `report.md` from
it (never the raw mart, never Garmin) -- see `skills/coach/SKILL.md` for how the skill is
invoked and routed, and `skills/coach/references/report.md` for what the narrative should
contain.

### Keeping the uploaded coach skill in sync

`skills/coach/` in this repo is the source of truth -- the whole directory, the `SKILL.md`
router plus its `references/` files -- and generated local plugins carry the public manifest. Account-hosted copies are separate: Cowork and claude.ai chat run the copy **uploaded to your Claude account**
(claude.ai -> Settings -> Capabilities -> Skills), which is synced *down* to Claude
Desktop, never up from this repo. There is no supported path to push it up, so **editing
the repo copy does not update the one Cowork and chat run** -- re-upload is manual, and the
drift is otherwise silent.

The form takes one archive, not a folder, so build it rather than zipping by hand:

```bash
task claude:package
```

That writes `dist/coach.zip` with the skill's own directory at the top level --
`coach/SKILL.md` and `coach/references/*.md`, never a bare `SKILL.md`, which the form
rejects. It is the same layout the official `.skill` packager writes, so a picker that
wants that extension needs the file renamed and nothing else.

`task claude:check` compares the canonical public Markdown resources under `skills/coach/`
against the copy Claude last synced to this machine and names each file that has drifted,
with what it observed -- the two copies differ, the file is missing from the account, or it
is left over on the account after a local delete. Run it after changing anything under
`skills/coach/`.

### Consolidating the agent notes

Claude Code's own notes about this repo (the **agent notes**, outside the repo under
`~/.claude/projects/<key>/memory/`) get a monthly **consolidation pass** from a Claude Desktop
scheduled task (`consolidate-agent-notes`, 1st of the month, 08:00; it runs on next launch if
the app was closed, and notifies when it finishes). The steps, rules and restore procedure are in
`docs/agents/consolidation-pass.md`. Two commands work on their own too:

```bash
task claude:memory-check    # index drift: notes with no index line, lines with no note
task claude:memory-backup   # copy the notes to memory-backups/<date>T<HHMM>/ beside them
```

Every pass leaves its backup folder with a `SUMMARY.md` of what it merged, corrected or
dropped, and why.

## Authoring and pushing a workout

Authoring is offline; pushing is the explicit Garmin write. Detailed input vocabulary
and confirmation rules live in `skills/coach/references/authoring.md` (ADR 0013).

```sh
garmin-coach author --date YYYY-MM-DD --request request.json
garmin-coach author --date YYYY-MM-DD --reuse-from YYYY-MM-DD
garmin-coach push --date YYYY-MM-DD
garmin-coach push --date YYYY-MM-DD --confirm
```

Review the dry run before confirmation. Plan guards refuse harder-than-planned work;
replacement is explicit and account-based idempotency preserves reusable workouts.
Repeating preserves the saved name/steps and rechecks the new date and plan; it never
pushes automatically. Choose one source: request, recommendation or `--reuse-from`.
Unresolved Hyrox authoring requests need the running/station choice and exit 1 without
a spec; rest is a successful no-workout outcome (exit 0).

Unreadable or malformed workout/receipt evidence is reported by date and retained.
Push/removal refuses unsafe evidence before login; status reports the local error.
The pushed-workout listing keeps readable entries and returns dated warnings. Plan
import retains accepted weeks, rebuilds their marts and warns when a conflict cannot
be judged; that warning is not proof that the plan agrees with the watch.
Keep `workout.json` and `push.json` as records. The nightly run never pushes workouts.
Earlier outbound probes are historical evidence in Git commit
`6fbd938a4c62b83812315de1a77d00946cc5ef50` under `scratch/`; their live calls are not tests.

## Coach MCP and clients

The coach reads the finished DB through `mcp__coach__*`; the installed router and its
references define the flows and tool contracts. Every response carries freshness
(`data_through`, `today_included`, `partial_fields`, `unconfirmed_days`). Missing
unconfirmed sessions are questions for the athlete, not evidence of skipped training.

A read-only question uses existing digest/snapshot reads, without report generation,
plan import or metric rebuild. Saved race/context lookups use the saved-context route;
calendar tools cannot read arbitrary race files. Missing access stays explicit.
The coach reads the athlete profile before advice and respects its exact-diff consent.

Same-day refresh is the guarded `refresh_today` path; historical missing days use
`repair_preview` and explicit approval before `repair_confirm`. Workout status checks
consult the account but do not feed metric computation. Ad-hoc Garmin tools remain
for developer exploration/fixture building, outside coaching reads.

Use [client setup](CLIENT_SETUP.md) for managed plugins, guarded source configuration,
synthetic acceptance and recovery. A missing supported runtime is an operator repair
step; a coaching conversation does not install substitute global dependencies.
Keep registration, protocol and fresh-conversation acceptance separate.

## Captured laps and interval history

`activity-laps ACTIVITY_ID` reads one saved activity's captured laps and descriptive
interval summary, or an explicit unavailable reason, without Garmin transport.
CLI and the `get_activity_laps` coach tool share the saved-data read and freshness
envelope. A captured lap is not automatically a repetition. See
[ADR 0037](adr/0037-captured-interval-evidence.md) for the verified evidence boundary.

An operator upgrading an existing installation first stops its writers and uses the
new program's offline `features` command to add the packaged schema and rebuild marts.
A guarded source client must be restarted only after this explicit offline step.
To import missing historical laps, use the supported selected-home invocation from
the source checkout (managed installations use their stable launcher):

```bash
poetry run garmin-coach --home /absolute/path/to/athlete features
poetry run garmin-coach --home /absolute/path/to/athlete backfill --from 2026-06-08 --to YYYY-MM-DD
poetry run garmin-coach --home /absolute/path/to/athlete features
```

The original installation's first real history is 2026-06-08; another installation
uses its own configured data start. Backfill imports raw/core without rebuilding
marts, so the final offline `features` is required. Same-day refresh already uses
the shared enrichment path and remains partial, without advancing watermarks.
Both lap endpoints must complete before replacing the captured set; a failed or
absent response retains the prior set, and a valid empty capture clears it.
This historical transport is an operator operation, never a coach-side live read.
