# Installing a versioned macOS release

The runtime installer is separate from the athlete installation. The managed program
lives in `~/Library/Application Support/Garmin Coach`; the athlete installation defaults
to `~/GarminCoach`. Settings, the database, saved Garmin login, plans, reports and the
profile stay in the athlete installation when the program changes.

## Release availability and acceptance

No Garmin Coach release assets have been published as part of this change. There is
therefore no active paste-and-run installation command yet. Do not copy a guessed
release URL into onboarding instructions.

The release workflow runs native candidate checks on relevant pull requests and builds
native arm64 and Intel wheelhouses for macOS 15+, supplies
pinned standalone CPython 3.13.16, and can publish a version-addressed prerelease only
after both native package checks pass. The documented support matrix is provisional
until those workflow results and the following consumer acceptance are recorded.
A temporary directory on a developer Mac and a GitHub runner are dependency-isolation
checks; neither is a clean consumer Mac with fresh assistant conversations.

| Check | Status |
| --- | --- |
| Offline checksum, private venv/launcher, repeat and temporary scheduler contracts | Automated tests |
| Local arm64 candidate, standalone Python and packaged stdio digest/snapshot | Passed 2026-10-17 on macOS 27.0.1; unpublished |
| Native arm64 released bundle, packaged stdio digest and snapshot | Workflow available; not run |
| Native Intel released bundle, packaged stdio digest and snapshot | Workflow available; not run |
| Clean Mac without a checkout/Python/Poetry | Pending |
| New athlete's interactive Garmin login/import | Pending, user operated |
| Fresh Work conversation from installed release | Pending |
| Fresh Claude Code conversation from installed release | Pending |
| Fresh Codex conversation from installed release | Pending |
| All selected clients share the same athlete installation | Pending |

The local candidate check used only verified binary wheels and standalone CPython
3.13.16 in temporary storage, with a scrubbed PATH and no borrowed developer
dependencies. It called both actual packaged stdio tools. It used neither Garmin
transport nor the real scheduler. This is evidence of dependency isolation on the
current host, not a released artifact or fresh-client acceptance.

Issue #86 retains the required clean-Mac and fresh-client acceptance checks until
they are actually observed. A written client configuration or local MCP protocol probe
cannot establish model instruction adherence.

## Preparing and publishing a release

Repository development still uses Poetry. Release tooling additionally needs the
Poetry export plugin; the workflow provisions pinned Poetry 2.2.1 and
`poetry-plugin-export` 1.9.0 without changing the installed user's environment.

1. Integrate the athlete onboarding and client integration changes, run the normal
   development checks, and choose a package version.
2. Commit the exact version and create/push its matching `v<version>` tag deliberately.
3. Run **macOS release candidate** with that existing tag and publication disabled.
   Both native jobs build a locked binary wheelhouse, audit the wheel, install the
   actual bundle with a private standalone Python, and call digest/snapshot through
   the packaged real stdio server. No developer dependencies are borrowed.
4. Review the native results, then run the workflow with publication enabled to
   publish that tested tag as a prerelease. It uploads architecture-specific runtime
   bundles, manifests, installer scripts and SHA-256 lists.
5. Verify the uploaded installer asset exists. Copy its exact versioned download URL
   into the acceptance record and user instructions, with the required architecture.
   The command consists of downloading that **existing** `install-arm64.sh` or
   `install-x86_64.sh` asset with curl and passing it to `/bin/sh -s --` in Terminal.
   Do not use a moving branch or latest release URL as if it were an immutable version.

