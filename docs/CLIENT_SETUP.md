# Local assistant setup

Use an athlete installation prepared with onboarding, and the managed runtime's
stable commands. Assistant sign-in belongs to Work, Codex, or Claude Code; Garmin
sign-in belongs to the local onboarding command. Client setup does not log into
either account and does not import data.

```sh
garmin-coach --home ~/GarminCoach clients configure --clients codex
garmin-coach --home ~/GarminCoach clients configure --clients work,claude-code
garmin-coach --home ~/GarminCoach clients status
```

The second configure adds clients to the first selection. All selections use the
same absolute athlete installation and saved Garmin identity. Work and Codex share
one OpenAI host registration; Claude Code has a separate registration. Only selected
hosts are configured. Unrelated client settings and plugins are not edited.

The default program directory is `~/Library/Application Support/Garmin Coach`.
`--runtime-root` selects a different managed runtime. Generated local marketplaces
and registration receipts live under its `integrations/` directory. Their plugins
carry the canonical coach router and every reference; their stdio command points
to the stable `bin/garmin-coach-mcp` launcher with an explicit `--home`. They contain
no athlete database, profile, login tokens, or source-development guide.

The Python distribution also exposes the operations through
`python -m garmin_coach.clients --home PATH --runtime-root PATH configure --clients codex`.
This is useful for the installer and recovery when the main command is unavailable.

## Finish in the selected client

Successful plugin registration reports `needs_activation`. Open the athlete
installation as the primary folder of a **local** conversation, restart the client,
and enable or trust its plugin through the client's supported UI if requested.
Inspect the installed plugin's coach skill, all three references, and MCP tools.
Keep the client's existing approval settings.

For Work, use the ChatGPT desktop app with local execution. Its local plugin
marketplace and MCP configuration are shared with Codex on that host. A successful
Codex command is not proof that Work used the tools. If no `codex` executable is
available, setup prepares the marketplace and reports `client_missing`; install the
CLI to register it, or add the generated marketplace through a supported client UI.
An unavailable Work local-plugin capability remains an explicit acceptance gap.
Do not substitute a hosted HTTP integration or another assistant.

Claude Code uses `claude plugin marketplace add` and `claude plugin install` at user
scope. Work/Codex use `codex plugin marketplace add` and `codex plugin add`. Command
failures are reported without copying private client output into logs. Rerun configure
after fixing the client; a completed marketplace step is retained after partial failure.

The receipt records historical registration, resource hashes, source/package versions,
runtime availability, and pending actions. Status verifies prepared files and whether
they match current source resources. It does not infer what an active conversation
loaded. `client_acceptance: not_run` and `protocol_check: not_run` remain explicit;
the separate local protocol check or an actual transcript supplies that evidence.
When updating a cached plugin, disconnect its owned registration and configure from
the new runtime, then repeat activation and acceptance.

## Verify with synthetic data

First use the synthetic installation documented in [the existing client validation
guide](CHATGPT_WORK.md). Start a fresh conversation in each selected client and ask:

> This is a synthetic installation. Read the coach router, reporting reference and
> available profile. Call only get_digest and get_snapshot. Explain the dates of the
> evidence and missing values. Do not refresh Garmin, write a plan or profile, or
> push or remove workouts. End with the digest's disclaimer verbatim.

Record the client and version, discovered skill and reference paths, selected
installation, tool calls, freshness explanation, null handling and final disclaimer.
Separately request plan/workout/profile previews and verify that the conversation
waits for the existing explicit confirmation before a write. Offline fake-publisher
tests exercise the actual transport interlocks; a live write is not needed for setup.
Local protocol and package tests cannot demonstrate that a model followed instructions.

## Disconnect

```sh
garmin-coach --home ~/GarminCoach clients remove --clients work
garmin-coach --home ~/GarminCoach clients remove --clients work --confirm
```

The first command previews removal. The second removes only owned registrations;
removing Work retains the shared OpenAI host while Codex remains selected. Omit
`--clients` to disconnect every configured assistant. Changed owned resources are
refused for review. Athlete records and unrelated settings are preserved.

## Sources and acceptance status

Checked 2026-10-17 against [OpenAI MCP configuration](https://learn.chatgpt.com/docs/extend/mcp),
[OpenAI plugin packaging](https://developers.openai.com/plugins/build/plugins),
[OpenAI CLI plugin commands](https://learn.chatgpt.com/docs/developer-commands),
[Claude Code plugins](https://code.claude.com/docs/en/plugins), and
[Claude Code local marketplaces](https://code.claude.com/docs/en/plugin-marketplaces).
The implementation was checked with temporary files, fake registration commands,
the built wheel and real stdio reads from a copied plugin cache. Fresh authenticated
Work, Codex and Claude Code acceptance for this distribution remains **not run**.
The earlier source-checkout acceptance in [WORK_VALIDATION.md](WORK_VALIDATION.md)
does not establish acceptance of these newly generated packages.
