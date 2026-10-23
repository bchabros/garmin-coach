# Writing the coaching report

Read this before touching `digest.json` or `snapshot.json` - including a quick standing
answer that never writes a report file. It carries what the fields mean, which of them
may be null, and the structure of `report.md`.

## Read-only questions

For a quick standing answer, a chat-only report or an explicitly read-only request,
use `get_digest` and `get_snapshot` when the sanctioned coach MCP tools are available.
Their payload is under `data`; keep the accompanying `freshness` metadata when
explaining evidence dates, partial data and unconfirmed days. The field descriptions
below apply to that payload too. Read only the additional tools needed for the question.

Without MCP, use existing artifacts from the selected athlete installation and name
any missing or stale evidence. Do not generate report files, import plans, rebuild
metrics, refresh Garmin or repair a day as part of this read-only route. If the
required evidence is unavailable, state the prerequisite rather than constructing it.
Answer the question using the profile rail, null handling and source rules; finish
with the digest's disclaimer verbatim when a digest is available. Stop after the answer.

## Requested report artifacts

Enter this procedure only when the athlete requests a saved report or charts, and
file access and a supported runtime for the selected installation are available.
A chat-only report uses the read-only route above. If a managed runtime is unavailable,
report that prerequisite; source-checkout commands are not an installation fallback.

1. **Generate the digest and charts.** Run:

   ```bash
   poetry run garmin-coach plan import       # cache plans/<monday>_week.md (transport-free)
   poetry run garmin-coach report            # or: --from YYYY-MM-DD --to YYYY-MM-DD
   ```

   `plan import` first, because the report reads the plan of record from the DB cache.
   It imports authored weeks and rebuilds their marts itself; it never touches Garmin.
   Report accepted weeks, any `invalidated_pushes`, and dated `warnings` for conflict
   checks blocked by unreadable workout evidence. Warnings do not establish agreement
   with the watch. If it fails, state
   the error and any accepted weeks, then stop rather than scoring against a stale cache.

   `report` writes `reports/{today}/`: `digest.json`, `hrv_band.png`, `acwr.png`, and
   `snapshot.json` (the current standing). If it fails because the mart is empty, run
   `poetry run garmin-coach features` first, then retry. No additional rebuild is
   needed after a successful plan import. Never run `sync`/`backfill` yourself - that would call Garmin live, which the golden rule
   forbids from the coach layer; tell the operator to run it instead.

   Use the managed launcher or supported source environment described in `SKILL.md`
   ("Running the CLI"). Examples above assume the athlete installation is selected.

