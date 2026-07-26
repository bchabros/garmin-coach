# ADR 0014 - The coach MCP server (reads, same-day refresh, workout push)

## Status

Accepted

## Context

The roadmap's read-MCP was specified as a thin conversational read layer: "read-only
by construction - no write tools, no Garmin transport in this server". Three forces
pushed against that clause once the surrounding system matured:

- **Reads alone were the smaller half of the friction.** Real coaching sessions mixed
  hand-written SQL (solved by read tools) with two things a read-only server cannot
  do: surface *today's* morning HRV/readiness (issue #8 - the mart stops at yesterday
  by design) and finish an agreed session by scheduling it to the watch (Phase 11
  shipped that path, but CLI-only).
- **The interlocks already exist.** Phase 11 (ADR 0013) settled how a Garmin write is
  contained: a pure author seam, an out-of-seam publisher, a confirm interlock, and
  account-as-source-of-truth idempotency. Exposing that path over MCP adds a caller,
  not a new write surface.
- **The athlete asked for it** - one server carrying "the best functions we have so
  far", including workout authoring, rather than a read-only window plus a terminal.

## Decision

- **One server (`coach`, stdio, versioned `.mcp.json`), four tool groups.** Read tools
  over the finished DB; local writes (`log_rpe`, `log_niggle` - transport-free);
  one transport read (`refresh_today`); and the workout push pair. Isolation lives in
  the code - every tool delegates to a pure function in `mcp_tools` that reuses the
  same seams the CLI uses - not in process boundaries. This supersedes the "read-only
  by construction" clause.
- **The golden rule's intent is preserved, restated for tools:** no metric ever
  depends on live Garmin. `refresh_today` and the push pair only trigger the existing
  transport paths (`daily.run_refresh_today`, `publish`); the metrics/coach layer
  still never imports `client`, and the digest/marts compute identically whether or
  not any MCP tool ever runs.
- **Same-day refresh never advances watermarks.** `refresh_day` pulls today raw-first
  with stream isolation but writes no watermark, so the nightly sync re-pulls the day
  complete. Today is partial by definition; every tool response carries a freshness
  envelope (`data_through`, `today_included`, `partial_fields`) so a chat session
  cannot mistake intraday values for final ones. Alerts stay owned by the nightly
  path - signals over a partial day would false-fire.
- **The push is gated by a preview-hash handshake, stricter than the CLI.** The CLI
  trusts the human behind `--confirm`; over MCP the caller is an agent, so
  `push_confirm` requires the canonical `spec_hash` returned by `push_preview` and
  refuses any other value without touching the account. An agent can only confirm
  what it has actually previewed, and an edited spec invalidates the token.

## Consequences

- A chat session closes the full loop: read -> refresh -> decide -> author -> preview
  -> confirm, with the terminal needed only for operating the pipeline itself.
- The server is a second consumer of the CLI's seams, so behaviour drift between the
  two surfaces is structurally impossible (both call the same functions).
- `refresh_today` shares the login rate-limit exposure of any transport call (429):
  cached tokens make it cheap, but a session should call it at most once per read,
  not per tool call.
- The name "read-MCP" is retired; the roadmap section now points at epic #18.

## Annex: `get_workout_status` joins the transport tools (issue #41)

The tool groups above list three tools that reach Garmin - `refresh_today` and the push
pair. `get_workout_status` is now a fourth, and it crosses the boundary in the opposite
direction from the rest: it is a *read* tool that must consult the account to answer
honestly.

The reason is that its previous answer came entirely from `reports/{date}/push.json`. A
receipt records what a push did; presenting it as what the account holds now made the
tool assert `applied: true` for a workout the athlete had deleted, and a coaching session
repeated that claim back to them. No local artifact can be made truthful here, because
the fact being reported lives on the account.

Consequences specific to this tool:

- **It degrades instead of failing.** Any transport failure - a login error, a 429, a
  timeout - resolves to `reconciled.state: "unverified"`, never an exception. A status
  read is not worth taking the server down for, and the previous behaviour (answering
  confidently from a stale file) is exactly what must not survive an outage.
- **It skips transport when there is nothing to check.** A date with no receipt, or a
  receipt naming no workout, returns a null finding and never logs in, so the common
  case pays nothing.
- **No opt-out flag.** A `verify=False` parameter was considered and rejected: the
  caller is an agent, and an agent in a hurry would set it and reproduce the incident.
- **It writes to disk as well** (issue #43). A changed finding is appended to
  `push.json` under `reconciled`, so a later read - including an offline one - is not
  thrown back on the stale claim. This is the second boundary the tool crosses: it is a
  read tool that both reaches the network and writes a local artifact. The receipt's own
  fields are never mutated, because "we pushed it and it worked" describes an event that
  did happen; rewriting `applied` to `false` would falsify the history to fix a read and
  destroy the only trace of the push. Only a state change writes, and an `unverified`
  read never writes at all - absence of information is not information.
- **The golden rule is untouched.** No metric consults this path; the finding describes
  the athlete's Garmin account, not their training, and the marts compute identically
  whether or not the tool ever runs.

The rate-limit note above extends to this tool: a session that asks about several dates
pays one library read per date.
