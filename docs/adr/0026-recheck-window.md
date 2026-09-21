# ADR 0026 - A day Garmin has answered for is not final: the re-check window

## Status

Accepted. Amends the watermark clause of [ADR 0001](0001-phase-1-incremental-sync.md).

## Context

ADR 0001 says: "Advance a stream watermark only to the last date successfully processed
by that stream." An empty response is a successful call, so a day whose run was still
sitting on the watch at 06:00 was stamped as synced and never asked about again
(issue [#69](https://github.com/bchabros/garmin-coach/issues/69)). On 2026-09-13 the
nightly run asked for 2026-09-11..2026-09-12, received an empty list, and moved the
activities watermark to 2026-09-12; the athlete's 7.41 km run of 2026-09-12 reached
Garmin Connect later that day and was recovered only by a hand-run `backfill`, after
the weekly review had already read the week as a deload.

The ADR did not consider that "successfully processed" and "the data existed yet" are
different things. The same gap applies to the daily streams, which Garmin itself fills
in late: a night's HRV lands after the night, and readiness is revised during the
morning. Since July, 12 of 72 activities reached the database later than the first
nightly pull after them, most because the night before had failed on login
(issue #71) or had not run at all.

## Decision

- **The re-check window** is the trailing days ending at the run's end date (yesterday
  by default). Every incremental sync pulls the whole window again, for the activities
  stream and for every daily stream.
- **Width: 3 days**, one transport setting (`sync_recheck_days`). The observed lateness
  was about a day; three leaves margin for a weekend without the phone, and lets the
  next nights make up a night that failed or never ran. A width of 1 is the old
  behaviour.
- **A day becomes final on its last pull.** The watermark is never written past the
  first day of the window, so a day is pulled on the three nightly runs after it and is
  recorded as final on the third. The watermark means "the last date treated as final",
  not "the last date asked about".
- **Start rule.** A stream starts from the earlier of the day after its watermark and
  the first day of the window, never before `data_start`. That makes the first run
  after deployment re-pull the window although the stored watermarks already sat at
  yesterday, and keeps a long absence a single catch-up pass.
- **A watermark never moves backwards.** A stored watermark ahead of the cap stays put
  until the cap overtakes it.
- **Re-enrichment is not special-cased.** Window activities have weather and exercise
  sets fetched again. The hand-logged set overlay (ADR 0022) is a separate table and an
  activity upsert does not cascade into it.
- Everything else in ADR 0001 stands: per-stream isolation, partial success, the
  range call with its per-day fallback, bootstrap from the core table. `backfill` and
  the same-day refresh (which already writes no watermark) are unchanged.

## Alternatives considered

- **Mark empty days in a table of their own** and re-ask about them until a late upload
  is implausible. More precise, but a second bookkeeping path, and it does nothing for
  the daily streams Garmin revises after the fact.
- **Do not advance the watermark on an empty day.** Does not work alone: the watermark
  bootstrap falls back to the newest stored activity, so a rest streak would be
  re-fetched from the last activity every night, without bound.
- **Only log a warning on an empty day.** Makes the gap visible, repairs nothing.

## Consequences

- About 23 Garmin calls a night instead of about 9 (three days of five daily streams,
  the activities range, two enrichment calls per window activity), a few seconds longer.
- `raw_payloads` grows by the window every night; that is its design (ADR 0018). Core
  tables upsert, so their row counts do not change.
- A re-pull replaces a day's core row with whatever Garmin returns now. That is what
  makes late fills arrive, and it is the same trust `backfill` has always placed in a
  re-fetch. A payload that came back `None` is skipped and never blanks a stored row.
- A run uploaded more than three days late still needs a hand-run `backfill`.
- A review written before a late run arrives can still be wrong for a day. Telling the
  coach that the trailing days are provisional is tracked in issue #72.
