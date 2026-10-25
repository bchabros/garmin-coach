# Local assistant setup

An assistant serves one explicitly selected athlete installation. Assistant sign-in
belongs to Work, Codex or Claude Code; Garmin login belongs to the user's terminal
onboarding. Client setup itself neither logs into Garmin nor imports history.

## Managed installation

Use the stable launchers from [distribution](DISTRIBUTION.md):

```sh
garmin-coach --home ~/GarminCoach clients configure --clients codex
garmin-coach --home ~/GarminCoach clients configure --clients work,claude-code
garmin-coach --home ~/GarminCoach clients status
```

Selections are additive. Work/Codex share one OpenAI host registration; Claude Code
has its own. Each host has one user-wide binding. Setup checks the selected
installation, ownership receipts and native inventory before registration. A familiar
plugin name alone does not establish ownership. Conflicts require removing the old
binding through its original runtime/installation or inspecting the native plugin UI.
Unrelated plugins/settings are preserved.

The default runtime is `~/Library/Application Support/Garmin Coach`; `--runtime-root`
selects another. Generated marketplaces and receipts live in `integrations/`.
Plugins include only the canonical `SKILL.md` and report, planning, authoring and
saved-context references listed by `coach_resources.json`. Their stdio command uses
`bin/garmin-coach-mcp` with explicit `--home`. Personal profile/race files, tokens,
databases and development guides stay outside generated plugins.

Work/Codex registration uses `codex plugin marketplace add` and `codex plugin add`;
Claude Code uses `claude plugin marketplace add` and `claude plugin install`.
If the executable is missing, setup prepares artifacts and reports `client_missing`.
Install the relevant CLI or use a supported native UI. Keep an unavailable Work local
capability explicit rather than substituting a hosted server or another client.

Setup journals each generation before replacement. Retry resumes partial registration
and refuses edited/unowned artifacts. Claude refresh uses its update command;
OpenAI refresh requires remove/add and can reset permissions. Repeat activation and
fresh-chat acceptance after refresh. Old owned extra Markdown requires hash-verified
removal and a clean reinstall; unknown/edited Markdown blocks configuration for review.
Backups are preserved and restore filters retain only canonical public resources.

`python -m garmin_coach.clients --home PATH --runtime-root PATH configure --clients codex`
is the recovery entry when the main CLI is unavailable.

## Activation and acceptance

Registration reports `needs_activation`. Open the athlete installation as the primary
folder of a fresh **local** conversation, restart the client if needed, and complete
its supported trust/plugin activation UI. Preserve existing approval preferences.
Verify the actual router, all references and coach tools in each selected client.

Registration, protocol checks and fresh model acceptance are distinct. Status verifies
prepared files/hashes, versions and runtime availability; it cannot know what a chat
loaded. `client_acceptance: not_run` and per-client pending acceptance stay explicit.
`doctor` performs the separate bounded local stdio handshake and digest read.
A successful Codex command does not prove that Work can use its tools.

## Source-checkout setup

Use the checkout's existing Poetry environment with Python 3.13. Canonical skill
symlinks are `.agents/skills/coach` for Codex and `.claude/skills/coach` for Claude Code.
The guarded launcher finds the in-project environment or Poetry's interpreter;
it never installs dependencies, logs into Garmin or migrates a database.

```sh
COACH_SOURCE="$(pwd)"
python3 "$COACH_SOURCE/scripts/coach_project.py" check --project "$COACH_SOURCE"
python3 "$COACH_SOURCE/scripts/coach_project.py" config --project "$COACH_SOURCE" --client codex
python3 "$COACH_SOURCE/scripts/coach_project.py" config --project "$COACH_SOURCE" --client claude
```

`config` only prints configuration. Merge the generated `[mcp_servers.coach]` into
`.codex/config.toml`, or the coach entry into `.mcp.json`, preserving unrelated entries.
Regenerate if the checkout/interpreter moves. Trust the project and verify connection
in a fresh client conversation (`/mcp` in Claude Code).

