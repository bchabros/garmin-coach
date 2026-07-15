# 02 - Recommender core (`recommend.py`) + wire into digest

Status: ready-for-agent
Blocked by: 01
Sources: `docs/prd/phase-10-recommender/PRD.md` (Seam and inputs; Horizon; The
`recommendation` block; Intensity vocabulary and the downgrade ladder; Composition:
signal -> action). Deps: Phase 6 (zones/pace), Phase 9 (block/taper).

## Goal

Turn the finished digest into a single prospective recommendation for tomorrow. After this
ticket, `garmin-coach report` emits a working `recommendation` block carrying the intensity
verdict, cap, pace, and cited reasoning. `avoid` and `replan` are stubbed here (empty list /
null) and filled by later tickets.

## Scope

- **New module `recommend.py`**, one public pure function:
  `recommend(digest, planned_intent, thresholds) -> dict`. Reads only the passed `digest`
  (its `signals`, `zones`, `weekly`, `plan`, `window`) and `planned_intent`. Never opens the
  DB, never calls Garmin. Keep helpers private and single-purpose.
- **Horizon = tomorrow** (`to_date + 1`); the block carries `target_date`. "Yesterday" for
  triggers means `to_date`.
- **The block** (this ticket fills every field except `avoid`/`replan`):
  `target_date, planned_intent, intended_type, intensity_cap, pace_target_s_per_km,
  downgraded, rationale, avoid ([]), replan (null)`.
  - `intended_type` from the `plan_template.intent` vocabulary
    (`rest | quality | easy | hyrox | tempo`), only ever softened from `planned_intent`.
  - Hardness ladder `rest < easy < tempo < hyrox = quality`; each fired action names a cap,
    and `intended_type = min(planned_intent, every cap)` (order-independent,
    most-conservative-wins).
  - `intensity_cap` in `Z2 | Z3 | Z4 | null`, from `digest["zones"]`.
  - `pace_target_s_per_km`: `z2_pace_ceiling_s_per_km` when cap is `Z2`,
    `threshold_pace_s_per_km` for a tempo/quality target; `null` when the zones mart has no
    measured pace or no zones row.
  - `downgraded = intended_type != planned_intent`.
  - `rationale` = the signal codes that actually changed the outcome; empty when nothing
    fired.
- **Composition (signal -> action)**, iterating `digest["signals"]`:
  - `HRV_LOW_MORNING`, `ACWR_OUT_OF_RANGE` (only when `facts.acwr > facts.sweet_hi`),
    `NIGGLE_REDUCED_MODE`, `DELOAD_ADVISED` -> cap `easy`, `intensity_cap = Z2`.
  - `TWO_HARD_DAYS` only when `facts.trailing == true` -> cap `easy`.
  - `HARD_RPE_YESTERDAY` -> cap `easy`.
  - `AEROBIC_LOW_SHORTAGE` -> no type cap; if the resolved type is `easy`, set
    `pace_target_s_per_km` to the Z2 pace ceiling.
  - `TAPER_ACTIVE` -> no type cap; `intensity_cap` may not be left `null` (no all-out); cited.
  - Cite a signal only when it actually changed the outcome.
- **Wire into `build_digest`** (the orchestrator, like `_plan_section`/`_zones_section`):
  read `plan_template.intent` for `weekday(to_date + 1)`, pass it as `planned_intent`, and
  append the returned block under `recommendation`. Emit the key only when `to_date is not
  None`; the empty-window branch omits it.

## Tests (`test_recommend.py`, `test_digest.py`)

- Pure golden fixtures over hand-crafted digests: green (no downgrade), hot ACWR, HRV low,
  aerobic deficit (pace forced to Z2 ceiling on an easy day), deload advised, taper week
  (cap not null, type not forced to easy on taper alone), hard-RPE-yesterday, and a
  most-conservative-wins case with several downgrade signals resolving to the single lowest
  type with all cited.
- `ACWR_OUT_OF_RANGE` below the sweet spot does not downgrade.
- `TWO_HARD_DAYS` with `trailing == false` does not downgrade.
- `pace_target_s_per_km` is `null` when zones carry no measured pace.
- Through `build_digest`: the `recommendation` block is present, gated on `to_date`, reads
  tomorrow's `plan_template` intent, and `avoid == []` / `replan == null`.

## Done when

- `garmin-coach report` writes a `recommendation` block whose `intended_type`,
  `intensity_cap`, `pace_target_s_per_km`, `downgraded`, and `rationale` reflect the digest's
  signals, only ever softening the planned session.
- `task check` green.
