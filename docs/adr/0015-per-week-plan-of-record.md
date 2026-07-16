# ADR 0015 - The per-week plan of record (file as source, model proposes, tool validates)

## Status

Accepted

## Context

Until issue #21 the only planned-intent source was `plan_template`: a static
`dow -> intent` table with no week key, seeded once with a rough shape of the athlete's
week. It answered `snapshot.planned_intent_today`, `weekly.plan_adherence`, and the
`planned_intent` starting point of `recommend()`.

The athlete's real plan is authored per week in `plans/<monday>_week.md`, whose
"Zamiar (dla silnika)" column already carries the intent vocabulary, and which gets
revised mid-week when signals warrant. Nothing ingested those files. For week
2026-07-13, six of seven days diverged from the template; `get_snapshot()` reported
`rest` for Thursday 2026-07-16 while the plan of record said `quality` (tempo 8x1 km).
Three forces set the shape of the fix:

- **The divergence was silent and load-bearing.** Adherence was scored against a plan
  the athlete never agreed to, and `recommend()` only ever *softens* - so a template
  `rest` could never be raised to a planned `quality`. Wrong inputs produced
  confidently wrong coaching.
- **The Markdown carries more than the engine can hold.** Paces, HR caps, the
  rationale, and the revision log live in prose. Moving plan authoring into the DB
  would lose exactly the part that makes a plan reviewable.
- **An unplanned week had no owner.** When no plan file existed the system silently
  served the template and nothing said so, so the gap was invisible in a chat session.

## Decision

**The Markdown file is the source of record; the DB is a derived cache.** `plan_week`
(`(week_start, dow)` PK) holds the parsed intent column; `plans/<monday>_week.md` holds
the truth. Re-importing a revision overwrites the cache. `plan_template` survives only
as the fallback for weeks with no authored plan.

**One resolver, always naming its source.** `core.plan.resolve_day/resolve_week` is the
only planned-intent read path: the authored week first, the template otherwise, plus a
`source` field. No direct `plan_template` read survives outside it - unowned drift is
what caused the issue.

**Parse errors fail loudly.** The importer raises rather than falling back to the
template; the nightly run degrades and names the file. A silently-wrong plan is the
failure being designed out, so a silent fallback would reintroduce it.

**The model proposes, the tool validates.** `plan_preview` / `plan_confirm` mirror the
workout-push handshake. The coach (LLM) composes the seven-day proposal from the reads
it already has - that is where the intelligence belongs. The deterministic layer only
checks the contract (vocabulary, Monday, seven days), writes the file, and imports it
through the same parser as a hand-written plan. `plan_confirm` refuses to overwrite an
existing week: revisions stay a manual edit, so prose is never clobbered by a tool that
cannot read it.

**`PLAN_MISSING` states the gap.** An informational digest signal when the current week
runs on the template. A fact, not an instruction - proposing a plan stays the coach's
decision, consistent with how every other signal here behaves.

**The planned vocabulary is richer than the mart can observe, so adherence compares
classes.** Planned intents are `rest | easy | tempo | strength | hyrox | crossfit |
quality`; the mart classifies a finished day from load alone into `rest | easy |
strength | quality`. Load numbers cannot tell a crossfit session from a hyrox one, so
comparing raw labels would score every richer intent as a permanent mismatch - a latent
bug that predated this issue (`tempo` and `hyrox` were already legal and already could
never match). `core.plan.intent_class` collapses both sides for the match test only;
the stored grid keeps what was meant beside what happened.

## Consequences

- `get_snapshot().planned_intent_today` for 2026-07-16 returns `quality`, and
  `athlete_status` gains `plan_source_today` so a reader can tell an authored day from
  a fallback one.
- Adherence for already-materialized weeks is restated on the next rollup. Weeks where
  file and template agree are unaffected (2026-06-15 stays 1.0).
- `strength` becomes observable: the classifier reads `load_strength` before the load
  test, because Garmin is HR-blind to lifting and a real FBB session scores ~12 load.
  It requires no anaerobic work, so FBB + Hyrox still reads `quality`.
- `strength` is deliberately absent from the recommender's `_QUALITY_TYPES`: it is hard
  work (rank 2, softened by stress signals like any quality day), but a threshold
  *pace* target is meaningless for lifting.
- The recommender indexes `_INTENT_RANK` directly, so it must cover the whole
  vocabulary. A test pins the two together rather than trusting the next editor.
- Coach-authored weeks land in `plans/` looking like any other plan file, and are
  revised the same way. There is one ingestion path, not two.

## Alternatives considered

- **Author plans in the DB, export Markdown.** Rejected: it inverts the source of
  record for the half of the plan that matters most (the prose) and leaves two
  synchronisation directions to maintain.
- **A deterministic weekly-plan generator.** Rejected: it would be a second
  `recommend()` - a large new engine - to replace judgement the coach session already
  exercises well with the existing reads.
- **Let `plan_confirm` overwrite with a flag.** Rejected: the parser reads only the
  intent column, so a rewrite would silently drop the rationale and revision log the
  file exists to carry.
- **Keep the five-value vocabulary and treat crossfit as prose.** Reasonable, and it was
  the status quo. Rejected because the athlete wants the distinction machine-readable,
  and the intent-class mapping makes it cost nothing in adherence while fixing the
  pre-existing `tempo`/`hyrox` hole.
