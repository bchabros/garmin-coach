---
name: coach
description: Read the athlete's daily_metrics mart via the deterministic digest and write a concise, number-dense coaching report with two charts. Use when the user asks for a training report, coach read, weekly review, or "how am I doing".
---

# Coach

Turn the `daily_metrics` mart into a short coaching read. The heavy lifting is
deterministic Python; your job is the narrative. **Never query Garmin live and never
read the raw mart** - you consume the compact digest only.

## Procedure

1. **Generate the digest and charts.** Run:

   ```bash
   poetry run garmin-coach report            # or: --from YYYY-MM-DD --to YYYY-MM-DD
   ```

   This writes `reports/{today}/`: `digest.json`, `hrv_band.png`, `acwr.png`. If it
   fails because the mart is empty, run `poetry run garmin-coach features` first, then
   retry. Never run `sync`/`backfill` yourself - that would call Garmin live, which the
   golden rule forbids from the coach layer; tell the operator to run it instead.

2. **Read only `reports/{today}/digest.json`.** It has `window`, a `headline` block
   (latest ACWR + `acwr_reliable`, latest HRV vs its band, 7-day load + shares), a
   `signals` list already ordered alert > warn > info, a `zones` block (personal
   training zones; may be null), and a `disclaimer`. Do not open the mart or recompute
   anything.

3. **Write `reports/{today}/report.md`.** Structure:
   - **Nagłówek** - one line on the window and the headline numbers (ACWR + reliability,
     latest HRV vs baseline, 7-day load split). When the `zones` block is present, add
     the Z2 pace ceiling so the read is actionable: "trzymaj easy run pod X:XX/km"
     (convert `z2_pace_ceiling_s_per_km` to min:sec) and the Z2 HR ceiling
     (`z2_hi_bpm`). If `zones.stale` is 1, note it briefly - the zones come from an LTHR
     detection `lthr_age_days` days ago (on `lthr_detected_on`), past the staleness
     cadence; suggest a harder threshold effort to refresh them. Do not invent numbers
     when `zones` is null.
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
   - **Tydzień: plan vs realizacja** - only if the digest has a non-null `weekly` block
     (the latest complete week). One line on the week's numbers (`load_total`, the
     low/high/anaero shares, `monotony`/`strain`, `max_consec_hard`), then the adherence:
     state `plan_adherence` and walk the `plan_vs_actual` rows where `match` is false,
     naming the direction (e.g. "pt: plan quality, było rest"). If `was_deload` is true,
     say so - a deliberate deload is not lost fitness. Skip this block entirely when
     `weekly` is null.
   - **Wykresy** - embed both: `![HRV](hrv_band.png)` and `![ACWR](acwr.png)`.
   - **Zastrzeżenie** - end with the digest `disclaimer` verbatim.

## Tone

Concrete, numbers first, no filler. Polish prose (matches the athlete). This is a
reading of recorded data, not medical or coaching prescription - never phrase a signal
as a diagnosis or an order.

## Rules

- Thresholds and signal logic live in Python (`signals.py`, `coach_thresholds`). Do not
  reinvent them or hardcode numbers in prose beyond what `facts` provides.
- If a signal is absent from the digest, do not mention it. Silence means "not flagged".
- One report per run day; re-running overwrites `reports/{today}/`.
