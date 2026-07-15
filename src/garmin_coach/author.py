"""Phase 11 author: a workout request -> a deterministic workout spec.

Pure and offline. Turns a source-agnostic ``workout_request`` (from the Phase 10
recommender or the athlete) plus the finished marts it reads (personal
``athlete_zones``) into a Garmin-shaped ``workout_spec``. The CLI writes the spec
to ``reports/{date}/``; ``publish`` later consumes it. No DB, no network - and
``author`` never imports ``publish`` (the golden rule, applied to the write path;
see ADR 0013).

The spec's units are domain units (seconds per km, bpm). ``to_garmin`` converts a
finished spec into the Garmin ``RunningWorkout`` JSON the transport uploads,
reusing garminconnect's verified step/target structures.

This ticket authors the ``easy`` run only; tempo/quality structure, athlete
requests, and hybrid validation arrive in later tickets.
"""

from __future__ import annotations

from typing import Any

from garminconnect.workout import (
    ConditionType,
    RunningWorkout,
    SportType,
    TargetType,
    WorkoutSegment,
    create_cooldown_step,
    create_interval_step,
    create_recovery_step,
    create_warmup_step,
)

# System-authored workouts carry this name prefix so idempotency scans only our
# own workouts and the athlete can tell them apart in Garmin Connect.
GC_PREFIX = "GC"

# Default easy duration and how much slower than the Z2 ceiling the easy band runs.
EASY_DEFAULT_S = 45 * 60
EASY_PACE_SLOW_MARGIN_S = 40

# Spec step kind -> garminconnect step builder (all take duration + order + target).
_STEP_BUILDERS = {
    "warmup": create_warmup_step,
    "work": create_interval_step,
    "recovery": create_recovery_step,
    "cooldown": create_cooldown_step,
}


def author(request: dict[str, Any], context: dict[str, Any]) -> dict[str, Any] | None:
    """Build a workout spec from a request and the finished-mart context.

    Args:
        request: The workout request - ``sport``, ``origin``, ``date``,
            ``session_type`` and (from the recommender) ``intensity_cap`` /
            ``pace_target_s_per_km``.
        context: ``zones`` (the ``athlete_zones`` section, or None) and ``today``
            (the guard date).

    Returns:
        The workout spec, or None when the session type is ``rest`` (nothing to
        author is a correct, quiet outcome).

    Raises:
        ValueError: If the target date is in the past.
    """
    session_type = request["session_type"]
    warnings = _date_guard(request["date"], context["today"])
    if session_type == "rest":
        return None

    steps = _expand(session_type, request, context.get("zones"), warnings)
    return {
        "sport": request["sport"],
        "origin": request["origin"],
        "date": request["date"],
        "session_type": session_type,
        "name": f"{GC_PREFIX} {request['date']} {session_type}",
        "steps": steps,
        "warnings": warnings,
    }


def request_from_recommendation(
    recommendation: dict[str, Any], *, sport: str = "run"
) -> dict[str, Any]:
    """Build a workout request from a Phase 10 recommendation block.

    Args:
        recommendation: The digest's ``recommendation`` block.
        sport: The authoring family (only ``run`` is authored in this phase).

    Returns:
        A ``recommender``-origin workout request ready for ``author``.
    """
    return {
        "sport": sport,
        "origin": "recommender",
        "date": recommendation["target_date"],
        "session_type": recommendation["intended_type"],
        "intensity_cap": recommendation.get("intensity_cap"),
        "pace_target_s_per_km": recommendation.get("pace_target_s_per_km"),
        "structure": None,
    }


def _date_guard(date: str, today: str) -> list[str]:
    """Refuse a past date; warn (not block) when the target is today."""
    if date < today:
        raise ValueError(f"cannot author a workout for a past date: {date}")
    if date == today:
        return ["target date is today; the watch may not sync before the session"]
    return []


def _expand(
    session_type: str,
    request: dict[str, Any],
    zones: dict[str, Any] | None,
    warnings: list[str],
) -> list[dict[str, Any]]:
    """Expand a session type into ordered spec steps (easy only for now)."""
    if session_type == "easy":
        target = _easy_target(request, zones, warnings)
        return [{"kind": "work", "end": {"type": "time", "seconds": EASY_DEFAULT_S}, "target": target}]
    raise ValueError(f"unsupported session type: {session_type}")


