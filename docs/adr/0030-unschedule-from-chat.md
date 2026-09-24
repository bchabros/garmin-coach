# ADR 0030 - Taking a pushed workout off a day from chat

## Status

Accepted. Annex to [ADR 0014](0014-coach-mcp-server.md) (a third preview/confirm pair
that reaches Garmin) and an extension of the replace clause of
[ADR 0029](0029-athlete-owned-workout-names.md).

## Context

Since ADR 0013 the coach can put a workout on a day, and since ADR 0029 the same
workout can sit on several days. The reverse move had no chat-side path: when a
session was called off (illness, a moved day) the athlete removed the calendar entry by
hand in Garmin Connect, the day's push receipt kept claiming the workout was scheduled
until a status read noticed, and a plan re-import kept naming the day as one whose
workout the plan no longer allowed - asking for a re-author of a session that was no
longer on the watch (issue [#74](https://github.com/bchabros/garmin-coach/issues/74)).

Two things made the shape of the answer less obvious than "call `unschedule`":

- The receipts on disk are the only record of what the coach put where, and the annex
  to ADR 0014 settled that a receipt's own fields are never mutated - "we pushed it and
  it worked" describes an event that did happen. A removal is a second event on the
  same day, and it has to be recorded without rewriting the first.
- The replace rule of ADR 0029 deletes a one-day workout from the library. A removal
  looks like the same operation minus the re-upload, which made "delete it too" the
  tempting default.

## Decision

- **A removal is a preview and a confirm**, like the push and the repair.
  `unschedule_preview` reads the day's receipt for the workout and the account for what
  it still holds - the library entry by the receipt's id, the day's calendar entries -
  and hands over a token. `unschedule_confirm` removes those entries.
- **The preview reads the account.** A workout the account no longer holds, or one
  already off the day, is refused with a plain reason and no token: a token must never
  be handed over for a removal that cannot happen. The cost is one login per preview,
  which `push_preview` already pays.
- **The token covers the day, the workout id and the calendar entries the preview
  showed** - the same 16-hex shape as the push and repair tokens, nothing stored. A
  workout re-pushed, moved or removed by hand between the two calls makes the token
  stale, and the confirm refuses without touching the calendar.
- **The library is never touched.** No delete, whatever the name. A date-free workout
  may be on days no receipt knows about (ADR 0029); a date-named one left behind is a
  harmless stray the next push schedules back without a second upload - which is
  exactly how a called-off session returns after a sick week. The replace rule's delete
  was justified by a new version going up beside the old one; nothing goes up here.
- **The receipt keeps saying what the push did.** The confirm appends `unscheduled_at`
  and writes the `reconciled` block as an `unscheduled` finding beside the untouched
  push fields. A later push rewrites the receipt whole, so the marker leaves with the
  event it described.
- **Offline readers honour the event.** The plan-divergence read returns nothing for a
  receipt carrying `unscheduled_at` - covering `get_workout_status`, `plan_import` and
  `plan_confirm` at once - and the pushed-workouts listing carries the timestamp. Keyed
  on the recorded event rather than on the last reconciliation state, so the offline
  answer is deterministic.
- **A day with no receipt costs no login.** Both tools take a publisher factory, as
  `get_workout_status` does; the receipt is read first.
- **No date guard, and no CLI command.** Any day the receipt names can be cleared -
  today for an illness on the day, a past day for a missed session the athlete wants
  off the calendar - because removing a calendar entry cannot touch recorded training
  data. The orchestration sits in the publish module beside the push; the gap was a
  conversation gap, and a CLI command is one screen away if ever wanted.

## Consequences

- A called-off session is two calls in the conversation, and putting it back is the
  ordinary push pair.
- A fourth path reaches Garmin from chat (after refresh, repair and push): one library
  read plus one calendar read per call, and one calendar write per entry on the confirm.
- Every entry of the workout on that day goes, including one an earlier half-failed
  push doubled; entries of other workouts on the day stay.
- A removal that fails midway leaves what it removed and says so; the next preview shows
  what is left, and running it again is the fix.
- A workout the athlete unscheduled by hand still reports a plan divergence until a
  status read runs, as before this decision: only the coach's own removal is an event
  the receipt records.
- The library accumulates strays the coach never cleans up. Housekeeping stays the
  athlete's, in Connect, as ADR 0029 already accepted.
