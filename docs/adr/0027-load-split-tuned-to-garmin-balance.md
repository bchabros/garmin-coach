# ADR 0027 - Split every session's load by intensity, tuned to Garmin's own balance

## Status

Accepted. Supersedes the "Load buckets are total over nulls" clause of
[ADR 0002](0002-phase-2-metrics-semantics.md) and the `AEROBIC_LOW_SHORTAGE` targets of
[ADR 0003](0003-phase-3-coach-signals.md).

## Context

ADR 0002 filed each session whole into one bucket by Garmin's Training Effect:
anaerobic when `anaero_te >= 1.0`, else low when `aero_te < 2.5`, else high. Aerobic
Training Effect measures how much stimulus a session delivered, which grows with
duration as well as intensity, so a genuinely easy Zone 2 run of 45-55 minutes scores
2.8-3.6 and was filed as high (issue
[#70](https://github.com/bchabros/garmin-coach/issues/70)). Between 2026-06-08 and
2026-09-13 not one day had any low-aerobic load, although 28 of 35 runs averaged at or
under the personal Z2 ceiling, and `AEROBIC_LOW_SHORTAGE` fired every night.

The anaerobic rule distorted more. 10 of 39 runs and 20 of 23 Hyrox/HIIT sessions were
filed whole as anaerobic, among them easy runs with strides (2026-08-24: average HR 141,
96% of the time in zones 1-2). 67% of the last 28 days' load sat in the anaerobic bucket
where Garmin's own balance has 20%.

The athlete's goal for the fix was a split that reads as close to Garmin's 28-day load
balance as a per-session rule can. An exact copy is impossible: Garmin publishes only
the 28-day total, and the daily and weekly marts need a share per session. Garmin's
balance and our cardio load are the same quantity (3076 against 3072 load points on
2026-09-20), so candidates could be scored share for share.

Candidates, scored as the mean absolute difference of the three shares against
Garmin's balance over the 74 days 2026-07-06..2026-09-20 (last column: 2026-09-20,
where Garmin reads 32 / 47 / 20):

| rule | mean difference (points) | low / high / anaerobic |
|---|---|---|
| ADR 0002 (Training Effect flags) | 28 | 1 / 32 / 67 |
| plain minutes in zones 1-2 / 3-4 / 5 | 15 | 57 / 27 / 15 |
| zone minutes with textbook 1-5 weights (Edwards) | 8, drifting to 10 in the second half | 42 / 33 / 25 |
| zone minutes with weights tuned to Garmin | 4 | 34 / 41 / 25 |
| **chosen: anaerobic share from the two Training Effects, the rest by tuned zone weights** | **2.7** (2.5 first half, 2.9 second half, max 5.1) | 33 / 49 / 18 |

## Decision

- **Every cardio session is split, never filed whole.**
  1. *Anaerobic share:* the anaerobic Training Effect at half weight against the aerobic
     one, `0.5 x anaero_te / (aero_te + 0.5 x anaero_te)`; zero when both are absent.
  2. *The rest, between easy and hard:* by seconds in the watch's heart-rate zones
     weighted 1, 2, 4, 4, 8; zones 1-2 are easy (`load_low`), zones 3-5 hard
     (`load_high`).
- **Input zones are the watch's per-activity zone seconds**, which ADR 0007 left
  untouched. They agree with the personal bands where it matters: the watch's zone 2
  ends at 157 bpm, the personal band at 158, and zone 1/2 is 142 on both.
- **Fallback.** A session with no zone time keeps the ADR 0002 rule for the
  non-anaerobic part (aerobic TE below 2.5, or absent, is easy). No stored session needs
  it today. The buckets stay total over nulls and always sum to `load_day`.
- **Unchanged:** strength load goes whole to `load_strength`; the split applies to the
  blended session load (ADR 0010), so `load_day`, ACWR, monotony and strain do not move.
- **The tuned numbers are named constants in the marts layer**, not coach thresholds:
  they define a mart column, and changing them requires a full `features` recompute.
- **The alert follows Garmin's own lower bound.** With a Garmin-like split the easy share
  ranged 16-42% over the summer, so the 60% target was unreachable: the alert would have
  fired on 74 of 74 days. It now fires when the easy share over **the 28 days ending on
  the digest's last day** is below Garmin's lower limit for low-aerobic load as a share
  of its balance total (`ml_aero_low_min` over the three `ml_*` values, about 23% in
  September 2026), read from the newest day of those 28 that carries the whole balance.
  Both sides of the comparison describe the same 28 days, whatever window the digest
  covers. The fixed floor `aero_low_target_share` (0.25) stands in when Garmin publishes
  none. The hard-share condition and `aero_high_target_share` are gone; Garmin has no
  such condition either. Replayed over the summer with the real pipeline, the alert
  fires on 5 of 77 days and its verdict matches Garmin's phrase on 73 of 77.
  `garmin_agrees` stays as the cross-check.
- **A drift is watched, not assumed away.** After the marts are rebuilt the nightly run
  logs `daily: load split differs from Garmin's 28-day balance by N pp`.

## Alternatives considered

- **Keep the anaerobic flag and only fix low/high** (the first proposal in #70): leaves
  67% anaerobic, and flips the alert from always on to practically never on.
- **Textbook Edwards weights:** defensible without tuning, but 8-10 points off, which is
  not "close to Garmin".
- **Read Garmin's balance directly** for the headline: exact, but only for the 28-day
  total; days and weeks would still need a rule, and two rules would disagree.
- **Average HR against the personal Z2 ceiling** (as `personal_z2_minute_share` does):
  all-or-nothing per session, so a tempo run with a long warm-up lands entirely in high.

## Consequences

- The rule has two tuned knobs fitted on 74 strongly correlated days of one athlete's
  summer. The held-out check is reassuring (weights fitted on 2026-07-06..2026-08-12
  score 3.4 points on the later half; the round numbers chosen score 2.5 and 2.9), and
  the optimum is a flat plateau rather than a sharp peak, but a winter without Hyrox may
  not fit. The nightly log line is the tripwire; a sustained gap above about 5 points is
  the cue to re-run the comparison.
- The alert reads 28 days, not the digest's 7-day highlight window. The first version
  compared the last 7 days with Garmin's 28-day bound. On 2026-09-20, after the
  recompute, it fired on a hard peak-block week (19% easy) while the month was 33% easy
  and Garmin called it balanced: a one-week share swings between 8% and 47% here, so a
  single hard week crossed a bound that describes a month. The digest headline still
  shows the last 7 days, so a hard week stays visible without the alert.
- A recompute rewrites the whole history since `data_start`: past weeks change, and a
  report for a past date reads differently from what was written at the time.
- The coach skill text ("add Zone 2") needs no change. The signal's facts gain
  `target_source`.
