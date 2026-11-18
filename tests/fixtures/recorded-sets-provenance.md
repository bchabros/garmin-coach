# Recorded exercise-set source shapes

The source contract comes from the explicitly authorized operator experiment in
[#117](https://github.com/bchabros/garmin-coach/issues/117). Raw operator captures
remain private. Activity identifiers, dates, durations, repetitions and recognition
confidence values in these fixtures are deliberately artificial.

- `exercise_sets_candidates.json` retains the watch-origin shape: several recognition
  candidates, null generic variant names, subsecond work/rest durations and opaque
  nullable fields. A repetition-only correction must retain this complete shape.
- `exercise_sets_added_request.json` retains the approved request shape for appending
  one explicitly reported generic Lunge: null duration, no invented start/identifier,
  bodyweight zero, and native empty REST scaffolding with weight -1 as an unknown
  sentinel. Existing source entries remain complete.
- `exercise_sets_added.json` retains the corresponding accepted source shape, including
  server-supplied nullable fields and the explicit generic category with null variant.
  Removing the introduced work/rest tail returned the original source facts; the
  original fixture also represents that returned shape.

These are source-shape regression inputs, not real athlete measurements. Offline
SQLite/fake-account tests exercise the shared preview/confirmation and adapters;
they do not establish live account or UI acceptance. Dated operator outcomes and
client/transport versions belong to the issue/PR, and ADR 0039 owns the decision.
