#!/usr/bin/env bash
# Nightly entry point for cron / launchd: sync -> features -> alerts.
#
# Thin by design: logging and size-based rotation happen inside
# `garmin-coach daily` (see src/garmin_coach/daily.py + config.py). This wrapper
# only pins the working directory and execs the CLI, so its exit code is the
# run's exit code (0 = ok, 1 = degraded, 2 = failed). Anything printed to
# stdout/stderr is captured by launchd/cron.
#
# Usage: scripts/daily.sh [--to YYYY-MM-DD]
set -euo pipefail

cd "$(dirname "$0")/.."
exec poetry run garmin-coach daily "$@"
