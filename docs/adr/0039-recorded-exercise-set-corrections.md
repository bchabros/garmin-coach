# ADR 0039 - Correct recorded exercise sets against reviewed account evidence

## Status

Accepted design ([#117](https://github.com/bchabros/garmin-coach/issues/117),
2026-10-31). Cached previews, correction baselines, retirement history and historical
rechecks and approval-bound confirmation are implemented. Add/delete transport-format
verification and operator acceptance remain pending; #117 stays open.

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
- Build from the complete freshly read account payload, preserving opaque fields,
  rests and captured timing for surviving sets. Propose and show retained/new/deleted
  sets and their alignment with the athlete's correction, allowing alignment changes
  before approval. Bind approval to the activity, reviewed account/manual evidence and
  proposed alignment/changes. Re-read immediately before writing; a relevant change
  requires a fresh preview and approval. Never automatically retry an uncertain write
  or restore an old whole payload over a possible concurrent app edit.
- Add/delete support requires verified source evidence for added-set fields, count
  changes and timing, with unrecorded time explicit. Unsupported timing refuses a
  production preview; zero duration is not an established contract. Retained sets do
  not lose watch timing merely because their names, repetitions or weights change.
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
its personal records. Synthetic guesses, another project's implementation and passing
offline tests do not establish that acceptance. Before closing #117, an explicitly
authorized operator must establish added/deleted-set payload evidence and inspect the
Garmin UI and surviving timing. These acceptance gates and all implementation tickets
remain open in #117. No duplicate-activity upload or deletion of the original recording
is a fallback.
