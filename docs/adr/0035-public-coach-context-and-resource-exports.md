# ADR 0035 - Public coach context and consistent resource exports

## Status

Accepted for issue #99, extending the resource ownership rules of ADR 0034.

## Context

Before issue #99 the coach router had no route to saved race results or dated
reviews, and installed plugins carried no guide for them. Each packager chose its
skill files separately: native plugins and the wheel used hard-coded lists, while the
standalone skill ZIP and its drift check took every Markdown file under
`skills/coach/`, and the release bundle every tracked file there. A recursive choice
can export private athlete notes, and earlier installations may own private content
at `references/context.md` (ADR 0034).

## Decision

Extend the public coach instructions with a generic saved-context
reference. It routes race comparisons to the athlete's private race index and cited
result tables, and older coaching questions to dated records. The public package
carries lookup rules, not those records. Current Garmin-derived metrics still come
from sanctioned coach reads; external race evidence and arithmetic scenarios carry
their own provenance.

Name this new resource `references/saved-context.md`. The historical
`references/context.md` name remains excluded because prior installations can own
private athlete content at that path. Reclassifying it as public would let an older
snapshot pass the public filter with private bytes. Old private-resource cleanup and
native-cache reinstallation remain governed by ADR 0034.

Use one standard-library-readable public resource list in the program package for
native plugins and the legacy standalone skill ZIP/check. The list
(`coach_resources.json`) and the rule that reads it (`coach_resources.py`: regular
files reached without a symlink from the skill directory) live together; release
tooling, the build backend and the standalone packager load that module by path, so
none of them imports application dependencies. Poetry's explicit includes and
release-bundle exports must agree with that list, verified through built-package
tests. Arbitrary Markdown is excluded; symlinked public resources are refused rather
than following files outside the approved source.

The new generation has the router plus four references. Restoring an older owned
snapshot may legitimately provide only the original router and three references;
require that original core and restore the saved public bytes without inventing a
new reference or accepting private extras. A later configure prepares the current
complete generation through the existing ownership transaction.

A current read-only answer uses MCP or existing artifacts. Saved reports and charts
enter the explicitly requested local generation path using an already supported
runtime. Null planned intent remains unknown, even with no downgrade signal. These
instruction changes preserve profile exact-diff approval and the existing plan,
repair and workout preview/confirmation gates.

Ordinary source builds use a small in-tree PEP 517 backend that validates regular,
non-symlink public resources and each distribution format's explicit include list,
then delegates to Poetry's backend. An extension build script was rejected: the
installed Poetry core does not execute it for source distributions, and configuring
one changes otherwise pure wheel tags. The wrapper remains in the source archive
so a wheel rebuilt from that archive applies the same validation.

## Considered options

Rejected alternatives were recursive Markdown packaging (which can export private
notes), making the old context path public (which changes snapshot privacy semantics),
and requiring application imports in the legacy packager (which breaks its standalone
setup role). A public manifest plus explicit wheel includes adds a small consistency
contract instead; package tests enforce it.

## Consequences

Validation uses standard-library JSON, paths and TOML on the application's Python
3.13. A conditional TOML reader supports build frontends running Python below 3.11;
the second build requirement also makes Poetry CLI use the declared PEP 517 backend
instead of bypassing it with its internal fast build path. This adds no application
runtime dependency. A clean isolated source build may fetch its declared build
requirements unless they are cached; managed binary installation does not run Poetry.
