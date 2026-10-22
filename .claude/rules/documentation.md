# Documentation rules

Changed public functions, methods and classes under `src/` use Google-style docstrings.
Describe behavior and non-obvious constraints. Add `Args:`, `Returns:` and `Raises:`
where useful; one line is enough for an obvious operation. Keep examples in this domain.

`task docstrings` checks source with Ruff's Google convention:
`ruff check src --select D --ignore D100,D104,D105,D107`.
`task check` includes that gate with offline tests, lint and mypy.

Keep each rule authoritative in one place and link to it from other guides.
Update `CLAUDE.md` and its byte-identical `AGENTS.md` mirror together.
The packaged `src/garmin_coach/core/schema.sql` is the sole schema source.

## Documentation cleanup before completing a PR

Before the final review of every PR, and again after a material scope change:

1. Review tracked documentation affected by the change, including PRDs, plans,
   investigation reports and duplicate guides. Compare their claims with the final
   implementation and the issue's acceptance criteria.
2. Move still-needed explanations to the maintained guides, binding decisions and
   their reasons to ADRs, and terms to the glossary. Keep acceptance evidence and
   deferred work in the issue or PR, with follow-up issues where needed.
   When shortening an ADR, retain explicit supersession links and scope, the concrete
   incident or evidence behind the decision, and reasons for rejecting alternatives.
3. Remove obsolete duplicates and tracked PRDs or temporary plans whose scope is
   fully implemented and verified in this PR or an already merged PR. Git history
   retains the originals; avoid creating an archive directory. A closed issue or
   unmerged PR alone does not establish completion. Retain documents needed for
   unfinished scope or pending acceptance, and state what remains.
4. Fix references to removed files and check the documentation links and shared
   `CLAUDE.md` / `AGENTS.md` guidance. Keep unrelated documentation and private or
   untracked athlete files outside this cleanup.
5. Add a brief documentation-cleanup outcome to the PR description: what was removed,
   where lasting information moved, and what remains pending. If nothing was obsolete,
   say so. Complete this check before reporting the PR ready for review.
