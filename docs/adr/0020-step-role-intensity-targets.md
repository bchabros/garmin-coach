# ADR 0020 - Intensity targets belong to every step role, and a zone name means heart rate

## Status

Accepted

## Context

ADR 0013 gave the workout request a `structure` override: per step role
(`warmup | work | recovery | cooldown`) an end condition, plus - on the work step only -
a custom pace band. So the athlete could say *when* a step ends but, everywhere except
the work step's pace, never *how hard* it should be.

Issue #24 recorded the cost. On 2026-07-16 the athlete asked for a warm-up run in Z2
ending on the lap button and a Z2 cool-down likewise. The lap-button ends were honoured,
because a key existed for them; the Z2 targets were dropped, because none did. The steps
were pushed untargeted and the athlete added the heart-rate bands by hand in Garmin
Connect. Nothing recorded the loss: the only target-related warning inspects the work
step's pace band, so the spec looked correctly authored.

The obvious minimal fix - two keys, `warmup_target` and `cooldown_target`, taking `z2` or
nothing - would have left the vocabulary incoherent in two ways. The work step reaches a
heart-rate band only by *degradation*, when no measured pace is available, so the athlete
could have asked for heart rate on a warm-up but not on a threshold interval. And the
structure would then express intensity two incommensurable ways: numerically for work
(seconds per km), by name for warm-up.

Three facts about the stored zones bound what is expressible at all. `athlete_zones`
holds four heart-rate upper bounds (`z1_hi_bpm` through `z4_hi_bpm`), so a band is formed
from a pair of *adjacent* bounds and the outer zones are open-ended - Z1 has no floor and
Z5 no ceiling. No athlete-level maximum heart rate is stored anywhere; the `max_hr` column
on activities is a per-activity maximum, not a physiological ceiling. And pace holds two
anchors only (threshold pace, the Z2 pace ceiling) - no ladder.

## Decision

- **One target vocabulary, applied to every role.** Each role a session type offers gains
  a `<role>_target` key of one shape: the word `none`, a zone name, or a single-key band
  (`hr_band` in bpm, `pace_band` in seconds per km). The band names are the ones the
  workout spec already uses, so translating a request into a spec stays close to identity
  and the glossary gains no third name for one concept. `work_target` therefore exists
  too; the older `work_pace_band` stays valid as its narrower spelling, and setting both
  is refused the way an end condition and its minutes alias already clash.

  The roles a session type accepts are **derived from the role table** that already drives
  the end conditions, not restated in a second list. A future role cannot then arrive with
  an end condition and no target. This is why `easy` gains `work_target`: it has a work
  role, and excluding it would need exactly the hand-maintained exception the derivation
  exists to avoid.

- **A zone name always means heart rate.** `"z2"` is never read as the stored Z2 pace
  ceiling. The ladder in `athlete_zones` is a heart-rate ladder with five rungs; pace has
  two anchors and no ladder, so a named pace zone would imply rungs that do not exist.
  Targeting by pace is the explicit `pace_band` form, which is also the only honest way to
  say it, since only two pace numbers are known.

- **The outer zones are reachable only as an explicit band.** `"z1"` and `"z5"` are
  refused as names, with an error naming the missing floor or ceiling and pointing at
  `hr_band`. The alternative - inventing a Z1 floor from resting heart rate, or a Z5
  ceiling from a multiple of threshold - would put a number on the athlete's watch that
  nobody measured. An explicit band expresses the same intent and reads nothing from the
  database, so the capability is not lost, only the shorthand.

- **A warning reports a decision the athlete did not make.** An explicit target that
  resolves is silent; a warning list that reports successes stops being read, which is how
  the 2026-07-16 loss went unnoticed in the first place. A named zone the ladder cannot
  bound warns - naming the key, the zone, and the unavailable band - and authors the step
  with no target rather than failing the session. The work chain's pace-to-heart-rate
  degradation keeps its own wording, because there a degradation genuinely happened.

  One warning survives that rule by not being about resolution at all. A work pace band
  clearly faster than the recommender's suggestion is still cited, under either spelling.
  It does not report that the athlete failed to get what they asked for - they got exactly
  it - it reports that the pace they asked for is harder than the day's advice. Silencing
  it for `work_target` alone would let the newer spelling escape a check its older synonym
  triggers, which is the kind of silent hole this ADR exists to close.

Default values are deliberately **not** recorded here. An absent key preserves today's
behaviour on every role, and that is ordinary behaviour, reversible in one line, not an
architectural commitment.

## Consequences

- The athlete can express any intensity on any role, including the outer zones, without
  the system ever deriving a target from a bound it has not measured.
- Extending the nameable zones later is additive: storing a Z1 floor and a Z5 ceiling
  would turn two refusals into two more table entries, with no change to the value shape.
- Cadence, power, grade, and the other Garmin target types remain out of reach. The spec
  speaks three target shapes against Garmin's ten, and the rest need new ingest and new
  translation, not a new key.
- Extends ADR 0013's structure override rather than amending it: every request valid
  before this change is still valid and authors identically.
