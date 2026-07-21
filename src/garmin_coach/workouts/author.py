"""Workout authoring: a workout request -> a deterministic workout spec.

Pure and offline. Turns a source-agnostic ``workout_request`` (from the
recommender or the athlete) plus the finished marts it reads (personal
``athlete_zones``) into a Garmin-shaped ``workout_spec``. The CLI writes the spec
to ``reports/{date}/``; ``publish`` later consumes it. No DB, no network - and
``author`` never imports ``publish`` (the golden rule, applied to the write path;
see ADR 0013).

The spec's units are domain units (seconds per km, bpm). ``to_garmin`` converts a
finished spec into the Garmin ``RunningWorkout`` JSON the transport uploads,
reusing garminconnect's verified step/target structures.

Run authoring covers ``easy``/``tempo``/``quality``; ``rest`` yields no spec, a
``hyrox`` recommendation asks the athlete for the run/station split. Strength
authoring expands ``structure.exercises`` entries into flat per-set steps with
rests between sets (issue #16); the ``hiit`` sport is deferred until its ticket.
"""

from __future__ import annotations

from itertools import count
from typing import Any

from garminconnect.workout import (
    ConditionType,
    RunningWorkout,
    SportType,
    StepType,
    TargetType,
    WorkoutSegment,
    create_cooldown_step,
    create_interval_step,
    create_recovery_step,
    create_repeat_group,
    create_warmup_step,
)

from garmin_coach.workouts import exercises

# System-authored workouts carry this name prefix so idempotency scans only our
# own workouts and the athlete can tell them apart in Garmin Connect.
GC_PREFIX = "GC"

# Default easy duration and how much slower than the Z2 ceiling the easy band runs.
EASY_DEFAULT_S = 45 * 60
EASY_PACE_SLOW_MARGIN_S = 40

# Threshold work is a symmetric window around threshold pace (seconds per km).
THRESHOLD_PACE_MARGIN_S = 5

# How far past the suggested pace an explicit band's fast bound must reach to warn.
PACE_BAND_WARN_MARGIN_S = 5

# Default tempo structure: an easy warmup, a continuous threshold block, an easy cooldown.
TEMPO_WARMUP_S = 10 * 60
TEMPO_WORK_S = 20 * 60
TEMPO_COOLDOWN_S = 10 * 60

# Default quality structure: warmup, a conservative interval set, cooldown.
QUALITY_WARMUP_S = 10 * 60
QUALITY_COOLDOWN_S = 10 * 60
QUALITY_REPS = 4
QUALITY_WORK_S = 3 * 60
QUALITY_RECOVERY_S = 2 * 60

# Spec step kind -> garminconnect step builder (all take duration + order + target).
_STEP_BUILDERS = {
    "warmup": create_warmup_step,
    "work": create_interval_step,
    "recovery": create_recovery_step,
    "cooldown": create_cooldown_step,
}

# Default rest between sets for exercise sports, overridable per entry.
STRENGTH_REST_S = 90

# Allowed request enumerations.
_SPORTS = ("run", "hiit", "strength")
_ORIGINS = ("recommender", "athlete")
_SESSION_TYPES = ("rest", "easy", "tempo", "quality", "hyrox", "strength")

# Which session types each authored sport may carry (hiit joins with its ticket).
_SPORT_SESSION_TYPES = {
    "run": frozenset({"rest", "easy", "tempo", "quality", "hyrox"}),
    "strength": frozenset({"strength"}),
}

# Per exercise sport: the default seconds of rest between sets.
_REST_DEFAULT_S = {"strength": STRENGTH_REST_S}

# Session-type hardness, for spotting an athlete request that exceeds the
# recommender's advice. Mirrors the recommender's intent ranking.
_HARDNESS = {"rest": 0, "easy": 1, "tempo": 2, "quality": 3, "hyrox": 3}

# Per session type: the (end_key, min_key) pairs whose end a structure override may set.
# The min_key is the pre-11a minutes alias kept for back-compat (easy uses ``duration_min``).
_STRUCTURE_ROLES = {
    "easy": (("work_end", "duration_min"),),
    "tempo": (
        ("warmup_end", "warmup_min"),
        ("work_end", "work_min"),
        ("cooldown_end", "cooldown_min"),
    ),
    "quality": (
        ("warmup_end", "warmup_min"),
        ("work_end", "work_min"),
        ("recovery_end", "recovery_min"),
        ("cooldown_end", "cooldown_min"),
    ),
}


