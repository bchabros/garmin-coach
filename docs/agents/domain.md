# Domain Docs

How the engineering skills should consume this repo's domain documentation when
exploring the codebase. Layout: **single-context**.

## Before exploring, read these

- **`docs/glossary.md`** -- the single source of truth for this project's domain
  terms. Read it first; it is the vocabulary these skills must speak.
- **`CONTEXT.md`** at the repo root, if it exists. This repo does not have one
  yet; `/domain-modeling` (reached via `/grill-with-docs` and
  `/improve-codebase-architecture`) may create one lazily. Until then,
  `docs/glossary.md` is the domain reference.
- **`docs/adr/`** -- read the ADRs that touch the area you're about to work in
  (currently `0001`..`0008`).

If any of these don't exist, **proceed silently**. Don't flag their absence;
don't suggest creating them upfront.

## File structure

Single-context repo:

```
/
├── docs/glossary.md      <- domain vocabulary (single source of truth)
├── docs/adr/             <- architectural decisions (0001..)
├── CONTEXT.md            <- optional; created lazily by /domain-modeling
└── src/
```

## Use the glossary's vocabulary

When your output names a domain concept (an issue title, a refactor proposal, a
hypothesis, a test name), use the term as defined in `docs/glossary.md`. Don't
drift to synonyms the glossary explicitly avoids.

If the concept you need isn't in the glossary yet, that's a signal -- either
you're inventing language the project doesn't use (reconsider) or there's a real
gap (note it for `/domain-modeling`).

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than
silently overriding:

> _Contradicts ADR-0006 (post-Phase-5 architecture deepening) -- but worth
> reopening because..._
