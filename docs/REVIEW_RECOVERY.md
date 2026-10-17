# Review corrections for the installation series

Issue [#97](https://github.com/bchabros/garmin-coach/issues/97) responds to the independent
review of #84–#87 at `a69f7a6d0a06666315b8e1d9def6c7219c00da10`. All development checks use
temporary athlete installations, runtime roots and HOME/XDG directories. Real athlete
files, assistant registrations and launchd jobs are excluded.

## Reported P1 findings

| Finding | Correction and evidence |
| --- | --- |
| First-day import interruption mistaken for adoption | Initial checkpoint precedes transport; old partial setup status also resumes. CLI regressions cover both forms. |
| Setup accepts external saved Garmin login | Setup refuses resolved token paths outside the selected athlete installation, including symlink escapes, before login. Legacy operational commands retain explicit external-path support. |
| Interrupted plugin generation cannot retry | The integration receipt journals old/new hashes before writes; CLI interruption tests cover retry, status and removal. |
| Global plugins can mix athlete installations | Runtime selection, serialized per-user client bindings and native inventory checks refuse conflicts before mutation; tests include stale runtimes, distinct athletes and a CLI appearing after preparation. |
| Interrupted runtime preparation cannot retry | Build intent is recorded before the first release write; failed attempts are preserved and retried in fresh sibling directories. Activation journals launcher/pointer changes. |
| Interrupted update disconnects all clients and blocks maintenance | Activation no longer eagerly removes all clients; persisted update journals are recovered on retry. Legacy preparing journals restore clients too, because v0.1.0 detached them before advancing its phase. CLI tests terminate preparation and post-activation reads, including preservation of edited launchers. |
| Identical update can rebuild active runtime | Identical verified manifests return unchanged without preparation or a backup; a regression forbids calling the builder. |
| Missing client breaks update and rollback | `client_missing` is a valid prepared outcome. Pending/failed registration still prevents successful completion; real native-failure fixtures exercise rollback. |
| Every failed update suggests incompatible-schema repair | Failure output identifies restoration/recovery outcome and gives a retry action without asserting a schema fault. Native output remains withheld. |
| Removed client reappears after installer repeat | Confirmed client removal updates the program receipt's selected clients; CLI regression checks both receipts. |

## Other confirmed behavior corrections

- Setup refuses inherited history/template overrides on resume and missing explicit
  cutoff when adopting manually configured history. An earlier requested report does
  not rewind current standing or zones.
- Setup treats optional endpoint rate limits as incomplete work; absent/unsupported
  optional data remains nonfatal. Raw transport errors and upstream library logs are
  suppressed during setup. MCP weekly reads preserve unknown adherence without a plan.
- Native marketplace/plugin registration and launchd activation can succeed immediately
  before interruption. Retry reconciles exact owned native inventory rather than repeating
  the add blindly; uncertain plugin generations force the appropriate refresh. Scheduler
  readback refuses changed/unknown diagnostic formats and requires native acceptance.
- Source configuration and plain Poetry wheel builds select exactly the four public
  coach resources. Synthetic private Markdown regressions cover source, installed
  distribution metadata and a copied dirty checkout; private athlete context is excluded.
  Prior owned private Markdown is removed through the generation journal and forces a
  clean native reinstall; unknown/edited files block setup. Restore filters old snapshots
  rather than reintroducing private resources.
- Complete bootstrap repeats reuse recorded compatible history. Scheduler failures
  retain an honest summary/action, scheduling intent precedes plist visibility, and
  repeat setup preserves the selected time. Legacy daily jobs are reported and block
  enabling a duplicate; they are never adopted or removed implicitly.
- Rosetta is refused with native-architecture guidance. Release tooling rejects
  untracked skill resources generically. Tests and native CI execute the generated
  installer shell and installed CLI under temporary HOME; no release URL is invented.
- Client additions preserve successful registrations absent from PATH. Preparing states
  remain visible, case aliases identify the same installation, and removal checkpoints
  only attempted/owned native resources. Native stdin is closed. Generated instructions
  use stable launchers and omit source dependency-install fallbacks.
- Rollback restores prior plugin snapshots through the current generation transaction,
  preserving unrelated files. Claude uses its update command; OpenAI's supported refresh
  still requires remove/add and may require permissions to be approved again.
- Doctor reports corrupt program receipts, missing local tokens, legacy jobs and pending
  ownership separately. Its bounded stdio probe initializes the verified server, lists
  tools and reads the explicitly selected database without migrations or transport.
- Compatibility validation checks required index/view/trigger definitions and reference
  policy keys as well as tables/columns/primary keys. Personal policy values and compatible
  additional schema objects remain preserved. Removal previews list owned resources and
  retained personal paths; an absent owned plist does not skip stopping its verified label.

## Standards and observations

The installation command forms now agree. Changed source lines respect the documented
100-character limit; recovery/activation responsibilities are split into focused helpers.
Developer testing seams, the bootstrap's standard-library-only contract, the gotchas
heading, explicit template choices, operator links and agent pointers are current.
ADR 0034 records client binding and recovery trade-offs; the glossary names the different
receipts, bindings, runtime root and recovery backup. Shared runtime defaults/receipt reads
and activation now reuse their owning module.

Several observations are deliberately not broad refactors: first-run template seeding
remains in the existing database-opening seam so all initial creation paths share the
same choice; invalid input is translated into an actionable CLI installation error.
Unknown plan values are fixed at the MCP read boundary without rewriting historical
adherence rows. The wheel's existing skill resource location is retained to avoid an
unrelated packaging migration; canonical resource paths are explicitly allowlisted and
package content is audited. Each new update attempt keeps a fresh consistent database
backup rather than reusing a potentially stale one. Historical PR bodies and frozen architecture records are not
rewritten.


The review's broader observations have these dispositions:

| Observation | Disposition |
| --- | --- |
| Generic exception handling hides recovery actions | Known lifecycle refusals retain actionable messages through `LifecycleError`; unexpected/native output remains redacted because it can include private settings. Client/bootstrap stage failures retain explicit retry actions. |
| Repeated runtime defaults, state readers and pointer updates | Lifecycle reuses distribution's owned defaults, receipt reader and activation. Independent atomic writers remain at their existing module boundaries; bootstrap cannot import application dependencies. |
| Repeated host switches and string states | Client host selection uses `_host`; lifecycle accepts actual preparation states, including missing CLIs. A cross-module enum/module migration is deferred because bootstrap must remain independently importable. |
| No-plan logic appears in multiple marts | The observable MCP contract now returns unknown matched values without a plan. Existing persistence and mart branches remain; no historical adherence rewrite is introduced. |
| Template selection belongs in another layer | Existing database creation remains the shared seam. Invalid template input now produces a controlled installation error before database creation or login. |
| Test environment and live-login subprocess risks | HOME/XDG and inherited athlete choices are isolated. Doctor missing-token tests forbid starting login; a real stdio subprocess reads only a temporary selected database. |
| Skill resources live at the wheel's top level | The existing location is retained. Explicit public-file packaging and runtime selection, wheel-content tests and publication audit enforce its contents. |
| `ready`, protocol checks and per-surface acceptance are ambiguous | Preparation never infers `ready`; each selected surface records pending acceptance. `clients status` inspects local artifacts and recorded registration; native inventory guards mutations, while `doctor` performs the separate stdio probe. Fresh-client behavior remains a human check. |
| Generated stdio test can inherit installation selection | The fixture removes inherited selection and uses an explicit temporary installation; package/cache stdio tests retain the unrelated-cwd contract. |
| Historical PR descriptions use another shape | Existing published PR bodies remain historical records. The corrective PR uses the current overview/bullet and evidence conventions. |

Read-only SQLite may create WAL/SHM coordination files beside a WAL database. Diagnosis
preserves records and schema; immutable SQLite mode was rejected because it can hide
committed WAL records. This filesystem side effect is documented, not claimed eliminated.

## Remaining acceptance

Automated preparation does not establish client acceptance. Keep issues #85–#87's human
checks open until real Garmin login/MFA and lower-library retries, native launchd,
consumer clean-Mac cold starts, and fresh authenticated Work/Codex/Claude Code
conversations are recorded. Native CI executes both architecture installer shells; an
Intel runner is still not a clean consumer installation. Reapproving permissions after
OpenAI plugin refresh remains a real-client acceptance item.

The corrected source package is version 0.1.1. The published v0.1.0 tag/assets are
unchanged and do not contain these corrections.
A new versioned candidate must pass native release checks before a corrected package
is offered to a new athlete.
