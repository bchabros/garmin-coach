"""Recommender: a finished digest -> tomorrow's session recommendation.

Pure and read-only over the digest the coach engine already built. Turns the
digest's own ``signals`` into a single prospective answer for tomorrow, only ever
*softening* the weekly template's planned intent (signal-driven,
most-conservative-wins). Every recommendation cites the signal codes that changed
it, so the advice is explainable by construction. No DB, no Garmin.

The ``avoid`` list and ``replan`` menu are stubbed here (empty / None); later
tickets fill them.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

# Intent hardness over the plan vocabulary (``core.plan.INTENTS``; a test pins the
# two together, because ``_downgrade`` indexes this map directly). ``hyrox``,
# ``crossfit`` and ``quality`` share the top rank; a cap names a maximum allowable
# type and the recommendation is the minimum of the planned intent and every cap
# that fired.
_INTENT_RANK = {
    "rest": 0,
    "easy": 1,
    "tempo": 2,
    "strength": 2,
    "hyrox": 3,
    "crossfit": 3,
    "quality": 3,
}
# The quality types are the *running* hard sessions: they take the taper's Z4
# ceiling and a threshold pace target. ``strength`` is deliberately absent - it is
# hard work, but a pace target is meaningless for lifting and Garmin is HR-blind
# to it anyway (see marts/load.py).
_QUALITY_TYPES = ("tempo", "hyrox", "crossfit", "quality")

# Signals that cap tomorrow to easy AND pin an explicit Z2 heart-rate ceiling.
_EASY_Z2_CODES = (
    "HRV_LOW_MORNING",
    "ACWR_OUT_OF_RANGE",
    "NIGGLE_REDUCED_MODE",
    "DELOAD_ADVISED",
)
# Signals that cap tomorrow to easy but set no HR ceiling of their own.
_EASY_ONLY_CODES = ("TWO_HARD_DAYS", "HARD_RPE_YESTERDAY")

# Movement-overlap stack signals whose keys become tomorrow's avoid-list (real, measured
# stacks - never a niggle mapped to a fabricated pattern).
_STACK_CODES = ("PATTERN_STACK", "MUSCLE_OVERLAP")


def recommend(
    digest: dict[str, Any],
    planned_intent: str | None,
    thresholds: dict[str, float],
) -> dict[str, Any]:
    """Build the ``recommendation`` block for tomorrow from a finished digest.

    Args:
        digest: The digest dict (its ``signals``, ``zones``, and ``window`` are read).
        planned_intent: Tomorrow's ``plan_template`` intent, the starting point.
        thresholds: Effective coach thresholds (unused here; reserved for later rules).

    Returns:
        The recommendation block: ``target_date``, ``planned_intent``,
        ``intended_type``, ``intensity_cap``, ``pace_target_s_per_km``, ``downgraded``,
        ``rationale``, ``avoid`` (empty), and ``replan`` (None).
    """
    zones = digest.get("zones")
    fired = {s["code"]: s for s in digest["signals"] if _signal_applies(s)}

    caps_to_easy = any(c in fired for c in _EASY_Z2_CODES + _EASY_ONLY_CODES)
    sets_z2 = any(c in fired for c in _EASY_Z2_CODES)
    taper = "TAPER_ACTIVE" in fired
    aerobic = "AEROBIC_LOW_SHORTAGE" in fired

    intended_type = _downgrade(planned_intent, caps_to_easy)
    intensity_cap = _cap(intended_type, sets_z2, taper)
    pace = _pace(intended_type, intensity_cap, zones, aerobic)
    downgraded = intended_type != planned_intent
    return {
        "target_date": _tomorrow(digest["window"]["to"]),
        "planned_intent": planned_intent,
        "intended_type": intended_type,
        "intensity_cap": intensity_cap,
        "pace_target_s_per_km": pace,
        "downgraded": downgraded,
        "rationale": _rationale(
            digest["signals"], fired, downgraded, intended_type, intensity_cap, pace
        ),
        "avoid": _avoid(fired),
        "replan": _replan(digest, thresholds),
    }


def _signal_applies(signal: dict[str, Any]) -> bool:
    """Whether a signal actually arms a recommendation rule (not merely present)."""
    code = signal["code"]
    if code == "ACWR_OUT_OF_RANGE":
        return signal["facts"]["acwr"] > signal["facts"]["sweet_hi"]
    if code == "TWO_HARD_DAYS":
        return bool(signal["facts"].get("trailing"))
    return True


def _downgrade(planned_intent: str | None, caps_to_easy: bool) -> str | None:
    """Soften the planned intent to easy when any downgrade signal fired; never raise."""
    if planned_intent is None or not caps_to_easy:
        return planned_intent
    if _INTENT_RANK[planned_intent] <= _INTENT_RANK["easy"]:
        return planned_intent
    return "easy"


def _cap(intended_type: str | None, sets_z2: bool, taper: bool) -> str | None:
    """The heart-rate ceiling: Z2 for a stress downgrade, Z4 to keep a taper off all-out.

    The taper ceiling applies only to a quality-type session, where "no all-out" is a
    real constraint; an easy session is already below it, so taper adds no ceiling there.
    """
    if intended_type in (None, "rest"):
        return None
    if sets_z2:
        return "Z2"
    if taper and intended_type in _QUALITY_TYPES:
        return "Z4"
    return None


def _measured(zones: dict[str, Any] | None) -> bool:
    """Whether the zones carry a regression-measured pace (not a fallback multiplier)."""
    return bool(zones and str(zones.get("source") or "").startswith("regression"))


def _pace(
    intended_type: str | None,
    intensity_cap: str | None,
    zones: dict[str, Any] | None,
    aerobic: bool,
) -> float | None:
    """The pace target in seconds/km, or None when there is no measured pace to give."""
    if not _measured(zones) or intended_type in (None, "rest"):
        return None
    assert zones is not None  # _measured guarantees it
    if aerobic and intended_type == "easy":
        return zones["z2_pace_ceiling_s_per_km"]
    if intensity_cap == "Z2":
        return zones["z2_pace_ceiling_s_per_km"]
    if intended_type in _QUALITY_TYPES:
        return zones["threshold_pace_s_per_km"]
    return None


def _rationale(
    signals: list[dict[str, Any]],
    fired: dict[str, dict[str, Any]],
    downgraded: bool,
    intended_type: str | None,
    intensity_cap: str | None,
    pace: float | None,
) -> list[str]:
    """The signal codes that actually changed the recommendation, in severity order."""
    aerobic_set_pace = intended_type == "easy" and intensity_cap != "Z2" and pace is not None
    cited: list[str] = []
    for signal in signals:
        code = signal["code"]
        if code not in fired:
            continue
        if code in _EASY_Z2_CODES and intensity_cap == "Z2":
            cited.append(code)
        elif code in _EASY_ONLY_CODES and downgraded:
            cited.append(code)
        elif code == "AEROBIC_LOW_SHORTAGE" and aerobic_set_pace:
            cited.append(code)
        elif code == "TAPER_ACTIVE" and intensity_cap == "Z4":
            cited.append(code)
    return cited


def _avoid(fired: dict[str, dict[str, Any]]) -> list[str]:
    """The movement patterns / muscles stacked on the latest day, sorted and de-duplicated."""
    keys: set[str] = set()
    for code in _STACK_CODES:
        signal = fired.get(code)
        if signal is not None:
            keys.update(k for k in signal["facts"]["keys"].split(",") if k)
    return sorted(keys)


def _replan(digest: dict[str, Any], thresholds: dict[str, float]) -> dict[str, Any] | None:
    """A re-plan menu when last complete week missed too many planned sessions.

    A menu the athlete chooses from - never an executed re-plan. ``extend`` and
    ``continue`` are cited by the current block; ``rebuild`` is always offered but is a
    manual suggestion, because ``plan_template`` has no session priority to rebuild from.
    """
    weekly = digest.get("weekly")
    if not weekly or not weekly.get("plan_vs_actual"):
        return None
    missed = sum(
        1 for row in weekly["plan_vs_actual"] if row["planned"] != "rest" and not row["match"]
    )
    if missed < thresholds["replan_missed_sessions"]:
        return None

    plan = digest.get("plan")
    block = plan["block"] if plan else None
    block_cite = (
        f"weeks_to_event={plan['weeks_to_event']}, block={block}" if plan else "no anchor race"
    )
    return {
        "week_start": weekly.get("week_start"),
        "missed": missed,
        "recommended": "extend" if block in ("base", "build") else "continue",
        "options": [
            {"id": "extend", "cite": block_cite},
            {"id": "rebuild", "cite": "manual: no session priorities in plan_template"},
            {"id": "continue", "cite": block_cite},
        ],
    }


def _tomorrow(to_date: str) -> str:
    """The day after the window's last day (tomorrow relative to the report horizon)."""
    return (_dt.date.fromisoformat(to_date) + _dt.timedelta(days=1)).isoformat()
