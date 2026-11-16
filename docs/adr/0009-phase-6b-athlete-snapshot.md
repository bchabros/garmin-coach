# ADR 0009 - Phase 6b: athlete snapshot (`athlete_status` mart)

## Status

Accepted

## Context

A current-standing answer needs one stable deterministic surface rather than repeated ad-hoc joins.

## Decision

- Persist a recomputed singleton `athlete_status`, composed from finished marts/core
  at the tail of `features`, after weekly/zones/overlap. Rollup never commits itself.
- Bound latest readings to `computed_at`; use that date for planned intent. Mirror
  all zone bounds/provenance so the snapshot is self-contained. ADR 0017 declares
  when a standing singleton does not match a historical report horizon.
- Trend means current value plus signed change over available history and actual
  span. Below the configured minimum span, delta/span are null, not invented.
- VO2max trends use ingested training status; HRV delta uses Garmin weekly-average
  HRV, because our recomputed baseline is constant across the mart's current history.
  Load/ACWR shares reuse existing policy helpers.
- Block/countdown/taper come from ADR 0012; planned intent uses ADR 0015's resolver,
  with null intent when neither authored week nor agreed template exists.
- Amendment approved in #103: remove weight and race-prediction snapshot paths and
  their unused policy/schema requirements because no ETL supplies them. A future
  ingestion feature needs its own issue. Preserve extra legacy storage, while public
  snapshot reads explicitly project only supported fields.
- Amendment approved in #106: restore these capabilities from newly ingested
  observations, with source dates, units and supported 28-day changes. Keep retired
  tables/columns intact and separate; their values are not evidence of ingestion.
  Preserve every weighing and select each source day's last for its trend. Garmin
  race forecasts remain attributed estimates, with each distance's own date and
  history. Short or missing comparisons are null; report their actual span.
  These sources may establish the materialization horizon when other streams are
  absent. That horizon does not establish fresh HRV, sleep or readiness evidence;
  preserve missing fields and each source's dates rather than inventing recovery.

## Consequences

CLI, MCP and reports share the persisted standing without network access. Historical
recomputations remain reproducible, and legacy columns cannot leak retired values
back into the API. No destructive migration is needed for this scope reduction.

## Dated weekly evidence (#120)

A separate weekly block now retains completed Monday-Sunday periods, the additional
baseline week and partial current-week context. A Sunday horizon closes the week.
Comparisons retain missing periods and do not silently substitute an older baseline.
Existing VO2max/HRV long-window delta/span fields retain their meanings.

The anonymized status fixture distinguishes generic VO2max `calendarDate` from
the daily observation date and contains a separate Training Status source date.
Those dates are retained when supplied; old rows remain unknown until an explicit
evidence-supported reimport. No verified event identity exists, so event counts
remain unknown. Repeated daily copies are not averaged into measurements.

This trades a single trend claim for dated, possibly conflicting evidence. A
comparison of endpoints carrying the same known source date is unavailable, not a
zero change. The actual estimate-date span must cover the requested comparison
span; where source dates are unknown, observation dates provide only a limited
stored-snapshot comparison. Both spans and their basis are returned explicitly.

For this weekly reporting view, evidence at least one seven-day cadence old is
stale for a comparison. This is a reporting sufficiency rule, not a physiological
expiry claim or a replacement Garmin Training Status. Stale endpoints retain their
values as context but cannot establish a change. Unknown source dates retain
unknown estimate freshness: observation age is an explicit fallback, and a recent
saved row alone never proves a fresh estimate. Carried values expose that age and
its basis. Comparisons inside an onboarding-cut week remain unavailable.

## Recorded running context investigation (#120)

Audited `activities_range.json`, the captured/typed fields in
`activity_laps_structured_outdoor.json`, `activity_laps_unstructured_run.json`,
`activity_laps_treadmill.json`, `activity_laps_stations_lap_ended.json`, and
`status_day.json` against the persisted activity/lap/status columns. These support
activity/lap distances, durations, speeds and observed heart-rate averages/maxima,
plus dated Garmin status/estimate observations. They do not substantiate historical
HR sensor type, satellite mode, track detection/lane, configured maximum HR or a
verified matched route. The anonymous device-keyed status map identifies neither
an HR sensor nor its settings; an activity name/type is not settings provenance.

Those historical settings therefore remain unknown in sanctioned reads. Observed
`max_hr` is a measurement, never a configured maximum; reported chest-strap context
remains a separately attributed athlete statement. This is a limit of the audited
fixtures and persisted source contract, not a claim that every upstream Garmin
endpoint lacks such fields. No live account or current settings were used to
retroactively populate history. Supporting a verified dated source later would
require explicit deterministic ingestion evidence.

Before/after period comparisons require a baseline ending before the selected
period, retain status rows while withholding values dated after the period end,
and expose work/lap evidence separately from activity averages. Both CLI and MCP
accept attributed reported context and return controlled input errors. They never
claim matched runs or establish a cause for Maintaining/Productive. Real fresh-client
acceptance remains separate from offline synthetic/protocol verification.
