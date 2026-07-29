# Operations runbook

For agents and humans **operating the system** (Claude Cowork pointed at the DB,
plus scheduled runs). Changing the code instead? See `docs/DEVELOPMENT.md`. Term
definitions live in `docs/glossary.md`; deep design detail for the nightly path is in
`docs/prd/phase-4.md`.

The golden rule still holds here: this layer reads the finished DB. Only `backfill`,
`sync`, `refresh-today`, and `daily` talk to Garmin (plus the out-of-seam `push`);
`features` and `report` never do.

## Directory map (what lives where)

- `reports/{date}/` -- deterministic coach artifacts: `digest.json`, `hrv_band.png`,
  `acwr.png`, and the narrative `report.md`. One folder per report run day.
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
- `docs/` -- system docs (this runbook, `DEVELOPMENT.md`, `PROJECT.md`, `glossary.md`, PRDs,
  ADRs). `skills/coach/` -- the coach skill (narrative layer over the digest).
- `data/garmin.db` -- SQLite system-of-record. `logs/` -- nightly run logs.

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
- **Routine catch-up:** `sync` advances each stream from its watermark; then `features`
  rebuilds the marts.
- **After editing a plan:** `plan import` caches `plans/*.md` immediately (idempotent;
  re-importing a mid-week revision just overwrites). The nightly run does the same scan,
  so an edit takes effect by morning at the latest. A revision that leaves an
  already-pushed workout harder than the new plan prints a `conflict:` line naming that
  date (issue #22) -- the import still succeeds; re-author and re-push that day. It reads
  the receipts under `--reports-dir` (default `./reports`); the nightly scan does not
  perform this check.
- **Nightly:** `daily` (or `scripts/daily.sh`) chains plans -> sync -> features -> alerts.
  Alerts are the digest's `warn`/`alert` signals, logged; **no charts** on the nightly path.
  A malformed plan file **degrades** the run (exit 1) and names the file -- it never falls
  back silently to the template.
- Scheduling is documented, not auto-installed: see
  `scripts/com.garmincoach.daily.plist.example` for a launchd template.

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

## Logs

In-process `RotatingFileHandler`, configured by `daily` itself (not the shell wrapper).
Config keys (`config.py`, overridable via env / `.env`):

- `LOG_PATH` (default `./logs/daily.log`)
- `LOG_MAX_BYTES` (default `1_000_000`)
- `LOG_BACKUP_COUNT` (default `5`)

`degraded` runs log at WARNING, `failed` at ERROR.

## Gotchas (operational)

- **Login rate limits (429).** Garmin returns 429 (IP-level) on repeated login attempts.
  Once tokens are cached in `~/.garminconnect`, resume avoids the login endpoint -- don't
  hammer it, **wait it out**. A 429 typically surfaces as a `failed` run.
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
router plus its `references/` files -- but Claude Code is the only surface that reads it
from disk. Cowork and claude.ai chat run the copy **uploaded to your Claude account**
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

`task claude:check` catches that: it compares every Markdown file under `skills/coach/`
against the copy Claude last synced to this machine and names each file that has drifted,
with what it observed -- the two copies differ, the file is missing from the account, or it
is left over on the account after a local delete. Run it after changing anything under
`skills/coach/`.

## Authoring and pushing a workout (Phase 11)

Turning a recommendation (or your own session) into a structured Garmin workout is a
**two-command, out-of-seam** path -- the only place the system writes to Garmin. It is
never run from the nightly automation, and it bends the golden rule deliberately (see
`docs/adr/0013-phase-11-workout-authoring-and-push.md`).

1. **Author** (pure, no network) -- writes `reports/{date}/workout.json`:

   ```bash
   garmin-coach author --date 2026-07-17 --from-recommendation   # from the Phase 10 read
   garmin-coach author --date 2026-07-17 --request req.json       # from your own workout_request
   ```

   `rest` produces no spec; a `hyrox` recommendation asks you to say run-vs-station, and
   either answer is a hand-written `--request` file: a run one under a run session type
   (`easy`/`tempo`/`quality`) with explicit structure, or a `hiit` one carrying
   `structure.exercises`. `session_type: hyrox` is only authored under `sport: hiit` --
   there is no run template for it, so `--sport` alone cannot answer the split. A
   recommendation's intent picks the sport by itself (`strength` -> strength, `crossfit` ->
   hiit, run types -> run); `--sport` overrides it. Warnings (target is today, no measured
   pace, an override of the recommender's advice, an unknown exercise) are printed and
   written into the spec.

   **A session harder than the plan of record for that date is refused** (issue #22,
   ADR 0021), naming both intents; softer is always allowed. The hardness order is
   `rest < easy < tempo = strength < hyrox = crossfit = quality`. To author above the
   plan, change the plan first: edit `plans/<monday>_week.md` and re-run
   `garmin-coach plan import`.

2. **Push** -- **dry-run unless `--confirm`.** No `--confirm` prints the payload and exits
   without touching the account; there is no `--dry-run` flag to forget:

   ```bash
   garmin-coach push --date 2026-07-17            # dry-run: shows payload + plan + warnings
   garmin-coach push --date 2026-07-17 --confirm  # writes: upload + schedule, writes push.json
   garmin-coach push --date 2026-07-17 --confirm --replace   # overwrite a changed same-name workout
   ```

   Idempotency uses the **account** as the source of truth (workouts are named `GC {date}
   {type}` and carry a `gc-hash` of the spec): an identical re-push is a no-op, a changed one
   needs `--replace`, and a push that half-fails (uploaded, not scheduled) is completed by
   running `push` again -- it skips the upload. Exit codes: `0` success/dry-run/no-op, `1`
   refused (needs `--replace`) or missing spec, `2` a partial push (see the `error` in
   `push.json`).

   The plan guard runs here **again**, against the plan as it stands at push time: a spec
   authored before the plan was revised is refused (exit `1`), and `--replace` does not
   override it -- that flag overwrites a different workout, it is not a licence to outrank
   the plan. Re-author the date instead.

**Custom run structure (Phase 11a).** An `athlete`/hybrid request may carry a `structure`
block that shapes the run template (`warmup + reps x (work + recovery) + cooldown`, one
homogeneous interval block) beyond its defaults. Keys:

- `reps` - interval count.
- `<role>_end` for each role (`warmup`/`work`/`recovery`/`cooldown`) - one of `"lap"` (the
  watch lap button, "on-click"), `{"distance_m": N}` (metres), or `{"min": N}` (minutes;
  fractional minutes are honoured and rounded to the nearest second, so `{"min": 2.5}` is a
  2:30 step). A `work` step may not be `"lap"`. The pre-11a `<role>_min` / `duration_min`
  keys still work; giving both a `*_end` and its `*_min` for one role is an error.
- `work_pace_band: [fast_s_per_km, slow_s_per_km]` (faster bound first) - a custom pace
  window on the work step. It overrides the recommender's `pace_target_s_per_km` and skips
  the pace -> HR -> none degradation. A band clearly faster than the recommender's
  suggestion adds a (non-blocking) cited warning.
- `<role>_target` for each role - how hard that step should be (issue #24, ADR 0020). One
  of `"none"`; a zone name `"z2"`/`"z3"`/`"z4"`, resolved to that heart-rate band from
  `athlete_zones`; or an explicit window, `{"hr_band": [low_bpm, high_bpm]}` or
  `{"pace_band": [fast_s_per_km, slow_s_per_km]}`, narrower bound first. A zone name
  always means heart rate. `"z1"` and `"z5"` are refused - the ladder stores four upper
  bounds, so the outer zones have no floor and no ceiling; give them as an explicit
  `hr_band`. Zone names and `"none"` are case-insensitive (`"Z2"` reads as `"z2"`).
  Omitting the key keeps the role's default: no target on `warmup`, `recovery`, and
  `cooldown`, the pace -> HR -> none chain on `work`. `work_target` and `work_pace_band`
  are two spellings of one thing; giving both is an error, and either spelling triggers the
  faster-than-advised warning above. Asking for a target and getting it is otherwise
  silent; a named zone with no stored band is a (non-blocking) warning and a step with no
  target - authoring never fails over an unavailable target.

**Strength / HIIT sessions (issue #16).** `sport: strength` (session type `strength`) and
`sport: hiit` (session types `hyrox` / `crossfit`) author from a `structure.exercises`
list instead of the run roles. Each entry is one exercise with uniform sets:

- `exercise` - the athlete's name for it ("back squat", "wall balls", "sled push");
  resolved against the curated whitelist in `workouts/exercises.py` to Garmin's
  `category`/`exerciseName` pair. An unknown name warns and authors an unlabeled step -
  it never blocks.
- `sets` - how many sets; each becomes its own step on the watch (flat steps, never a
  repeat group - the shape the live probes proved).
- `reps: N` **or** `time: {"min": N} | {"s": N}` - exactly one; rep-ended or time-ended
  work.
- `weight_kg` (optional) - per-set weight, always kilograms.
- `rest` (optional) - `{"min": N}` / `{"s": N}` / `"lap"` after each of this entry's
  sets; defaults to 90 s (strength) / 60 s (hiit). The session's trailing rest is
  dropped. Rep-ended steps count 0 s toward the duration estimate (Garmin recomputes on
  device).

Ramping weight is consecutive entries of the same exercise (3x100 kg then 1x110 kg =
two entries). The `GC {date} {type}` naming, `gc-hash` idempotency, and the confirm
interlock are identical to the run path.

"Tempo Thursday: warm-up on-click, 8x(1km at 3:40-4:00, 2:00 jog), cool-down on-click"
becomes (canonical fixture: `tests/fixtures/tempo_request.json`):

```json
{
  "sport": "run", "origin": "athlete", "date": "2026-07-23",
  "session_type": "quality", "pace_target_s_per_km": null,
  "structure": {
    "reps": 8,
    "warmup_end": "lap",
    "work_end": {"distance_m": 1000},
    "work_pace_band": [220, 240],
    "recovery_end": {"min": 2},
    "cooldown_end": "lap"
  }
}
```

The estimated duration is approximate for distance/lap ends (a distance step with a band is
estimated from its midpoint; lap steps count as 0) - Garmin recomputes it on the device.

**Manual live-push acceptance (run once).** The live transport wrapper is validated by a
single confirmed push, not by CI:

1. `garmin-coach author --date <a near future date> --from-recommendation`
2. `garmin-coach push --date <that date>` -- confirm the dry-run payload looks right.
3. `garmin-coach push --date <that date> --confirm` -- then check Garmin Connect / the watch
   shows exactly one scheduled run workout named `GC ...`.
4. Re-run step 3 -- it must report `noop` and create no duplicate.

If the response-field extraction in `GarminWorkoutPublisher` (workout id, schedule id,
scheduled-list shape) does not match what the live account returns, fix it here -- that
mapping is deliberately settled by this step, not guessed in a unit test.

Passed on 2026-07-17 (a `quality` push): create scheduled exactly one `GC 2026-07-17
quality` (warmup + 4x interval repeat + cooldown, Z4 HR band), the re-push reported
`noop` with no duplicate, and all four response mappings (`get_workouts`,
`upload_workout`, `schedule_workout`, `get_scheduled_workouts`) matched unchanged.

**Strength/HIIT acceptance (issue #16, run once per sport).** The same four steps with
an exercise request instead of `--from-recommendation`: one `sport: strength` push and
one `sport: hiit` push (an `--request` JSON with `structure.exercises`), each confirmed
on the account (labels, weights, and rests render; the re-push reports `noop`). The raw
payload shape was proven by the live probes (`scratch/phase11_strength_push_probe.py`
2026-07-15, `scratch/issue16_hiit_push_probe.py` 2026-07-21); this step validates the
production author -> publish path end to end.

Passed on 2026-07-21: `GC 2026-07-23 crossfit` (hiit, 25 steps, workout 1639411347)
and `GC 2026-07-28 strength` (33 steps, workout 1639412392) each created + scheduled
with both response ids extracted, and each re-push reported `noop` with no duplicate.
An account read-back of the strength workout confirmed the last unproven field:
`weightValue: 100` round-trips with `weightUnit` kilogram (`unitId` 8), alongside
verbatim exercise labels, the 120 s rest override, the 90 s default, and the dropped
trailing rest.

## The coach MCP server (mcp__coach__*)

One local stdio server (entry point `garmin-coach-mcp`), registered in the repo's
versioned `.mcp.json`. It is a thin layer over the same functions the CLI uses (see
ADR 0014); the exploratory `mcp__garmin__*` server stays separate and ad-hoc-only.
Which clients pick it up and how is covered in "Registering the server" below — it is
**not** automatic everywhere.

- **Read tools** (local DB, no Garmin): `get_snapshot`, `get_digest`,
  `get_recent_activities(n)`, `get_weekly(week_start)`, `get_zones`,
  `get_plan(week_start)`, `get_recommendation(date)`, `get_events`,
  `get_workout_status(date)`.
- **Local writes** (transport-free): `log_rpe(activity_id, rpe, ...)`,
  `log_niggle(body_part, severity, ...)` — same validation as `log-rpe` in the CLI.
- **Plan of record** — `get_plan(week_start?)` returns the resolved week with a
  per-day `source` (`plan_week` = the athlete authored it, `plan_template` = the
  fallback shape answered) and `has_plan`. When a week is unplanned the digest also
  carries the `PLAN_MISSING` signal. To plan it: compose seven `{planned, intent}`
  days from the athlete's history and standing, call `plan_preview(week_start, days)`
  to validate and **show the table to the athlete**, then `plan_confirm(week_start,
  days)` to write `plans/<monday>_week.md` and cache it. Confirm **refuses an
  already-authored week** — revise that file by hand and re-import, so its paces,
  rationale, and revision log survive (see ADR 0015). A confirmed week that leaves an
  already-pushed workout too hard comes back with `invalidated_pushes` naming those
  days — the week is written either way; tell the athlete which days to re-author.
- **`refresh_today`** — the MCP form of `refresh-today` (see above): pulls today
  partial, rebuilds the mart, never advances watermarks. Call it at most once per
  coach read; it shares the login rate-limit exposure (429) of any transport call.
- **Workout push** — `author_workout(date, request?)` writes `workout.json`;
  `push_preview(date)` returns the resolved action, the Garmin payload, and a
  `confirm_token`; `push_confirm(date, confirm_token, replace?)` writes to the account
  and **refuses any token other than the previewed one**. The token covers the workout,
  its date, *and* the plan of record for that date, so a spec retargeted or a plan
  revised after the preview cannot be confirmed. Show the preview to the athlete before
  confirming — the handshake exists so an agent cannot push what it has not displayed.
  Both `author_workout` and the push pair **refuse a session harder than the plan of
  record** (issue #22, ADR 0021), and `replace` does not override that; change the plan
  for the date first.
- **`get_workout_status(date)`** — the authored spec, the push receipt, and
  `reconciled`: that receipt checked against the Garmin account (issue #41). Read
  `reconciled.state`, not `push.applied` — the receipt records what the push did,
  while the state says what the account holds now: `live` (in the library and on that
  date's calendar), `edited` (scheduled, but rewritten in Connect), `unscheduled` (in
  the library, unpinned or moved to another day), `missing` (deleted), or `unverified`
  (Garmin unreachable — say so rather than quoting the receipt as fact). `renamed_to`
  names the workout's current title when the athlete renamed it in Connect; that is
  allowed and is not a fault. `steps_changed` carries the step verdict beside the
  state — visible even when the state is `unscheduled`, and `null` when the local spec
  was re-authored since the push and so cannot evidence what was sent, which is why
  `live` claims nothing about the steps on its own. `plan_divergence` answers the
  separate, offline question — non-null when the session on the account is *harder*
  than the plan of record now says for that date, naming the pushed type, the current
  planned intent, and when it was pushed. It means the plan was revised after the push;
  report it and offer to re-author, nothing on the watch is changed automatically. This
  tool reaches Garmin, so it shares the 429 exposure above — one library read plus one
  calendar read per date, and one extra read only when the account's copy was touched
  after the push. A date with no receipt costs nothing and never logs in.

**Reading the freshness envelope.** Every response carries
`{data_through, today_included, partial_fields}`. If `today_included` is true, any
field listed in `partial_fields` (load, ACWR, zone minutes, RHR, stress, body
battery) is an intraday running value — quote it as "so far today", never as final.
Sleep, HRV, and readiness are morning-complete and safe to read all day. In
`get_recent_activities`, an activity dated today additionally carries
`partial_today: true` — its training-effect numbers may still settle, so treat them
as provisional too.

### Registering the server in a client

The `.mcp.json` in the repo root is a **Claude Code** convention; other clients need
their own setup. In all cases the `garmin-coach-mcp` script must exist first
(`poetry install` registers it).

- **Claude Code (CLI + IDE), run in this folder** — auto-discovered from `.mcp.json`.
  On first use the project server shows `Pending approval`; approve it once and the
  `mcp__coach__*` tools appear. To re-prompt after a config change, reset the trust
  choice with `claude mcp reset-project-choices`.
- **Claude Desktop** — does **not** read `.mcp.json`. Add the server to
  `claude_desktop_config.json` (macOS:
  `~/Library/Application Support/Claude/claude_desktop_config.json`). Desktop launches
  the command from its own working directory, and the server resolves `.env`,
  `./data/garmin.db`, and `./reports` **relative to its cwd** — so the command must
  `cd` into the repo first. Poetry's `-C <dir>` flag is not enough: it points poetry
  at the project but leaves the server's cwd unchanged (verified — the subprocess
  keeps the caller's cwd), which would silently bootstrap an empty DB elsewhere.
  Wrap the launch in a shell instead:

  ```json
  {
    "mcpServers": {
      "coach": {
        "command": "/bin/zsh",
        "args": [
          "-c",
          "cd /Users/Chabi/garmin-coach && /Users/Chabi/.local/bin/poetry run garmin-coach-mcp"
        ]
      }
    }
  }
  ```

  Use poetry's **absolute path** (a GUI app does not inherit your shell `PATH`, so a
  bare `poetry` often fails to resolve); `which poetry` prints it. If the launcher
  still cannot find its runtime, add an `env` block with a `PATH` that includes your
  Python/poetry bin dirs. Quit Claude Desktop fully (Cmd+Q) and reopen it to pick up
  the config change.

  `task claude:register` writes exactly that entry for you (idempotent; backs the
  config up first, and refuses to write while Desktop is running because Desktop
  rewrites the file as it runs). This is a **per-machine, one-time** step: the entry
  invokes the `garmin-coach-mcp` console script by name, so code changes -- including
  moving the module -- never require re-running it. `task claude:check` reports
  whether the entry is present and current without writing anything.
- **Claude Cowork** — **works, via the device bridge** (verified 2026-07-16: Cowork
  listed the `coach` tools and reported them as reaching this machine by bridge).
  Cowork does not read `claude_desktop_config.json` itself; it relays tool calls to
  the server Claude Desktop already runs on the paired device. The pairing shows up
  as `preferences.remoteToolsDeviceName` in the Desktop config (here:
  `macbookpro-home`). So the Desktop registration above is what makes the tools
  reachable from Cowork too -- no public URL, no separate deployment. Consequences
  worth knowing: the bridge only works while that machine is up and Claude Desktop is
  running, and the server still reads the **local** `data/garmin.db`, so Cowork sees
  whatever this machine has synced.

  This corrects an earlier claim in this file that Cowork was "not supported" and
  needed a remote MCP connector. Adding a **custom connector** in Cowork settings is
  indeed remote-URL-only; the bridge is a separate mechanism, and it is the one in
  use here.
- **claude.ai in a browser** — untested here. The bridge is a Claude Desktop feature,
  so a browser session on a machine that is not running Desktop has no path to this
  server. If you need it there, check whether the same device bridge covers your
  session before assuming it does.

**Running the server without poetry.** On any host where poetry is unavailable (it
needs Python 3.13), launch the module directly after installing the deps once, and
register *that* command wherever a stdio command is accepted:

```bash
pip install mcp matplotlib pydantic pydantic-settings python-dotenv garminconnect curl-cffi --break-system-packages
PYTHONPATH=src python3 -m garmin_coach.mcp.server
```

(`matplotlib` is needed because the server's import chain reaches `report` ->
`charts`, even though the MCP tools never render a chart.)

## Cowork agent notes

**Prefer the `mcp__coach__*` tools if they are present.** When the device bridge is up
(see "Registering the server in a client"), Cowork reaches the coach server on the
paired machine and the one-call read tools are the sanctioned surface -- no sandbox
setup, no dependency install. The notes below are the fallback for when the bridge is
unavailable (that machine is off, Desktop is closed) and Cowork is working against a
copy of the folder in its Linux sandbox instead.

For Claude running in Cowork (pointed at this folder, commands via the Linux sandbox):

- **Read context first.** Open `memory/athlete-profile.md`, then the latest `plans/` file,
  before advising -- the same rail the coach skill carries ("The athlete profile" in
  `skills/coach/SKILL.md`), repeated here because the sandbox may be running without it.
  One sandbox caveat the skill cannot see: these files are a **copy** of the folder, so an
  amendment written here never reaches the athlete's own profile. Hand them the lines to
  paste instead.
- **`poetry` may be missing in the sandbox.** It needs Python 3.13; the sandbox ships 3.10,
  so `poetry run ...` fails. To generate a report without poetry, install the runtime deps
  once and invoke the CLI module directly:

  ```bash
  pip install matplotlib pydantic pydantic-settings python-dotenv garminconnect curl-cffi --break-system-packages
  PYTHONPATH=src python3 -m garmin_coach.cli report      # or: features, if the mart is empty
  ```

  Only run **read-side** commands (`report`, `features`) this way. **Never run
  `sync`/`backfill`/`daily` from the sandbox** -- those call Garmin live, and the operator's
  Mac already runs the nightly job (golden rule).
- **Same-day data is not in the DB yet** (backfill excludes "today"). To read a just-finished
  workout or today's calories/readiness/status, use the `mcp__garmin__*` tools for **ad-hoc
  exploration only** -- never wire them into the pipeline or the coach layer.
- **Athlete-facing output is Polish and number-dense.** Prefer the report artifacts plus the
  `plans/` and `memory/` Markdown; the athlete likes charts. Keep the repo's no-emoji rule.
- **Weekly follow-up.** After a full week closes, the plan-vs-actual for that week is only
  reliable once the Sunday sync lands (Monday morning) -- `weekly` in the digest / the
  `weekly_plan_actual` mart.
