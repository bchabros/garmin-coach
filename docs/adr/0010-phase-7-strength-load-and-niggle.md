# ADR 0010 - Phase 7: strength and Hyrox load model + niggle log

## Status

Accepted

## Context

HR-derived Garmin load undercounts lifting, while raw RPE×minutes would overwhelm the existing load scale.

## Decision

- Scale subjective session load as configured `srpe_load_scale * rpe * duration_min`
  using total session duration. The provisional scale aligns strength with the
  existing Garmin-load currency; it is not independent physiological validation.
- Strength (`Siła`) uses sRPE; other disciplines use `max(Garmin, sRPE)`. Hyrox/HIIT
  retains its actual HR-derived load. Missing Garmin load is zero.
- Supply a configured default RPE only for strength. Apply defaults at blend time;
  core `session_rpe` contains only actual athlete reports, never synthetic observations.
- Keep strength in a separate bucket included in total load/ACWR/monotony/hard-day
  logic but excluded from aerobic shares. The four buckets sum to blended load.
- One load-hard definition remains; a session RPE signal is a separate subjective fact,
  not a competing definition used by consecutive-hard-day classification.
- A body-part niggle is active for the configured window from its latest report;
  a lower re-log clears severity early. Reduced-mode is read from core in the digest,
  with flat scalar facts, without an eternal resolve state.
- Transport-free RPE logging recomputes from the session date. Niggle logging writes
  core and is visible on the next digest, without unnecessary mart rebuilding.

## Consequences

Strength stress becomes visible without inflating easy aerobic share or discarding
honest cardio load. Policy defaults are provisional model choices; logged observations
remain the durable source of truth.
