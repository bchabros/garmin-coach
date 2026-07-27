# ADR 0021 - Nothing harder than the plan of record reaches the watch

## Status

Accepted

## Context

Issue #22 recorded what the system allowed. On 2026-07-15 at 17:28 the recommender
authored and pushed `GC 2026-07-17 quality` to Garmin as `workout_id 1632796265`:
10 min warm-up + 4x3 min at HR 164-173 with 2 min recovery + 10 min cool-down. The
plan of record for that week, saved hours earlier, had already downgraded Friday
17.07 from `quality` to `easy 10 km, HR <145`, to break a Thursday-then-Friday pair
of hard days. The workout went on the watch and contradicted a coaching decision
made the same afternoon.

Nothing in the write path compared the two. `author` derives its session type from
`recommend()`, which starts from the resolved planned intent and only ever softens
it - so a recommender-origin session cannot outrank the plan by itself. Every other
way into the path could: an explicit athlete request naming a session type, and any
spec pushed after the plan it was authored against was revised. The idempotency
marker could not catch the second case either. `spec_hash` covers a workout's name
and steps, deliberately not the plan or the date (ADR 0013, ADR 0019), so a receipt
stays `applied: true` and the workout stays scheduled no matter what the plan later
says.

ADR 0015 had already settled where "what was planned" comes from: one resolver,
authored `plan_week` first and the static `plan_template` as fallback, always
naming which answered. What was missing was an ordering - the resolver says *what*
was planned, not whether a proposed session is *more* than that.

## Decision

- **The hardness ladder is part of the plan vocabulary.** `INTENT_RANK` moves from
  `recommend._INTENT_RANK` to `core.plan`, beside the `INTENTS` it ranks
  (`rest 0 < easy 1 < tempo = strength 2 < hyrox = crossfit = quality 3`). The
  recommender's softening and the guard's refusal are the same ladder read in
  opposite directions, so they cannot drift apart into two different notions of
  which session is harder.

- **Harder than the plan is refused; softer is not.** The guard fires on strictly
  greater rank, never on inequality. A session below the plan is the sanctioned
  downgrade the recommender produces on any day with a stress signal, and refusing
  it - or reporting it as a divergence - would fire on the system's normal
  operating mode and be ignored within a week.

- **The same check runs at authoring and again at push.** `author` refuses a
  request above the plan of record for its date; `publish` repeats the check with
  the plan resolved at push time. The second is not redundant: a spec written on
  Monday and pushed on Wednesday was judged against a plan that may no longer
  exist, and the author-time guard cannot see a revision that happened after it
  ran. `--replace` does not override either - it exists to overwrite a *different
  workout*, not to outrank a coaching decision.

- **The confirm token covers the plan of record.** Following ADR 0019's split, the
  plan joins name, steps, and date in `confirm_token`, and stays out of `spec_hash`.
  A plan revised between preview and confirm invalidates the preview exactly as
  retargeting the spec does; account-side idempotency is untouched.

- **The guard reads the resolved intent, template included.** A week with no
  authored plan is guarded by `plan_template`, which is the same value the
  recommender was already bounded by. The alternative - guarding only authored
  weeks - would leave every unplanned week open to the exact push this ADR
  refuses, and would make the guard's presence depend on a file existing. The cost
  is that an unplanned week must be planned before a harder session can be
  authored, which `plan_preview` / `plan_confirm` make a single call.

- **Report the divergence, never repair it.** `get_workout_status` gains
  `plan_divergence` and both plan-ingestion paths gain `invalidated_pushes`;
  neither deletes, unschedules, or rewrites anything on the account. Silently
  mutating what is on the watch is the class of action this system avoids - push
  is already preview/confirm gated - and the failure this ADR addresses was
  precisely a write nobody asked for. The athlete re-authors and re-pushes; the
  system only makes sure they know.

- **A divergence is reported when it is created, not only when it is asked about.**
  Revising a week is the moment an already-pushed workout can become too hard, so
  `plan import` and `plan_confirm` check that week's dates against the receipts and
  name the conflicting days. Waiting for someone to ask about that date is what put
  the wrong session on the watch on the morning of.

## Consequences

- The receipt gains `session_type` and `planned_intent`: what was pushed, and the
  plan it was measured against. Receipts written before this change carry neither;
  the divergence check falls back to the local spec's session type, which is
  correct unless that spec has since been re-authored.
- A session harder than the plan is now unreachable without changing the plan
  first. For a week with a plan file that is a manual edit plus `plan import`
  (ADR 0015 keeps revisions manual, because the file holds prose the intent
  vocabulary cannot); for an unplanned week, `plan_confirm` writes one.
- `plan_divergence` needs no Garmin call - the plan is in the DB and the receipt is
  on disk - so it is answered even when `reconciled` degrades to `unverified`.
- Extends ADR 0013 (authoring and push), ADR 0015 (plan of record), and ADR 0019
  (the confirm token). No spec, receipt, or plan file valid before this change
  becomes invalid.
