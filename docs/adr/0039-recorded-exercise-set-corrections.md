# ADR 0039 - Correct recorded exercise sets against reviewed account evidence

## Status

Accepted design ([#117](https://github.com/bchabros/garmin-coach/issues/117),
2026-10-31). Preview, approval-bound confirmation, correction history and historical
rechecks are implemented. The observed nullable add/delete contract extends this
decision; implementation and operator acceptance evidence are recorded in #117.

## Context

On 2026-10-27, correcting misidentified and nameless strength sets locally left Garmin
Connect showing the watch's original record. Later app edits can also be hidden by the
local overlay. The athlete wants both chat and app corrections, including older sessions,
while retaining recorded timing and the history of their own corrections.

## Decision

- Extend [ADR 0014](0014-coach-mcp-server.md) with one isolated recorded-set correction
  boundary shared by conversational and terminal adapters. Cached activity-set reads
  and metrics remain offline; selected-activity refresh, account-reading preview and
  confirmed account writes are explicit transport operations. Validate local activity,
  complete correction, identity and unit prerequisites before connecting.
- One correction row means one intended working set for the account proposal.
  [ADR 0022](0022-manual-set-overlay.md)'s existing circuit rows remain station
  descriptions with record-only set quantities. Do not reinterpret retained rows or
  silently expand rounds/quantities into account sets; ambiguous count or order requires
  an explicit ordered working-set description from the athlete.
- Preserve the captured/manual `max_weight` storage contract in grams and show
  kilograms to the athlete with an explicit conversion. Missing reported repetitions
  or weight preserve the current account values for a surviving set; unknown values
  for an added set require the verified transport contract, not invented measurements.
- Resolve exercise identities through the existing curated Garmin taxonomy.
  Movement classifications from #118 cannot supply Garmin exercise identities;
  unresolved names refuse before an account write. This leaves the authored-workout
  unit and unknown-name contracts of [ADR 0016](0016-strength-hiit-authoring.md) intact.
- A source set may contain several recognition candidates and null variant names.
  Preserve that complete list for a repetition/weight-only correction against an
  explicitly selected captured index. An explicit reported identity replaces guesses
  with the curated choice, using the native certainty value. A known category with
  a null variant is an explicit generic recorded exercise, not an inferred specific
  variant; recorded generic identities and authored-workout aliases remain distinct.
- Build from the complete freshly read account payload, preserving opaque fields,
  rests and captured timing for surviving sets. Propose and show retained/new/deleted
  sets and their alignment with the athlete's correction, allowing alignment changes
  before approval. Bind approval to the activity, reviewed account/manual evidence and
  proposed alignment/changes. Re-read immediately before writing; a relevant change
  requires a fresh preview and approval. Never automatically retry an uncertain write
  or restore an old whole payload over a possible concurrent app edit.
- Added work uses the operator-observed null duration and absent start/identifier
  contract. Native empty rests use null timing and weight -1 as an unknown sentinel,
  never a negative load. Garmin can display null time as 0:00; we do not invent a
  measured zero or synthesize positive timing. New work requires an explicit exercise,
  repetitions and kilogram weight (zero for bodyweight); unsupported unknown new
  measurements still refuse. Retained work/rest entries keep their complete source
  timing and opaque fields. Deleting work removes its following rests; trailing
  unrecorded rest scaffolding may be removed only when it has no other information.
- Pairing uses unique identity/weight evidence, then identity, repeated-identity
  order and remaining source order. Heuristics require review. For an override,
  every row has a captured work index or null for new work; omitted source indices
  are deleted. Additions require surviving indices in source order, preserving the
  source timeline rather than reordering measured segments. Existing alignment-only
  corrections continue assigning reported values to the reviewed source slots.
- Persist validated read-back raw-first and replace captured sets atomically. Verify
  identities, counts and approved values/timing against the proposal. Extend ADR 0022
  with durable correction history. For a confirmed account write, only matching,
  durably saved read-back permits atomic archival of the correction and retirement
  of its active overlay. This is retained history, not destructive clearing;
  failed or mismatching verification keeps the overlay and reports the unresolved
  account outcome.
- Record the captured-set baseline for a new manual correction. It identifies the
  evidence the athlete corrected, not an account edit timestamp. Show changed captured
  and manual evidence together. The athlete chooses captured evidence, retiring the
  overlay while retaining its history, or the manual correction, retaining it against
  the reviewed current baseline for a later confirmed push. Neither legacy missing
  baselines nor observation dates establish which edit is newer.
- Extend [ADR 0026](0026-recheck-window.md)'s configurable recent window (default three
  days) with rotating historical strength/HIIT set rechecks across seven successful
  nightly runs. Read only the historical share due for that run, excluding activities
  already read by the recent window, through the existing rate-limit/transport boundary.
  Work grows with eligible history rather than a new fixed nightly request quota.
  Failed reads or missed runs leave coverage due/incomplete; record observation dates
  and progress without inventing source edit times. Selected-activity refresh shares
  persistence/conflict handling and rebuilds immediately; nightly batches feed the
  existing single mart pass. Preserve onboarding/report horizons and stream watermarks.
- Unavailable, malformed or wrong-activity responses are not verified empty set lists.
  They must not clear prior captured evidence, retire corrections or complete coverage.
  Report account write, read-back verification, captured save, overlay retirement and
  derived refresh separately. Extend [ADR 0036](0036-athlete-facts-survive-rebuild-failure.md):
  saved facts/history survive a failed atomic mart rebuild, with offline recovery in
  the same installation. Failure after an account write must not claim the account was
  untouched; recovery starts from fresh source evidence rather than a blind retry.

## Consequences

Classification mapping (#118), correction of recorded sets (#117), and strength personal
records (#121) remain separate capabilities. Historical rereads trade additional
rate-limited transport work for eventual coverage without assuming an edit timestamp.

An API read-back does not prove that Garmin's UI displays corrected names or updates
its personal records. Another project's implementation and passing offline tests
cannot replace source-format and UI observation. The operator experiment records
approved before/after payloads, UI display and surviving timing in #117; only anonymized
source shapes with artificial measurements enter fixtures. Live operator acceptance
is separate from development tests, which use temporary SQLite and fake transport.
No duplicate-activity upload or deletion of the original recording is a fallback.