2. **Read only `reports/{today}/digest.json`.** It has `window`, a `headline` block
   (latest ACWR + `acwr_reliable`, latest HRV vs its band, 7-day load + shares), a
   `signals` list already ordered alert > warn > info, a `zones` block (personal
   training zones; may be null), a `plan` block (the periodization standing; may be
   null), a `movement` block (per-set map coverage; may be null), a `recommendation`
   block (tomorrow's prospective session advice; absent when there is no horizon), and a
   `disclaimer`. Do not open the mart or recompute anything.

   Also read `reports/{today}/snapshot.json` (may be absent if the mart was never
   materialized). It is the current standing: `computed_at`, `vo2max` (+ `vo2max_delta`
   / `vo2max_span_days`), `hrv_baseline`/`hrv_sd` (+ `hrv_delta`
   / `hrv_span_days`), `acwr` +
   `acwr_reliable`, `load_7d` + shares, `readiness_score`/`readiness_level`,
   `sleep_debt_h`, heat/altitude acclimation, the personal zones (mirrored), and
   `planned_intent_today`/`planned_label_today` + `plan_source_today`. Every field may
   be null; never invent a number a null field does not provide.

   `plan_source_today` says where today's plan came from: `plan_week` is the week the
   athlete authored (the plan of record), `plan_template` is the repeating fallback
   shape they selected, not an authored plan for that week. A null source means no
   planned session is known. Never present a `plan_template`
   day as "your plan"; say the week is unplanned and the template is standing in.

3. **Write `reports/{today}/report.md`.** Structure:
   - **Twoje aktualne staty** - only when `snapshot.json` is present. One compact block
     opening the report with where the athlete stands now: VO2max and its trend (state
     `vo2max_delta` over `vo2max_span_days` days when non-null, e.g. "VO2max 52, +1.0 w
     24 dni"), HRV baseline + its weekly-average trend, Training Readiness
     (`readiness_score` + `readiness_level`), the load/ACWR standing (`acwr` with
     `acwr_reliable` - call it *orientacyjny* when false - plus `load_7d` and the
     `low_share`/`high_share`/`anaero_share` split), the zones headline (`z2_hi_bpm` HR
     ceiling and `z2_pace_ceiling_s_per_km` as min:sec - "easy pod X:XX/km"; note
     `zones_stale` when 1), and today's plan (`planned_label_today`, flagged as the
     fallback template when `plan_source_today` is `plan_template`). Skip any sub-item
     whose value is null. Skip the whole block when `snapshot.json` is absent.
   - **Nagłówek** - one line on the window and the headline numbers (ACWR + reliability,
     latest HRV vs baseline, 7-day load split). When the `zones` block is present, add
     the Z2 pace ceiling so the read is actionable: "trzymaj easy run pod X:XX/km"
     (convert `z2_pace_ceiling_s_per_km` to min:sec) and the Z2 HR ceiling
     (`z2_hi_bpm`). If `zones.stale` is 1, note it briefly - the zones come from an LTHR
     detection `lthr_age_days` days ago (on `lthr_detected_on`), past the staleness
     cadence; suggest a harder threshold effort to refresh them. Do not invent numbers
     when `zones` is null. If `zones.matches_horizon` is false, the zones are the
     athlete's *current* standing next to an as-of window - say so instead of dating
     them to the window.
   - **Sygnały** - one short paragraph per signal, most severe first. State the actual
     numbers from `facts`. Map each code to a concrete action:
     - `HRV_LOW_MORNING` -> degrade today's quality session to easy.
     - `ACWR_OUT_OF_RANGE` -> over-reaching / detraining risk; if `reliable` is false,
       call the ratio *orientacyjny* (indicative), do not over-react.
     - `AEROBIC_LOW_SHORTAGE` -> too much grey zone, add Zone 2. Note whether Garmin
       agrees (`garmin_agrees`): agreement strengthens the call, disagreement -> hedge.
       When `facts.personal_z2_minute_share` is present, cite both reads: the load-bucket
       share and the personal-zone share (how much of your run time was actually at avg
       HR under your Z2 ceiling). If the two diverge, say so - it is itself informative.
     - `TWO_HARD_DAYS` -> flag the back-to-back stack; if `trailing` is true, it is an
       *upcoming* risk (the Friday-into-Saturday pattern), not just history.
     - `HRV_SLEEP_CONFOUND` -> caution: the worst HRV may be sleep-driven, not training;
       do not confuse causes.
     - `DELOAD_ADVISED` -> load has climbed for several weeks into a hot ACWR or high
       monotony; suggest a back-off (deload) week. State `rise_weeks`, `acwr_end`, and
       `monotony` from `facts`.
     - `PATTERN_STACK` -> the same movement pattern(s) (`facts.keys`, e.g. hinge) were
       loaded on back-to-back days without a rest day; name the pattern(s) and
       `overlap_max`, suggest spacing them or inserting recovery.
     - `MUSCLE_OVERLAP` -> the same muscle group(s) (`facts.keys`, e.g. grip + posterior
       chain) stacked across adjacent sessions; flag the recovery risk on those tissues.
     - `HARD_RPE_YESTERDAY` -> yesterday's session was subjectively very hard
       (`facts.rpe` on Borg CR10); the next day should back off. This feeds tomorrow's
       recommendation below.
     - `PLAN_MISSING` -> the week starting `facts.week_start` has no authored plan.
       With `facts.source=plan_template`, a repeating shape stands in; with a null
       source there is no fallback and the planned intent is unknown. Offer to plan the week (read
       `references/planning.md` before you propose one); do not treat the template days
       as agreed sessions.
   - **Movement coverage** - when the digest's `movement` block is present and
     `sets_unmapped` > 0, add one brief line that the overlap read is partial: N of
     `sets_total` sets are unmapped (`unmapped` names), so those exercises need adding to
     the movement map. Skip entirely when `movement` is null or nothing is unmapped.
     `UNKNOWN` in that list is a Hyrox / group-HIIT circuit the watch recorded as one
     nameless set: ask the athlete for the stations and log them with `log_sets`
     (`mcp__coach__log_sets(activity_id, stations)`) so the overlap read can see them.
   - **Tydzień: plan vs realizacja** - only if the digest has a non-null `weekly` block
     (the latest complete week). One line on the week's numbers (`load_total`, the
     low/high/anaero shares, `monotony`/`strain`, `max_consec_hard`), then the adherence:
     state `plan_adherence` when known and walk only `plan_vs_actual` rows where `match` is false,
     naming the direction (e.g. "pt: plan quality, było rest"). If `was_deload` is true,
     say so - a deliberate deload is not lost fitness. Skip this block entirely when
     `weekly` is null.

     A null adherence or daily `match` means the comparison cannot be assessed without
     a plan. Name the gap without describing unknown days as missed sessions.

     `planned` and `actual` are recorded at different granularities on purpose, so trust
     `match` over your own comparison of the two strings. `planned` is what the athlete
     meant (any of the seven intents); `actual` is only what the load can show
     (`rest`/`easy`/`strength`/`quality`), because load numbers cannot tell a crossfit
     session from a hyrox one. So `planned: crossfit` with `actual: quality` is a
     **match** - do not report it as a divergence.
   - **Blok i odliczanie** - only if the digest has a non-null `plan` block. State the
     training block (`block`: base/build/peak/taper) and the countdown
     (`weeks_to_event`) to `race_date` (`race_type`), and say whether the plan calls
     this week a deload (`is_deload`). Interpret the block, do not just name it: base is
     volume, build is specific work, peak is sharpening, taper is cutting load.
     **Never invent a phase when `plan` is null** - say plainly that no goal race is
     recorded, so the system does not know what is being trained for.
     - `TAPER_ACTIVE` -> the taper has started; do not add load. This phase only states
       the fact - do not turn it into a prescription beyond that.
     - `RACE_PROXIMITY` -> a race is `facts.weeks_to_event` weeks out. When
       `facts.needs_decision` is true, ask the athlete to commit or drop it; when
       `facts.needs_date_pinned` is true, ask them to pin the exact date, because the
       taper is planned off it.
     - When the `plan` says `is_deload` but the `weekly` block shows no drop in
       `load_total` (and no `DELOAD_ADVISED`), name the divergence - the plan asked for
       a recovery week and it did not happen. Plan and reality are tracked separately on
       purpose; the gap is the finding.
   - **Rekomendacja na dziś** - only when the digest has a non-null `recommendation`
     block. It advises the *next* session (`target_date`, i.e. tomorrow relative to the
     window) and only ever softens what the plan of record planned - it can never raise
     an easy day into a hard one. If `planned_intent` or `intended_type` is null,
     the plan is unknown: name the missing plan or template and offer to plan the week
     through `references/planning.md`. An empty `rationale` and `downgraded=false`
     do not establish a session. Do not invent rest, easy work or keep-plan advice
     for that missing intent. Otherwise render:
     - The session: `intended_type` (rest/easy/tempo/strength/hyrox/crossfit/quality)
       with the `intensity_cap` when non-null (Z2/Z3/Z4 HR ceiling) and
       `pace_target_s_per_km` as min:sec/km when non-null. A `strength` session never
       carries a pace - a running threshold pace is meaningless for lifting, so its
       absence there is by design, not missing data.
       If `pace_target_s_per_km` is null, do not invent a pace
       (the zones are still on a fallback multiplier). If `downgraded` is true, say it is
       a step down from the known `planned_intent` and give the reason; if false and
       the planned intent is known, tell the athlete to keep the planned session.
     - The reasons: translate every code in `rationale` using the Sygnały mapping above
       (e.g. `HRV_LOW_MORNING`, `ACWR_OUT_OF_RANGE`, `TWO_HARD_DAYS`,
       `HARD_RPE_YESTERDAY`, `DELOAD_ADVISED`, `AEROBIC_LOW_SHORTAGE`, `TAPER_ACTIVE`).
       For `NIGGLE_REDUCED_MODE`, name the affected body part from that signal's
       `facts.body_part` in the `signals` list. Only with a known planned intent does
       an empty `rationale` with `downgraded=false` mean "trzymaj plan".
     - `avoid` - when non-empty, one short line naming the movement patterns / muscle
       groups to keep off tomorrow (they stacked on back-to-back days).
     - `replan` - when present (not null), the last complete week missed `replan.missed`
       planned sessions, so a broken week now needs realigning. Lay out the three
       `options` (`extend` / `rebuild` / `continue`) with their `cite`, and mark
       `replan.recommended` as the suggested one for the current block. Frame it as the
       athlete's choice, not an order; note that `rebuild` is a manual call, because the
       system holds no session priorities to rebuild from.
     - This whole block is a suggestion, never a prescription - keep the reading framing.
     Skip the entire block when `recommendation` is absent.
   - **Wykresy** - embed both: `![HRV](hrv_band.png)` and `![ACWR](acwr.png)`.
   - **Zastrzeżenie** - end with the digest `disclaimer` verbatim.

