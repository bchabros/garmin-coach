# ADR 0030 - Taking a pushed workout off a day from chat

## Status

Accepted

## Context

Taking a workout off one day must not delete a reusable library workout or its other dates.

## Decision

- Account-reading preview/confirmation binds the date, workout ID and all displayed
  calendar entries. No receipt means no login; missing/already-unscheduled workout
  yields a refusal without a token. Changed account state invalidates preview.
- Remove every entry for that workout on the selected day only; never delete the
  library, regardless of naming. Past/today dates are allowed; no general date guard
  or new CLI removal command is added.
- Append unscheduled_at and reconciliation beside untouched push-event fields.
  A later push rewrites its receipt and clears the marker.
- Suppress offline divergence after recorded removal until status sees scheduling
  again. A hand removal is known only after an account status read.
- Partial removal stays explicit; retry only from a fresh preview.

## Consequences

Calendar removal has a contained confirmation handshake. It cannot erase other
scheduled dates or falsify the history of the original push.
