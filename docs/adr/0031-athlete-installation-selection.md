# ADR 0031 - Selecting the athlete installation

## Status

Accepted. Generalizes the guarded project launch of issue
[#82](https://github.com/bchabros/garmin-coach/issues/82) to every entry point
(issue [#83](https://github.com/bchabros/garmin-coach/issues/83)).

## Context

Until now every path the program touched - the database, plans, reports, logs, and the
`.env` that configures them - resolved against the folder the process happened to start
in. That was harmless while the only installation was the source checkout and every
launch `cd`-ed into it first. It stops being harmless once the program is installed as a
package and launched by assistant clients: a plugin's server starts from a
client-managed cache folder, and with nothing there the coach server bootstrapped a
fresh, empty database on its first call. Two assistants started from two folders would
then hold two training histories, and a second athlete on the same Mac would share the
owner's saved Garmin login and sync the owner's training into their own database.

The owner's existing installation is the checkout itself, configured by its `.env`
(database path relative, Garmin login explicitly at `~/.garminconnect`), and it is run by
a nightly launchd job, Claude Desktop, Claude Code and Work - none of which may break.

## Decision

- **The selected installation is named or recognized, never inferred from the program.**
  `--home <folder>` (CLI and coach server) or `GARMIN_COACH_HOME` names it. With
  neither, the folder the run starts from is the installation **only if it holds a
  `.env`**; otherwise the run stops with an instruction. The same `.env` test applies to
  a named folder, so a mistyped path fails instead of becoming a new installation.
- **Relative paths in an installation's `.env` resolve against the installation**, not
  the starting folder. Reports always live in the installation's `reports/`.
- **Conflicts are refused, not resolved by precedence.** `--home` and
  `GARMIN_COACH_HOME` naming different folders is an error. So is an environment value
  for a path setting (`DB_PATH`, `PLANS_DIR`, `LOG_PATH`, `GARMINTOKENS`) or for
  `GARMIN_EMAIL` that differs from what the selected installation configures. Other
  settings keep the usual environment-over-`.env` precedence; paths written in the
  installation's own `.env` (outside its folder included) and per-command flags such as
  `--plans-dir` remain the supported overrides.
- **Only a manual import creates a database.** `backfill` and `sync` may create the
  selected installation's database; the coach server, the nightly `daily` run and every
  other command refuse a missing one. Upgrading an existing database's schema is
  unchanged by this decision.
- **The saved Garmin login belongs to the installation by default**
  (`<installation>/.garminconnect`). An explicit `GARMINTOKENS` keeps working, which is
  how the owner's login stays at `~/.garminconnect`.
- **The owner's checkout is adopted in place.** It already holds a `.env`, so the
  nightly job, Claude Desktop, Claude Code and Work keep launching it unchanged.

## Considered options

- **A dedicated marker file** instead of `.env`: unambiguous, but it needs a one-time
  step in the existing checkout and a second file beside the settings. Rejected because
  a foreign `.env` only gets as far as the missing-database refusal.
- **Always require an explicit selection**: the strictest, but it would force every
  existing launch (nightly job, three client entries, terminal habits) to change in the
  same release.
- **Fall back to the source checkout the code runs from**: convenient for the owner, but
  a development session in a worktree would then act on the owner's real data.
- **Environment over `.env` (the library default)**: a stale `DB_PATH` exported in a
  shell or inherited by a GUI client would silently point a run at another
  installation's database - exactly the access this decision exists to prevent.

## Consequences

- A fresh clone needs `cp .env.example .env` before its first import; `.env` is no
  longer optional.
- Tests must select a temporary installation explicitly; a shared guard disables the
  starting-folder fallback and the environment variable so no test can reach the
  owner's installation.
- Creating a new installation from a template, with a guided Garmin login, belongs to
  issue #84; telling a packaged client's chat where the profile lives belongs to #85.
