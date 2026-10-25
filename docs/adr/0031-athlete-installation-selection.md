# ADR 0031 - Selecting the athlete installation

## Status

Accepted

## Context

Program location and starting directory must not silently select another athlete's personal records.

Repeated settings reads also allowed an edit during a running server operation to mix
database, folder or recheck choices. Q12–Q13 of #102 fixed the restart contract for T5.

## Decision

- Select once per run: explicit --home, GARMIN_COACH_HOME, or starting folder only
  when it has .env. Named existing installations also require .env; never infer from
  the program/checkout location. Resolve config-relative paths against installation.
- Capture configuration once per command/server run (#102 T5). `.env` edits require
  a new command or server restart; observations, plans, workouts and saved token files
  at the captured paths remain live. Keep fresh per-operation database connections and
  separate lazy import/publisher factories; never retain an account session merely to
  hold configuration. This trades immediate config reload for consistent operations.
- Inherited relative environment paths are starting-folder-relative. Conflicting
  selection/path/email refuses; equivalent resolved paths, explicit external .env
  paths and per-command overrides remain supported. Other environment precedence stays.
- One installation database opener owns selection, missing-file refusal and optional
  creation. Import commands may create; server/daily/ordinary reads require existing DB.
  Saved login defaults inside installation; explicit legacy external paths remain.
- #84 setup amendment: explicitly name creation, preview the athlete's own history
  start/optional template and separately confirm import. Never seed owner observations.
- Adopt existing personal files/settings/values/template in place. Without checkpoint,
  inspect existing evidence read-only; a local read cannot claim complete history.
- Setup checkpoints cover only full committed days and bind database/history identity,
  independently of nightly watermarks. Extending the same history resumes; invalid or
  replaced identity cannot skip imports. Declined choices preserve existing bytes.

## Consequences

One installation can serve several assistants while program updates/removal remain
separate. Source checkouts are not automatically installations; fixtures and development
tests select isolated temporary records explicitly.
