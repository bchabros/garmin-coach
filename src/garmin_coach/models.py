"""Pure normalizers: Garmin payload dict -> core-table row dict.

No I/O, no side effects. Missing fields become None rather than raising, so a
sparse onboarding payload normalizes without special-casing at the call site.
The discipline mapping lives here (single source of truth, easy to extend).
"""
from __future__ import annotations

from typing import Any

# gtype (activityType.typeKey) -> discipline label used across the app.
_DISCIPLINE = {
    "running": "Bieganie",
    "trail_running": "Trail",
    "hiit": "Hyrox/HIIT",
    "strength_training": "Siła",
    "ski_touring": "Skitury",
    "backcountry_skiing": "Skitury",
}


def map_discipline(gtype: str | None, name: str | None = None, elev_gain: float | None = None) -> str | None:
    """Map a Garmin activity type to a discipline label.

    Running with a trail signal (name mentions trail, or notable elevation) is
    promoted to Trail; everything else falls back to the static table.
    """
    if gtype is None:
        return None
    if gtype == "running":
        n = (name or "").lower()
        if "trail" in n or (elev_gain is not None and elev_gain >= 400):
            return "Trail"
    return _DISCIPLINE.get(gtype)


def _date_of(start_local: str | None) -> str | None:
    return start_local.split(" ")[0] if start_local else None


def normalize_activity(p: dict[str, Any]) -> dict[str, Any]:
    """Normalize a single activity summary payload to an `activities` row."""
    gtype = (p.get("activityType") or {}).get("typeKey")
    start_local = p.get("startTimeLocal")
    return {
        "activity_id": p.get("activityId"),
        "start_local": start_local,
        "start_gmt": p.get("startTimeGMT"),
        "date": _date_of(start_local),
        "gtype": gtype,
        "discipline": map_discipline(gtype, p.get("activityName"), p.get("elevationGain")),
        "name": p.get("activityName"),
        "workout_id": p.get("workoutId"),
        "dur_s": p.get("duration"),
        "moving_dur_s": p.get("movingDuration"),
        "elapsed_dur_s": p.get("elapsedDuration"),
        "distance_m": p.get("distance"),
        "elev_gain_m": p.get("elevationGain"),
        "elev_loss_m": p.get("elevationLoss"),
        "min_elev_m": p.get("minElevation"),
        "max_elev_m": p.get("maxElevation"),
        "avg_speed_mps": p.get("averageSpeed"),
        "max_speed_mps": p.get("maxSpeed"),
        "avg_gap_speed": p.get("avgGradeAdjustedSpeed"),
        "avg_hr": p.get("averageHR"),
        "max_hr": p.get("maxHR"),
        "hr_z1_s": p.get("hrTimeInZone_1"),
        "hr_z2_s": p.get("hrTimeInZone_2"),
        "hr_z3_s": p.get("hrTimeInZone_3"),
        "hr_z4_s": p.get("hrTimeInZone_4"),
        "hr_z5_s": p.get("hrTimeInZone_5"),
        "avg_power": p.get("avgPower"),
        "max_power": p.get("maxPower"),
        "norm_power": p.get("normPower"),
        "pwr_z1_s": p.get("powerTimeInZone_1"),
        "pwr_z2_s": p.get("powerTimeInZone_2"),
        "pwr_z3_s": p.get("powerTimeInZone_3"),
        "pwr_z4_s": p.get("powerTimeInZone_4"),
        "pwr_z5_s": p.get("powerTimeInZone_5"),
        "aero_te": p.get("aerobicTrainingEffect"),
        "anaero_te": p.get("anaerobicTrainingEffect"),
        "te_label": p.get("trainingEffectLabel"),
        "training_load": p.get("activityTrainingLoad"),
        "aero_te_msg": p.get("aerobicTrainingEffectMessage"),
        "anaero_te_msg": p.get("anaerobicTrainingEffectMessage"),
        "avg_cadence": p.get("averageRunningCadenceInStepsPerMinute"),
        "max_cadence": p.get("maxRunningCadenceInStepsPerMinute"),
        "avg_stride_cm": p.get("avgStrideLength"),
        "avg_gct_ms": p.get("avgGroundContactTime"),
        "avg_gct_balance": p.get("avgGroundContactBalance"),
        "avg_vosc_cm": p.get("avgVerticalOscillation"),
        "avg_vratio": p.get("avgVerticalRatio"),
        "avg_resp": p.get("avgRespirationRate"),
        "min_resp": p.get("minRespirationRate"),
        "max_resp": p.get("maxRespirationRate"),
        "vo2max_at": p.get("vO2MaxValue"),
        "calories": p.get("calories"),
        "bmr_calories": p.get("bmrCalories"),
        "water_ml": p.get("waterEstimated"),
        "steps": p.get("steps"),
        "mod_intensity_min": p.get("moderateIntensityMinutes"),
        "vig_intensity_min": p.get("vigorousIntensityMinutes"),
        "bb_diff": p.get("differenceBodyBattery"),
        "split_1k_s": p.get("fastestSplit_1000"),
        "split_1mi_s": p.get("fastestSplit_1609"),
        "split_5k_s": p.get("fastestSplit_5000"),
        "split_10k_s": p.get("fastestSplit_10000"),
        "location_name": p.get("locationName"),
        "is_pr": 1 if p.get("pr") else 0,
    }


