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
``hyrox`` recommendation asks the athlete for the run/station split. The exercise
sports (``strength``, ``hiit``) expand ``structure.exercises`` entries into flat
per-set steps with rests between sets (issue #16).
"""

from __future__ import annotations

from itertools import count
from typing import Any, NamedTuple

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

# Default rest between sets for the exercise sports, overridable per entry.
STRENGTH_REST_S = 90
HIIT_REST_S = 60

# Allowed request enumerations.
_SPORTS = ("run", "hiit", "strength")
_ORIGINS = ("recommender", "athlete")
_SESSION_TYPES = ("rest", "easy", "tempo", "quality", "hyrox", "strength", "crossfit")

# Which session types each authored sport may carry. A hyrox session is run-dominant
# under sport run (where it asks for the split) and station-based under sport hiit.
_SPORT_SESSION_TYPES = {
    "run": frozenset({"rest", "easy", "tempo", "quality", "hyrox"}),
    "strength": frozenset({"strength"}),
    "hiit": frozenset({"hyrox", "crossfit"}),
}

# The sports authored from structure.exercises (as opposed to the run roles).
_EXERCISE_SPORTS = frozenset(("strength", "hiit"))

# Per exercise sport: the default seconds of rest between sets.
_REST_DEFAULT_S = {"strength": STRENGTH_REST_S, "hiit": HIIT_REST_S}

# Session-type hardness, for spotting an athlete request that exceeds the
# recommender's advice. Mirrors the recommender's intent ranking; strength sits
# beside tempo (a real stress despite its HR-blind load), crossfit beside hyrox.
_HARDNESS = {
    "rest": 0,
    "easy": 1,
    "tempo": 2,
    "strength": 2,
    "quality": 3,
    "hyrox": 3,
    "crossfit": 3,
}

# Unambiguous recommendation intents map straight to a sport; hyrox never does -
# the athlete says whether it is run-dominant (run) or station-based (hiit).
_SPORT_FOR_INTENT = {"strength": "strength", "crossfit": "hiit"}


class _Role(NamedTuple):
    """One step role a run session type offers, and the structure keys that shape it."""

    name: str
    end_key: str
    min_key: str

    @property
    def target_key(self) -> str:
        """The structure key setting this role's intensity target."""
        return f"{self.name}_target"


# Per session type: the roles a structure override may shape. The min_key is the pre-11a
# minutes alias kept for back-compat (easy spells its work role's alias ``duration_min``).
# This table is the single source of which keys a session type accepts - both the end
# conditions and the intensity targets are derived from it, so the two cannot drift apart.
_STRUCTURE_ROLES = {
    "easy": (_Role("work", "work_end", "duration_min"),),
    "tempo": (
        _Role("warmup", "warmup_end", "warmup_min"),
        _Role("work", "work_end", "work_min"),
        _Role("cooldown", "cooldown_end", "cooldown_min"),
    ),
    "quality": (
        _Role("warmup", "warmup_end", "warmup_min"),
        _Role("work", "work_end", "work_min"),
        _Role("recovery", "recovery_end", "recovery_min"),
        _Role("cooldown", "cooldown_end", "cooldown_min"),
    ),
}


_NO_TARGET = {"type": "none"}

# What an athlete writes in a ``<role>_target`` to ask for no target at all.
_NO_TARGET_WORD = "none"

# The heart-rate zones a target may name, and the pair of adjacent upper bounds each
# one spans. A zone name always means heart rate: the ladder in ``athlete_zones`` is a
# heart-rate ladder, while pace holds two anchors and no ladder at all (ADR 0020).
_ZONE_BOUNDS = {
    "z2": ("z1_hi_bpm", "z2_hi_bpm"),
    "z3": ("z2_hi_bpm", "z3_hi_bpm"),
    "z4": ("z3_hi_bpm", "z4_hi_bpm"),
}

# The outer zones are unnameable and why: the ladder stores four upper bounds, so Z1 has
# no floor and Z5 no ceiling, and no athlete-level maximum heart rate is stored anywhere.
_EDGE_ZONE_GAPS = {"z1": "floor", "z5": "ceiling"}


class _BandShape(NamedTuple):
    """How to name one band kind's bounds when refusing a malformed one."""

    bounds: str
    inverted: str


# Both kinds are narrower-bound-first, so one ordering check serves both.
_BAND_SHAPES = {
    "hr_band": _BandShape("[low_bpm, high_bpm]", "low bound must be below the high bound"),
    "pace_band": _BandShape(
        "[fast_s_per_km, slow_s_per_km]",
        "fast bound must be faster (smaller) than the slow bound",
    ),
}


class HyroxSplitRequired(Exception):
    """Raised when a Hyrox recommendation needs the athlete to choose run vs station."""

    def __init__(self) -> None:
        super().__init__(
            "hyrox is run-dominant or station-based; author it as a run session type "
            "(easy/tempo/quality with explicit structure), or as a hiit request carrying "
            "the station exercises"
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
        HyroxSplitRequired: If a Hyrox session needs the athlete to choose its kind.
    """
    _validate_request(request)
    _validate_sport_session(request["sport"], request["session_type"])

    session_type = request["session_type"]
    if request["sport"] == "run" and session_type == "hyrox":
        raise HyroxSplitRequired

    warnings = _date_guard(request["date"], context["today"])
    if session_type == "rest":
        return None

    warnings.extend(_hybrid_warnings(request, context))
    if request["sport"] in _EXERCISE_SPORTS:
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
    band = _work_pace_band(request.get("structure") or {})
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


def _work_pace_band(structure: dict[str, Any]) -> list[float] | None:
    """The work step's explicit pace band from either spelling, or None when unset."""
    target = structure.get("work_target")
    if isinstance(target, dict) and "pace_band" in target:
        return target["pace_band"]
    return structure.get("work_pace_band")


def _rationale_cite(recommendation: dict[str, Any] | None) -> str:
    """The ' (CODE, CODE)' citation of a recommendation's rationale, or '' when absent."""
    codes = (recommendation or {}).get("rationale") or []
    return f" ({', '.join(codes)})" if codes else ""


def request_from_recommendation(
    recommendation: dict[str, Any], *, sport: str | None = None
) -> dict[str, Any]:
    """Build a workout request from a recommendation block.

    Args:
        recommendation: The digest's ``recommendation`` block.
        sport: An explicit authoring family, or None to map it from the
            recommendation's intent (``strength`` -> strength, ``crossfit`` ->
            hiit, run types -> run). A hyrox intent maps to run, where ``author``
            asks for the run-vs-station split.

    Returns:
        A ``recommender``-origin workout request ready for ``author``.
    """
    return {
        "sport": sport or _SPORT_FOR_INTENT.get(recommendation["intended_type"], "run"),
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


class _Targets:
    """Resolves each step role's intensity target for one authored session.

    One resolver serves a whole expansion so that every role asks the same
    question instead of the expansion sites deciding for themselves. A role
    resolves at most once: the work chain appends warnings as a side effect,
    and reusing the answer keeps each warning to a single appearance.
    """

    def __init__(
        self,
        session_type: str,
        request: dict[str, Any],
        zones: dict[str, Any] | None,
        warnings: list[str],
    ) -> None:
        self._session_type = session_type
        self._request = request
        self._structure = request.get("structure") or {}
        self._zones = zones
        self._warnings = warnings
        self._resolved: dict[str, dict[str, Any]] = {}

    def for_role(self, role: str) -> dict[str, Any]:
        """The target for one step role, resolved on first ask and reused after."""
        if role not in self._resolved:
            self._resolved[role] = self._resolve(role)
        return self._resolved[role]

    def _resolve(self, role: str) -> dict[str, Any]:
        """An asked-for target wins; otherwise work runs its chain and the rest carry none."""
        asked = self._asked_for(role)
        if asked is not None:
            return asked
        if role != "work":
            return _NO_TARGET
        if self._session_type == "easy":
            return _easy_target(self._request, self._zones, self._warnings)
        return _threshold_target(self._request, self._zones, self._warnings)

    def _asked_for(self, role: str) -> dict[str, Any] | None:
        """The target the athlete set on this role, or None when they set none.

        Honouring what was asked for is silent - the athlete already knows what
        they asked. Only a named zone the stored ladder cannot bound has anything
        to report, and it degrades to no target rather than failing the author.
        """
        key = f"{role}_target"
        target = self._structure.get(key)
        if target is None:
            return None
        if target == _NO_TARGET_WORD:
            return _NO_TARGET
        if isinstance(target, str):
            return self._zone_band(target, key)
        kind, band = next(iter(target.items()))
        return _band_target(kind, band)

    def _zone_band(self, zone: str, key: str) -> dict[str, Any]:
        """A named zone as a heart-rate band, degrading to no target when unbounded."""
        low_key, high_key = _ZONE_BOUNDS[zone]
        low = (self._zones or {}).get(low_key)
        high = (self._zones or {}).get(high_key)
        if low is None or high is None:
            self._warnings.append(
                f"{key}: no {zone.upper()} heart-rate band in your zones; authored without a target"
            )
            return _NO_TARGET
        return {"type": "hr_band", "low_bpm": low, "high_bpm": high}


def _expand(
    session_type: str,
    request: dict[str, Any],
    zones: dict[str, Any] | None,
    warnings: list[str],
) -> list[dict[str, Any]]:
    """Expand a session type into ordered spec steps, honouring any structure override."""
    structure = request.get("structure") or {}
    targets = _Targets(session_type, request, zones, warnings)
    if session_type == "easy":
        end = _end_condition(structure, "work_end", "duration_min", EASY_DEFAULT_S)
        return [_step("work", end, targets.for_role("work"))]
    if session_type == "tempo":
        return _expand_tempo(structure, targets)
    if session_type == "quality":
        return _expand_quality(structure, targets)
    raise ValueError(f"unsupported session type: {session_type}")


def _expand_tempo(structure: dict[str, Any], targets: _Targets) -> list[dict[str, Any]]:
    """Warmup + a continuous threshold block + cooldown."""
    return [
        _step(
            "warmup",
            _end_condition(structure, "warmup_end", "warmup_min", TEMPO_WARMUP_S),
            targets.for_role("warmup"),
        ),
        _step(
            "work",
            _end_condition(structure, "work_end", "work_min", TEMPO_WORK_S),
            targets.for_role("work"),
        ),
        _step(
            "cooldown",
            _end_condition(structure, "cooldown_end", "cooldown_min", TEMPO_COOLDOWN_S),
            targets.for_role("cooldown"),
        ),
    ]


def _expand_quality(structure: dict[str, Any], targets: _Targets) -> list[dict[str, Any]]:
    """Warmup + a homogeneous repeat block of work + recovery + cooldown."""
    # Built in step order so any warnings a role raises read in the order the
    # athlete will run the session, the same way tempo's already do.
    warmup = _step(
        "warmup",
        _end_condition(structure, "warmup_end", "warmup_min", QUALITY_WARMUP_S),
        targets.for_role("warmup"),
    )
    interval = {
        "kind": "repeat",
        "reps": int(structure.get("reps", QUALITY_REPS)),
        "steps": [
            _step(
                "work",
                _end_condition(structure, "work_end", "work_min", QUALITY_WORK_S),
                targets.for_role("work"),
            ),
            _step(
                "recovery",
                _end_condition(structure, "recovery_end", "recovery_min", QUALITY_RECOVERY_S),
                targets.for_role("recovery"),
            ),
        ],
    }
    return [
        warmup,
        interval,
        _step(
            "cooldown",
            _end_condition(structure, "cooldown_end", "cooldown_min", QUALITY_COOLDOWN_S),
            targets.for_role("cooldown"),
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
    for role in _STRUCTURE_ROLES.get(session_type, ()):
        end = structure.get(role.end_key)
        if end is None:
            continue
        if structure.get(role.min_key) is not None:
            raise ValueError(
                f"structure sets both {role.end_key} and {role.min_key}; give only one"
            )
        _validate_end(end, role.end_key)
    _validate_pace_band(structure)
    _validate_targets(structure, session_type)


def _allowed_structure_keys(session_type: str) -> set[str]:
    """The structure keys a session type accepts (its role ends/mins/targets, band, reps)."""
    keys = {"work_pace_band"}
    if session_type == "quality":
        keys.add("reps")
    for role in _STRUCTURE_ROLES.get(session_type, ()):
        keys.update((role.end_key, role.min_key, role.target_key))
    return keys


def _validate_targets(structure: dict[str, Any], session_type: str) -> None:
    """Check each role's intensity target, and that work carries only one spelling.

    Raises:
        ValueError: If a target is malformed, or work sets both ``work_target`` and
            the older ``work_pace_band``.
    """
    if structure.get("work_target") is not None and structure.get("work_pace_band") is not None:
        raise ValueError("structure sets both work_target and work_pace_band; give only one")
    for role in _STRUCTURE_ROLES.get(session_type, ()):
        target = structure.get(role.target_key)
        if target is not None:
            _validate_target(target, role.target_key)


def _validate_target(target: Any, key: str) -> None:
    """Check one role's target is ``"none"`` or a single well-formed band.

    Raises:
        ValueError: If the target is neither the no-target word nor a one-key
            ``hr_band``/``pace_band`` mapping holding a valid window.
    """
    if target == _NO_TARGET_WORD:
        return
    if isinstance(target, str):
        _validate_zone_name(target, key)
        return
    if not isinstance(target, dict) or len(target) != 1:
        raise ValueError(
            f'{key} must be "{_NO_TARGET_WORD}", a zone name, or one of '
            f"{{'hr_band': [low_bpm, high_bpm]}} / {{'pace_band': [fast_s_per_km, slow_s_per_km]}}"
        )
    kind, band = next(iter(target.items()))
    if kind not in _BAND_SHAPES:
        raise ValueError(f"{key} must be a {' or '.join(sorted(_BAND_SHAPES))}, not {kind}")
    _validate_band(band, f"{key} {kind}", kind)


def _validate_zone_name(zone: str, key: str) -> None:
    """Check a named zone is one the heart-rate ladder can actually bound.

    Raises:
        ValueError: If the zone is one of the open-ended outer zones, or is not a
            zone name at all.
    """
    if zone in _ZONE_BOUNDS:
        return
    if zone in _EDGE_ZONE_GAPS:
        raise ValueError(
            f"{key} cannot name {zone}: the zone ladder stores no {_EDGE_ZONE_GAPS[zone]} for it; "
            'give an explicit {"hr_band": [low_bpm, high_bpm]} instead'
        )
    raise ValueError(f"{key} unknown zone {zone}; nameable zones are {', '.join(_ZONE_BOUNDS)}")


def _validate_pace_band(structure: dict[str, Any]) -> None:
    """Check the older explicit work pace band is a well-formed, faster-first window.

    Raises:
        ValueError: If the band is not a two-element positive ``[fast, slow]`` with
            ``fast < slow``.
    """
    band = structure.get("work_pace_band")
    if band is not None:
        _validate_band(band, "work_pace_band", "pace_band")


def _validate_band(band: Any, label: str, kind: str) -> None:
    """Check a target band is a two-element, positive, correctly ordered window.

    Args:
        band: The candidate ``[first, second]`` bounds.
        label: How to name the offending key in an error (e.g. ``warmup_target hr_band``).
        kind: ``hr_band`` or ``pace_band``, selecting the wording of both errors.

    Raises:
        ValueError: If the band is not two positive numbers, or its bounds are
            ordered the wrong way round.
    """
    shape = _BAND_SHAPES[kind]
    if (
        not isinstance(band, list | tuple)
        or len(band) != 2
        or not all(isinstance(x, int | float) and x > 0 for x in band)
    ):
        raise ValueError(f"{label} must be {shape.bounds}, both positive")
    if band[0] >= band[1]:
        raise ValueError(f"{label} {shape.inverted}")


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
        return {"type": "time", "seconds": round(float(minutes) * 60)}
    return {"type": "time", "seconds": default_s}


def _end_descriptor(end: Any) -> dict[str, Any]:
    """Turn a validated request end value into a spec end descriptor."""
    if end == "lap":
        return {"type": "lap"}
    if "distance_m" in end:
        return {"type": "distance", "metres": int(end["distance_m"])}
    return {"type": "time", "seconds": round(float(end["min"]) * 60)}


def _band_target(kind: str, band: list[float]) -> dict[str, Any]:
    """An explicit band as a spec target, in the bound order its kind spells."""
    if kind == "hr_band":
        return {"type": "hr_band", "low_bpm": band[0], "high_bpm": band[1]}
    return {"type": "pace_band", "fast_s_per_km": band[0], "slow_s_per_km": band[1]}


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


# Garmin sport-type descriptors for the exercise sports.
_GARMIN_SPORT_TYPES = {
    "strength": {
        "sportTypeId": SportType.STRENGTH_TRAINING,
        "sportTypeKey": "strength_training",
        "displayOrder": 5,
    },
    "hiit": {
        "sportTypeId": SportType.HIIT,
        "sportTypeKey": "hiit",
        "displayOrder": 9,
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
    if spec["sport"] in _EXERCISE_SPORTS:
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


# One workout's steps in authored shape: executable steps and repeat groups, nested as
# the payload nests them. Comparable with ``==`` - that is the whole point of it.
AuthoredShape = list["AuthoredStep | AuthoredRepeat"]


class AuthoredStep(NamedTuple):
    """One executable step reduced to the fields this module authors."""

    step_type: str | None
    end_condition: str | None
    end_value: float | None
    target_type: str | None
    target_one: float | None
    target_two: float | None
    zone: int | None
    category: str | None
    exercise: str | None
    weight_kg: float | None


class AuthoredRepeat(NamedTuple):
    """A repeat group reduced to its iteration count and the steps it holds."""

    iterations: int | None
    steps: AuthoredShape


def authored_shape(payload: dict[str, Any]) -> AuthoredShape:
    """A workout payload's steps reduced to the fields this module authors.

    The mirror of :func:`to_garmin`, and it lives here for that reason: it encodes
    which fields a payload carries because we put them there. Reading a workout back
    off the account returns the same steps decorated with ids, units, and defaults no
    upload ever sent (``stepId``, ``childStepId``, ``weightValue``, ``strokeType``,
    ``endConditionCompare``), so raw payloads never compare equal. Projecting both
    sides through this makes "are these the same steps?" answerable (issue #42).

    Args:
        payload: A Garmin workout payload, either built by ``to_garmin`` or read back
            from the account.

    Returns:
        One entry per step, nested for repeat groups, comparable with ``==``.
    """
    segments = payload.get("workoutSegments") or []
    steps = segments[0].get("workoutSteps") if segments else []
    return _authored_steps(steps)


def _authored_steps(steps: Any) -> AuthoredShape:
    """The authored shape of a step list, recursing into repeat groups."""
    return [_authored_step(step) for step in steps or []]


def _authored_step(step: dict[str, Any]) -> AuthoredStep | AuthoredRepeat:
    """The authored shape of one step: a repeat group, or one executable step."""
    if step.get("type") == "RepeatGroupDTO":
        return AuthoredRepeat(
            iterations=step.get("numberOfIterations"),
            steps=_authored_steps(step.get("workoutSteps")),
        )
    return AuthoredStep(
        step_type=_nested_key(step.get("stepType"), "stepTypeKey"),
        end_condition=_nested_key(step.get("endCondition"), "conditionTypeKey"),
        end_value=_rounded(step.get("endConditionValue")),
        target_type=_nested_key(step.get("targetType"), "workoutTargetTypeKey"),
        target_one=_rounded(step.get("targetValueOne")),
        target_two=_rounded(step.get("targetValueTwo")),
        zone=step.get("zoneNumber"),
        category=step.get("category"),
        exercise=step.get("exerciseName"),
        weight_kg=_authored_weight(step.get("weightValue")),
    )


def _authored_weight(value: Any) -> float | None:
    """A step's authored weight, with the account's ``-1`` no-weight default read as none.

    Garmin stamps ``weightValue: -1`` on every step it returns, including the run steps
    no upload ever gave a weight to; taking it at face value would make every pushed
    run look edited.
    """
    rounded = _rounded(value)
    return None if rounded is None or rounded < 0 else rounded


def _nested_key(block: Any, field: str) -> Any:
    """One field out of a Garmin enum block, tolerating a missing block."""
    return block.get(field) if isinstance(block, dict) else None


def _rounded(value: Any) -> float | None:
    """A number rounded past the noise Garmin's float round-tripping introduces."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(float(value), 3)


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
