"""Load blend: fuse Garmin training load with Foster session-RPE.

Pure, total functions - no I/O. The blend maps each activity to a single load in
Garmin-load units so strength/Hyrox stress becomes visible to ``load_day``, ACWR,
Foster monotony/strain, and the hard-day signals without rescaling them. Foster
sRPE (``rpe * minutes``) is scaled (``srpe_load_scale``) into Garmin-load units so
the two are comparable. See docs/adr/0010-phase-7-strength-load-and-niggle.md.
"""

from __future__ import annotations

from ..core.models import STRENGTH_DISCIPLINE  # HR-blind discipline replaced by session-RPE

__all__ = ["STRENGTH_DISCIPLINE", "activity_load", "blend", "cardio_split", "srpe_load"]

# The per-session split of cardio load, tuned on 2026-09-21 to reproduce Garmin's own
# 28-day load balance (ADR 0027): mean difference under 3 percentage points over 74 days.
# Changing either number changes a mart column, so it needs a full `features` recompute.
ANAEROBIC_TE_WEIGHT = 0.5  # anaerobic Training Effect counts half against the aerobic one
ZONE_LOAD_WEIGHTS = (1.0, 2.0, 4.0, 4.0, 8.0)  # a hard minute counts for more than an easy one
EASY_ZONES = 2  # zones 1-2 are easy work, zones 3-5 hard
FALLBACK_EASY_AERO_TE = 2.5  # no zone time: aerobic Training Effect below this is easy


def srpe_load(
    discipline: str | None,
    logged_rpe: float | None,
    duration_s: float | None,
    *,
    scale: float,
    sila_default_rpe: float,
) -> float | None:
    """Foster session-RPE for one activity, scaled into Garmin-load units.

    ``sRPE = scale * rpe * duration_min``. The strength discipline substitutes
    ``sila_default_rpe`` when no RPE is logged (so a strength day is never scored
    at Garmin's HR-blind value); other disciplines get no default.

    Args:
        discipline: Mapped discipline label (e.g. ``Bieganie``, ``Siła``).
        logged_rpe: The athlete's Borg CR10 rating, or None if unlogged.
        duration_s: Total session duration in seconds (the Foster standard).
        scale: ``srpe_load_scale`` - maps ``rpe * minutes`` into Garmin-load units.
        sila_default_rpe: Fallback RPE for a strength session with no logged RPE.

    Returns:
        The scaled sRPE, or None when there is no effective RPE or no duration.
    """
    rpe = logged_rpe
    if rpe is None and discipline == STRENGTH_DISCIPLINE:
        rpe = sila_default_rpe
    if rpe is None or duration_s is None:
        return None
    return scale * rpe * (duration_s / 60.0)


def blend(discipline: str | None, garmin_load: float | None, srpe: float | None) -> float:
    """One activity's blended load contribution, in Garmin-load units.

    The strength discipline takes the sRPE directly (Garmin is blind to lifting),
    falling back to the Garmin load only when sRPE is uncomputable. Every other
    discipline takes ``max(garmin_load, sRPE)`` so a logged RPE can only raise an
    already-honest cardio load, never lower it. NULL loads are treated as 0.
    """
    g = garmin_load or 0.0
    if discipline == STRENGTH_DISCIPLINE:
        return srpe if srpe is not None else g
    return max(g, srpe or 0.0)


def activity_load(
    discipline: str | None,
    garmin_load: float | None,
    logged_rpe: float | None,
    duration_s: float | None,
    *,
    scale: float,
    sila_default_rpe: float,
) -> float:
    """One activity's blended load: Foster sRPE fused with the Garmin load.

    A convenience over :func:`srpe_load` + :func:`blend` for callers that hold the
    raw inputs (discipline, Garmin load, logged RPE, duration).
    """
    srpe = srpe_load(
        discipline, logged_rpe, duration_s, scale=scale, sila_default_rpe=sila_default_rpe
    )
    return blend(discipline, garmin_load, srpe)


def cardio_split(
    aero_te: float | None,
    anaero_te: float | None,
    zone_seconds: tuple[float | None, ...],
) -> tuple[float, float, float]:
    """Share of one cardio session's load that is easy, hard and anaerobic work.

    Every session is split, never filed whole. The anaerobic share is the anaerobic
    Training Effect, weighted by ``ANAEROBIC_TE_WEIGHT``, against the aerobic one; the
    rest is divided between easy (the first ``EASY_ZONES`` zones) and hard work by time
    in the watch's own zones, weighted by ``ZONE_LOAD_WEIGHTS``. A session with no zone
    time falls back to the old Training Effect rule for the rest (aerobic TE below
    ``FALLBACK_EASY_AERO_TE``, or absent, is easy). Total over nulls.

    Args:
        aero_te: Garmin's aerobic Training Effect, 0-5, or None.
        anaero_te: Garmin's anaerobic Training Effect, 0-5, or None.
        zone_seconds: Seconds in HR zones 1-5 as recorded by the watch; None counts as 0.

    Returns:
        ``(easy, hard, anaerobic)`` shares that sum to 1.
    """
    aerobic = aero_te or 0.0
    anaerobic_weighted = ANAEROBIC_TE_WEIGHT * (anaero_te or 0.0)
    total_te = aerobic + anaerobic_weighted
    anaerobic = anaerobic_weighted / total_te if total_te > 0 else 0.0

    weighted = [w * (seconds or 0.0) for w, seconds in zip(ZONE_LOAD_WEIGHTS, zone_seconds)]
    total_time = sum(weighted)
    if total_time > 0:
        easy_of_rest = sum(weighted[:EASY_ZONES]) / total_time
    else:
        easy_of_rest = 1.0 if aerobic < FALLBACK_EASY_AERO_TE else 0.0

    rest = 1.0 - anaerobic
    return rest * easy_of_rest, rest * (1.0 - easy_of_rest), anaerobic