def _score_pct(scores: dict[str, Any], key: str) -> Any:
    node = scores.get(key)
    return node.get("value") if isinstance(node, dict) else None


def normalize_sleep(date: str, p: dict[str, Any]) -> dict[str, Any]:
    """Normalize a get_sleep_data payload to a `sleep` row.

    The rich summary lives under dailySleepDTO; RHR/HRV/BB ride at the top level.
    """
    dto = p.get("dailySleepDTO") or {}
    scores = dto.get("sleepScores") or {}
    need = dto.get("sleepNeed") or {}
    return {
        "date": date,
        "total_s": dto.get("sleepTimeSeconds"),
        "deep_s": dto.get("deepSleepSeconds"),
        "rem_s": dto.get("remSleepSeconds"),
        "light_s": dto.get("lightSleepSeconds"),
        "awake_s": dto.get("awakeSleepSeconds"),
        "nap_s": dto.get("napTimeSeconds"),
        "deep_pct": _score_pct(scores, "deepPercentage"),
        "rem_pct": _score_pct(scores, "remPercentage"),
        "light_pct": _score_pct(scores, "lightPercentage"),
        "score": _score_pct(scores, "overall"),
        "score_feedback": dto.get("sleepScoreFeedback"),
        "awake_count": dto.get("awakeCount"),
        "restless_count": p.get("restlessMomentsCount"),
        "avg_sleep_stress": dto.get("avgSleepStress"),
        "resting_hr": p.get("restingHeartRate"),
        "avg_sleep_hr": dto.get("avgHeartRate"),
        "avg_overnight_hrv": p.get("avgOvernightHrv"),
        "avg_resp": dto.get("averageRespirationValue"),
        "low_resp": dto.get("lowestRespirationValue"),
        "high_resp": dto.get("highestRespirationValue"),
        "skin_temp_dev_c": dto.get("avgSkinTempDeviationC"),
        "bb_change": p.get("bodyBatteryChange"),
        "sleep_need_min": need.get("actual"),
        "bedtime_local": dto.get("sleepStartTimestampLocal"),
        "waketime_local": dto.get("sleepEndTimestampLocal"),
    }


def _hrv_baseline(v: Any) -> Any:
    """Garmin's HRV baseline is null during onboarding, then a band object
    {lowUpper, balancedLow, balancedUpper, markerValue}. Reduce to the balanced
    band's lower edge (a stable integer); raw_payloads keeps the full band."""
    if isinstance(v, dict):
        return v.get("balancedLow")
    return v


def normalize_hrv(date: str, p: dict[str, Any]) -> dict[str, Any]:
    """Normalize a get_hrv_data payload to an `hrv_nightly` row (summary only)."""
    s = p.get("hrvSummary") or {}
    return {
        "date": date,
        "avg_hrv": s.get("lastNightAvg"),
        "night_high_5min": s.get("lastNight5MinHigh"),
        "weekly_avg": s.get("weeklyAvg"),
        "garmin_baseline": _hrv_baseline(s.get("baseline")),
        "status": s.get("status"),
        "feedback_phrase": s.get("feedbackPhrase"),
    }


# Wellness fields that count as "real data"; if all are null the day is an
# explicit gap (onboarding / watch not worn) -> has_data = 0.
_WELLNESS_SIGNALS = (
    "totalSteps", "restingHeartRate", "averageStressLevel", "maxStressLevel",
    "bodyBatteryHighestValue", "bodyBatteryLowestValue", "avgWakingRespirationValue",
    "moderateIntensityMinutes", "vigorousIntensityMinutes",
)


