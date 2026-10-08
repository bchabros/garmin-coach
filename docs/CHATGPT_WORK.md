# Local coaching and development in Work, Codex, and Claude Code

This setup uses the current macOS Poetry checkout. The folder serves coaching and
development requests; start separate chats for distinct outcomes. The coach router
and all references remain canonical under `skills/coach/`. Codex discovery through
`.agents/skills/coach` and Claude Code discovery through `.claude/skills/coach`
both link to that directory.

**Validation status:** local stdio MCP, offline tests, and a user-reported fresh
Work fixture read are verified. Other client checks, Work development, and the
final Work read from the existing athlete installation remain pending.
Configuration presence, a Codex conversation, and a protocol test do not prove Work
success. Record results in [the validation record](WORK_VALIDATION.md); #82 stays
incomplete until the required Work coaching and development results exist.

## Prepare the runtime

From the source checkout, run `poetry install` with Python 3.13 or newer as allowed
by the package. Existing data must already have been imported and its marts built.
The launcher finds the checkout's in-project virtualenv, or asks Poetry for its
existing interpreter. It never installs dependencies or logs into Garmin.

Set the source checkout's absolute path in your terminal:

```sh
COACH_SOURCE="$(pwd)"
python3 "$COACH_SOURCE/scripts/coach_project.py" check --project "$COACH_SOURCE"
```

`check` validates project guidance, canonical skill references, runtime settings,
the existing database, and a populated digest/snapshot. It then starts the actual
guarded stdio server and calls `get_digest` and `get_snapshot`. Output lists tools,
runtime version and data dates; it omits athlete numbers, profile text and secrets.
`client_acceptance: not_run` means only the local protocol was checked.

The guarded `serve` launch selects the project before reading configuration and
requires an existing database and schema. It does not bootstrap or migrate it.
The legacy bare `garmin-coach-mcp` entry point retains its previous behavior.
Use the guarded configuration below for this setup.

Explicit runtime environment overrides still take precedence over the selected
project's configuration. Review any deliberately set `DB_PATH` before running a
check; the launcher does not change an operator's overrides.

## Connect a client manually

Generate the configuration for the intended **athlete installation folder**:

```sh
python3 "$COACH_SOURCE/scripts/coach_project.py" config --project "$COACH_SOURCE" --client codex
python3 "$COACH_SOURCE/scripts/coach_project.py" config --project "$COACH_SOURCE" --client claude
```

These commands print configuration and write no client settings. Generated paths
are specific to the selected checkout and interpreter. Regenerate after moving
the checkout or replacing its virtualenv.

### Codex

Merge the generated `[mcp_servers.coach]` table into the project's
`.codex/config.toml`. Preserve existing tables; replace an existing coach table
instead of appending a duplicate. Keep machine-specific configuration local.
Trust the intended project through the client's supported UI, then start a fresh
conversation. Check that the coach skill, its references, and tools are visible.

Codex's documented repo skill discovery supports the canonical skill symlink.
The project guide also routes coaching requests to the router explicitly, so the
workflow's location is available even before implicit skill activation is tested.

### Claude Code

Merge only the generated `coach` entry into the project's `.mcp.json`, preserving
other entries. The tracked entry is the legacy bare server; the generated entry
uses the guarded launcher. Start a fresh Claude Code conversation in the project
and approve the project MCP server through its supported trust prompt.
Check the actual connection with `/mcp`.

The existing Claude Desktop helper and uploaded skill path remain available.
A Desktop upload is not evidence that Work or Claude Code discovered that skill.

### ChatGPT Work on the local Mac

Use a local project with the intended folder selected as primary and choose local
execution. In the desktop Work view, use `Cmd+O` (Open folder). In the macOS
folder chooser, use `Cmd+Shift+G` to enter the installation's absolute path and
select Open. If that command is unavailable, check Settings > Keyboard Shortcuts
for Open folder and record the missing capability; do not assume a generic plus
menu can attach a local folder. Verify the product mode and version in the
application; its displayed name alone is insufficient. The primary folder supplies
the durable project guide.

The official desktop MCP documentation describes project configuration and manual
STDIO setup. Try the generated guarded launcher through the supported local MCP
configuration. Confirm the tools in **Work itself** before recording success.

Install the complete canonical coach skill using the mechanism actually available
in that Work version. A manual skill installation is acceptable; verify the router
and every bundled reference. Repo symlink discovery is documented for Codex, so
do not infer Work discovery from it. If a supported plugin package or additional
activation is required, record that evidence before adding such an adapter.

