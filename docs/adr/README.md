# Architecture decisions

Accepted records preserve the decision, its reason and consequences. Read later
amendments before applying an earlier clause; shortening a record does not revoke
behavior. Numbers and filenames are stable. Work/specs and acceptance evidence live
in GitHub issues and PRs; earlier phase documents remain in Git history.

| ADR | Decision |
| --- | --- |
| 0001 | [Phase 1 incremental sync semantics](0001-phase-1-incremental-sync.md) |
| 0002 | [Phase 2 metrics (mart) semantics](0002-phase-2-metrics-semantics.md) |
| 0003 | [Phase 3 coach signals + digest semantics](0003-phase-3-coach-signals.md) |
| 0004 | [Phase 4 automation (nightly orchestrator + alerts)](0004-phase-4-automation.md) |
| 0005 | [Phase 5: weekly rollups, plan-vs-actual, and deload detection](0005-phase-5-weekly-rollups-and-plan-vs-actual.md) |
| 0006 | [Post-Phase-5 architecture deepening](0006-post-phase-5-architecture-deepening.md) |
| 0007 | [Phase 6: personal training zones (`athlete_zones` mart)](0007-phase-6-personal-zones.md) |
| 0008 | [Layered documentation (thin shared core + per-role guides)](0008-docs-layering.md) |
| 0009 | [Phase 6b: athlete snapshot (`athlete_status` mart)](0009-phase-6b-athlete-snapshot.md) |
| 0010 | [Phase 7: strength and Hyrox load model + niggle log](0010-phase-7-strength-load-and-niggle.md) |
| 0011 | [Phase 8: per-set capture + movement-pattern overlap](0011-phase-8-movement-overlap.md) |
| 0012 | [Phase 9: race-date periodization](0012-phase-9-race-date-periodization.md) |
| 0013 | [Phase 11: workout authoring and push](0013-phase-11-workout-authoring-and-push.md) |
| 0014 | [The coach MCP server (reads, same-day refresh, workout push)](0014-coach-mcp-server.md) |
| 0015 | [The per-week plan of record (file as source, model proposes, tool validates)](0015-per-week-plan-of-record.md) |
| 0016 | [Strength/HIIT authoring: flat steps and a taxonomy-held whitelist](0016-strength-hiit-authoring.md) |
| 0017 | [The report horizon bounds sources; standing reads declare provenance](0017-report-horizon-bounds-sources.md) |
| 0018 | [Raw identity includes the payload hash; enrichment gaps are reported, not degrading](0018-raw-identity-and-enrichment-gaps.md) |
| 0019 | [The confirm token is not the gc-hash](0019-confirm-token-separate-from-gc-hash.md) |
| 0020 | [Intensity targets belong to every step role, and a zone name means heart rate](0020-step-role-intensity-targets.md) |
| 0021 | [Nothing harder than the plan of record reaches the watch](0021-plan-guard-on-authoring-and-push.md) |
| 0022 | [A hand-logged set overlay makes Hyrox circuits visible to the movement mart](0022-manual-set-overlay.md) |
| 0023 | [A run session type is a default shape, not a permitted shape](0023-session-type-is-a-default-shape.md) |
| 0024 | [The plan guard reads what the watch will ask, not what the template is called](0024-hardness-measured-from-targets.md) |
| 0025 | [Preserve coaching history before expiring report artifacts](0025-profile-history-and-report-retention.md) |
| 0026 | [A day Garmin has answered for is not final: the re-check window](0026-recheck-window.md) |
| 0027 | [Split every session's load by intensity, tuned to Garmin's own balance](0027-load-split-tuned-to-garmin-balance.md) |
| 0028 | [Gap repair from chat: a second transport tool, bounded and previewed](0028-gap-repair-from-chat.md) |
| 0029 | [The workout's name is the athlete's, and a date-free name is reusable](0029-athlete-owned-workout-names.md) |
| 0030 | [Taking a pushed workout off a day from chat](0030-unschedule-from-chat.md) |
| 0031 | [Selecting the athlete installation](0031-athlete-installation-selection.md) |
| 0032 | [A versioned private macOS runtime and opt-in scheduler](0032-versioned-macos-runtime.md) |
| 0033 | [ADR 0033 — Program ownership and storage compatibility during lifecycle operations](0033-program-lifecycle-ownership.md) |
| 0034 | [Exclusive client bindings and resumable owned generations](0034-exclusive-client-binding-and-recovery.md) |
| 0035 | [Public coach context and consistent resource exports](0035-public-coach-context-and-resource-exports.md) |

Key amendments: 0006/0017 bound historical reporting and transactions; 0010/0027
replace original load bucketing; 0015 allows optional templates after #84; 0019/0021
refine confirmation; 0023/0024 separate session shape from hardness; 0029/0030 refine
reuse/removal; 0031–0035 govern installation, native ownership and public resources.
#103 retires duplicate phase history and unpopulated snapshot paths while preserving
legacy database and input compatibility.
