# memory/

Long-term context about the athlete: the qualitative material that **does not fit in the
DB** -- goals, tendencies, coaching decisions, preferences, open threads. SQLite holds the
numbers; this folder holds the coach-athlete thread, so a new session can pick it up
without starting from zero.

This README is tracked. Nothing else here is: the profile is the athlete's own prose and
stays private, like `plans/`.

Files:
- `athlete-profile.md` -- the profile itself (goal, physiology, tendencies, decisions,
  preferences, open threads).

## Rules

- Markdown, written in the athlete's language (Polish), read at the start of a session and
  appended to as facts appear. This README is the exception: it documents conventions, so
  it is English like the rest of the repo's docs.
- Numbers always come from the DB. The profile summarises them, it never replaces them.
- One fact, one place. When cleaning, merge duplicates and correct what has gone stale.

## What belongs here

One test decides it: **could the pipeline compute this?** If it could, it belongs in the DB,
and a copy here is a second version that rots.

- **Judgements the data cannot make.** "Sensitive to heat", "rest days are not rest",
  "does not feel Friday's fatigue, but Saturday's readiness disagrees" -- the reading of a
  pattern, not the pattern itself.
- **Decisions, with the reason attached.** "Pace-sensitive sessions go on fresh legs" is
  worth little without its why; the why is what survives when the situation changes and the
  decision has to be re-taken.
- **What was tried and what it did.** A lighter Friday producing a clearly better Saturday
  is the most expensive knowledge in this folder: it took weeks of observation and no query
  returns it.
- **Decisions still open.** A session swap under consideration, an event being weighed.
  `get_events` holds what is confirmed; the thinking around it has nowhere else to live.
- **Constraints from outside training.** Travel, work peaks, gym access, equipment. The DB
  knows none of it, and it explains weeks that otherwise look like lost discipline.
- **How the athlete wants to be spoken to.** Language, length, charts, bluntness.

## What does not belong here

- **Anything already in `snapshot.json` or `digest.json`.** VO2max, HRV baseline, RHR, zone
  ceilings, ACWR, heat acclimation, training status -- all recomputed nightly. A figure
  copied here is out of date the day after it is written. Keep the interpretation ("above
  ~5:30/km it drifts into Z3"), drop the value.
- **Anything the DB decides.** The clearest failure this folder has produced: the profile
  stated there was no race date and therefore no periodization, while the digest carried a
  confirmed Hyrox on 2026-10-17, twelve weeks out (issue #52). Where the two disagree the
  DB wins, and the profile line is the one to fix.
- **Single sessions, niggles, RPEs.** Those are rows (`log_niggle`, `log_rpe`, the activity
  tables). What belongs here is the pattern they add up to.
- **The training week.** That is `plans/<monday>_week.md`, which the engine reads.

## Contract: the date line

Below the title, `athlete-profile.md` carries the date it was last updated: the line opens
with `_Ostatnia aktualizacja:`, then `YYYY-MM-DD`, then usually the athlete's own note on
which data the profile summarises. That date is how the coach skill knows the profile's age,
so an amendment replaces the date alone and leaves the note as they wrote it.

Without the line nothing can tell that the profile has gone stale, which is why
`tests/test_coach_skill_profile.py` holds every copy of it -- the router's section, this
note, and (where the file exists) the profile's own line: an edit that drops the line
fails `task check` instead of failing silently in a conversation months later.

## Open threads

A thread the coach can act on carries the date it comes due, so an overdue one gets named
instead of quietly re-read: `- [do 2026-07-13] policzyć realizację tygodnia 06-12.07`
(`do` being the athlete's "by"). Any legible date works -- the skill reads prose, not a
format -- but a consistent shape makes an expired thread hard to miss.

## What the coach skill does with this file

The source of truth is the `## The athlete profile` section of `skills/coach/SKILL.md`:
when the profile is read, how its age is judged, and how an amendment is proposed. Two
things are worth knowing before opening this folder:

- **Numbers come from the DB** -- the profile supplies qualitative context, never a value.
- **Writing needs consent** -- the skill shows the exact lines and waits for an explicit
  yes; a declined offer leaves the file byte for byte as it was.
