# ADR 0019 - The confirm token is not the gc-hash

## Status

Accepted

## Context

ADR 0013 introduced `spec_hash` as the idempotency marker: a short hash of the
canonical spec (its name and steps), written into the Garmin workout description as
`gc-hash:...` so a re-push of the same workout resolves to a no-op instead of a
duplicate. ADR 0014 then reused that same value as the token of the MCP
preview -> confirm handshake: `push_preview` returns it, `push_confirm` refuses
anything else.

Issue #37 found that one value cannot do both jobs. The hash covers `name` and
`steps` but not `date`, while `publish` reads `spec["date"]` to decide what to
schedule and which day to check for an activity collision. So the identical session
authored for two days produces one hash - and a token previewed for Monday would
confirm the push for Tuesday. The athlete sees one day in the preview and the account
gets another, with the collision warning computed for the day that was not pushed.

Adding the date to `spec_hash` would have been the one-line fix and is wrong:
rescheduling a workout would then look like a *different* workout to the account,
breaking the very idempotency ADR 0013 built the hash for.

A second, quieter inconsistency sat next to it: the spec is loaded from
`reports/<date>/workout.json`, and the activity-collision check uses that folder date,
while the push uses the spec's own `date`. Nothing held the two in agreement.

## Decision

- **Two hashes, two jobs.** `spec_hash` (name + steps) stays exactly as it was and
  keeps its account-side meaning. A new `confirm_token` (name + steps + date) gates
  the handshake. `push_preview` returns both - the token to confirm with, the hash for
  reference - and `push_confirm` takes `confirm_token`. The MCP parameter was renamed
  rather than redefined in place, because two values that mean different things must
  not share a name in the tool surface an agent calls.

- **A spec must agree with the folder it is filed under.** `_load_spec` refuses a
  `workout.json` whose `date` is not the folder's date, instead of silently preferring
  one of them. `author_workout` maintains the invariant by construction, so this only
  fires on a hand-edited file - exactly the case that used to push the wrong day.

- **`plan_preview` / `plan_confirm` need no token.** Audited for the same gap and
  found sound: `plan_confirm` re-validates the full proposal it is given rather than
  trusting a digest of an earlier one, and `write_week_file` refuses an already
  authored week. There is nothing for a stale token to bypass.

## Consequences

- The handshake now means what ADR 0014 said it meant: a push can only follow a
  preview that displayed *that* push, date included.
- Idempotency is untouched: the same workout moved to another day is still one
  workout on the account.
- Amends ADR 0014, which named `spec_hash` as the handshake token. Any caller
  passing `spec_hash=` to `push_confirm` must pass `confirm_token=` instead; the
  coach skill drives this through the MCP tool surface and needs no change.
- The CLI push path is unaffected: it has no preview/confirm split, `--confirm` is
  the interlock, and it reads the same spec file it pushes.
