# 02 - Custom pace band on the work step (+ hybrid pace warning)

Status: ready-for-agent
Blocked by: 01
Sources: `docs/prd/phase-11a-custom-run-authoring/PRD.md`, `docs/glossary.md`
(custom pace band), ADR 0013.

## Goal

Let the athlete state their own pace window on the work step and have it used verbatim -
the hybrid of pace, mirroring Phase 11's hybrid of session type. `work_pace_band:
[220, 240]` becomes exactly that `pace_band`, overriding the recommender's derived pace
and skipping the heart-rate fallback.

## Scope

- **Explicit band wins.** When `structure.work_pace_band` is present, the work target is a
  `pace_band` built directly from it; the recommender's `pace_target_s_per_km` and the
  pace -> HR -> none degradation are not consulted. Absent, behaviour is exactly Phase 11.
  Applies wherever a work target is built (`easy`/`tempo`/`quality`).
- **Validation** (extend `_validate_structure` from ticket 01). `work_pace_band` is a
  two-element `[fast_s_per_km, slow_s_per_km]`, both positive, with `fast < slow`;
  otherwise `ValueError` naming the field.
- **Hybrid pace warning.** For an `athlete`/hybrid request carrying an explicit band whose
  fast bound is meaningfully faster than the recommender's suggested pace, append a cited
  warning (reusing the recommendation's rationale codes) to `warnings[]`. Never blocks.
  Reuse the existing `_hybrid_warnings` shape.

## Acceptance criteria

- [ ] An explicit `work_pace_band` becomes the work `pace_band` verbatim, overrides the
      recommender pace, and emits no degradation warning (fallback suppressed).
- [ ] The band applies to the work step in `easy`, `tempo`, and `quality`.
- [ ] An inverted or non-positive band raises `ValueError` with a clear message.
- [ ] The hybrid pace warning fires (cited) when the band is harder than the recommender's
      suggestion, and does not fire otherwise; it never blocks authoring.
- [ ] All coverage offline on Seam 1; `publish` and CLI untouched.