class DeferredSportError(Exception):
    """Raised when a request's sport (``hiit``) awaits its authoring ticket."""

    def __init__(self, sport: str) -> None:
        super().__init__(f"sport '{sport}' is not authored yet; awaits the push spike")
        self.sport = sport


class HyroxSplitRequired(Exception):
    """Raised when a Hyrox recommendation needs the athlete to choose run vs station."""

    def __init__(self) -> None:
        super().__init__(
            "hyrox is run-dominant or station-based; specify a run request with explicit "
            "structure, or treat it as a hiit (station) session (deferred)"
        )


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
        ValueError: If the request is malformed or the target date is in the past.
        DeferredSportError: If the sport awaits its authoring ticket (``hiit``).
        HyroxSplitRequired: If a Hyrox session needs the athlete to choose its kind.
    """
    _validate_request(request)
    if request["sport"] == "hiit":
        raise DeferredSportError(request["sport"])
    _validate_sport_session(request["sport"], request["session_type"])

    session_type = request["session_type"]
    if session_type == "hyrox":
        raise HyroxSplitRequired

    warnings = _date_guard(request["date"], context["today"])
    if session_type == "rest":
        return None

    warnings.extend(_hybrid_warnings(request, context))
    if request["sport"] == "strength":
        structure = request.get("structure") or {}
        _validate_exercises(structure)
        steps = _expand_exercises(structure, request["sport"], warnings)
    else:
        _validate_structure(request.get("structure") or {}, session_type)
        warnings.extend(_pace_band_warning(request, context))
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


def _validate_request(request: dict[str, Any]) -> None:
    """Check a request carries the required, well-formed fields.

    Raises:
        ValueError: If a required field is missing or an enum value is unknown.
    """
    for field in ("sport", "origin", "date", "session_type"):
        if request.get(field) is None:
            raise ValueError(f"workout request is missing {field}")
    if request["sport"] not in _SPORTS:
        raise ValueError(f"unknown sport: {request['sport']}")
    if request["origin"] not in _ORIGINS:
        raise ValueError(f"unknown origin: {request['origin']}")
    if request["session_type"] not in _SESSION_TYPES:
        raise ValueError(f"unknown session_type: {request['session_type']}")


def _validate_sport_session(sport: str, session_type: str) -> None:
    """Check the session type belongs to the sport's authoring family.

    Raises:
        ValueError: If the session type is not valid for the sport.
    """
    if session_type not in _SPORT_SESSION_TYPES[sport]:
        raise ValueError(f"session_type '{session_type}' is not valid for sport '{sport}'")


def _validate_exercises(structure: dict[str, Any]) -> None:
    """Check an exercise sport's structure carries a well-formed exercises list.

    Raises:
        ValueError: If the structure has unknown keys, the list is missing or
            empty, or any entry is malformed.
    """
    unknown = set(structure) - {"exercises"}
    if unknown:
        raise ValueError(
            f"unknown structure keys for an exercise sport: {', '.join(sorted(unknown))}"
        )
    entries = structure.get("exercises")
    if not isinstance(entries, list) or not entries:
        raise ValueError("an exercise session needs structure.exercises: a non-empty list")
    for entry in entries:
        _validate_exercise_entry(entry)


def _validate_exercise_entry(entry: Any) -> None:
    """Check one exercise entry: exercise, sets, reps XOR time, optional weight and rest.

    Raises:
        ValueError: If a field is missing, malformed, or reps/time are not
            mutually exclusive.
    """
    if not isinstance(entry, dict):
        raise ValueError("each exercises entry must be a mapping")
    if not isinstance(entry.get("exercise"), str) or not entry["exercise"].strip():
        raise ValueError("each exercises entry needs a non-empty exercise name")
    sets = entry.get("sets")
    if not isinstance(sets, int) or sets <= 0:
        raise ValueError(f"exercise '{entry['exercise']}' sets must be a positive integer")
    if ("reps" in entry) == ("time" in entry):
        raise ValueError(f"exercise '{entry['exercise']}' needs exactly one of reps or time")
    if "reps" in entry and (not isinstance(entry["reps"], int) or entry["reps"] <= 0):
        raise ValueError(f"exercise '{entry['exercise']}' reps must be a positive integer")
    if "time" in entry:
        _validate_duration(entry["time"], f"exercise '{entry['exercise']}' time")
    weight = entry.get("weight_kg")
    if weight is not None and (not isinstance(weight, int | float) or weight <= 0):
        raise ValueError(f"exercise '{entry['exercise']}' weight_kg must be positive")
    rest = entry.get("rest")
    if rest is not None and rest != "lap":
        _validate_duration(rest, f"exercise '{entry['exercise']}' rest")


def _validate_duration(value: Any, label: str) -> None:
    """Check a duration descriptor is ``{"min": N}`` or ``{"s": N}`` with a positive N.

    Raises:
        ValueError: If the descriptor is not one of the two shapes or N is not positive.
    """
    if not isinstance(value, dict) or ("min" in value) == ("s" in value):
        raise ValueError(f'{label} must be {{"min": N}} or {{"s": N}}')
    key = "min" if "min" in value else "s"
    if not isinstance(value[key], int | float) or value[key] <= 0:
        raise ValueError(f"{label} {key} must be positive")


def _expand_exercises(
    structure: dict[str, Any], sport: str, warnings: list[str]
) -> list[dict[str, Any]]:
    """Expand exercise entries into flat per-set work steps with rests between sets.

    One step per set (never a repeat group - the probe-proven shape), a rest step
    after every set, and the session's trailing rest skipped.
    """
    steps: list[dict[str, Any]] = []
    for entry in structure["exercises"]:
        work = _exercise_work_step(entry, warnings)
        rest = {"kind": "rest", "end": _rest_end(entry, sport), "target": _NO_TARGET}
        for _ in range(entry["sets"]):
            steps.append(dict(work))
            steps.append(dict(rest))
    steps.pop()
    return steps


def _exercise_work_step(entry: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    """One entry's work step: end condition, resolved exercise label, optional weight."""
    step: dict[str, Any] = {"kind": "work", "end": _exercise_end(entry), "target": _NO_TARGET}
    pair = exercises.resolve(entry["exercise"])
    if pair is None:
        warnings.append(
            f"unknown exercise '{entry['exercise']}'; the step will be unlabeled on the watch"
        )
    else:
        step["exercise"] = {"category": pair[0], "name": pair[1]}
    if entry.get("weight_kg") is not None:
        step["weight_kg"] = entry["weight_kg"]
    return step


