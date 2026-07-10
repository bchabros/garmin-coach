# PRD - Garmin Coach - Phase 3: coach skill (report + signals + charts)

> Status: Ready for implementation (TDD) - Date: 2026-07-05
> Sources: `docs/PROJECT.md` Phase 3 + section 7, `docs/adr/0003-phase-3-coach-signals.md`, `docs/adr/0002-phase-2-metrics-semantics.md`, `docs/glossary.md`, grilling decisions.

## Problem Statement

Phase 2 materializes a gap-free `daily_metrics` mart, but nobody reads it. The
athlete still has to eyeball HRV, ACWR, and load buckets by hand to answer the
questions that matter week to week: is recovery suppressed this morning, is acute
load running hot, is the week drifting into too much grey-zone work, are two hard
days about to stack. There is no artifact that turns the mart into a short, dated,
number-dense coaching read - and no separation between the deterministic reading of
the data and the narrative written on top of it.

## Solution

Split the coach into a deterministic engine and a narrative layer.

- **Deterministic (Python, testable).** A single seam `build_digest(conn, from_date,
  to_date, thresholds)` reads `daily_metrics` + `training_status_daily`, computes a
  compact **digest**: a headline block (latest ACWR + reliability, latest HRV vs its
  band, 7-day load shares) and a list of **coach signals** (rules 1-5 from BUILD
  section 7). `charts.py` renders two PNGs from the mart (HRV +/-1 SD band, ACWR over
  time). `garmin-coach report [--from --to]` orchestrates both and writes
  `reports/{today}/digest.json` + the two PNGs.
- **Narrative (LLM, `skills/coach/SKILL.md`).** The skill runs `garmin-coach report`,
  reads the small `digest.json` (not the raw mart), and writes `report.md` - concise,
  number-driven prose with the two charts embedded and a fixed disclaimer that this is
  a reading of the data, not medical or coaching advice.

The digest is the token boundary: the LLM consumes a ~1-3 kB structured summary and
two images, never hundreds of raw mart rows. The engine never calls Garmin live - it
reads only the finished DB, honoring the golden rule.

## User Stories

1. As the athlete, I want a single dated report generated from my mart, so that I get
   one place to read my training state instead of eyeballing tables.
2. As the athlete, I want the report to open with my latest ACWR and whether it is
   reliable yet, so that I immediately know if I am ramping too fast.
3. As the athlete, I want a signal when my morning HRV is flagged low, so that I know
   to downgrade today's quality session to easy.
4. As the athlete, I want a signal when my recent load is too polarized toward hard
   work, so that I know to add Zone 2 (`AEROBIC_LOW_SHORTAGE`).
5. As the athlete, I want my own load-shortage signal cross-checked against Garmin's
   own `balance_phrase`, so that I can tell whether the two agree before I trust it.
6. As the athlete, I want a signal when my ACWR sits outside the comfort zone, so that
   I see over-reaching or detraining risk called out explicitly.
7. As the athlete, I want an out-of-range ACWR marked "indicative only" while my
   chronic window is still short (`n_chronic < 28`), so that I do not over-react to an
   unreliable ratio.
8. As the athlete, I want a signal when two hard days stack back-to-back, so that I
   catch my Friday-into-Saturday risk before it happens.
9. As the athlete, I want the report to note when my worst HRV nights track my sleep
   score rather than training, so that I do not blame training for a sleep problem.
10. As the athlete, I want an HRV chart with my personal +/-1 SD band, so that I can
    see at a glance which nights fell out of my normal range.
11. As the athlete, I want an ACWR-over-time chart with the comfort-zone lines, so that
    I can see the trajectory, not just today's number.
12. As the athlete, I want the ACWR chart to visibly mark the stretch where the ratio
    was still unreliable, so that I read the early part with caution.
13. As the athlete, I want every signal to carry the actual numbers behind it, so that
    the report is concrete and I can verify it against my data.
14. As the athlete, I want a default window of the last 28 days with the last 7
    highlighted, so that I get both trend context and a recent focus.
15. As the athlete, I want each report written to its own dated folder, so that I keep
    a history of reads without clobbering old ones.
16. As the athlete, I want the report to always carry a "reading, not advice"
    disclaimer, so that I never mistake it for medical or coaching prescription.
17. As the operator, I want `garmin-coach report [--from --to]` to regenerate the
    digest and charts deterministically, so that the same DB state yields the same
    digest every time.
