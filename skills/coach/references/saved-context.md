# Finding saved coaching context

Resolve project paths from the athlete's project root, even when this skill is
installed elsewhere. Bundled `references/` paths resolve from the skill directory.
Use the source relevant to the question rather than loading every folder.

## Sources by question

| Question | Starting point | What it establishes |
| --- | --- | --- |
| Race results, comparisons, station losses or time scenarios | `data/races/index.md`, then the linked review and result tables | External team/race results, source URLs and saved analysis |
| Preparation for a recorded race | `get_events`, then any linked preparation note | DB race date, priority, status and target; notes hold context and open decisions |
| A previous coaching read or lesson | The requested date's `reports/<date>/report.md` | What the coach concluded at that time; not today's standing |
| An authored training week | Planning reference, then `get_plan` or `plans/<monday>_week.md` | The plan of record and its revision notes |
| A previous workout or push | Authoring reference, then `get_pushed_workouts` and relevant dated records | Authored spec and push history; `get_workout_status` establishes current Garmin state |
| A preference, constraint or standing decision | `memory/athlete-profile.md` | Qualitative athlete context; its editing contract remains unchanged |
| Current Garmin-derived form, zones, load or recovery | Reporting reference, then sanctioned coach reads | Current digest/snapshot values and their freshness |

Read the existing reporting, planning or authoring reference before crossing into
its tool/file route. Finding a file does not authorize changing it or its associated
training plan, calendar, profile or Garmin workout.

## Race archives

For every race-result comparison or time scenario, first read `data/races/index.md`.
Follow its links to the relevant review, `manifest.json` and CSV tables. Select the
event, partner and division explicitly; a review can include several races. Use
published totals for the race decomposition and keep raw split `Diff` values
separate when rounding differs. Cite the table or source behind the inputs.

Use `get_events` for recorded goal dates, priority and target. An archived
`event.json` is a local reference copy; the DB calendar wins if they disagree.
Keep the athlete's precise wave time distinct from the category's start window.

For a time budget, state the assumed run distance, pace, station total and Roxzone
total. Label substitutions between races and distinguish the arithmetic scenario
from a physiological prediction. For SUB goals the finish must be below the target:
with whole-second race times, the largest acceptable finish is `target_s - 1`.
Elapsed run splits are not verified pace per kilometre; first/last timing sections
and venue layouts can differ. Doubles results describe the pair, not individual work.

## Focused lookup and missing files

When the index or an explicit link answers the question, stop searching. Otherwise
list only the relevant folder and search for the named race, partner, date or topic.
Use `rg --files --hidden --no-ignore <folder>` to include private project files,
then `rg` within the candidate notes. Avoid `.env`, Garmin tokens and direct SQLite
or mart inspection. This route adds file retrieval, not a new Garmin transport.

Treat archived reports as dated evidence and scenarios as assumptions, not achieved
results. Claims from a previous chat are not saved project context unless a file
actually contains them. Preserve the source date when using an older conclusion.

If an index is absent but the relevant folder exists, search that folder narrowly.
If the folder/file is absent or inaccessible, name what is missing and continue
with available evidence. Private archives are gitignored and may be absent in a
fresh clone, worktree or MCP-only client. MCP calendar/digest access does not itself
provide access to local CSVs. Ask for only the missing input needed for the question;
do not invent a saved record or refetch external results unless the request authorizes it.