## What the athlete can tell you that the watch cannot

Three local writes belong to the report flow, because what they record is what the next
read is missing. All three are transport-free. RPE and stations refresh derived data;
niggles are read directly without an unnecessary mart pass.

- **How hard it felt.** `log_rpe(activity_id, rpe, soreness?, mood?, note?)` - the Borg
  CR10 session RPE (1-10) for a stored activity. It blends into that day's load, so offer
  it when the athlete volunteers how a session felt and the digest shows no rating.
- **A niggle.** `log_niggle(body_part, severity, date?, note?)` - a sub-injury complaint,
  severity 1-5. Record it when the athlete mentions one; the niggle signal reads these.
- **The stations of a circuit.** `log_sets(activity_id, stations)` - see the movement
  coverage note above.

### Local write outcomes

Read `saved` separately from `recomputed` and `error`. If `saved=false`, the write was
refused or failed. If `saved=true` and `recomputed=false`, name the saved record and
explain that cached metrics are still older; do not say the entry was lost or add the
event again. A niggle normally has `recomputed=null`. For a partial goal change,
`block_before` is older evidence and `block_after` is unknown, not a refreshed block.

Use the returned `recovery_command` only in the same selected athlete installation
(for example `garmin-coach --home <the same installation> features`), then repeat the
cached read. Without terminal access, ask the operator to run it. This recovery is
offline; do not substitute a live Garmin refresh or claim the change was recomputed.

