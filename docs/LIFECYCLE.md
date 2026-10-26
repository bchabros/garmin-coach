# Diagnose, update and remove Garmin Coach

The athlete installation contains your records. The managed runtime under
`~/Library/Application Support/Garmin Coach` contains the program. Program removal
preserves the athlete installation, including its saved login, and any database,
plans or tokens you explicitly configured outside it.

These commands use the normal explicit selection. Replace the sample athlete installation
and use `--runtime-root` after the subcommand if your program is installed elsewhere.

```bash
garmin-coach --home "$HOME/GarminCoach" doctor
garmin-coach --home "$HOME/GarminCoach" doctor --live-auth
garmin-coach --home "$HOME/GarminCoach" program backup
garmin-coach --home "$HOME/GarminCoach" program update --manifest /path/to/release-manifest.json
garmin-coach --home "$HOME/GarminCoach" program remove --client claude-code
garmin-coach --home "$HOME/GarminCoach" program remove --client claude-code --confirm
garmin-coach --home "$HOME/GarminCoach" program remove
garmin-coach --home "$HOME/GarminCoach" program remove --confirm
```

Local diagnosis reads the existing database without migrating it, checks the runtime,
reports program and Python versions, verifies skill/client artifacts and examines the
owned daily job. A bounded stdio probe initializes the verified server, lists its tools
and reads the selected installation's digest and snapshot without migrations or writes. It never imports
training or contacts Garmin. A SQLite read-only connection may create WAL/SHM coordination
files beside a WAL database; it does not alter its records or migrate its schema. It reports client
registration state and recovery actions independently for each selected assistant.
An interrupted `preparing` registration remains incomplete even when another client
is prepared. Registration history remains separate from actual activation: `client_acceptance: not_run`
means no fresh client conversation has been accepted. Client command output and
credentials are withheld.

Database checks share schema compatibility and independent mart/snapshot dates with the
other installation flows. Diagnosis requires both dates to be present, without adding
an age or date-equality threshold. Candidate checks run the selected release's packaged
probe in its isolated interpreter; older supported releases retain their own probe and
validated read-only opener. A failed import, unsupported probe version or protocol error
refuses activation rather than falling back to an older check.

`--live-auth` explicitly attempts to resume the selected installation's saved Garmin
login. It does not perform sync or a workout operation and never prompts for account
credentials. An unverified result may mean expired credentials or an unavailable
network; renew the login by running setup from a terminal. Missing local tokens are
reported as `missing` before any login process is started. Token files alone do not
prove that a login is still valid. The login subprocess uses isolated Python imports;
the current folder and `PYTHONPATH` cannot replace the installed authentication code.

If the current interpreter is missing, the stable launcher explains how to repair it.
Rerun the pinned installer used for this release with the same athlete home. A Python
diagnostic cannot execute without a working Python runtime. Source checkout users
may continue their existing workflow; lifecycle commands do not adopt or delete a
checkout, assistant registration or manually created scheduler by implication.

## Updates and recovery

Download the release manifest for your Mac from the documented release process and
pass that local file to `program update`. Release preparation uses verified assets
and a separate version directory. It does not modify the active environment in place.

Before the candidate reads your storage, the updater creates a private recovery folder
under `<athlete-home>/backups/program/<timestamp>-<unique-id>/`. It contains a consistent
SQLite backup, `settings.env`, the prior program receipt and copies of generated client
assets/launchers when present. The backup includes committed WAL records and supports
an explicitly configured external database path. Treat it as private athlete data.
`program backup` creates the same backup independently; no retention policy deletes it.

The candidate validates the existing schema through a read-only connection. This
release refuses an incompatible schema upgrade. It never silently migrates your
database from a probe or launches a daily job to force a migration. Keep the printed
backup and use the explicit offline features recovery workflow described in
[OPERATIONS.md](OPERATIONS.md) only after reviewing the required storage change.

After successful checks, the updater activates the candidate, refreshes only selected
client plugins and retains the previous release. Restart each selected client and
verify its coach skill and tools in a fresh conversation. Work and Codex share their
host registration; Claude Code has a separate one. Updates do not add assistants,
permissions or a daily schedule. Repeating the already active verified release is a
no-op.

A failed update restores the prior runtime and integrations where supported. If an
assistant refuses rollback, the outcome is `recovery_required`, with the backup path
retained in the receipt. The prior runtime pointer is restored before attempting client
recovery. Diagnose first, preserve both releases and use the backup's receipt/assets
to repair the exact owned registration; do not delete history or reset the database.
An interrupted update is recovered from its recorded backup and previous runtime when
`program update` is retried. The journal records the selected athlete installation,
manifest, candidate and recovery phase. Preserve both runtimes and the backup if native
client recovery fails; retrying the same command attempts recovery again.
The program-state owner retains the existing v1 format and older field aliases.
For v0.1.0's pre-versioned update journal, even `preparing` may mean clients were
already detached, so recovery restores them. Unknown v1 phases are also treated as
potentially mutating; unsupported operation versions are refused with the receipt
preserved. Restoring prior activation retains newly recorded builds, release inventories
and launcher ownership. Unreadable program state is reported by doctor while its other
checks continue; backup preserves the original receipt bytes.
No command automatically overwrites the live database with an older backup.

## Removal

Removal previews by default. `--confirm` removes only resources whose ownership is
recorded and whose generated identity still matches. A modified launcher, release
inventory, plugin asset or scheduler causes a refusal so it can be inspected. Release
ownership includes an inventory whose digest is recorded outside the release itself.
Unknown notes, edited files and changed symlinks cause refusal before any client or
job is removed. Older releases without a verified inventory remain preserved and need
deliberate recovery using a separate runtime root. A missing
owned file is handled as partial installation state. A missing job plist does not skip
stopping its owned, installation-specific launchd label; unverifiable labels are refused.

Configured databases, plans, tokens and logs must be outside the program root.
Maintenance resolves these paths, including symlink aliases, before changing any
native registration. If personal storage overlaps the program, move those records to
the athlete installation or another separate folder and update its settings first.
Diagnosis names this as a storage conflict; backup remains available before repair.

Removing one assistant retains the others and the daily job. Removing Codex retains
the shared OpenAI registration while Work is still selected, and conversely. Full
removal disconnects the selected plugins, stops the exact owned launchd label and
removes the recorded runtime releases and launchers. Unknown files, unowned releases,
unrelated plugins and other jobs are preserved. A small removal receipt is retained
so repeated removal can report the same athlete installation. Generated integration
files may remain after client registration is removed; they contain no athlete records.

The command reports where the athlete installation remains. It does not erase the
database, profile, authored plans, reports, workout specs, push receipts, tokens or
recovery backups, and it does not remove anything from Garmin.

## Acceptance evidence

Offline tests use temporary installations, real SQLite and fake acquisition/client/
launchctl boundaries. They cover WAL backups, storage refusal, owned activation/client
rollback, interruptions, shared-host removal, scheduler failure, symlink aliases,
unknown release contents and isolated authentication imports.

Consumer/native acceptance remains separately pending in [distribution](DISTRIBUTION.md)
and [client setup](CLIENT_SETUP.md#activation-and-acceptance). Use synthetic evidence
first and the tester's own terminal for any explicitly requested Garmin authentication.