def _exercise_end(entry: dict[str, Any]) -> dict[str, Any]:
    """A rep-ended or time-ended work end for one exercise entry."""
    if "reps" in entry:
        return {"type": "reps", "count": entry["reps"]}
    return {"type": "time", "seconds": _seconds(entry["time"])}


def _rest_end(entry: dict[str, Any], sport: str) -> dict[str, Any]:
    """The rest end after each of an entry's sets: override, or the sport default."""
    rest = entry.get("rest")
    if rest is None:
        return {"type": "time", "seconds": _REST_DEFAULT_S[sport]}
    if rest == "lap":
        return {"type": "lap"}
    return {"type": "time", "seconds": _seconds(rest)}


def _seconds(duration: dict[str, Any]) -> int:
    """The seconds of a validated ``{"min": N}`` / ``{"s": N}`` duration descriptor."""
    if "min" in duration:
        return int(duration["min"] * 60)
    return int(duration["s"])


def _hybrid_warnings(request: dict[str, Any], context: dict[str, Any]) -> list[str]:
    """Warn (never block) when an athlete request exceeds the recommender's advice.

    Only athlete-origin requests are validated, and only when the context carries a
    recommendation to compare against. The warning cites the recommender's own
    rationale codes so the athlete sees exactly which signals they are overriding.
    """
    if request["origin"] != "athlete":
        return []
    recommendation = context.get("recommendation")
    if not recommendation:
        return []
    advised = recommendation.get("intended_type")
    requested = request["session_type"]
    if advised is None or _HARDNESS.get(requested, 0) <= _HARDNESS.get(advised, 0):
        return []
    cite = _rationale_cite(recommendation)
    return [f"you asked for {requested} but the recommender advises {advised}{cite}"]


def _pace_band_warning(request: dict[str, Any], context: dict[str, Any]) -> list[str]:
    """Warn (never block) when an athlete's explicit band is faster than the suggestion.

    Fires when the band's fast bound reaches meaningfully past (beyond a small margin) the
    recommender's suggested pace. Cites the recommendation's rationale codes when present.
    """
    if request["origin"] != "athlete":
        return []
    structure = request.get("structure") or {}
    band = structure.get("work_pace_band")
    suggested = request.get("pace_target_s_per_km")
    if not band or suggested is None:
        return []
    fast, slow = band
    if fast >= suggested - PACE_BAND_WARN_MARGIN_S:
        return []
    cite = _rationale_cite(context.get("recommendation"))
    return [
        f"your pace band {fast}-{slow} s/km is faster than the recommended {suggested} s/km{cite}"
    ]