18. As the operator, I want re-running `report` for the same day to overwrite that
    day's folder, so that regeneration is idempotent.
19. As the operator, I want thresholds read from `coach_thresholds`, so that I can
    tune the coach's behavior by editing data, not code or the skill.
20. As the operator, I want the report engine to read only the DB, so that generating a
    report can never trigger a Garmin login or rate limit.
21. As the coach skill, I want a compact `digest.json` rather than the raw mart, so
    that I can write the narrative without burning tokens on hundreds of rows.
22. As the coach skill, I want the two charts pre-rendered on disk, so that I only have
    to reference them, not produce them.
23. As a developer, I want signals and the digest tested at one DB-boundary seam, so
    that tests survive refactors of the signal math.
24. As a developer, I want a golden-style fixture where known signals fire, so that any
    change to a signal definition is caught as a diff against known-good output.

## Implementation Decisions

- **Primary seam - `build_digest(conn, from_date=None, to_date=None, thresholds=None)`**
  in a new `digest.py`. It reads `daily_metrics` and `training_status_daily` for the
  window, computes the headline and the signal list, and returns a plain dict (the
  digest). It writes nothing and calls nothing external. Default window = the trailing
  28 calendar days ending yesterday (never "today", matching the backfill contract).
- **Signals live in `signals.py`** as pure helpers over already-read rows, invoked by
  `build_digest`. A signal is `{code, severity, facts, garmin_agrees?}` where
  `severity in {info, warn, alert}` and `facts` is a flat dict of scalars. Signal
  semantics (rules 1-5) are pinned in ADR-0003; summary:
  - `AEROBIC_LOW_SHORTAGE` - our own: over the trailing window the easy-load share is
    below target while hard-load share is above. `garmin_agrees` = latest
    `training_status_daily.balance_phrase == "AEROBIC_LOW_SHORTAGE"`.
  - `ACWR_OUT_OF_RANGE` - latest `acwr` outside `[acwr_risk_low, acwr_sweet_hi]`;
    `alert` above `acwr_risk_high`, else `warn`; downgraded and marked indicative while
    `n_chronic < acwr_min_chronic_days`.
  - `HRV_LOW_MORNING` - latest day `hrv_low_flag == 1`; recommends degrading the
    quality session to easy.
  - `TWO_HARD_DAYS` - two consecutive days each with `load_day >= hard_te_load`.
  - `HRV_SLEEP_CONFOUND` - Pearson r between `hrv` and `sleep_score` over the window is
    high and a low-HRV night is present; an `info` caveat, not an alert.
- **Thresholds** are read from the existing `coach_thresholds` table by `report.py` and
  passed into `build_digest` as a dict. Code supplies defaults for any key the table
  does not seed; the table is authoritative for keys it does define (override). No new
  seed rows are required by this phase; keys used: `hrv_low_k_sd`, `acwr_risk_low`,
  `acwr_sweet_hi`, `acwr_risk_high`, `acwr_min_chronic_days`, `hard_te_load`,
  `aero_low_target_share`/`aero_high_target_share` (Rule 1), and
  `hrv_sleep_r_min`/`hrv_sleep_min_pairs` (Rule 5).
- **HRV band source of truth is the mart.** Signals and the HRV chart use
  `daily_metrics.hrv_baseline`/`hrv_sd`/`hrv_low_flag` (fresh, whole-window per Phase
  2). `coach_thresholds.hrv_baseline_ms`/`hrv_sd_ms` are only a manual fallback/override
  when the mart lacks a band (e.g. too few nights).
- **Charts - `charts.py`** renders two PNGs with matplotlib (new Poetry dependency):
  `hrv_band.png` (daily HRV line + shaded `baseline +/- 1 SD` band + marked low nights)
  and `acwr.png` (ACWR line + comfort-zone reference lines at `acwr_risk_low`,
  `acwr_sweet_hi`, `acwr_risk_high` + shaded region where `n_chronic < 28`).
- **Orchestration - `report.py`** and a `garmin-coach report [--from --to]` command in
  `cli.py`, wired like `features`. It loads thresholds, calls `build_digest`, renders
  charts, and writes `reports/{YYYY-MM-DD}/` (generation date): `digest.json` +
  `hrv_band.png` + `acwr.png`. Re-running the same day overwrites the folder. It does
  NOT write `report.md`; that is the skill's job.
