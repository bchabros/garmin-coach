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
``hyrox`` recommendation asks the athlete for the run/station split. A ``hyrox``
run request carrying ``structure.stations`` authors the race's own shape: one run
then one station per entry, the runs ended by distance (or the lap button on race
day) and the stations by the lap button, each station named on the step so the
watch shows what comes next. The exercise sports (``strength``, ``hiit``) expand
``structure.exercises`` entries into flat per-set steps with rests between sets
(issue #16).
"""

from __future__ import annotations

from collections.abc import Sequence
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

from garmin_coach.core import plan as _plan
from garmin_coach.workouts import exercises, hardness as _hardness_of

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
    "station": create_interval_step,
    "recovery": create_recovery_step,
    "rest": create_recovery_step,
    "cooldown": create_cooldown_step,
}

# Default rest between sets for the exercise sports, overridable per entry.
STRENGTH_REST_S = 90
HIIT_REST_S = 60

# Default standing rest between run repeats, the length a jog recovery already defaults to.
RUN_REST_S = QUALITY_RECOVERY_S

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


class _WorkChain(NamedTuple):
    """What a work role falls back to when the athlete asks for no target.

    Turns the recommender's suggested pace into a band by widening it each way, and
    names the zone the band degrades to when no pace was measured at all.
    """

    fast_margin_s: int
    slow_margin_s: int
    zone: str
    label: str


# An easy work step runs at or slower than the suggestion; threshold work sits in a
# symmetric window around it. Each degrades to the heart-rate zone it is named for.
_EASY_CHAIN = _WorkChain(0, EASY_PACE_SLOW_MARGIN_S, "z2", "easy (Z2 band)")
_THRESHOLD_CHAIN = _WorkChain(
    THRESHOLD_PACE_MARGIN_S, THRESHOLD_PACE_MARGIN_S, "z4", "threshold (Z4 band)"
)


# What each work chain measures to on the hardness scale, so an untargeted work step
# ranks by what the athlete will actually run rather than counting for nothing.
_CHAIN_HARDNESS = {"z2": "easy", "z4": "threshold"}


class _Role(NamedTuple):
    """One step role a run session offers: what shapes it, and what it defaults to.

    The keys are derived from the role name, bar the pre-11a minutes alias, which is
    irregular on ``easy`` and so is spelled out.
    """

    name: str
    min_key: str
    default_s: int
    default_target: _WorkChain | None = None
    alias_min_key: str | None = None

    @property
    def end_key(self) -> str:
        """The structure key setting this role's end condition."""
        return f"{self.name}_end"

    @property
    def target_key(self) -> str:
        """The structure key setting this role's intensity target."""
        return f"{self.name}_target"

    @property
    def min_keys(self) -> tuple[str, ...]:
        """Every spelling of this role's minutes alias, the current one first."""
        return (self.min_key,) if self.alias_min_key is None else (self.min_key, self.alias_min_key)

    @property
    def keys(self) -> tuple[str, ...]:
        """Every structure key that shapes this role, and so every key that summons it."""
        return (self.end_key, self.target_key, *self.min_keys)


# Every step role a run session may carry, in the order they are run. A session type's
# own table below says which of these it expands when the request shapes nothing; any of
# a role's keys summons the rest, so no type rejects a role another type accepts (#61).
_ROLE_ORDER = ("warmup", "work", "recovery", "rest", "cooldown")

# What a summoned role runs for when the session type has no default of its own - the
# values ``tempo`` and ``quality`` already use. ``work`` is absent on purpose: every run
# type defaults its own work length, so a work role is never summoned into existence.
_SUMMONED_DEFAULT_S = {
    "warmup": TEMPO_WARMUP_S,
    "recovery": QUALITY_RECOVERY_S,
    "rest": RUN_REST_S,
    "cooldown": TEMPO_COOLDOWN_S,
}

# Per session type: the roles it expands by default, in the order they are run, each with
# the length and the target it falls back to. This table is the single source of what a
# default role carries, so no two of those can drift apart; what a request may *ask* for
# is the vocabulary above, which is wider (issue #61).
_STRUCTURE_ROLES = {
    "easy": (_Role("work", "work_min", EASY_DEFAULT_S, _EASY_CHAIN, "duration_min"),),
    "tempo": (
        _Role("warmup", "warmup_min", TEMPO_WARMUP_S),
        _Role("work", "work_min", TEMPO_WORK_S, _THRESHOLD_CHAIN),
        _Role("cooldown", "cooldown_min", TEMPO_COOLDOWN_S),
    ),
    "quality": (
        _Role("warmup", "warmup_min", QUALITY_WARMUP_S),
        _Role("work", "work_min", QUALITY_WORK_S, _THRESHOLD_CHAIN),
        _Role("recovery", "recovery_min", QUALITY_RECOVERY_S),
        _Role("cooldown", "cooldown_min", QUALITY_COOLDOWN_S),
    ),
}

# Per session type: how many times the work step and the pause after it repeat when the
# structure does not say. A type listed here runs its work inside a repeat block.
_DEFAULT_REPS = {"quality": QUALITY_REPS}

# The roles that pause between repeats - a jog or a stand - one of which every repeat
# block ends on. Asking for both leaves the block with no single pause, so it is refused.
_PAUSE_ROLES = ("recovery", "rest")

# The pause a repeat block falls back to when the request asks for repeats but no pause.
_DEFAULT_PAUSE_ROLE = "recovery"

# A Hyrox run-station sequence: one run then one station per ``structure.stations``
# entry, in race order. Neither role has a default target - a Hyrox run is paced by
# the athlete's own band, not the threshold chain, and a station by heart rate only
# when asked - so both fall back to no target. The warmup and cooldown are optional
# here: authored only when the structure gives them an end.
_HYROX_RUN_ROLE = _Role("run", "run_min", 0)
_HYROX_STATION_ROLE = _Role("station", "station_min", 0)
_HYROX_EDGE_ROLES = (
    _Role("warmup", "warmup_min", TEMPO_WARMUP_S),
    _Role("cooldown", "cooldown_min", TEMPO_COOLDOWN_S),
)
_HYROX_TARGET_KEYS = tuple(
    role.target_key for role in (*_HYROX_EDGE_ROLES, _HYROX_RUN_ROLE, _HYROX_STATION_ROLE)
)
_HYROX_STRUCTURE_KEYS = frozenset(
    {"stations", _HYROX_RUN_ROLE.end_key, *_HYROX_TARGET_KEYS}
    | {key for role in _HYROX_EDGE_ROLES for key in (role.end_key, role.min_key)}
)

# The race run between stations, when the structure does not say otherwise.
HYROX_RUN_DEFAULT_M = 1000

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


class _BandKind(NamedTuple):
    """One band kind's error wording: how its bounds are spelled, and their ordering rule."""

    bounds_spelling: str
    order_rule: str


# Both kinds are narrower-bound-first, so one ordering check serves both.
_BAND_KINDS = {
    "hr_band": _BandKind("[low_bpm, high_bpm]", "low bound must be below the high bound"),
    "pace_band": _BandKind(
        "[fast_s_per_km, slow_s_per_km]",
        "fast bound must be faster (smaller) than the slow bound",
    ),
}


def _no_target() -> dict[str, Any]:
    """A fresh no-target descriptor.

    Built per step rather than shared: a spec step is a plain dict that later stages
    copy and decorate, so one module-level literal would alias across every untargeted
    step in the workout.
    """
    return {"type": "none"}


class HyroxSplitRequired(Exception):
    """Raised when a Hyrox recommendation needs the athlete to choose run vs station."""

    def __init__(self) -> None:
        super().__init__(
            "hyrox is run-dominant or station-based; author it as a run session type "
            "(easy/tempo/quality with explicit structure), as a hyrox run request whose "
            "structure.stations lists the race's stations (one run before each), or as a "
            "hiit request carrying the station exercises"
        )


def author(request: dict[str, Any], context: dict[str, Any]) -> dict[str, Any] | None:
    """Build a workout spec from a request and the finished-mart context.

    Args:
        request: The workout request - ``sport``, ``origin``, ``date``,
            ``session_type`` and (from the recommender) ``intensity_cap`` /
            ``pace_target_s_per_km``.
        context: ``zones`` (the ``athlete_zones`` section, or None), ``today``
            (the guard date), and ``planned_intent`` - the plan of record's intent
            for the target date, which nothing authored may exceed (issue #22).

    Returns:
        The workout spec, or None when the session type is ``rest`` (nothing to
        author is a correct, quiet outcome).

    Raises:
        ValueError: If the request is malformed, the target date is in the past, or
            the session is harder than the plan of record for that date.
        HyroxSplitRequired: If a Hyrox session needs the athlete to choose its kind.
    """
    _validate_request(request)
    _validate_sport_session(request["sport"], request["session_type"])

    session_type = request["session_type"]
    planned = context.get("planned_intent")
    if request["sport"] == "run" and session_type == "hyrox" and not _is_hyrox_sequence(request):
        # A session with no steps to measure is guarded by its type, and the refusal
        # must not be reachable only after answering the run-vs-station question.
        _refuse_if_harder(_plan.guard_error(request["date"], session_type, planned))
        raise HyroxSplitRequired

    warnings = _date_guard(request["date"], context["today"])
    if session_type == "rest":
        _refuse_if_harder(_plan.guard_error(request["date"], session_type, planned))
        return None

    warnings.extend(_hybrid_warnings(request, context))
    if request["sport"] in _EXERCISE_SPORTS:
        structure = request.get("structure") or {}
        _validate_exercises(structure)
        steps = _expand_exercises(structure, request["sport"], warnings)
        measured = None
    elif session_type == "hyrox":
        _validate_hyrox_structure(request["structure"])
        warnings.extend(_pace_band_warning(request, context))
        steps = _expand_hyrox(request, context.get("zones"), warnings)
        # A station sequence expands from a list, not from the role table, so there is
        # no work chain to rank an untargeted step against and no session-wide pace to
        # measure: the guard falls back to the session type (ADR 0023, ADR 0024).
        measured = None
    else:
        _validate_structure(request.get("structure") or {}, session_type)
        warnings.extend(_pace_band_warning(request, context))
        steps = _expand(session_type, request, context.get("zones"), warnings)
        measured = _measure(steps, session_type, context.get("zones"), warnings)
    _guard(request["date"], session_type, measured, planned)
    hardness = measured.hardness if measured else None
    spec = {
        "sport": request["sport"],
        "origin": request["origin"],
        "date": request["date"],
        "session_type": session_type,
        "name": f"{GC_PREFIX} {request['date']} {session_type}",
        "steps": steps,
        "warnings": warnings,
    }
    if hardness is not None:
        spec["hardness"] = hardness
    return spec


def _measure(
    steps: list[dict[str, Any]],
    session_type: str,
    zones: dict[str, Any] | None,
    warnings: list[str],
) -> _hardness_of.Measurement | None:
    """How hard this session measures, or None when the stored ladder cannot say.

    An untargeted work step ranks by the chain it will actually run at, since that is
    what the watch shows the athlete (issue #62, ADR 0024).
    """
    chain = next(
        (role.default_target for role in _STRUCTURE_ROLES[session_type] if role.name == "work"),
        None,
    )
    measured = _hardness_of.measure(
        steps,
        zones,
        threshold_tolerance_s=THRESHOLD_PACE_MARGIN_S,
        untargeted_work=_CHAIN_HARDNESS.get(chain.zone) if chain else None,
    )
    if measured is None:
        warnings.append(
            "no hardness measured: the zone ladder cannot rank this session's targets; "
            "the plan guard falls back to the session type"
        )
    return measured


def _guard(
    date: str,
    session_type: str,
    measured: _hardness_of.Measurement | None,
    planned: str | None,
) -> None:
    """Refuse a session above the plan of record, by what it measures or by its type.

    Raises:
        ValueError: If the session is harder than the plan of record for its date.
    """
    if measured is None:
        _refuse_if_harder(_plan.guard_error(date, session_type, planned))
        return
    if not _plan.is_harder(measured.hardness, planned):
        return
    evidence = _hardness_of.describe_step(measured.step)
    raise ValueError(_plan.spec_guard_error(date, measured.hardness, planned, evidence))


def _refuse_if_harder(error: str | None) -> None:
    """Raise the plan guard's refusal when there is one.

    Raises:
        ValueError: If the guard produced a refusal.
    """
    if error is not None:
        raise ValueError(error)


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


def _is_hyrox_sequence(request: dict[str, Any]) -> bool:
    """Whether a run hyrox request carries the station list that makes it authorable."""
    structure = request.get("structure")
    return isinstance(structure, dict) and "stations" in structure


def _validate_hyrox_structure(structure: dict[str, Any]) -> None:
    """Check a Hyrox sequence structure: its stations, ends, and targets.

    Raises:
        ValueError: If the structure has unknown keys, the station list is malformed,
            an edge role sets both an end and a minutes alias, or an end or target is
            malformed.
    """
    unknown = set(structure) - _HYROX_STRUCTURE_KEYS
    if unknown:
        raise ValueError(f"unknown structure keys for hyrox: {', '.join(sorted(unknown))}")
    _validate_stations(structure["stations"])
    run_end = structure.get(_HYROX_RUN_ROLE.end_key)
    if run_end is not None:
        _validate_end(run_end, _HYROX_RUN_ROLE.end_key)
    for role in _HYROX_EDGE_ROLES:
        end = structure.get(role.end_key)
        if end is None:
            continue
        if structure.get(role.min_key) is not None:
            raise ValueError(
                f"structure sets both {role.end_key} and {role.min_key}; give only one"
            )
        _validate_end(end, role.end_key)
    for key in _HYROX_TARGET_KEYS:
        target = structure.get(key)
        if target is not None:
            _validate_target(target, key)


def _validate_stations(stations: Any) -> None:
    """Check the station list: non-empty, each a label or a ``{label, end?}`` mapping.

    A station may end on the lap button or a clock, never a distance: the watch would
    measure a distance by GPS, which an erg or a sled lane does not move.

    Raises:
        ValueError: If the list is missing or empty, a label is not a non-empty
            string, or an end is a distance or otherwise malformed.
    """
    if not isinstance(stations, list) or not stations:
        raise ValueError("a hyrox sequence needs structure.stations: a non-empty list")
    for station in stations:
        entry = {"label": station} if isinstance(station, str) else station
        if not isinstance(entry, dict) or set(entry) - {"label", "end"}:
            raise ValueError('each station is a label or a {"label": ..., "end": ...} mapping')
        if not isinstance(entry.get("label"), str) or not entry["label"].strip():
            raise ValueError("each station needs a non-empty label")
        end = entry.get("end")
        if end is None:
            continue
        if isinstance(end, dict) and "distance_m" in end:
            raise ValueError(
                f"station '{entry['label']}' cannot end on a distance; give \"lap\" or a time"
            )
        _validate_end(end, f"station '{entry['label']}' end")


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
        rest_end = _rest_end(entry, sport)
        for _ in range(entry["sets"]):
            steps.append(dict(work, target=_no_target()))
            steps.append({"kind": "rest", "end": rest_end, "target": _no_target()})
    steps.pop()
    return steps


def _exercise_work_step(entry: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    """One entry's work step: end condition, resolved exercise label, optional weight."""
    step: dict[str, Any] = {"kind": "work", "end": _exercise_end(entry), "target": _no_target()}
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


def _work_pace_band(structure: dict[str, Any]) -> Sequence[float] | None:
    """The explicit pace band on the work (or Hyrox run) step, or None when unset.

    Reads all three spellings: ``work_target``, the older ``work_pace_band``, and the
    Hyrox sequence's ``run_target`` - the latter never coexists with the other two,
    because no session type accepts both key sets.
    """
    for key in ("work_target", _HYROX_RUN_ROLE.target_key):
        target = structure.get(key)
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
        request: dict[str, Any],
        zones: dict[str, Any] | None,
        warnings: list[str],
    ) -> None:
        self._request = request
        self._structure = request.get("structure") or {}
        self._zones = zones
        self._warnings = warnings
        self._resolved: dict[str, dict[str, Any]] = {}

    def for_role(self, role: _Role) -> dict[str, Any]:
        """The target for one step role, resolved on first ask and reused after."""
        if role.name not in self._resolved:
            self._resolved[role.name] = self._resolve(role)
        return self._resolved[role.name]

    def _resolve(self, role: _Role) -> dict[str, Any]:
        """An asked-for target wins; otherwise the role falls back to its table default."""
        asked = self._asked_for(role)
        if asked is not None:
            return asked
        if role.default_target is None:
            return _no_target()
        return self._chain(role.default_target)

    def _asked_for(self, role: _Role) -> dict[str, Any] | None:
        """The target the athlete set on this role, or None when they set none.

        Honouring what was asked for is silent - the athlete already knows what
        they asked. Only a named zone the stored ladder cannot bound has anything
        to report, and it degrades to no target rather than failing the author.
        """
        target = self._structure.get(role.target_key)
        if target is None:
            return None
        if isinstance(target, str):
            word = target.lower()
            if word == _NO_TARGET_WORD:
                return _no_target()
            return self._zone_band(word, role.target_key)
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
            return _no_target()
        return {"type": "hr_band", "low_bpm": low, "high_bpm": high}

    def _chain(self, chain: _WorkChain) -> dict[str, Any]:
        """A work role's default: an explicit band, else the suggested pace widened to one.

        The recommender only carries a measured pace when zones are regression-backed, so
        an absent ``pace_target_s_per_km`` is the signal to degrade. An explicit athlete
        band wins over both and suppresses the degradation.
        """
        band = _work_pace_band(self._structure)
        if band is not None:
            return _band_target("pace_band", band)
        pace = self._request.get("pace_target_s_per_km")
        if pace is None:
            return self._degrade_to_hr(chain)
        return _band_target("pace_band", [pace - chain.fast_margin_s, pace + chain.slow_margin_s])

    def _degrade_to_hr(self, chain: _WorkChain) -> dict[str, Any]:
        """Degrade a missing pace target to the chain's heart-rate band, then to none at all.

        Its wording stays distinct from an unavailable asked-for target: here a
        degradation genuinely happened, so the two events do not share one sentence.
        """
        low_key, high_key = _ZONE_BOUNDS[chain.zone]
        zones = self._zones
        if zones and zones.get(high_key) is not None:
            self._warnings.append(f"no measured pace; targeting {chain.label} by heart rate")
            return {"type": "hr_band", "low_bpm": zones[low_key], "high_bpm": zones[high_key]}
        self._warnings.append("no target: no measured pace or heart-rate band; time only")
        return _no_target()


def _expand(
    session_type: str,
    request: dict[str, Any],
    zones: dict[str, Any] | None,
    warnings: list[str],
) -> list[dict[str, Any]]:
    """Expand a session type into ordered spec steps, honouring any structure override.

    Raises:
        ValueError: If the session type has no run structure to expand.
    """
    if session_type not in _STRUCTURE_ROLES:
        raise ValueError(f"unsupported session type: {session_type}")
    structure = request.get("structure") or {}
    roles = _roles_for(session_type, structure)
    targets = _Targets(request, zones, warnings)
    # Built in run order, so any warning a role raises reads in the order the athlete
    # will run the session rather than in the order the payload nests them.
    steps = [_role_step(structure, role, targets) for role in roles]
    reps = _repeat_count(session_type, structure)
    if reps is None:
        return steps
    return _with_repeat_block(steps, reps)


def _roles_for(session_type: str, structure: dict[str, Any]) -> tuple[_Role, ...]:
    """The roles this session authors: the type's defaults, plus any the structure asks for.

    A session type is a default shape, not a permitted one (issue #61, ADR 0023): any of a
    role's keys summons it, and a role the request never mentions appears only when the
    type defaults it. Run order comes from the vocabulary, not from the request.
    """
    defaults = {role.name: role for role in _STRUCTURE_ROLES[session_type]}
    wanted = _wanted_role_names(session_type, structure, defaults)
    return tuple(
        defaults.get(name) or _summoned_role(name) for name in _ROLE_ORDER if name in wanted
    )


def _wanted_role_names(
    session_type: str, structure: dict[str, Any], defaults: dict[str, _Role]
) -> set[str]:
    """Which roles this session authors, before they are put back into run order.

    A repeats request with no pause key still needs something to run between the
    repeats, so the default pause joins the set rather than the block being built
    around a role the session does not have.
    """
    wanted = {
        name
        for name in _ROLE_ORDER
        if name in defaults or any(key in structure for key in _role_keys(name))
    }
    asked_pauses = {
        name for name in _PAUSE_ROLES if any(key in structure for key in _role_keys(name))
    }
    if asked_pauses:
        # An asked-for pause replaces the type's default one: a session type that
        # defaults a jog recovery still runs a standing rest when asked for it.
        wanted -= set(_PAUSE_ROLES) - asked_pauses
    elif _repeat_count(session_type, structure) is not None and not (wanted & set(_PAUSE_ROLES)):
        wanted.add(_DEFAULT_PAUSE_ROLE)
    return wanted


def _role_keys(name: str) -> tuple[str, ...]:
    """Every structure key that shapes - and so summons - the role of this name."""
    return (f"{name}_end", f"{name}_target", f"{name}_min")


def _repeat_count(session_type: str, structure: dict[str, Any]) -> int | None:
    """How many times the work step and its pause repeat, or None for no repeat block."""
    if "reps" in structure:
        return int(structure["reps"])
    return _DEFAULT_REPS.get(session_type)


def _summoned_role(name: str) -> _Role:
    """A role the session type does not default, at the shared length and with no target."""
    return _Role(name, f"{name}_min", _SUMMONED_DEFAULT_S[name])


def _expand_hyrox(
    request: dict[str, Any], zones: dict[str, Any] | None, warnings: list[str]
) -> list[dict[str, Any]]:
    """Expand a Hyrox sequence: optional warmup, run + station per entry, optional cooldown.

    Every run shares one end and one target, and every station one target, so each
    resolves once and the step dicts are copied per position rather than aliased.
    """
    structure = request["structure"]
    targets = _Targets(request, zones, warnings)
    warmup, cooldown = (_hyrox_edge_step(structure, role, targets) for role in _HYROX_EDGE_ROLES)
    run_end = _hyrox_run_end(structure)
    run_target = targets.for_role(_HYROX_RUN_ROLE)
    station_target = targets.for_role(_HYROX_STATION_ROLE)
    steps: list[dict[str, Any]] = [warmup] if warmup else []
    for station in structure["stations"]:
        steps.append(_step("work", dict(run_end), dict(run_target)))
        steps.append(_station_step(station, dict(station_target)))
    if cooldown:
        steps.append(cooldown)
    return steps


def _hyrox_edge_step(
    structure: dict[str, Any], role: _Role, targets: _Targets
) -> dict[str, Any] | None:
    """A warmup or cooldown step when the structure gives the role an end, else None."""
    if structure.get(role.end_key) is None and structure.get(role.min_key) is None:
        return None
    return _role_step(structure, role, targets)


def _hyrox_run_end(structure: dict[str, Any]) -> dict[str, Any]:
    """The end every Hyrox run shares: the explicit ``run_end``, else the race kilometre."""
    end = structure.get(_HYROX_RUN_ROLE.end_key)
    if end is not None:
        return _end_descriptor(end)
    return {"type": "distance", "metres": HYROX_RUN_DEFAULT_M}


def _station_step(station: str | dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    """One station step: lap-ended unless the entry says a time, labelled for the watch."""
    entry = {"label": station} if isinstance(station, str) else station
    end = entry.get("end")
    step = _step("station", _end_descriptor(end) if end is not None else {"type": "lap"}, target)
    step["label"] = entry["label"].strip()
    return step


def _role_step(structure: dict[str, Any], role: _Role, targets: _Targets) -> dict[str, Any]:
    """One role's spec step: its resolved end condition and its resolved intensity target."""
    return _step(role.name, _end_condition(structure, role), targets.for_role(role))


def _with_repeat_block(steps: list[dict[str, Any]], reps: int) -> list[dict[str, Any]]:
    """Fold the work step and the pause after it into the repeat block they run inside.

    The block is found by step kind, not by position, so whatever surrounds it - a
    warm-up, a cool-down, both or neither - passes through untouched.
    """
    first = next(i for i, step in enumerate(steps) if step["kind"] == "work")
    last = next(i for i, step in enumerate(steps) if step["kind"] in _PAUSE_ROLES)
    interval = {"kind": "repeat", "reps": reps, "steps": steps[first : last + 1]}
    return [*steps[:first], interval, *steps[last + 1 :]]


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
    _validate_reps(structure)
    roles = _roles_for(session_type, structure)
    for role in roles:
        _validate_role_length(structure, role)
    _validate_pause(structure, session_type, roles)
    _validate_targets(structure, roles)


def _validate_reps(structure: dict[str, Any]) -> None:
    """Check a repeat count is a positive whole number of repeats.

    Raises:
        ValueError: If ``reps`` is present but not a positive integer.
    """
    reps = structure.get("reps")
    if "reps" in structure and (not isinstance(reps, int) or isinstance(reps, bool) or reps <= 0):
        raise ValueError("reps must be a positive integer")


def _validate_pause(structure: dict[str, Any], session_type: str, roles: Sequence[_Role]) -> None:
    """Check the repeats have exactly one kind of pause, and something to run between.

    A pause the session type does not default is almost always a repeat session written
    without its ``reps``; authoring one work step and one dangling pause would be a
    silent misreading of it. Two kinds of pause at once leaves no single answer to what
    the athlete does between repeats.

    Raises:
        ValueError: If both a jog recovery and a standing rest were asked for, or a
            pause was asked for on a type that neither defaults a repeat count nor was
            given one.
    """
    asked = {role.name: _asked_key(structure, role) for role in roles if role.name in _PAUSE_ROLES}
    given = {name: key for name, key in asked.items() if key is not None}
    if len(given) > 1:
        recovery, rest = (given[name] for name in _PAUSE_ROLES)
        raise ValueError(
            f"structure sets both {recovery} and {rest}; a repeat runs one pause, "
            "a jog recovery or a standing rest"
        )
    if "reps" in structure or _DEFAULT_REPS.get(session_type) is not None:
        return
    for name, key in given.items():
        raise ValueError(f"{key} asks for a {name} step, which runs between repeats; give reps too")


def _asked_key(structure: dict[str, Any], role: _Role) -> str | None:
    """The first of a role's keys the structure sets, or None when it sets none."""
    return next((key for key in role.keys if key in structure), None)


def _validate_role_length(structure: dict[str, Any], role: _Role) -> None:
    """Check one role sets its length exactly one way, and that the end itself is legal.

    Raises:
        ValueError: If the role sets an end and a minutes alias at once, sets two
            spellings of the alias at once, or its end is malformed.
    """
    given = [key for key in role.min_keys if structure.get(key) is not None]
    end = structure.get(role.end_key)
    if end is not None and given:
        raise ValueError(f"structure sets both {role.end_key} and {given[0]}; give only one")
    if len(given) > 1:
        raise ValueError(f"structure sets both {given[0]} and {given[1]}; give only one")
    if end is not None:
        _validate_end(end, role.end_key)


def _allowed_structure_keys(session_type: str) -> set[str]:
    """The structure keys a session type accepts (its role ends/mins/targets, band, reps)."""
    keys = {"work_pace_band", "reps"}
    for name in _ROLE_ORDER:
        keys.update((f"{name}_end", f"{name}_min", f"{name}_target"))
    for role in _STRUCTURE_ROLES.get(session_type, ()):
        keys.update(role.keys)
    return keys


def _validate_targets(structure: dict[str, Any], roles: Sequence[_Role]) -> None:
    """Check each role's intensity target, and that work carries only one spelling.

    Raises:
        ValueError: If a target is malformed, or work sets both ``work_target`` and
            the older ``work_pace_band``.
    """
    legacy_band = structure.get("work_pace_band")
    if legacy_band is not None:
        _validate_band(legacy_band, "work_pace_band", "pace_band")
        if structure.get("work_target") is not None:
            raise ValueError("structure sets both work_target and work_pace_band; give only one")
    for role in roles:
        target = structure.get(role.target_key)
        if target is not None:
            _validate_target(target, role.target_key)


def _validate_target(target: Any, key: str) -> None:
    """Check one role's target is a spelled-out word or a single well-formed band.

    Raises:
        ValueError: If the target is neither the no-target word, nor a nameable zone,
            nor a one-key ``hr_band``/``pace_band`` mapping holding a valid window.
    """
    if isinstance(target, str):
        _validate_target_word(target, key)
        return
    if not isinstance(target, dict) or len(target) != 1:
        raise ValueError(
            f'{key} must be "{_NO_TARGET_WORD}", a zone name, or one of '
            f"{{'hr_band': [low_bpm, high_bpm]}} / {{'pace_band': [fast_s_per_km, slow_s_per_km]}}"
        )
    kind, band = next(iter(target.items()))
    if kind not in _BAND_KINDS:
        raise ValueError(f"{key} must be a {' or '.join(sorted(_BAND_KINDS))}, not {kind}")
    _validate_band(band, f"{key} {kind}", kind)


def _validate_target_word(word: str, key: str) -> None:
    """Check a spelled-out target is the no-target word or a zone the ladder can bound.

    Case is not significant - the athlete says "Z3" as readily as "z3".

    Raises:
        ValueError: If the zone is one of the open-ended outer zones, or the word is
            neither the no-target word nor a zone name at all.
    """
    zone = word.lower()
    if zone == _NO_TARGET_WORD or zone in _ZONE_BOUNDS:
        return
    if zone in _EDGE_ZONE_GAPS:
        raise ValueError(
            f"{key} cannot name {zone}: the zone ladder stores no {_EDGE_ZONE_GAPS[zone]} for it; "
            'give an explicit {"hr_band": [low_bpm, high_bpm]} instead'
        )
    raise ValueError(f"{key} unknown zone {zone}; nameable zones are {', '.join(_ZONE_BOUNDS)}")


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
    wording = _BAND_KINDS[kind]
    if (
        not isinstance(band, list | tuple)
        or len(band) != 2
        or not all(isinstance(x, int | float) and x > 0 for x in band)
    ):
        raise ValueError(f"{label} must be {wording.bounds_spelling}, both positive")
    if band[0] >= band[1]:
        raise ValueError(f"{label} {wording.order_rule}")


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


def _end_condition(structure: dict[str, Any], role: _Role) -> dict[str, Any]:
    """Resolve a role's end: an explicit end descriptor, an old ``*_min`` alias, or default."""
    end = structure.get(role.end_key)
    if end is not None:
        return _end_descriptor(end)
    for key in role.min_keys:
        minutes = structure.get(key)
        if minutes is not None:
            return {"type": "time", "seconds": round(float(minutes) * 60)}
    return {"type": "time", "seconds": role.default_s}


def _end_descriptor(end: Any) -> dict[str, Any]:
    """Turn a validated request end value into a spec end descriptor."""
    if end == "lap":
        return {"type": "lap"}
    if "distance_m" in end:
        return {"type": "distance", "metres": int(end["distance_m"])}
    return {"type": "time", "seconds": round(float(end["min"]) * 60)}


def _band_target(kind: str, band: Sequence[float]) -> dict[str, Any]:
    """An explicit band as a spec target, in the bound order its kind spells."""
    if kind == "hr_band":
        return {"type": "hr_band", "low_bpm": band[0], "high_bpm": band[1]}
    return {"type": "pace_band", "fast_s_per_km": band[0], "slow_s_per_km": band[1]}


def _step(kind: str, end: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    """A spec step with an explicit end descriptor (time / distance / lap)."""
    return {"kind": kind, "end": end, "target": target}


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
    if step["kind"] == "rest":
        # garminconnect ships no rest builder: a standing rest is a recovery step
        # restamped with the rest step type the exercise sports already push.
        executable.stepType = _REST_STEP_TYPE
    if "label" in step:
        # The step's notes: what the watch shows for a station beside the timer.
        executable.description = step["label"]
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