def _rationale_cite(recommendation: dict[str, Any] | None) -> str:
    """The ' (CODE, CODE)' citation of a recommendation's rationale, or '' when absent."""
    codes = (recommendation or {}).get("rationale") or []
    return f" ({', '.join(codes)})" if codes else ""


def request_from_recommendation(
    recommendation: dict[str, Any], *, sport: str = "run"
) -> dict[str, Any]:
    """Build a workout request from a recommendation block.

    Args:
        recommendation: The digest's ``recommendation`` block.
        sport: The authoring family (only ``run`` is authored for now).

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
    """Expand a session type into ordered spec steps, honouring any structure override."""
    structure = request.get("structure") or {}
    if session_type == "easy":
        target = _easy_target(request, zones, warnings)
        end = _end_condition(structure, "work_end", "duration_min", EASY_DEFAULT_S)
        return [_step("work", end, target)]
    if session_type == "tempo":
        return _expand_tempo(structure, _threshold_target(request, zones, warnings))
    if session_type == "quality":
        return _expand_quality(structure, _threshold_target(request, zones, warnings))
    raise ValueError(f"unsupported session type: {session_type}")


def _expand_tempo(structure: dict[str, Any], work: dict[str, Any]) -> list[dict[str, Any]]:
    """Warmup + a continuous threshold block + cooldown."""
    return [
        _step(
            "warmup",
            _end_condition(structure, "warmup_end", "warmup_min", TEMPO_WARMUP_S),
            _NO_TARGET,
        ),
        _step("work", _end_condition(structure, "work_end", "work_min", TEMPO_WORK_S), work),
        _step(
            "cooldown",
            _end_condition(structure, "cooldown_end", "cooldown_min", TEMPO_COOLDOWN_S),
            _NO_TARGET,
        ),
    ]


def _expand_quality(structure: dict[str, Any], work: dict[str, Any]) -> list[dict[str, Any]]:
    """Warmup + a homogeneous repeat block of work + recovery + cooldown."""
    interval = {
        "kind": "repeat",
        "reps": int(structure.get("reps", QUALITY_REPS)),
        "steps": [
            _step("work", _end_condition(structure, "work_end", "work_min", QUALITY_WORK_S), work),
            _step(
                "recovery",
                _end_condition(structure, "recovery_end", "recovery_min", QUALITY_RECOVERY_S),
                _NO_TARGET,
            ),
        ],
    }
    return [
        _step(
            "warmup",
            _end_condition(structure, "warmup_end", "warmup_min", QUALITY_WARMUP_S),
            _NO_TARGET,
        ),
        interval,
        _step(
            "cooldown",
            _end_condition(structure, "cooldown_end", "cooldown_min", QUALITY_COOLDOWN_S),
            _NO_TARGET,
        ),
    ]


def _validate_structure(structure: dict[str, Any], session_type: str) -> None:
    """Validate a structure override's keys and per-role end conditions.

    Raises:
        ValueError: If a key is not valid for the session type, a role sets two ends at
            once, an end is malformed or out of range, or a work step ends on the lap button.
    """
    if not structure:
        return
    unknown = set(structure) - _allowed_structure_keys(session_type)
    if unknown:
        raise ValueError(f"unknown structure keys for {session_type}: {', '.join(sorted(unknown))}")
    for end_key, min_key in _STRUCTURE_ROLES.get(session_type, ()):
        end = structure.get(end_key)
        if end is None:
            continue
        if structure.get(min_key) is not None:
            raise ValueError(f"structure sets both {end_key} and {min_key}; give only one")
        _validate_end(end, end_key)
    _validate_pace_band(structure)


def _allowed_structure_keys(session_type: str) -> set[str]:
    """The structure keys a session type accepts (its role ends/mins, plus band and reps)."""
    keys = {"work_pace_band"}
    if session_type == "quality":
        keys.add("reps")
    for end_key, min_key in _STRUCTURE_ROLES.get(session_type, ()):
        keys.update((end_key, min_key))
    return keys


def _validate_pace_band(structure: dict[str, Any]) -> None:
    """Check an explicit work pace band is a well-formed, faster-first window.

    Raises:
        ValueError: If the band is not a two-element positive ``[fast, slow]`` with
            ``fast < slow``.
    """
    band = structure.get("work_pace_band")
    if band is None:
        return
    if (
        not isinstance(band, list | tuple)
        or len(band) != 2
        or not all(isinstance(x, int | float) and x > 0 for x in band)
    ):
        raise ValueError("work_pace_band must be [fast_s_per_km, slow_s_per_km], both positive")
    if band[0] >= band[1]:
        raise ValueError("work_pace_band fast bound must be faster (smaller) than the slow bound")


def _validate_end(end: Any, end_key: str) -> None:
    """Check one role's end value is a well-formed, allowed end condition.

    Raises:
        ValueError: If the end is not lap/time/distance-shaped, out of range, or a lap
            button on a work step.
    """
    if end == "lap":
        if end_key == "work_end":
            raise ValueError("a work step cannot end on the lap button; give a time or distance")
        return
    if not isinstance(end, dict) or ("distance_m" in end) == ("min" in end):
        raise ValueError(f'{end_key} must be "lap", {{"min": N}}, or {{"distance_m": N}}')
    if "distance_m" in end:
        if not isinstance(end["distance_m"], int) or end["distance_m"] <= 0:
            raise ValueError(f"{end_key} distance_m must be a positive integer")
    elif not isinstance(end["min"], int | float) or end["min"] <= 0:
        raise ValueError(f"{end_key} min must be positive")


def _end_condition(
    structure: dict[str, Any], end_key: str, min_key: str, default_s: int
) -> dict[str, Any]:
    """Resolve a role's end: an explicit end descriptor, an old ``*_min`` alias, or default."""
    end = structure.get(end_key)
    if end is not None:
        return _end_descriptor(end)
    minutes = structure.get(min_key)
    if minutes is not None:
        return {"type": "time", "seconds": int(minutes) * 60}
    return {"type": "time", "seconds": default_s}


