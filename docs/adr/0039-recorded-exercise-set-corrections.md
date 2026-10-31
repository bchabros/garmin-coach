# ADR 0039 - Correct recorded exercise sets against reviewed account evidence

## Status

Accepted design ([#117](https://github.com/bchabros/garmin-coach/issues/117),
2026-10-31); implementation, transport-format verification and operator acceptance
remain pending.

## Context

On 2026-10-27, correcting misidentified and nameless strength sets locally left Garmin
Connect showing the watch's original record. Later app edits can also be hidden by the
local overlay. The athlete wants both chat and app corrections, including older sessions,
while retaining recorded timing and the history of their own corrections.

## Decision

- Extend [ADR 0014](0014-coach-mcp-server.md) with an isolated recorded-set
  preview/confirmation boundary. Read the complete account payload, show
  retained/new/deleted sets and bind approval to
  the displayed account and manual evidence. Re-read before a confirmed write;
  relevant changes require a new preview. Metrics continue to use finished data only.
- Preserve rests, timing and unrelated fields for retained sets. Added-set timing
  must follow verified source evidence, with unrecorded time explicit. Synthetic
  guesses and another project's implementation do not establish Garmin acceptance.
- Extend [ADR 0022](0022-manual-set-overlay.md) by preserving manual corrections as
  durable history. Retire the active overlay only after a verified matching account
  read-back is durably stored. Archive the correction and retire the overlay atomically,
  so future app edits can reach the analysis without losing correction history.
  A failed or mismatching read-back retains the overlay and reports the unresolved outcome.
- Record a captured-set baseline with new manual corrections. Differences expose
  a conflict for the athlete to resolve; they do not automatically establish which
  edit is newer. Existing corrections without a baseline remain explicitly unknown.
- Recheck stored strength/HIIT set evidence across a seven-night cycle, with an
  explicit selected-activity refresh available sooner. This trades bounded additional
  reads for eventual coverage without inventing a source modification timestamp.
  Failures or missed runs leave coverage incomplete and are reported as such.
- Keep activity-set writes behind the existing saved-login, rate-limit and transport
  boundary. Account-write success, local persistence, read-back verification and
  derived refresh are separate outcomes; a late failure must not become a blind retry.

## Consequences

Classification mapping (#118), correction of recorded sets (#117), and strength personal
records (#121) remain separate capabilities. An API read-back does not prove that Garmin's
UI displays corrected names or updates its records. Before closing #117, an operator
must verify adding/deleting sets with retained timing and inspect the Garmin UI.
No duplicate-activity upload or deletion of the original recording is a fallback.
