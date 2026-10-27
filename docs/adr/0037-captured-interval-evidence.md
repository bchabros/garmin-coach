# ADR 0037 - Captured laps and verified executed interval groups

## Status

Accepted

## Context

Issue #104 began with six 3:45 treadmill work repetitions. Developer captures on
2026-10-26 showed 25 laps, including twelve work laps, but six explicit typed work
groups of 225 seconds. A structured outdoor example has nine work laps in three
3 km groups. A plain run has a whole-session INTERVAL_ACTIVE without membership;
a lap-ended station session recorded as running has only ACTIVE groups. Neither
ACTIVE nor activity type alone proves running interval repetitions.

## Decision

- Fetch both chronological laps and typed splits through deterministic per-activity
  enrichment. One response lacks explicit grouping; category split summaries lack
  chronology/membership. Two requests are necessary for this verified contract.
- Keep each payload raw-first and normalize laps, typed groups and explicit membership
  as separate core observations. Raw retains omitted fields for offline reprocessing.
  A complete successful capture atomically replaces all three sets and invalidates
  its old derived summary. If either request fails or returns None, retain the prior
  complete capture; retain any successful raw response, and report exceptions as
  enrichment misses under ADR 0018. Malformed response containers also preserve core.
- Source lap indices, workout-step indices and typed-group message indices are
  different identities. Repeated step indices never merge separate executions.
  Accept explicit, contiguous, disjoint, complete interval-group membership with
  matching direct lap roles and consistent known workout/step indices for work and
  recovery. Warm-up/cool-down keep their direct roles even when step indices are
  absent. Ignore overlapping RWD run/walk classifications as execution evidence.
- Support running, trail_running and treadmill_running under the same evidence
  contract; eligibility uses the recorded Garmin type, including null-discipline
  treadmill activities. All-ACTIVE station-like captures lack verified structured
  boundaries and stay captured-only. Unknown roles, missing memberships or ambiguous
  identities also stay readable with an unavailable-summary reason. Do not classify
  stations or infer roles from names, HR, pace, lap count or planned workout steps.
- Verified execution roles are WARMUP, ACTIVE, RECOVERY and COOLDOWN. The developer
  fixtures do not verify REST / INTERVAL_REST. Such an unknown role preserves captured
  laps but makes the running summary unavailable; it is not silently treated as
  RECOVERY. RECOVERY is a source role, not proof of jogging or standing. Planned
  rest steps sent to the watch do not establish the executed endpoint's JSON contract;
  actual captured REST evidence is required before adding that mapping.
- Order and duplicate detection use the same parsed UTC instants. A timezone-free
  startTimeGMT is UTC; equivalent text separators or explicit offsets preserve its
  execution order. The original captured timestamp text remains unchanged.
- Preserve typed-group time, distance and HR as source aggregates. Do not reconstruct
  a group HR average from rounded lap averages. Captured sums have small duration
  differences and up to 2.08 m distance difference in these examples; elapsed times
  are not additive. Source timestamps establish order, not exact end-to-start timing.
  Fixtures do not establish manual versus auto-lap triggers or a treadmill sensor.
- Materialize descriptive summaries inside the existing features transaction.
  Replace only summaries whose activity dates are inside the inclusive data-start /
  report-cutoff window; summaries outside it remain intact. A historical rebuild
  previously removed later summaries by clearing the whole table. These are dated
  activity results, not a current-standing singleton. Read adapters share one
  saved-data flow and freshness envelope. A changed capture cannot
  return its older summary before recomputation. Complete-week entries select only
  their own week's activities; current partial detail remains a separate read.
- The two HR comparisons and outdoor pace spread describe recorded groups and coverage.
  They add no physiological-drift claim, timed HRR, recovery-quality score, threshold,
  signal or recommendation. Treadmill pace spread and reported-console overlays are
  outside this issue; the latter remains #105.

## Consequences

Two enrichment requests increase transport work but preserve the distinction between
laps and actual execution groups. Conservative unavailable results are preferable to
invented repetitions. Existing installations add empty tables through the supported
offline bootstrap/features path before guarded clients restart; historical observations
require explicit operator backfill followed by offline features. No client or development
test migrates the real athlete installation. Anonymized fixtures and transport acceptance
are separate from a full production historical import or a fresh assistant conversation.
