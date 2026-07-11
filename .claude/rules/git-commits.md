# Git Commits & Merging

## Rules

- **Commit messages** keep the existing convention from `git log`
  (`FEAT:` / `FIX:` / `DOCS:` prefixes, imperative subject). Use `CHORE:` for
  tooling, dependency, and repo-maintenance commits that are neither a feature,
  a fix, nor documentation.
- **Merging** — always squash merge when merging a branch or PR (`--squash` /
  "Squash and merge"), so each feature lands as a single commit on `main`.

## Pull request descriptions

When preparing a PR description (or a summary "for the PR"), always use this shape:

- **Overview first**: 3-5 plain sentences describing what the change does, from the
  reader's perspective (not a change-by-change log).
- **Then bullet points**: one bullet per change, each a single sentence describing
  that change or behaviour.

Keep it factual, and mirror the `FEAT:`/`FIX:`/`DOCS:` vocabulary already in
`git log`.