def normalize_wellness(date: str, p: dict[str, Any]) -> dict[str, Any]:
    """Normalize a get_user_summary payload to a `daily_wellness` row.

    Sets has_data=0 when every meaningful signal is null (explicit gap), so gaps
    are distinguishable from "not yet fetched".
    """
    has_data = 0 if all(p.get(k) is None for k in _WELLNESS_SIGNALS) else 1
    return {
        "date": date,
        "has_data": has_data,
        "rhr": p.get("restingHeartRate"),
        "steps": p.get("totalSteps"),
        "floors": p.get("floorsAscended"),
        "intensity_mod_min": p.get("moderateIntensityMinutes"),
        "intensity_vig_min": p.get("vigorousIntensityMinutes"),
        "stress_avg": p.get("averageStressLevel"),
        "stress_max": p.get("maxStressLevel"),
        "rest_min": p.get("restStressDuration"),
        "low_stress_min": p.get("lowStressDuration"),
        "med_stress_min": p.get("mediumStressDuration"),
        "high_stress_min": p.get("highStressDuration"),
        "resp_avg": p.get("avgWakingRespirationValue"),
        "resp_min": p.get("lowestRespirationValue"),
        "resp_max": p.get("highestRespirationValue"),
        "spo2_avg": p.get("averageSpo2"),
        "spo2_min": p.get("lowestSpo2"),
        "bb_min": p.get("bodyBatteryLowestValue"),
        "bb_max": p.get("bodyBatteryHighestValue"),
        "bb_charged": p.get("bodyBatteryChargedValue"),
        "bb_drained": p.get("bodyBatteryDrainedValue"),
        "bb_feedback": (p.get("bodyBatteryDynamicFeedbackEvent") or {}).get("feedbackShortType"),
    }


def normalize_readiness(date: str, p: Any) -> dict[str, Any]:
    """Normalize a get_training_readiness payload (a list) to one row per date."""
    rec = p[0] if isinstance(p, list) and p else (p if isinstance(p, dict) else {})
    return {
        "date": date,
        "score": rec.get("score"),
        "level": rec.get("level"),
        "feedback_short": rec.get("feedbackShort"),
        "recovery_time_min": rec.get("recoveryTime"),
        "acwr_factor_pct": rec.get("acwrFactorPercent"),
        "hrv_factor_pct": rec.get("hrvFactorPercent"),
        "sleep_factor_pct": rec.get("sleepScoreFactorPercent"),
        "stress_factor_pct": rec.get("stressHistoryFactorPercent"),
        "sleep_hist_pct": rec.get("sleepHistoryFactorPercent"),
        "valid_sleep": 1 if rec.get("validSleep") else 0,
    }


def _first_device(mapping: Any) -> dict[str, Any]:
    """Pick the single device entry from a device-id-keyed map (no id hardcoding)."""
    if isinstance(mapping, dict) and mapping:
        return next(iter(mapping.values())) or {}
    return {}


def normalize_status(date: str, p: dict[str, Any]) -> dict[str, Any]:
    """Normalize a get_training_status payload to a `training_status_daily` row."""
    ts = _first_device(
        (p.get("mostRecentTrainingStatus") or {}).get("latestTrainingStatusData")
    )
    acute = ts.get("acuteTrainingLoadDTO") or {}
    lb = _first_device(
        (p.get("mostRecentTrainingLoadBalance") or {}).get("metricsTrainingLoadBalanceDTOMap")
    )
    vo2 = ((p.get("mostRecentVO2Max") or {}).get("generic") or {})
    return {
        "date": date,
        "status": ts.get("trainingStatusFeedbackPhrase"),
        "status_phrase": ts.get("trainingStatusFeedbackPhrase"),
        "fitness_trend": ts.get("fitnessTrend"),
        "garmin_acute_load": acute.get("dailyTrainingLoadAcute"),
        "garmin_chronic_load": acute.get("dailyTrainingLoadChronic"),
        "garmin_acwr": acute.get("dailyAcuteChronicWorkloadRatio"),
        "garmin_acwr_status": acute.get("acwrStatus"),
        "chronic_min": acute.get("minTrainingLoadChronic"),
        "chronic_max": acute.get("maxTrainingLoadChronic"),
        "ml_aero_low": lb.get("monthlyLoadAerobicLow"),
        "ml_aero_high": lb.get("monthlyLoadAerobicHigh"),
        "ml_anaerobic": lb.get("monthlyLoadAnaerobic"),
        "ml_aero_low_min": lb.get("monthlyLoadAerobicLowTargetMin"),
        "ml_aero_low_max": lb.get("monthlyLoadAerobicLowTargetMax"),
        "ml_aero_high_min": lb.get("monthlyLoadAerobicHighTargetMin"),
        "ml_aero_high_max": lb.get("monthlyLoadAerobicHighTargetMax"),
        "ml_anaerobic_min": lb.get("monthlyLoadAnaerobicTargetMin"),
        "ml_anaerobic_max": lb.get("monthlyLoadAnaerobicTargetMax"),
        "balance_phrase": lb.get("trainingBalanceFeedbackPhrase"),
        "vo2max": vo2.get("vo2MaxPreciseValue"),
        "fitness_age": vo2.get("fitnessAge"),
    }
