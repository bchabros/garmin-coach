# ADR 0029 - The workout's name is the athlete's, and a date-free name is reusable

## Status

Accepted. Amends the replace clause of [ADR 0013](0013-phase-11-workout-authoring-and-push.md)
and extends the naming discussion of
[ADR 0019](0019-confirm-token-separate-from-gc-hash.md).

## Context

Every pushed workout was named `GC {date} {session_type}` by the author, with nothing in
the request able to influence it. On the watch and in the Connect calendar a 10 km
threshold block and a 4x2 km interval session both read "GC 2026-07-30 tempo", so the
athlete had to open the steps to tell them apart, or rename each one by hand in Garmin
Connect (issue [#58](https://github.com/bchabros/garmin-coach/issues/58)).

The original spec kept the `GC` prefix and the date mandatory on two grounds: the prefix
is how a coach-owned workout is recognised on the account, and the date keeps names
unique so the account lookup's name fallback cannot match two workouts and trip the
ambiguity refusal. The athlete overruled the first and the second turned out to be a
feature, not a constraint:

- Ownership is already carried by the `gc-hash:` tag written into the workout
  description. The name is decoration next to it, and the acceptance check that read the
  prefix can read the tag instead.
- `spec_hash` covers name plus steps, and the push path already schedules an existing
  workout, rather than uploading a second copy, when the same fingerprint reappears on a
  new date. A name that embeds the date makes that impossible by construction; a name
  without it makes the same steps *one* workout the athlete can put on several days -
  which is exactly what they asked for ("jakby użyć tego treningu więcej niz raz").

That left one hazard. Replacing a workout was unschedule + delete + upload. For a
workout on several days - including days the athlete scheduled by hand in Connect, which
no receipt knows about - the delete silently empties every one of those days.

## Decision

- **The name belongs to the athlete.** A request may carry `label` (the part after
  `GC <date>`) or `name` (the whole name), never both. With neither, the session type
  names it as before. Both are trimmed and refused when empty, when they carry a line
  break or control character, when they exceed their cap, and - for a label - when it
  starts with `GC ` (the prefix is added, not repeated).
- **The coach proposes a label** for sessions with a shape worth naming, in the language
  the athlete is speaking or the watch's when they ask; easy runs and rest keep the type.
  This is skill prose, not code.
- **Ownership is the tag, not the prefix.** `gc-hash:` in the description identifies a
  coach-authored workout; the manual acceptance check reads it.
- **A date-free name is a reusable workout.** No new mechanism: the fingerprint already
  decides. `author_workout(reuse_from=<date>)` copies a day's spec onto another date,
  re-running that date's date guard and plan guard, and the push then schedules the
  existing workout rather than uploading a duplicate. `get_pushed_workouts` lists the
  receipts so the coach can find the session the athlete means.
- **A replace deletes only a one-day workout.** If the account's workout is named
  `GC <the requested date> ...` it is deleted as before. Otherwise it is taken off the
  requested date and kept, and the new version is uploaded beside it. The preview says
  which of the two will happen, and warns - suggesting a new name - when the library will
  then hold two workouts of the same name.

## Consequences

- The watch finally says what the session is, and a session the athlete repeats exists
  once on the account with several dates.
- Renaming is a change: a new name is a new fingerprint, so the preview reports
  `replace` and the athlete confirms it. Garmin's API has no rename, so a rename is
  unschedule-and-upload however it is dressed up; making it an ordinary replace keeps
  one path and all its safety checks.
- The library can accumulate old versions of a reusable workout, because the safe answer
  to "this may be on days I cannot see" is to keep it. The same-name warning is the
  nudge to name the new version; housekeeping stays the athlete's, in Connect.
- A workout the athlete renamed by hand in Connect is now kept on a replace too - its
  name no longer carries the date, and that is precisely the case where the receipts
  cannot know which days it sits on.
- The name fallback in the account lookup can match two same-named workouts and refuse
  as ambiguous. That refusal already existed and is why the warning is worded as it is.
- Taking a workout off a single day, without replacing it, is still not possible from
  chat (issue #74).
