"""Measuring how hard an authored session is, from the targets its steps carry.

Pure and offline. The plan guard used to rank a request's ``session_type``, which
names a default shape and cannot say how hard the session will be (issue #62, ADR
0024). This module answers that instead: it reads the resolved steps of a workout
spec and reports where they sit on the scale ``core.plan`` orders plans on.

It lives beside ``author`` rather than in ``core.plan`` because walking a spec's
steps is knowledge of the spec, not of the plan of record; the scale itself, and
the comparison the guard makes, stay in ``core.plan`` where the planned intents are.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, NamedTuple

from garmin_coach.core.plan import HARDNESS_RANK

# A step that adds nothing to the session's hardness - an untargeted warm-up, cool-down
# or standing rest. Distinct from None, which means a target was found and the stored
# ladder could not measure it, and so leaves the whole session unmeasured.
_UNRANKED = "unranked"


class Measurement(NamedTuple):
    """How hard a session measures, and the step that decided it."""

    hardness: str
    step: dict[str, Any]


def measure(
    steps: Sequence[dict[str, Any]],
    zones: dict[str, Any] | None,
    *,
    threshold_tolerance_s: float,
    untargeted_work: str | None = None,
) -> Measurement | None:
    """How hard an authored session actually is, measured from the targets on its steps.

    The hardest step decides, and within a band its harder edge decides - the faster
    pace, the higher heart rate - because that is what the watch will allow. The
    deciding step comes back with the answer so a refusal can name it without the
    walk being repeated.

    Args:
        steps: The spec's steps, repeat groups included; their nested steps are walked.
        zones: The ``athlete_zones`` section the boundaries come from, or None.
        threshold_tolerance_s: How far inside threshold pace still counts as threshold
            work - the authoring chain's own margin, so a session targeting the
            threshold band ranks ``threshold`` by construction rather than by luck.
        untargeted_work: What a work step with no target contributes, being the
            session type's default chain; None leaves it contributing nothing.

    Returns:
        The measured hardness and the step that decided it, or None when nothing could
        be measured - no zones, no targeted step, or a ladder missing the bound a
        target needs. An absent answer is the guard's cue to fall back to the session
        type's rank.
    """
    if not zones:
        # Without a ladder nothing can be ranked, not even a step whose chain is known:
        # a session the athlete runs by feel is not evidence of any intensity.
        return None
    graded = [
        (_step_hardness(step, zones, threshold_tolerance_s, untargeted_work), step)
        for step in _walk(steps)
    ]
    if any(word is None for word in (word for word, _ in graded)):
        return None
    ranked = [(word, step) for word, step in graded if word != _UNRANKED]
    if not ranked:
        return None
    word, step = max(ranked, key=lambda pair: HARDNESS_RANK[str(pair[0])])
    return Measurement(str(word), step)


def describe_step(step: dict[str, Any] | None) -> str:
    """The deciding step in the athlete's terms: which role, and what it targets."""
    if step is None:
        return "the session"
    target = step.get("target") or {}
    if target.get("type") == "pace_band":
        band = f"{_mmss(target['fast_s_per_km'])}-{_mmss(target['slow_s_per_km'])}/km"
    elif target.get("type") == "hr_band":
        band = f"HR {target['low_bpm']}-{target['high_bpm']}"
    else:
        band = "its default target"
    return f"the {step['kind']} step at {band}"


def _mmss(seconds: float) -> str:
    """A pace in seconds per km as the mm:ss the athlete reads off the watch."""
    minutes, rest = divmod(round(seconds), 60)
    return f"{minutes}:{rest:02d}"


def _walk(steps: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every executable step of a spec, repeat groups flattened into their contents."""
    flat: list[dict[str, Any]] = []
    for step in steps:
        if step.get("kind") == "repeat":
            flat.extend(_walk(step.get("steps") or []))
        else:
            flat.append(step)
    return flat


def _step_hardness(
    step: dict[str, Any],
    zones: dict[str, Any],
    tolerance_s: float,
    untargeted_work: str | None,
) -> str | None:
    """One step's hardness, ``_UNRANKED`` when it adds nothing, None when unmeasurable."""
    target = step.get("target") or {}
    kind = target.get("type")
    if kind == "none" or kind is None:
        if step.get("kind") != "work":
            return _UNRANKED
        return untargeted_work or _UNRANKED
    if kind == "pace_band":
        return _ranked_by_pace(target["fast_s_per_km"], zones, tolerance_s)
    if kind == "hr_band":
        return _ranked_by_hr(target["high_bpm"], zones)
    return _UNRANKED


def _ranked_by_pace(
    fastest_s_per_km: float, zones: dict[str, Any], tolerance_s: float
) -> str | None:
    """A pace band's rank, judged by its faster edge against the two stored pace anchors."""
    ceiling = zones.get("z2_pace_ceiling_s_per_km")
    threshold = zones.get("threshold_pace_s_per_km")
    if ceiling is None or threshold is None:
        return None
    if fastest_s_per_km >= ceiling:
        return "easy"
    if fastest_s_per_km >= threshold - tolerance_s:
        return "threshold"
    return "hard"


def _ranked_by_hr(highest_bpm: float, zones: dict[str, Any]) -> str | None:
    """A heart-rate band's rank, judged by its upper edge against the Z2 ceiling and LTHR."""
    z2_hi = zones.get("z2_hi_bpm")
    lthr = zones.get("lthr_bpm")
    if z2_hi is None or lthr is None:
        return None
    if highest_bpm <= z2_hi:
        return "easy"
    if highest_bpm <= lthr:
        return "threshold"
    return "hard"
