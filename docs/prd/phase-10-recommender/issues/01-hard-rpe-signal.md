# 01 - `HARD_RPE_YESTERDAY` signal + `hard_rpe` threshold

Status: ready-for-agent
Blocked by: -
Sources: `docs/prd/phase-10-recommender/PRD.md` (New signal: `HARD_RPE_YESTERDAY`;
Thresholds). Deps: Phase 7 (`session_rpe`).

## Goal

Bring the athlete's subjective session-RPE into the uniform signals channel, so a
genuinely hard session yesterday is a first-class digest finding - and so the Phase 10
recommender can consume it without ever touching `session_rpe` directly. This ticket
stands alone: it is useful in the digest on its own and does not depend on the recommender.

## Scope

- **New signal function in `signals.py`**: `HARD_RPE_YESTERDAY`. Fires when the last day
  of the window (`to_date`) has a `session_rpe` row - joined to `activities` on
  `date == to_date` - with `rpe >= hard_rpe`. When more than one rated session lands that
  day, the maximum RPE governs.
- **Shape** (flat scalars, like every other signal):
  `{code: "HARD_RPE_YESTERDAY", severity: "warn", facts: {activity_id, rpe, date}}`.
  Silent (`None`) when there is no rated session on `to_date`, or the max RPE is below the
  floor.
- **New threshold** `hard_rpe = 8` in `thresholds.DEFAULTS` (Borg CR10; overridable via the
  `coach_thresholds` table, like every other threshold). No schema change.
- **Wire into `build_digest`**: read the rated session(s) for `to_date`, compute the signal,
  and include it in the `signals` candidate list so it sorts and renders like the rest. It
  is only computed when `to_date is not None` (same guard the other `to_date`-scoped
  signals use).

## Tests (`test_signals.py`, `test_digest.py`)

- Fires at `rpe == hard_rpe` and above; silent below.
- Picks the maximum RPE when two sessions are rated on `to_date`.
- Silent with no `session_rpe` row for `to_date`, and silent when a rated session exists on
  an earlier day but not on `to_date`.
- `facts` carries `activity_id`, `rpe`, `date`; scalars only.
- Through `build_digest`: a seeded fixture with a hard-RPE session on the latest day surfaces
  the signal in `signals`; a soft-RPE fixture does not.

## Done when

- A hard session (RPE >= 8) logged on the latest mart day appears as `HARD_RPE_YESTERDAY`
  in `digest.json`.
- The floor is configurable through `coach_thresholds` and defaults to 8.
- `task check` green.
