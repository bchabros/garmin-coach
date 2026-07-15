# 03 - Athlete request + hybrid validation + sport gating

Status: ready-for-agent
Blocked by: 02
Sources: `docs/prd/phase-11-workout-push/PRD.md` (Origin and hybrid validation; Sport),
ADR 0013.

## Goal

Let the workout come from the athlete, not only the recommender, and let the
recommender check an athlete's idea before it goes to the watch - without a second
code path. Adds the `--request` input and the warns-never-blocks hybrid validation.

## Scope

- **`author --request <path>`** reads an athlete-authored `request.json`
  (`origin: athlete`), validates it against the `workout_request` schema.
- **Structure override**: explicit structure fields in the request override the
  per-type defaults from tickets 01/02 (session length, interval count).
- **Hybrid validation**: run the request's session type against the digest's fired
  signals (the same set the recommender uses). Conflicts (e.g. request asks `tempo`
  when signals cap to easy/Z2) become cited entries in `warnings[]`. **Never a
  block** - the spec is always produced to match the request.
- **Sport gating**: `sport: run` authors as normal; `sport: hiit | strength` validate
  against the schema but `author` answers they await the push spike (see ticket 07).
- **Hyrox split**: a `hyrox` recommendation does not guess run/station; `author` asks
  the athlete to specify (run -> authorable; station -> `hiit`, deferred).

## Acceptance criteria

- [ ] `--request` reads and schema-validates an athlete request; malformed rejected.
- [ ] Explicit structure in the request overrides per-type defaults.
- [ ] A conflicting request still authors, with a cited warning in the spec (no block).
- [ ] `hiit`/`strength` requests validate but return the deferred-to-spike answer.
- [ ] A `hyrox` recommendation asks for the run/station split rather than guessing.
- [ ] Tests offline (Seam 1).
