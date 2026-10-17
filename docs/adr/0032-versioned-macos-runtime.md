# ADR 0032 - A versioned private macOS runtime and opt-in scheduler

## Status

Accepted design for issue #86; consumer clean-Mac and fresh-client acceptance pending.

## Context

An athlete installation holds durable personal state (ADR 0031), while a source checkout
is a development tool. A new user should not need a checkout or Poetry, and another
athlete must never receive the owner's personal records. The distribution mechanism
also determines how clients keep a stable server command across program replacement.
ADR 0004 deliberately left scheduler installation to the operator; issue #86 now asks
for an explicitly chosen setup step.

## Decision

Build version-addressed native macOS releases using Poetry and its lock. Each release
contains an application wheel, binary dependency wheelhouse, canonical coach assets
and version/source manifest. A generated installer pins verified standalone Python and
release assets, supplies a private venv and installs without an index. System Python,
shell profiles and developer tools remain independent.

Keep the managed runtime outside the athlete installation. Version-addressed release
directories are prepared and checked before atomic activation. Stable CLI/MCP launchers
point at the active release. Runtime receipts identify versions and proven owned program
and scheduler files; they never contain credentials or personal report/profile content.
A per-release inventory records file hashes, directories and symlink targets before
readiness. Destructive repair/removal accepts only verified remaining owned entries;
missing program files can be repaired, while unknown/edited files and unverified
incomplete releases are preserved. Known bytecode caches tied to inventoried Python
source files are the only mutable runtime content excluded from that comparison.

Delegate athlete login/import and client setup to their existing entry points. Terminal
credentials prompts stay visible. Protocol readiness, configuration preparation and
observed client acceptance are distinct results. A released asset URL and clean-Mac
acceptance cannot be claimed before they actually exist.

Extend ADR 0004's scheduling decision only: setup may install a per-installation user
LaunchAgent after explicit opt-in and schedule/command preview. It uses a stable absolute
launcher, explicit athlete selection and saved login, with no password or immediate run.
The existing deterministic daily pipeline, logging and exit-code contracts remain intact.
An unchanged owned job is reused; an unrelated or edited job is preserved.

## Considered options

A source clone plus Poetry preserves the operator workflow but does not meet clean-Mac
installation. Depending on Homebrew or system Python makes setup alter a separate
user-managed environment. A frozen application executable reduces interpreter setup but
adds native build/signing complexity for scientific dependencies. A pinned standalone
Python plus locked wheels is explicit, inspectable and testable on each architecture.

## Consequences

Native release jobs must prove binary wheel availability and run packaged stdio checks.
A consumer clean-Mac test with the selected clients remains separate from CI/developer
isolation smoke. Program update/removal must preserve the athlete installation and use
the same manifest/ownership records. Third-party Python/dependency license notices must
stay in released artifacts. This first release does not silently auto-update or migrate.
