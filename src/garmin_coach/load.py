"""Phase 7 load blend: fuse Garmin training load with Foster session-RPE.

Pure, total functions - no I/O. The blend maps each activity to a single load in
Garmin-load units so strength/Hyrox stress becomes visible to ``load_day``, ACWR,
Foster monotony/strain, and the hard-day signals without rescaling them. Foster
sRPE (``rpe * minutes``) is scaled (``srpe_load_scale``) into Garmin-load units so
the two are comparable. See docs/adr/0010-phase-7-strength-load-and-niggle.md.
"""

from __future__ import annotations

from .models import STRENGTH_DISCIPLINE  # HR-blind discipline replaced by session-RPE

__all__ = ["STRENGTH_DISCIPLINE", "activity_load", "blend", "srpe_load"]


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


def blend(
    discipline: str | None, garmin_load: float | None, srpe: float | None
) -> float:
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
