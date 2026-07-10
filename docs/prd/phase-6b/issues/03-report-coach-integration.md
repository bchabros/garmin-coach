# 03 - Report + coach integration: emit `snapshot.json`, add "Twoje aktualne staty"

Status: ready-for-agent
Parent: `docs/prd/phase-6b/PRD.md`

## What to build

Generating a coach report also writes the snapshot beside the digest, and the coach
narrative opens with a short current-standing header. The coach skill now reads two
deterministic finished-DB artifacts (`digest.json` + `snapshot.json`); the golden rule
holds - neither calls Garmin.

## Acceptance criteria

- [ ] `report.generate_report` writes `reports/{date}/snapshot.json` alongside
      `digest.json` and the charts.
- [ ] `skills/coach/SKILL.md` gains a "Twoje aktualne staty" section fed from
      `snapshot.json` (current markers, zones headline, load/ACWR, planned intent).
- [ ] The section degrades gracefully when a snapshot field is NULL.
- [ ] A report-generation test asserts `snapshot.json` is emitted with the standing.

## Blocked by

- 01 - Snapshot skeleton (needs the `snapshot.json` shape). Independent of 02.
