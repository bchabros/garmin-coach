# Testing workflow

Work test-first for behavior changes: one failing test at an established seam, then
its minimal implementation. Existing behavior tests cover refactoring; avoid tests
that merely restate code. The agreed seams are in `docs/DEVELOPMENT.md`.

Use temporary installations, real SQLite and fake Garmin/client/scheduler transport.
Development tests never use the athlete's real installation or contact Garmin.
Payload fixtures are anonymized real examples; normalizers also handle missing fields.
Preserve the active MFA, publisher and installation test seams.

Run focused tests with `poetry run pytest <path>`, then `task check` before committing.
Native client conversations, own-account login and clean-Mac acceptance are separate
operator checks; a passing protocol test does not establish model adherence.
