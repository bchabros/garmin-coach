# 01 - Goal event (core) + `event` CLI

Status: ready-for-agent
Blocked by: -
Sources: `docs/prd/phase-9-periodization/PRD.md` (Core: `goal_event`, Anchoring rules,
CLI). ADR: `docs/adr/0012-phase-9-race-date-periodization.md`.

## Goal

The athlete can record what they are training for, and change it as plans firm up. No
blocks or taper yet - just the event, the anchor, and a countdown. This is the ground
truth every other ticket in the phase reads.

## Scope

- **Core table `goal_event`.** Manually-written ground truth (like `session_rpe` /
  `niggle`), never sourced from Garmin. Carries the event date, `type`
  (`hyrox | run_race`), `priority` (A/B/C), `target_s` (INTEGER seconds, nullable),
  `note`, and the two independent uncertainty axes:
  - `status` (`confirmed | tentative`) - *whether the athlete will start*
  - `date_precision` (`exact | approx`) - *whether the exact day is known*

  These are orthogonal on purpose (glossary: "status (goal event)", "date_precision").
  Do not collapse them. Add to `src/garmin_coach/schema.sql` **and** the `docs/schema.sql`
  mirror (`test_schema_sync.py` guards this).

- **Do not seed the athlete's own events.** A race calendar is personal data, not repo
  content. The events go in through the CLI.

- **Pure anchor selection.** `anchor_event(events, today) -> event | None`: the nearest
  *upcoming* event with `priority = 'A'` and `status = 'confirmed'`. Nothing else anchors,
  regardless of proximity. Past events never anchor. No anchor -> `None` (an explicit
  "I do not know what you are training for", not a fallback).

- **DB helpers** for insert / update / read of `goal_event`, following the existing
  `_upsert` pattern in `db.py`.

- **CLI `garmin-coach event add | list | update`.** `status` and `date_precision` are
  *designed to change* (the athlete buys the slot, or commits to the tune-up), so `update`
  is the point of the command, not an afterthought. `event list` shows each event, which
  one is the anchor, and whole weeks to each.

## Tests (`test_db.py`, `test_cli.py`)

- `anchor_event`: picks the nearest upcoming confirmed A; ignores a *nearer* tentative B;
  ignores a *nearer* confirmed B; ignores a past A; returns `None` with no events and with
  only-tentative / only-past events.
- `goal_event` write + read round-trips; `update` flips `status` (`tentative -> confirmed`)
  and `date_precision` (`approx -> exact`) and the change is visible on re-read.
- `event list` marks exactly one anchor, or none.
- `target_s` stores seconds (3600), not free text.

## Done when

- `garmin-coach event add --date 2026-10-17 --type hyrox --priority A --status confirmed
  --date-precision approx --target 1:00:00` records the race, and `event list` shows it as
  the anchor with its countdown.
- A second, `tentative` event can be recorded without becoming the anchor.
- `task check` green.