The installer verifies Python, bundle and manifest hashes before execution. Runtime
requirements come from the Poetry lock, are binary wheels only, and are installed with
required hashes and no package index. A missing compatible wheel stops release
preparation; users never need a compiler. Standalone Python and its licenses come from
[python-build-standalone](https://gregoryszorc.com/docs/python-build-standalone/main/running.html).
Checksums fetched from the same release establish content consistency; the initial
installer remains trusted code obtained over GitHub HTTPS.

Intel macOS uses a platform-specific lock for `cryptography` 48.0.1, the last
[official universal2 wheel](https://pypi.org/project/cryptography/48.0.1/#files); the
existing 49.0.0 lock remains on other platforms. Upstream
[removed Intel support in 49](https://cryptography.io/en/49.0.0/changelog/). This
compatibility ceiling needs maintenance review before future release promotion; it
is not a source-build fallback. The complete locked Intel wheelhouse must still pass
the native installation and stdio checks.

Each architecture has a dedicated installer. It uses macOS-provided shell, curl, tar
and shasum. It provisions Python privately and does not modify system Python, install
Homebrew/Poetry/Xcode, or edit shell profiles. A client must already be installed and
signed in; its normal trust/activation UI remains a user step.

## Terminal setup and repeating it

The installer names the athlete installation, asks for any subset of `work`, `codex`
and `claude-code`, then delegates to the existing athlete `setup` and `clients configure`
commands. Login, password and MFA prompts remain attached to Terminal. A new athlete
chooses their real-data start and weekly template; existing athletes rerun without
changing these choices. No owner files are copied.

Optional arguments are `--home <athlete installation>`, `--runtime-root <program root>`,
`--clients work,codex,claude-code`, `--to YYYY-MM-DD`,
`--configure-only`, `--daily HH:MM`,
and `--no-schedule`. First-run data choices are terminal prompts in athlete onboarding;
existing choices are preserved when setup resumes.

Setup reads only newly written onboarding result metadata after each terminal run;
a nonzero exit without a new result is incomplete with unknown import outcome. It
checks the client status command after registration and preserves each selected
client's state and required actions, including missing apps and failed registration.

Setup records actual stages in the program root's `install-state.json`, including
versions, selected installation, client preparation and scheduler ownership. This
receipt contains no password, tokens, profile prose or athlete numbers. Partial import,
missing evidence, missing apps and activation requirements remain visible. Running the
same versioned command again reuses the selected installation and resumes incomplete
stages. The installer does not silently upgrade to a different release.

Each prepared release has an inventory of its owned files, directories and symlink
targets. Repair/removal verifies every remaining entry against that inventory; missing
owned program files are allowed, while added or edited entries are preserved. Only
known Python bytecode caches associated with inventoried source files may change.

A missing private Python/venv makes the stable launcher print a repair instruction.
Rerun the same versioned installer, keeping the athlete installation. Do not delete the
athlete installation to repair the program. An edited or unrelated launcher, release,
client entry or job is preserved and needs deliberate repair. An interrupted initial
program build with no complete ownership inventory is also preserved; select a fresh
program root or review those files before manual recovery. Personal athlete state
does not need to be removed.

## Optional daily refresh

Scheduling defaults off. Setup shows the local daily time (06:00 by default), selected
installation, exact command and log destinations before asking to enable it. `--daily
HH:MM` is an explicit opt-in; `--no-schedule` skips the offer. Leaving it off is a
completed setup. Repeating setup leaves an already-owned enabled job in place;
removing a job is a separate lifecycle operation.

One installation-specific user LaunchAgent invokes the absolute stable CLI launcher
with `--home <athlete installation> daily`. It does not run immediately or continuously.
It uses the saved Garmin login and never puts a password in the plist. If saved login
cannot resume without a terminal, the existing daily path fails with an actionable log;
resume interactively in Terminal. Existing daily exit codes remain 0/1/2.

The setup receipt tracks the exact owned plist contents. Repeated setup avoids duplicate
jobs and refuses to overwrite an unrelated or edited job. Developer tests use a temporary
LaunchAgents directory and fake launchctl; they never operate the developer's real job.

## Consumer acceptance record

Use an actually clean supported Mac/user environment and the published architecture
installer. Record exact command/tag, artifact hashes, OS/CPU, account prerequisites,
client/application versions, installation paths, optional scheduling choice and results.
Do not record credentials or personal athlete numbers.

First create a synthetic installation and check each selected client in a fresh chat.
Then the new athlete runs their own interactive Garmin onboarding and bounded history
import. Confirm a readable first digest, or its honest missing-evidence state. Repeat
installation, interrupt/resume one stage and check that personal state remains intact.
For each selected client use this first-read prompt:

> Read the coach skill, reporting reference and my profile. Use only get_digest and
> get_snapshot. Explain the evidence dates, missing data and freshness. Do not refresh
> Garmin or write anything. End with the digest disclaimer.

Record actual tool calls and instruction/profile/reference access separately for Work,
Claude Code and Codex. Explain any unavailable capability; do not count another client
as evidence. Accounts and trust actions remain explicit, user-operated steps.
