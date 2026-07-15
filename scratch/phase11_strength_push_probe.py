"""Phase 11 spike: probe whether Garmin accepts a hand-built strength/HIIT workout.

Sandbox script (exempt from the docstring/lint gate). It hand-builds a strength
workout payload -- there is NO typed StrengthWorkout/HIIT class in garminconnect
0.3.6, only Running/Cycling/Swimming/Walking/MultiSport/FitnessEquipment/Hiking --
and, only with --confirm, uploads it via the raw upload_workout endpoint to see if
the create surface accepts it.

The deliverable of this spike is *knowledge*, not a feature: it settles the
endpoint question the library cannot answer. No production strength code depends on
it. Record the outcome in docs/prd/phase-11-workout-push/strength-spike-findings.md.

Usage:
    python scratch/phase11_strength_push_probe.py            # dry-run: print the payload
    python scratch/phase11_strength_push_probe.py --confirm  # live: upload, print result, delete

The --confirm run writes to the real Garmin account (then deletes the probe
workout). It is a manual, operator-run step -- never wire it into anything.
"""

from __future__ import annotations

import argparse
import json
import sys

from garminconnect.workout import ConditionType, SportType, StepType

from garmin_coach import client

# A hand-built strength workout payload. The exercise schema (category/name, sets,
# reps) is undocumented; this is a best-effort mimic of the running structure with
# the strength sport type and a rep-based end condition. If Garmin rejects it, that
# is the finding.
PROBE_PAYLOAD = {
    "workoutName": "GC SPIKE strength probe",
    "sportType": {
        "sportTypeId": SportType.STRENGTH_TRAINING,
        "sportTypeKey": "strength_training",
        "displayOrder": 5,
    },
    "description": "phase-11 spike; safe to delete",
    "estimatedDurationInSecs": 600,
    "workoutSegments": [
        {
            "segmentOrder": 1,
            "sportType": {
                "sportTypeId": SportType.STRENGTH_TRAINING,
                "sportTypeKey": "strength_training",
                "displayOrder": 5,
            },
            "workoutSteps": [
                {
                    "type": "ExecutableStepDTO",
                    "stepOrder": 1,
                    "stepType": {
                        "stepTypeId": StepType.INTERVAL,
                        "stepTypeKey": "interval",
                        "displayOrder": 3,
                    },
                    "endCondition": {
                        "conditionTypeId": ConditionType.REPS,
                        "conditionTypeKey": "reps",
                        "displayOrder": 10,
                        "displayable": True,
                    },
                    "endConditionValue": 10.0,
                    "category": "SQUAT",
                    "exerciseName": "BARBELL_BACK_SQUAT",
                },
            ],
        }
    ],
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe Garmin's strength workout create endpoint.")
    parser.add_argument("--confirm", action="store_true", help="Actually upload (then delete).")
    args = parser.parse_args()

    print("Payload:")
    print(json.dumps(PROBE_PAYLOAD, indent=2))

    if not args.confirm:
        print("\nDry-run. Re-run with --confirm to upload to the live account.")
        return 0

    api = client.login_api()
    try:
        created = api.upload_workout(PROBE_PAYLOAD)
    except Exception as exc:  # noqa: BLE001 - the rejection IS the finding
        print(f"\nENDPOINT REJECTED the strength payload: {type(exc).__name__}: {exc}")
        print("Finding: fall back to Runna's model - strength stays local (spec + report).")
        return 1

    workout_id = created.get("workoutId")
    print(f"\nENDPOINT ACCEPTED the strength payload. workoutId={workout_id}")
    print("Full response:")
    print(json.dumps(created, indent=2))
    print("\nFinding: strength push is feasible - open a Phase 11b issue for production support.")
    if workout_id is not None:
        api.delete_workout(workout_id)
        print(f"Cleaned up probe workout {workout_id}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