If local execution, skill installation or MCP access is unavailable, record the
specific unavailable capability. Keep Work acceptance pending and do not silently
switch to a hosted setup or claim that another client's result proves Work support.

## Run acceptance on a synthetic installation first

Create a new empty test installation. This command refuses a nonempty destination
and uses no Garmin transport:

```sh
COACH_FIXTURE="$(mktemp -d /tmp/garmin-coach-work-fixture.XXXXXX)"
python3 "$COACH_SOURCE/scripts/coach_project.py" fixture --project "$COACH_FIXTURE"
python3 "$COACH_SOURCE/scripts/coach_project.py" check --project "$COACH_FIXTURE"
python3 "$COACH_SOURCE/scripts/coach_project.py" config --project "$COACH_FIXTURE" --client codex
```

Select the fixture folder as the local chat's primary folder and use its generated
coach configuration. The fixture contains guidance, complete coach resources and a
clearly labelled synthetic profile. Its mart and snapshot horizon is **2026-07-03**.
It contains sparse synthetic evidence, not personal records or a realistic history.
Its dated profile is intentionally old when tested in October: the coach should
identify that age and missing evidence rather than inventing newer observations.

In a fresh Work conversation, send:

> This is the synthetic fixture installation. Read the project guide, use the coach
> workflow, read its reporting reference and available profile, then call get_digest
> and get_snapshot. Explain my standing and the dates of the evidence. Do not refresh
> Garmin, write a plan or profile, or push or remove a workout.

Record tool calls, reference/profile access, fixture identity and freshness handling.
Repeat discovery and read checks in fresh Codex and Claude Code chats separately.
The runtime cannot infer that the model read the references; assess that in the chat.

For planning and authoring, fixture chats may inspect and show proposals. Complete
push/removal confirmations are covered offline with `FakePublisher`; never point
a chat write test at the real account. Keep plan previews and profile amendment
diffs reviewable and retain their existing human confirmation requirements.

## Demonstrate Work development in an isolated checkout

Create a temporary detached test checkout from the implementation branch and
install its Poetry dependencies. Do not copy the personal configuration, database,
reports, plans or profile into it. Use it as the primary folder for a separate
fresh local Work conversation.

Ask Work to read the project guide and development guide, inspect
`tests/test_schema_sync.py`, explain the schema mirror check, and run that test.
Then ask it to make a small documentation change describing the check, show its
diff, and rerun the relevant offline test. Record the actual files inspected,
reviewable diff and test result. This test change remains in the disposable test
checkout. A coaching request alone is not authorization for a source change.

## Final acceptance on the existing athlete installation

Reconnect the intended real project and its generated guarded coach configuration.
In a fresh local Work chat, send:

> Jaka mam forme? Przeczytaj instrukcje coacha, referencje raportu i moj dostepny
> profil. Uzyj get_digest i get_snapshot z tej instalacji i wyjasnij aktualnosc
> danych. Bez odswiezania Garmin i bez zapisywania planu, profilu lub treningu.

The response must use local deterministic facts and the freshness envelope, explain
missing/unconfirmed evidence, and handle a missing or stale profile honestly.
Record success and the execution surface without copying private numbers or profile
content into the repository or issue. This Work result and its development result
are required before #82 is complete.

## Recovery

| Diagnostic | Action |
| --- | --- |
| Runtime missing | Run `poetry install` in the source checkout; regenerate configuration if its interpreter moved. |
| Project guidance missing | Select the actual project/fixture folder, not an unrelated launch directory. |
| Skill or references missing | Restore/install the complete canonical coach directory and regenerate an uploaded copy after changes. |
| Database missing | Select the existing installation; a new installation requires its separately documented import. The check does not create one. |
| Schema or coach data missing | Run the documented offline `poetry run garmin-coach features` from the intended installation after import; repeat the check. |
| MCP connection failed | Check the generated command, selected folder, installed dependencies and client trust/activation; repeat local check before the fresh-chat test. |
| Local check passes, Work cannot use it | Record the application version and unavailable capability; verify the Work-specific supported route. |

## Sources

Checked 2026-10-08: [local projects](https://learn.chatgpt.com/docs/projects?surface=app),
[skills and local discovery](https://learn.chatgpt.com/docs/build-skills?product=breeze),
[MCP configuration](https://learn.chatgpt.com/docs/extend/mcp),
[desktop commands](https://learn.chatgpt.com/docs/reference/commands), and
[Claude Code MCP](https://code.claude.com/docs/en/mcp).
These establish documented mechanisms; the validation record establishes what was
actually exercised here.
