# Exclusive client bindings and resumable owned generations

User-scoped plugins share the `coach` skill/tool namespace, so distinct athlete
installations cannot safely be registered simultaneously on the same assistant host
without a verified client isolation contract. For issue #97, serialize one client
binding per user and host, retain Work/Codex's shared OpenAI binding, and refuse a
conflicting athlete installation or runtime root before native mutation. Legacy
bindings require native inventory evidence; removal remains scoped to the original
owned registration.

A process interruption is a normal installation outcome. Persist the old/new owned
artifact generation before replacement, prepare runtimes in fresh sibling directories,
and record update recovery intent before activation. Rollback reuses the integration
transaction with preserved ownership rather than deleting integration directories or
invoking an older client's recovery implementation. Unknown edits remain preserved.

Unique plugin names alone were rejected because they do not establish conversation
isolation for identical skills and MCP servers. Eagerly detaching every client before
activation was rejected because a crash would unnecessarily disconnect all hosts and
reinstallation can reset client permissions. Native inventory and preparation history
remain distinct from fresh-conversation client acceptance.

New update intent has an explicit format version. A legacy v0.1.0 preparing journal
is treated as possibly having detached clients, because that release advanced its
phase after native removal. Native operations that can succeed before a process is
interrupted require ownership readback before replay; an absent acknowledgment alone
is insufficient evidence that nothing happened.

Generated skill content is restricted to the four public coach resources, including
when restoring an older snapshot. An owned obsolete resource is removed only after
its hash and deletion intent are recorded. A prior receipt owning private Markdown
requires a clean native plugin reinstall, even if the source file has disappeared,
because the native cache can still retain it. This targeted reinstall accepts the
permission-reapproval cost for privacy; unknown or edited resources are preserved and
require operator inspection before activation.


ADR 0035 extends resource selection with a new generic saved-context reference and a
shared public list. It preserves the legacy private-context exclusion and the recovery
rules above, including restoration of the original four-resource generation.
