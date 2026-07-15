# 05 - Coach skill renders "Rekomendacja na dzis"

Status: ready-for-agent
Blocked by: 02, 03, 04
Sources: `docs/prd/phase-10-recommender/PRD.md` (Rendering; Further Notes). Coach skill:
`skills/coach/SKILL.md`.

## Goal

Close the phase's user-facing DoD: the coach report renders a single, cited recommendation
for tomorrow in Polish, assembled from the `recommendation` block the digest now carries.
This is the integrate-and-verify slice - it reads the full block (intensity verdict + avoid
+ re-plan) that tickets 02-04 built.

## Scope

- **New "Rekomendacja na dzis" section** in `skills/coach/SKILL.md`, rendered from
  `digest["recommendation"]`:
  - the recommended `intended_type` with its `intensity_cap` and `pace_target_s_per_km`,
    and whether it is a downgrade from `planned_intent`;
  - the cited reasoning in Polish prose, translated from the `rationale` signal codes (and,
    for an active niggle, naming the `body_part` the `NIGGLE_REDUCED_MODE` signal carries);
  - the `avoid` list when non-empty;
  - the re-plan menu when `replan` is present: the three options with their `cite`, and which
    one is `recommended`, framed as the athlete's choice.
- **Keep the disclaimer.** The recommendation is a reading plus a suggestion, never a
  prescription; preserve the existing `DISCLAIMER` framing.
- **All Polish narrative stays in the skill.** `recommend.py` emits only codes and scalars
  (code-style rule: Python is English-only); the skill owns the translation and phrasing.
- **Omit gracefully** when `recommendation` is absent (no horizon) - no empty section.

## Tests / verification

- No Python unit test owns skill prose; verify by generating a report against fixture DBs
  and reading `report.md`:
  - a green day renders "trzymaj plan" with no downgrade and no citations;
  - a hot-ACWR / HRV-low / niggle day renders the softened session with the cited reason(s)
    in Polish;
  - a missed-week fixture renders the three cited options with the recommended one marked.
- The existing digest/recommend golden tests (tickets 02-04) remain green.

## Done when

- Running the coach report on a downgraded-day fixture shows a Polish "Rekomendacja na dzis"
  section naming tomorrow's session, its cap/pace, the cited reasons, anything to avoid, and
  the re-plan menu when the week was missed.
- The disclaimer is intact and the section is omitted when there is no recommendation.
- `task check` green.