## When a read shows the data is wrong or missing

The reads are only as good as what is in the DB, and three things can be fixed from the
conversation instead of from a terminal (issue #72). Every one of them rebuilds the marts
itself, so the next read is already current - and every one of them needs you to say what
changed afterwards.

- **A day the plan expected a session on, with nothing recorded.** Every response carries
  `freshness.unconfirmed_days` (the digest repeats it in `window`): each entry is a day
  inside the re-check window with a planned session and no stored activity. It is **not**
  a skipped session - the watch may not have uploaded yet. Name the day and its planned
  intent, ask whether the session happened, and withhold every conclusion that depends on
  it (a deload, a missed session, an adherence score). If it did happen: the athlete syncs
  the watch, then `repair_preview(from_date, to_date)` shows what each day of the range
  holds, you show that to the athlete, and `repair_confirm(from_date, to_date,
  confirm_token)` pulls it. At most 14 days, never today - today is `refresh_today`.
- **A wrong or missing race.** `get_events` lists them; `event_add(...)` records one and
  `event_update(event_id, ...)` corrects one (pin an approximate date, commit a tentative
  start). Both answer with the block calendar's row for this week **before and after** -
  report that move ("blok przechodzi z budowania w szczyt"), because a wrong race date
  silently mis-dates every week of the periodization.
- **A plan file the athlete edited by hand.** `plan_import()` re-reads
  `plans/<monday>_week.md` into the plan of record. It reports the weeks it read and any
  `invalidated_pushes` - days whose already-pushed workout the edited plan no longer
  allows; name those days and offer to re-author them. Surface dated `warnings` too:
  those days could not be checked, even when the conflict list is empty.

## Rules

- If a signal is absent from the digest, do not mention it. Silence means "not flagged".
- One report per run day; re-running overwrites `reports/{today}/`.