- **Skill - `skills/coach/SKILL.md`** instructs: run `garmin-coach report`, read the
  latest `reports/{date}/digest.json`, and compose `report.md` in that folder -
  number-dense prose, the two charts embedded, signals rendered in priority order
  (alert > warn > info), and the fixed disclaimer. The skill reads the digest, never
  the mart.
- **`reports/` is git-ignored** - generated per-run artifacts, not source.
- **Schema:** no change expected. `daily_metrics`, `training_status_daily`, and
  `coach_thresholds` already exist. If a column is missing, edit the package copy
  `src/garmin_coach/schema.sql` and re-sync `docs/schema.sql` (guarded by
  `tests/test_schema_sync.py`).

## Testing Decisions

- Good tests here assert only external behavior: seed `daily_metrics` and
  `training_status_daily` rows into a temp SQLite, call `build_digest(conn, ...)`, and
  assert on the returned digest dict (headline values and the signal list). No
  assertions on private helpers or intermediate structures. This is the same
  DB-boundary seam used by `test_features.py`/`test_sync.py`/`test_db.py`; reuse the
  `conftest.py` DB fixtures.
- **Golden-style fixture (`test_digest.py`).** Seed a small frozen mart slice
  (`tests/fixtures/digest_golden.sql`, anonymized, regenerable from `data/garmin.db`)
  covering a window engineered so each rule fires at least once. Run `build_digest` and
  assert the deterministic output:
  - headline `acwr`, `n_chronic`, and `acwr_reliable` match the seeded latest day;
  - `HRV_LOW_MORNING` present exactly when the latest day has `hrv_low_flag == 1`;
  - `ACWR_OUT_OF_RANGE` severity is `warn` vs `alert` at the `acwr_risk_high` boundary,
    and is marked indicative when `n_chronic < 28`;
  - `TWO_HARD_DAYS` fires on the seeded consecutive hard-day pair and not on a single
    hard day;
  - `AEROBIC_LOW_SHORTAGE` fires on the polarized window and carries
    `garmin_agrees == true` when the seeded `balance_phrase` matches;
  - every signal's `facts` are scalars (SQLite-bindable), and `severity` is one of
    `info|warn|alert`.
- **Vertical slices (unit-level behavior via the same seam), each red -> green:** empty
  window yields an empty signal list and a null-ish headline; ACWR exactly on a comfort
  bound does not flag; `garmin_agrees == false` when `balance_phrase` differs;
  `HRV_SLEEP_CONFOUND` only emits when enough paired nights exist; thresholds override
  changes which signals fire; window defaults to the trailing 28 days ending yesterday.
- **Out of seam (validated by a live run, not unit tests):** `charts.py` (PNGs exist
  and are non-empty), `report.py`/`cli.py` (folder + files written, idempotent
  overwrite), and `SKILL.md` (a real `report.md` reads well against the digest). Prior
  art: `test_features.py` (golden regression), `test_db.py` (idempotency),
  `test_models.py` (pure-value expectations).

## Out of Scope

- **Rule 6 - plan vs actual** (`plan_template` vs realized sessions) - deferred to
  Phase 5, per BUILD section 7.
- `weekly_metrics` mart and any weekly rollups - later phase.
- Any live Garmin call from the coach layer - forbidden by the golden rule; monthly
  load balance and targets are read from `training_status_daily`, already in core.
- Trend/forecasting of ACWR or HRV beyond plotting the observed series.
- Multi-athlete support; interactive or web-served reports (Markdown + PNG only).
- Emitting `report.md` deterministically from Python - the narrative is the skill's job.

## Further Notes

- **Two `AEROBIC_LOW_SHORTAGE` sources, deliberately kept separate.** Garmin already
  writes its own `balance_phrase` into `training_status_daily` (its monthly-load view).
  We still compute our own signal from our daily buckets so the two are independent;
  `garmin_agrees` records whether they concur. Agreement strengthens the report's
  wording; disagreement makes it hedge. We never blindly forward Garmin's phrase.
- **Digest is non-durable, like the mart.** It is a recomputed view over `daily_metrics`
  (itself non-durable): the same folder date regenerates in place and historical HRV
  bands can shift as new nights arrive. `digest.json` is kept in the report folder for
  audit and as the skill's input, not as a system of record.
- **Token boundary is the whole point.** Everything expensive and repeatable (reading
  the mart, computing signals, drawing charts) is deterministic Python; the skill only
  ever sees the compact digest and two images, so narrative generation stays cheap and
  reproducible in its inputs.
