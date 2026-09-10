# ADR 0024 - The plan guard reads what the watch will ask, not what the template is called

## Status

Accepted

Supersedes the "one ladder read in two directions" decision of ADR 0021; the rest of
ADR 0021 stands.

## Context

ADR 0021 stopped anything harder than the plan of record reaching the watch, and settled
where hardness came from: `INTENT_RANK`, the ladder over the planned-intent vocabulary
(`rest 0 < easy 1 < tempo = strength 2 < hyrox = crossfit = quality 3`). The guard ranked
the request's `session_type` on it. That field also chose the session's default shape and
its default targets (ADR 0013, ADR 0023), so one word answered three questions and the
guard could only ever compare names.

Issue #62 recorded what a name lets through, and what it wrongly stops.

A name can lie downward. An `easy` request whose work step carries a threshold band
(`4:25-4:35/km` against a Z2 ceiling of 5:14) passed the guard on a planned easy day: the
name said easy, and nothing looked at the band. That is the same write ADR 0021 exists to
refuse, arriving through the one door it left open.

A name can lie upward. Threshold repeats had to be declared `quality`, which outranks
`tempo`, so they were refused on a planned tempo day although they ask the body for what
the planned continuous block asks. ADR 0023 removed the shape half of that bind - repeats
are now expressible on any type - but the guard would still have refused the honest name.

The workaround for both was to edit the plan of record, which mis-records the day: the
week's plan-vs-actual and plan adherence would then show quality work on a day that was an
easy run.

The obvious minimal fix - a second field the athlete sets - reopens exactly what ADR 0021
closed, since a threshold session could then be labelled `easy` on a planned rest day.
Whatever replaces the name has to be at least as hard to lie to.

## Decision

- **Hardness is measured from the targets the spec will put on the watch.** An authored
  run spec carries `hardness`: `easy`, `threshold` or `hard`. It is derived from the
  resolved steps, so the number the athlete will see is the number that is ranked, and it
  cannot be asserted independently of the session.

- **Its own three words, on the plan's numeric scale.** `easy | threshold | hard` map to
  ranks 1, 2, 3 - the same scale the planned intents sit on, so "harder than the plan" is
  one comparison of two numbers. The words differ from the plan's on purpose: reusing
  `tempo` would label a threshold interval session with a template name, which is the
  confusion this ADR exists to end. `strength` and the station sessions keep their place
  on the intent scale; nothing about them is measured.

- **The hardest step decides, and within a band its harder edge decides.** Every targeted
  step counts, a warm-up as much as the work: a Z4 warm-up is Z4 whatever the session is
  called. The faster pace bound and the higher heart-rate bound are what the watch will
  allow, so they are what is ranked. Steps inside a repeat group are walked.

- **Boundaries come from the athlete's own ladder.** At or slower than the stored Z2 pace
  ceiling, or at or below the Z2 heart-rate bound, is `easy`; inside threshold pace by the
  authoring chain's own margin, or at or below the lactate-threshold heart rate, is
  `threshold`; faster or higher is `hard`. The margin is the chain's, not a new constant,
  so a session targeting the threshold band ranks `threshold` by construction rather than
  by luck. Heart rate is compared against LTHR rather than the Z4 upper bound because the
  stored Z4 ceiling sits below LTHR, and a threshold session written as an LTHR band would
  otherwise rank `hard`.

- **An untargeted work step ranks by the chain it will run at**; an untargeted warm-up,
  recovery, rest or cool-down ranks nothing. What the watch shows is what is judged, and
  a step with no target shows nothing to judge - except the work step, whose chain the
  athlete will run by default.

- **Absence is the source.** No `hardness` is written when there is no zone ladder, for
  the exercise sports, or for the Hyrox run-station sequence, and the guard then ranks the
  session type exactly as before. One rule covers those three cases and every spec written
  before this change; a `hardness_source` field would only restate what the absence says.
  The no-ladder case also warns on the spec, because falling back is a decision the
  athlete did not make (the ADR 0020 rule for warnings).

- **Measured at authoring, read at push.** `publish` reads the spec's stored value rather
  than re-deriving it: the spec is frozen, so its hardness is frozen with it, and a push
  re-judges the same session against a plan that may have moved - never against zones that
  have. The receipt carries `hardness` beside `session_type` and `planned_intent`, and
  divergence reporting compares it when the receipt has one.

- **Out of both hashes.** `hardness` is a function of the steps and the ladder, so it adds
  nothing to `spec_hash` (account-side idempotency) or `confirm_token` (ADR 0019). A
  re-authored spec with unchanged steps is still a no-op push.

- **The recommender keeps descending the intent ladder.** It works from the plan's words
  and has no steps to measure, so it cannot use this. ADR 0021 made the two directions one
  ladder to stop them drifting; they are now one *scale* reached two ways - words for what
  was planned, measurement for what was authored - which keeps the property that mattered:
  a single ordering both sides agree on.

- **Adherence is untouched.** Plan-vs-actual compares the plan's word with the class the
  load shows. The spec has no seat in that comparison and gains none here.

- **The refusal names what decided.** At authoring: `2026-09-03 is planned as easy; the
  work step at 4:25-4:35/km is threshold - harder than the plan of record.` At push, where
  the evidence is the stored measurement rather than a fresh reading, the session is named
  instead of a step.

## Consequences

- The 2026-09-03 and 2026-09-09 sessions author and push under `easy` against a planned
  easy day, with no plan edit.
- Two behaviours change in opposite directions, both deliberately: an `easy`-named session
  at threshold pace on a planned easy day is now refused where it used to pass, and
  threshold repeats named `quality` now pass on a planned tempo day where they used to be
  refused.
- The stored threshold pace is currently a fallback multiplier, not a measurement
  (`threshold_pace_fallback+lthr`), so a session written faster than it ranks `hard` even
  when the athlete means it as threshold work. The guard is only as honest as the ladder;
  regression-backed zones are issue #13's first blocker, and this ADR makes the cost of
  that gap visible rather than hiding it behind a name.
- Specs and receipts written before this change are not rewritten and not re-ranked; they
  keep being guarded by session type. A spec re-authored later gains the field.
- Extends ADR 0013 and ADR 0023 (what a session type means) and amends ADR 0021 (what the
  guard reads). No spec, receipt or plan file valid before this change becomes invalid.