def _end_descriptor(end: Any) -> dict[str, Any]:
    """Turn a validated request end value into a spec end descriptor."""
    if end == "lap":
        return {"type": "lap"}
    if "distance_m" in end:
        return {"type": "distance", "metres": int(end["distance_m"])}
    return {"type": "time", "seconds": int(end["min"]) * 60}


_NO_TARGET = {"type": "none"}


def _step(kind: str, end: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    """A spec step with an explicit end descriptor (time / distance / lap)."""
    return {"kind": kind, "end": end, "target": target}


def _easy_target(
    request: dict[str, Any], zones: dict[str, Any] | None, warnings: list[str]
) -> dict[str, Any]:
    """The easy step's target: at or slower than the Z2 ceiling, degrading to HR -> none.

    The recommender only carries a measured pace when zones are regression-backed,
    so an absent ``pace_target_s_per_km`` is the signal to degrade. An explicit
    athlete band wins over both and suppresses the degradation.
    """
    explicit = _explicit_band(request)
    if explicit is not None:
        return explicit
    pace = request.get("pace_target_s_per_km")
    if pace is not None:
        return {
            "type": "pace_band",
            "fast_s_per_km": pace,
            "slow_s_per_km": pace + EASY_PACE_SLOW_MARGIN_S,
        }
    return _hr_or_none(zones, "z1_hi_bpm", "z2_hi_bpm", "easy (Z2 band)", warnings)


def _threshold_target(
    request: dict[str, Any], zones: dict[str, Any] | None, warnings: list[str]
) -> dict[str, Any]:
    """A threshold work target: an explicit band, else a band around threshold pace, else HR."""
    explicit = _explicit_band(request)
    if explicit is not None:
        return explicit
    pace = request.get("pace_target_s_per_km")
    if pace is not None:
        return {
            "type": "pace_band",
            "fast_s_per_km": pace - THRESHOLD_PACE_MARGIN_S,
            "slow_s_per_km": pace + THRESHOLD_PACE_MARGIN_S,
        }
    return _hr_or_none(zones, "z3_hi_bpm", "z4_hi_bpm", "threshold (Z4 band)", warnings)


def _explicit_band(request: dict[str, Any]) -> dict[str, Any] | None:
    """The athlete's custom work pace band as a target, or None when not given."""
    structure = request.get("structure") or {}
    band = structure.get("work_pace_band")
    if band is None:
        return None
    fast, slow = band
    return {"type": "pace_band", "fast_s_per_km": fast, "slow_s_per_km": slow}


def _hr_or_none(
    zones: dict[str, Any] | None,
    low_key: str,
    high_key: str,
    label: str,
    warnings: list[str],
) -> dict[str, Any]:
    """Degrade a missing pace target to a heart-rate band, then to no target at all."""
    if zones and zones.get(high_key) is not None:
        warnings.append(f"no measured pace; targeting {label} by heart rate")
        return {"type": "hr_band", "low_bpm": zones[low_key], "high_bpm": zones[high_key]}
    warnings.append("no target: no measured pace or heart-rate band; time only")
    return {"type": "none"}


# Garmin sport-type descriptors for the exercise sports (hiit joins with its ticket).
_GARMIN_SPORT_TYPES = {
    "strength": {
        "sportTypeId": SportType.STRENGTH_TRAINING,
        "sportTypeKey": "strength_training",
        "displayOrder": 5,
    },
}

# Garmin step-type descriptors the hand-built exercise payload uses.
_INTERVAL_STEP_TYPE = {
    "stepTypeId": StepType.INTERVAL,
    "stepTypeKey": "interval",
    "displayOrder": 3,
}
_REST_STEP_TYPE = {"stepTypeId": StepType.REST, "stepTypeKey": "rest", "displayOrder": 5}

# Garmin's unit descriptor for kilogram weights (the system's fixed weight unit).
_KILOGRAM_UNIT = {"unitId": 8, "unitKey": "kilogram", "factor": 1000.0}


def to_garmin(spec: dict[str, Any]) -> dict[str, Any]:
    """Translate a workout spec into a raw Garmin workout JSON payload.

    Run specs reuse garminconnect's typed ``RunningWorkout``; exercise-sport
    specs are hand-built (the library ships no typed strength/HIIT class).

    Args:
        spec: A finished workout spec from ``author``.

    Returns:
        The Garmin workout dict ready for ``upload_workout``.
    """
    if spec["sport"] in _GARMIN_SPORT_TYPES:
        return _exercise_payload(spec)
    order = count(1)
    nodes = [_garmin_node(node, order) for node in spec["steps"]]
    segment = WorkoutSegment(
        segmentOrder=1,
        sportType={"sportTypeId": SportType.RUNNING, "sportTypeKey": "running", "displayOrder": 1},
        workoutSteps=nodes,
    )
    workout = RunningWorkout(
        workoutName=spec["name"],
        estimatedDurationInSecs=_estimated_duration(spec["steps"]),
        workoutSegments=[segment],
    )
    return workout.to_dict()


def _exercise_payload(spec: dict[str, Any]) -> dict[str, Any]:
    """Hand-build the raw workout payload for an exercise-sport spec.

    Flat executable steps only (one per set, rests between) - the shape the live
    probes proved the create endpoint accepts.
    """
    sport_type = _GARMIN_SPORT_TYPES[spec["sport"]]
    steps = [
        _exercise_garmin_step(step, order) for order, step in enumerate(spec["steps"], start=1)
    ]
    return {
        "workoutName": spec["name"],
        "sportType": sport_type,
        "estimatedDurationInSecs": _estimated_duration(spec["steps"]),
        "workoutSegments": [{"segmentOrder": 1, "sportType": sport_type, "workoutSteps": steps}],
    }


def _exercise_garmin_step(step: dict[str, Any], order: int) -> dict[str, Any]:
    """One raw executable step (work or rest) for an exercise-sport spec step."""
    payload: dict[str, Any] = {
        "type": "ExecutableStepDTO",
        "stepOrder": order,
        "stepType": _INTERVAL_STEP_TYPE if step["kind"] == "work" else _REST_STEP_TYPE,
        **_exercise_end_condition(step["end"]),
    }
    if "exercise" in step:
        payload["category"] = step["exercise"]["category"]
        payload["exerciseName"] = step["exercise"]["name"]
    if "weight_kg" in step:
        payload["weightValue"] = float(step["weight_kg"])
        payload["weightUnit"] = _KILOGRAM_UNIT
    return payload


def _exercise_end_condition(end: dict[str, Any]) -> dict[str, Any]:
    """The endCondition/endConditionValue pair for a reps, time, or lap end."""
    if end["type"] == "reps":
        condition = {
            "conditionTypeId": ConditionType.REPS,
            "conditionTypeKey": "reps",
            "displayOrder": 10,
            "displayable": True,
        }
        return {"endCondition": condition, "endConditionValue": float(end["count"])}
    if end["type"] == "time":
        condition = {
            "conditionTypeId": ConditionType.TIME,
            "conditionTypeKey": "time",
            "displayOrder": 2,
            "displayable": True,
        }
        return {"endCondition": condition, "endConditionValue": float(end["seconds"])}
    condition = {
        "conditionTypeId": ConditionType.LAP_BUTTON,
        "conditionTypeKey": "lap.button",
        "displayOrder": 1,
        "displayable": True,
    }
    return {"endCondition": condition, "endConditionValue": None}


def _garmin_node(node: dict[str, Any], order: count[int]) -> Any:
    """Build one garminconnect node (executable step or repeat group) from a spec node."""
    if node["kind"] == "repeat":
        group_order = next(order)
        children = [_garmin_node(child, order) for child in node["steps"]]
        return create_repeat_group(node["reps"], children, group_order)
    return _garmin_step(node, next(order))


def _garmin_step(step: dict[str, Any], order: int) -> Any:
    """Build one garminconnect executable step from a spec step."""
    builder = _STEP_BUILDERS[step["kind"]]
    target_type = _garmin_target_type(step["target"])
    end = step["end"]
    executable = builder(_builder_end_value(end), step_order=order, target_type=target_type)
    _apply_end_condition(executable, end)
    _apply_target_values(executable, step["target"])
    return executable


def _builder_end_value(end: dict[str, Any]) -> float:
    """The seconds/metres the step builder wants; a placeholder for a lap-button end."""
    if end["type"] == "time":
        return end["seconds"]
    if end["type"] == "distance":
        return end["metres"]
    return 0.0  # lap: the builder needs a value; ``_apply_end_condition`` clears it


def _garmin_target_type(target: dict[str, Any]) -> dict[str, Any]:
    """The Garmin target-type descriptor for a spec target."""
    if target["type"] == "pace_band":
        return {
            "workoutTargetTypeId": TargetType.PACE_ZONE,
            "workoutTargetTypeKey": "pace.zone",
            "displayOrder": 1,
        }
    if target["type"] == "hr_band":
        return {
            "workoutTargetTypeId": TargetType.HEART_RATE_ZONE,
            "workoutTargetTypeKey": "heart.rate.zone",
            "displayOrder": 1,
        }
    return {
        "workoutTargetTypeId": TargetType.NO_TARGET,
        "workoutTargetTypeKey": "no.target",
        "displayOrder": 1,
    }


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
    """Override the builder's default (time) end condition for a distance or lap step."""
    if end["type"] == "distance":
        executable.endCondition = {
            "conditionTypeId": ConditionType.DISTANCE,
            "conditionTypeKey": "distance",
            "displayOrder": 3,
            "displayable": True,
        }
        executable.endConditionValue = float(end["metres"])
    elif end["type"] == "lap":
        executable.endCondition = {
            "conditionTypeId": ConditionType.LAP_BUTTON,
            "conditionTypeKey": "lap.button",
            "displayOrder": 1,
            "displayable": True,
        }
        executable.endConditionValue = None


def _estimated_duration(nodes: list[dict[str, Any]]) -> int:
    """Approximate the workout's seconds, counting repeat iterations.

    A time step contributes its seconds; a distance step with a pace band is estimated
    from the band midpoint; a lap step (or a distance step without a pace band) is
    unknowable and contributes 0. Garmin recomputes the real estimate on the device.
    """
    total = 0
    for node in nodes:
        if node["kind"] == "repeat":
            total += node["reps"] * _estimated_duration(node["steps"])
        else:
            total += _step_seconds(node)
    return total


def _step_seconds(step: dict[str, Any]) -> int:
    """The estimated seconds one executable step contributes (0 when unknowable)."""
    end = step["end"]
    if end["type"] == "time":
        return int(end["seconds"])
    if end["type"] == "distance":
        return _distance_seconds(end["metres"], step["target"])
    return 0  # lap or reps: unknowable


def _distance_seconds(metres: int, target: dict[str, Any]) -> int:
    """Estimate a distance step's seconds from its pace band midpoint, or 0 without one."""
    if target["type"] != "pace_band":
        return 0
    midpoint = (target["fast_s_per_km"] + target["slow_s_per_km"]) / 2
    return round(metres / 1000 * midpoint)
