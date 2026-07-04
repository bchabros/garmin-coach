# ADR 0001 - Phase 1 incremental sync semantics

## Status

Accepted

## Context

Phase 0 provides idempotent backfill, but it has no durable notion of daily progress.
Phase 1 needs `garmin-coach sync` to run repeatedly, pull only missing data, and
survive transient Garmin failures without losing successful work from other streams.

## Decision

- Track progress as a per-stream watermark in `sync_state`.
- Bootstrap a missing watermark from the relevant core table's maximum date.
- If a core table is empty, bootstrap that stream to `DATA_START_DATE - 1`.
- Advance a stream watermark only to the last date successfully processed by that stream.
- Keep daily streams on per-day transport calls in Phase 1.
- Add retry and isolation per daily stream/day.
- For activities, try a whole-window range call first and fall back to one-day range calls if the whole-window call fails after retry.
- Treat partial success as a successful CLI run when at least one stream progresses, while surfacing warnings for failed streams or days.

## Consequences

- Repeated sync runs converge as transient failures clear.
- One failed stream does not block other streams from progressing.
- Some streams may temporarily have different watermarks; this is expected.
- Automation can continue after useful partial progress, while hard failures still return a non-zero exit code.
- The design avoids adding Garmin range endpoints for daily streams until there is a measured need.
