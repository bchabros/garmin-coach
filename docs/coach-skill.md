# Coach Skill Guidance

Status: retired as executable guidance.

The active coach instructions live in `skills/coach/SKILL.md`.

That file is the single narrative module interface:

- run `poetry run garmin-coach report`;
- read only `reports/{today}/digest.json`;
- embed the generated HRV and ACWR charts;
- write `reports/{today}/report.md`;
- never read Garmin live;
- never bypass the digest by querying marts, core tables, or thresholds directly.

This document remains only as a signpost so older references do not send future
agents back to the pre-digest workflow.
