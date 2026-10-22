# Issue tracker: GitHub issues

Track work on `bchabros/garmin-coach`, titled by the capability gap it closes.
The issue body holds the problem, solution, decisions, testing notes and Definition
of Done. Tickets are a task-list checklist (`- [ ] T1 ...`); make a separate issue
only when the work needs its own discussion. `/to-spec` publishes the spec and
`/to-tickets` supplies the checklist. The PR closes the issue with `Closes #N`;
squash merge after its Definition of Done is met.

Architecture decisions belong in `docs/adr/`, vocabulary in `docs/glossary.md`.
Earlier phase specifications and project surveys remain in Git history.

- Publish with `gh issue create --body-file <file>`.
- Read a ticket with `gh issue view <number>`.
- Use the triage vocabulary in [triage-labels.md](triage-labels.md).
- For `/wayfinder`, keep the design map, questions, dependencies, answers and progress
  in the issue body/discussion, with links to separate investigations where needed.
- Record acceptance evidence in the issue or PR: dates, versions, synthetic setup,
  observed outcomes and still-pending checks. Keep private athlete records out.