The coach server captures `.env` and inherited configuration when it starts. After
changing configuration, restart/reconnect the server through the client; a new chat
alone may reuse the existing server. New observations, plans, workout files and saved
tokens at the configured paths remain live without a restart (see
[installation configuration](OPERATIONS.md#the-athlete-installation-where-every-run-reads-and-writes)).

The launcher requires the selected installation's existing compatible schema and
populated digest/snapshot. It refuses conflicting inherited `GARMIN_COACH_HOME`,
`DB_PATH`, `PLANS_DIR`, `LOG_PATH`, `GARMINTOKENS` or `GARMIN_EMAIL`; unset the conflict
or intentionally configure it in that installation's `.env`. Equivalent resolved
paths are accepted; explicit external paths are allowed. Missing DB/schema/data
requires the intended installation's import or offline `features` operation, never
an implicit launcher migration. Read-only SQLite checks may create WAL/SHM auxiliaries;
read-only records/schema does not promise zero filesystem effects.

For Work, select local execution and the intended folder as primary. On macOS use
Open folder (`Cmd+O`, then `Cmd+Shift+G` for an absolute path) when available; otherwise
inspect the product's supported shortcut and record the capability gap. Configure the
guarded stdio command through that Work version's supported local route. Verify skills
and tools in Work itself; Codex symlink discovery is not proof of Work discovery.

## Synthetic acceptance first

Create an empty synthetic installation without Garmin transport:

```sh
COACH_FIXTURE="$(mktemp -d /tmp/garmin-coach-work-fixture.XXXXXX)"
python3 "$COACH_SOURCE/scripts/coach_project.py" fixture --project "$COACH_FIXTURE"
python3 "$COACH_SOURCE/scripts/coach_project.py" check --project "$COACH_FIXTURE"
python3 "$COACH_SOURCE/scripts/coach_project.py" config --project "$COACH_FIXTURE" --client codex
```

Use that folder as primary and its generated configuration. The fixture has sparse
synthetic observations through **2026-07-03**, complete guidance/coach resources and
an explicitly synthetic dated profile. It is not a realistic personal history.
The shared packaged builder owns the settings and observations; source setup adds guidance
and manifest-selected public coach resources, excluding private context. An existing
nonempty destination is refused. The same bounded inventory/digest/snapshot probe serves
source checks, diagnosis and candidate checks; its result still leaves fresh-client
acceptance pending.
Send this in a fresh conversation in **each** selected client:

> This is a synthetic installation. Read the project guide, coach router, reporting
> reference and available profile. Call only get_digest and get_snapshot. Explain
> evidence dates, profile age and missing values. Do not refresh Garmin, write a plan
> or profile, or push/remove workouts. End with the digest's disclaimer verbatim.

Record client/version, discovered paths, selected installation, actual tool calls,
freshness, null handling and disclaimer. Preview plan/workout/profile changes separately
and verify the existing explicit confirmation gates. Fake-publisher tests exercise
transport interlocks; setup acceptance requires no live account write. Protocol and
package checks cannot establish model adherence.

For a Work development demonstration, use a disposable detached checkout with its own
Poetry environment and no personal state. Ask it to read AGENTS/DEVELOPMENT, inspect
and run `tests/test_agents_mirror.py`, make a small relevant documentation change,
show its diff and rerun the test. Keep that change in the disposable checkout.

Final source acceptance uses the intended existing installation in a fresh local Work
conversation. Explicitly request only digest/snapshot, correct folder/branch, profile
access/age, freshness and the original disclaimer; exclude plan import, report generation,
refresh and other writes. Record outcomes without copying private numbers or prose.

## Disconnect and recovery

```sh
garmin-coach --home ~/GarminCoach clients remove --clients work
garmin-coach --home ~/GarminCoach clients remove --clients work --confirm
```

The first command previews; the second removes only verified owned registrations.
Removing Work preserves the OpenAI registration while Codex remains selected, and
conversely. Omit `--clients` for all clients. Edited resources are preserved/refused.
Athlete records and unrelated settings remain. Use [maintenance](LIFECYCLE.md) for
runtime repair, backups and a bounded protocol diagnosis.

The legacy Desktop helper (`task claude:register`, `task claude:check`) and skill ZIP
(`task claude:package`) remain supported. The ZIP is a skill upload, not MCP registration.
Re-upload account-hosted copies after resource changes; verify a fresh conversation.
Legacy device-bridge observations do not establish a new client's capability.

## Evidence and sources

Source-checkout acceptance is recorded in closed [#82](https://github.com/bchabros/garmin-coach/issues/82).
Synthetic reads across Work/Codex/Claude and isolated Work development were observed;
initial Codex/Claude answers omitted the disclaimer. Work's first natural activation
included `plan_import`; only the later explicit digest/snapshot rerun qualified as
read-only. Plan/profile preview stops were observed separately.

Generated-distribution fresh authenticated Work/Codex/Claude acceptance is still
**not run**; source acceptance does not establish it. Clean-Mac, own-account login/MFA,
retry, native launchd and installed-client checks remain in [distribution](DISTRIBUTION.md)
and issues #85–#87. Never promote their status from local package/protocol tests.

Mechanisms were checked 2026-10-17 against [OpenAI MCP configuration](https://learn.chatgpt.com/docs/extend/mcp),
[plugin packaging](https://developers.openai.com/plugins/build/plugins),
[CLI commands](https://learn.chatgpt.com/docs/developer-commands),
[Claude Code plugins](https://code.claude.com/docs/en/plugins) and
[local marketplaces](https://code.claude.com/docs/en/plugin-marketplaces).
These are dated references, not a claim of later client acceptance.