def _easy_target(
    request: dict[str, Any], zones: dict[str, Any] | None, warnings: list[str]
) -> dict[str, Any]:
    """The easy step's target, degrading pace -> heart rate -> none.

    The recommender only carries a measured pace when zones are regression-backed,
    so an absent ``pace_target_s_per_km`` is the signal to degrade.
    """
    pace = request.get("pace_target_s_per_km")
    if pace is not None:
        return {
            "type": "pace_band",
            "fast_s_per_km": pace,
            "slow_s_per_km": pace + EASY_PACE_SLOW_MARGIN_S,
        }
    if zones and zones.get("z2_hi_bpm") is not None:
        warnings.append("no measured pace; targeting easy by heart rate (Z2 band)")
        return {"type": "hr_band", "low_bpm": zones["z1_hi_bpm"], "high_bpm": zones["z2_hi_bpm"]}
    warnings.append("no target: no measured pace or heart-rate band; time only")
    return {"type": "none"}


def to_garmin(spec: dict[str, Any]) -> dict[str, Any]:
    """Translate a workout spec into a Garmin ``RunningWorkout`` JSON payload.

    Args:
        spec: A finished workout spec from ``author``.

    Returns:
        The Garmin workout dict ready for ``upload_workout``.
    """
    steps = [_garmin_step(step, order) for order, step in enumerate(spec["steps"], start=1)]
    segment = WorkoutSegment(
        segmentOrder=1,
        sportType={"sportTypeId": SportType.RUNNING, "sportTypeKey": "running", "displayOrder": 1},
        workoutSteps=steps,
    )
    workout = RunningWorkout(
        workoutName=spec["name"],
        estimatedDurationInSecs=_estimated_duration(spec["steps"]),
        workoutSegments=[segment],
    )
    return workout.to_dict()


def _garmin_step(step: dict[str, Any], order: int) -> Any:
    """Build one garminconnect executable step from a spec step."""
    builder = _STEP_BUILDERS[step["kind"]]
    target_type = _garmin_target_type(step["target"])
    if step["kind"] == "warmup":
        executable = builder(step["end"]["seconds"], step_order=order, target_type=target_type)
    else:
        executable = builder(step["end"]["seconds"], order, target_type=target_type)
    _apply_end_condition(executable, step["end"])
    _apply_target_values(executable, step["target"])
    return executable


def _garmin_target_type(target: dict[str, Any]) -> dict[str, Any]:
    """The Garmin target-type descriptor for a spec target."""
    if target["type"] == "pace_band":
        return {"workoutTargetTypeId": TargetType.PACE_ZONE, "workoutTargetTypeKey": "pace.zone", "displayOrder": 1}
    if target["type"] == "hr_band":
        return {
            "workoutTargetTypeId": TargetType.HEART_RATE_ZONE,
            "workoutTargetTypeKey": "heart.rate.zone",
            "displayOrder": 1,
        }
    return {"workoutTargetTypeId": TargetType.NO_TARGET, "workoutTargetTypeKey": "no.target", "displayOrder": 1}


def _apply_target_values(executable: Any, target: dict[str, Any]) -> None:
    """Attach the Garmin ``targetValueOne``/``Two`` bounds for a spec target.

    Pace is stored as a speed range in m/s: the slow bound is the lower speed
    (``targetValueOne``), the fast bound the higher (``targetValueTwo``).
    """
    if target["type"] == "pace_band":
        executable.targetValueOne = 1000 / target["slow_s_per_km"]
        executable.targetValueTwo = 1000 / target["fast_s_per_km"]
    elif target["type"] == "hr_band":
        executable.targetValueOne = target["low_bpm"]
        executable.targetValueTwo = target["high_bpm"]


def _apply_end_condition(executable: Any, end: dict[str, Any]) -> None:
    """Override the builder's default (time) end condition for a distance step."""
    if end["type"] == "distance":
        executable.endCondition = {
            "conditionTypeId": ConditionType.DISTANCE,
            "conditionTypeKey": "distance",
            "displayOrder": 3,
            "displayable": True,
        }
        executable.endConditionValue = float(end["metres"])


def _estimated_duration(steps: list[dict[str, Any]]) -> int:
    """Sum the time-ended steps' seconds for the workout's duration estimate."""
    return int(sum(s["end"]["seconds"] for s in steps if s["end"]["type"] == "time"))
