# ADR 0023 - A run session type is a default shape, not a permitted shape

## Status

Accepted

## Context

ADR 0013 gave the workout request a `structure` override, and ADR 0020 gave every role
it offers an intensity target. Both were bounded by one table: per run session type, the
step roles that type accepts. `easy` listed `work`; `tempo` listed `warmup`, `work`,
`cooldown`; `quality` added `recovery` and a repeat count. The accepted structure keys
were derived from that table, so a key naming a role the type did not list was not
defaulted away - it was refused.

Issue #61 recorded the cost twice in one week. On 2026-09-03 and again on 2026-09-09 the
plan of record said `easy`; the athlete asked for the ordinary shape of that session -
a warm-up on the lap button, the block, a cool-down on the lap button - and authoring
refused it with `unknown structure keys for easy: cooldown_end, warmup_end`. Both
workouts went to the watch as a bare block, and both warm-ups and cool-downs were run
outside the workout and recorded nowhere. Nothing about either session was harder than
the plan; only its shape was unavailable.

The table was never a physiological claim. Nothing about an easy run forbids a warm-up,
and nothing about a tempo run forbids repeats - `4 x 8 min at threshold with 2 min float`
is an ordinary tempo session that had to be declared `quality` to be authored at all. The
table encoded the most common shape of each type and then made it the only one.

A second gap sat in the same table. The only pause between run repeats was `recovery`,
which the watch runs as a jog. Standing rest existed only in the exercise sports, between
sets, so a run session with standing rest between repeats had to be lied into a jog or
pushed through `hiit`.

## Decision

- **One role vocabulary for every run session type.** `warmup`, `work`, `recovery`,
  `rest` and `cooldown` are the roles a run request may shape, and a repeat count applies
  to any of them. The per-type table stops being an allow-list and becomes a *default*
  table: which roles the type expands, and at what length and target, when the request
  shapes nothing. What a request may ask for is derived from the vocabulary; what it gets
  in silence is derived from the type.

- **Any of a role's keys summons it.** An end, a minutes alias, or a target alone is
  enough. A role the request never mentions appears only when the type defaults it, so
  every request that authored before this change authors identically after it. Run order
  comes from the vocabulary, not from the order the request happens to nest its keys in.

- **One shared default length for a summoned role**: warm-up 10 min, cool-down 10 min,
  recovery 2 min, rest 2 min. These are the values `tempo` and `quality` already used, so
  no existing default moves, and a warm-up is the same length whichever type summons it.
  Work keeps a per-type default, because 45 minutes of easy running and 3 minutes of
  interval are not one number.

- **Standing rest is a step role, not a session type.** The word `rest` already names a
  session type (a day off, which yields no spec) and a spec step kind (between sets in the
  exercise sports). The step role reuses the step kind: the two live on different axes,
  and inventing a third word for the same thing on the watch would be worse than the
  collision. Its translation is a recovery step restamped with the rest step type, the
  descriptor the exercise sports already push - garminconnect ships no rest builder.

- **The repeat block is keyed on the structure, not on the session type.** A request
  carrying a repeat count folds the work step and its pause into a repeat group on any
  type. A pause asked for explicitly replaces the type's default pause, so a `quality`
  session asked for standing rest runs standing rest rather than both.

- **Two refusals, because both are near-certainly mistakes.** A pause on a type that
  neither defaults nor was given a repeat count is a repeat session written without its
  count - authoring one work step and one dangling pause would silently misread it. A jog
  recovery and a standing rest at once leaves no single answer to what happens between
  repeats. Each error names the key that caused it.

- **`work_min` is the spelling on every type.** `easy` spelled its work length
  `duration_min` from a time when it was one block; that spelling stays accepted, as the
  older one, and setting both is refused the way an end and its minutes alias already
  clash.

- **The list-shaped sessions stay outside this table.** The exercise sports (`strength`,
  `hiit` carrying `hyrox` and `crossfit`) expand from `structure.exercises`, and the Hyrox
  run-station sequence from `structure.stations`. They are lists of what to do, not sets
  of roles, and bending the role table around them would produce one mechanism that
  serves neither. Warm-up and cool-down for the exercise sports is issue #65.

## Consequences

- The athlete can put any shape of run on the watch under the session type that names it
  honestly, and the 2026-09-03 and 2026-09-09 sessions author under `easy`.
- A threshold session shaped as repeats no longer has to be called `quality`, which is
  half of what issue #62 needed; the other half - the plan guard reading a name rather
  than the targets - is ADR 0024.
- The glossary's *structure override* entry loses "a role a session type does not have
  accepts neither"; *session type* now means default shape and default targets.
- Removing a role a type defaults is still not expressible. Nothing asked for it: the
  reverse gap - a role that could not be added - is what this ADR closes.
- Extends ADR 0013 (the structure override) and ADR 0020 (targets on every role) rather
  than amending either. No request valid before this change becomes invalid, and no
  existing spec changes by a byte.
