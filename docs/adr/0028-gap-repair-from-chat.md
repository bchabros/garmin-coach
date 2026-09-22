# ADR 0028 - Gap repair from chat: a second transport tool, bounded and previewed

## Status

Accepted. Annex to [ADR 0014](0014-coach-mcp-server.md), alongside the
`get_workout_status` annex; builds on the re-check window of
[ADR 0026](0026-recheck-window.md).

## Context

The golden rule keeps the coach layer away from live Garmin: every metric is computed
from the finished DB, and the nightly ETL is the only routine that pulls. ADR 0014
admitted exactly one transport tool, `refresh_today`, for today's partial data, and its
annex admitted a second read, `get_workout_status`, against the workout account.

A data gap still needed a human at a keyboard. On 2026-09-13 the athlete pasted
`garmin-coach backfill --from 2026-09-11 --to 2026-09-12` because two days were missing
(issue [#72](https://github.com/bchabros/garmin-coach/issues/72)), and the same session
produced a weekly review that read a not-yet-uploaded Saturday run as a deload.

ADR 0026's re-check window closed most of that: the trailing three days are re-pulled
every night, so a late upload lands by itself. Two cases survive it:

- an upload later than the window - a watch not synced for four days or more;
- a day the athlete wants complete **now**. The review written on Sunday morning cannot
  wait for Monday's nightly run, and `refresh_today` pulls only today, not yesterday.

Without a chat-side repair, the unconfirmed-day warning introduced with this issue would
end every time in "open a terminal", which is the friction the coach MCP exists to remove.

## Decision

- **A repair is a preview and a confirm.** `repair_preview` reads only the DB and
  reports, per day of the range, the stored activity count, which daily streams
  answered, and what the plan of record expected. `repair_confirm` is what contacts
  Garmin.
- **The token covers the range and the per-day state.** A nightly run that fills the gap
  between preview and confirm makes the token stale, so a pull never runs against a
  picture that has moved. Same job as the push handshake's `confirm_token`
  (ADR 0019), same 16-hex shape, and nothing is stored.
- **Bounded to finished days.** At most 14 days per call; `to_date` on or after today is
  refused (that is `refresh_today`'s job), as is a start before `data_start`.
- **The range is pulled whole**, through the same `backfill` path the CLI uses: every
  stream, every day. A day missing only its sleep row is repaired too, and the rule
  stays one sentence the athlete can hold.
- **Watermarks are never written**, exactly as `refresh_today` never advances them: the
  nightly sync keeps owning what it has treated as final.
- **The marts are rebuilt in the same call**, so the read that follows the repair is
  already current.

## Consequences

- The coach can close a gap the athlete just noticed, in the conversation where they
  noticed it, and the unconfirmed-day warning now has an action attached.
- A third code path reaches Garmin. It is one call, range-bound, preview-gated, never
  scheduled and never looped; the routine pull stays with the nightly run.
- The repair re-fetches days that were already complete when the range spans them. That
  is a handful of API calls against a rate limit the nightly run lives inside every day,
  and it is what keeps the rule simple.
- A pull that fails mid-range leaves the days it did store. The next preview shows
  exactly that, and the repair is idempotent, so running it again is the fix.
- `backfill` remains available in the terminal for the onboarding load and for ranges
  wider than the cap.
