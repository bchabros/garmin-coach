# ADR 0003 - Phase 3 coach signals + digest semantics

## Status

Accepted

## Context

Phase 2 materializes the `daily_metrics` mart. Phase 3 adds the coach: a deterministic
engine that reads the mart (plus `training_status_daily`) and produces a compact
**digest** (headline + signals), two charts, and a `garmin-coach report` command, and a
narrative skill (`skills/coach/SKILL.md`) that writes `report.md` from the digest. The
BUILD doc (section 7) lists six coach rules in prose; rule 6 (plan vs actual) is Phase
5. The remaining rules are ambiguous in ways that change whether a signal fires; this
ADR pins them, and pins the seam and the transport boundary.

## Decision

- **Single seam.** `build_digest(conn, from_date, to_date, thresholds) -> dict` reads
  `daily_metrics` + `training_status_daily`, computes the headline and signals, and
  returns a plain dict. It writes nothing and calls nothing external. Tests seed a
  temp SQLite with mart + training-status rows, call `build_digest`, and assert on the
  returned dict - the same DB-boundary seam as `features(conn)`. `signals.py` holds
  pure helpers invoked by `build_digest`; `charts.py` and `report.py`/`cli.py` are out
  of seam (validated by a live run).

- **Transport boundary.** The coach layer reads only the finished DB; it never calls
  Garmin. Monthly load balance, its target bands, and Garmin's own ACWR/`balance_phrase`
  come from `training_status_daily`, populated by the ETL. This is the golden rule.

- **Token boundary.** The LLM skill consumes `digest.json` (~1-3 kB) and two PNGs, never
  raw mart rows. Everything expensive/repeatable is deterministic Python.

- **Default window.** Trailing 28 calendar days ending at the latest ingested
  `daily_metrics` day (`MAX(date)`), which under the backfill contract *is* yesterday in
  normal operation - never "today". `build_digest` reads no wall clock, so a default
  window stays a pure function of the DB it is handed (reproducible in tests without
  mocking `date.today()`). The report highlights the last 7 days within that window.
  `--from/--to` override.

- **Thresholds.** Read from `coach_thresholds` and passed into `build_digest` as a dict.
  The table is authoritative for keys it seeds; code supplies defaults for any key it
  does not. Keys used: `hrv_low_k_sd`, `acwr_risk_low` (0.8), `acwr_sweet_hi` (1.3),
  `acwr_risk_high` (1.5), `acwr_min_chronic_days` (28), `hard_te_load` (150),
  `aero_low_target_share` (0.60) and `aero_high_target_share` (0.40) for Rule 1, and
  `hrv_sleep_r_min` (0.5) and `hrv_sleep_min_pairs` (7) for Rule 5.

- **HRV band source of truth is the mart.** Signals and the HRV chart use
  `daily_metrics.hrv_baseline`/`hrv_sd`/`hrv_low_flag` (fresh, whole-window per Phase
  2). `coach_thresholds.hrv_baseline_ms`/`hrv_sd_ms` are a manual fallback/override only
  when the mart has no band (too few nights).

- **Signal shape.** `{code, severity, facts, garmin_agrees?}`. `severity in {info, warn,
  alert}`. `facts` is a flat dict of scalars (SQLite-bindable, JSON-safe). Signals are
  emitted in priority order alert > warn > info in the report.

- **Rule 1 - `AEROBIC_LOW_SHORTAGE` (our own + cross-check).** Over the trailing 7-day
  recent focus (the same slice the headline's `load_7d` highlights, per user story 14 -
  not the full 28-day window), compute the easy-load share
  `load_low / (load_low + load_high + load_anaerobic)` and the hard-load share. Flag
  when the easy share is below target and the hard share above (too much grey zone ->
  "add Z2"). It is computed from *our* daily buckets, independent of Garmin.
  `garmin_agrees` = latest `training_status_daily.balance_phrase ==
  "AEROBIC_LOW_SHORTAGE"`. Agreement strengthens the wording; disagreement makes the
  report hedge. We never blindly forward Garmin's phrase. `severity = warn`.

- **Rule 2 - `ACWR_OUT_OF_RANGE`.** Uses the latest day's own `acwr`. Flag when outside
  `[acwr_risk_low, acwr_sweet_hi]` = `[0.8, 1.3]`. `severity = alert` when `acwr >
  acwr_risk_high` (1.5), else `warn`. When `n_chronic < acwr_min_chronic_days` (28),
  mark the signal indicative (`facts.reliable = false`) and do not escalate to `alert`.

- **Rule 3 - `HRV_LOW_MORNING`.** Fires when the latest day has `hrv_low_flag == 1`.
  Recommends degrading today's quality session to easy. `severity = warn`. `facts`
  carry `hrv`, `baseline`, `sd`, and the threshold `baseline - hrv_low_k_sd * sd`.

- **Rule 4 - `TWO_HARD_DAYS`.** A day is "hard" when `load_day >= hard_te_load` (150).
  Fires on any two consecutive hard days in the window; a trailing pair ending at the
  window edge is highlighted as an upcoming-stacking risk (the athlete's known
  Friday-into-Saturday pattern). A single hard day does not fire. `severity = warn`.

- **Rule 5 - `HRV_SLEEP_CONFOUND`.** Compute Pearson r between `hrv` and `sleep_score`
  over paired nights in the window (require enough pairs, default >= 7). When r is high
  (reference r ~ 0.57) and a low-HRV night is present, emit an `info` caveat: the worst
  HRV may be sleep-driven, not training-driven - do not confuse causes. Not an alert.

- **Charts.** `hrv_band.png`: daily HRV line + shaded `baseline +/- 1 SD` band + marked
  low nights. `acwr.png`: ACWR line + reference lines at `acwr_risk_low`,
  `acwr_sweet_hi`, `acwr_risk_high` + shaded region where `n_chronic < 28`. matplotlib,
  PNG, written to `reports/{date}/`.

- **Output.** `garmin-coach report [--from --to]` writes `reports/{YYYY-MM-DD}/`
  (generation date): `digest.json` + `hrv_band.png` + `acwr.png`. Idempotent overwrite
  per day. `report.md` is written by the skill, not by Python. `reports/` is git-ignored.

## Consequences

- Signal definitions are testable in isolation at one seam, pinned by a golden-style
  fixture (`tests/fixtures/digest_golden.sql`) engineered so each rule fires.
- The digest is non-durable, like the mart: regenerating the same folder date can shift
  historical HRV bands as new nights arrive. It is an audit input for the skill, not a
  system of record.
- Keeping our `AEROBIC_LOW_SHORTAGE` independent of Garmin's `balance_phrase` costs a
  little duplication but buys an honest agreement check instead of a passthrough.
- matplotlib enters the dependency set (Poetry). The engine gains no network surface.
