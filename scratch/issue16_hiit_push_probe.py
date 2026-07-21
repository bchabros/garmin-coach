"""Issue #16 spike: probe whether Garmin accepts a hand-built HIIT workout.

Sandbox script (exempt from the docstring/lint gate). The phase-11 probe settled
that the raw create endpoint accepts a hand-built STRENGTH_TRAINING (5) payload;
this follow-up probes the remaining unknowns for issue #16:

1. Does SportType.HIIT = 9 accept the same payload shape?
2. Are time-ended work blocks accepted (endCondition = time), alongside the
   rep-ended shape the strength probe proved?
3. Does a time-ended REST step round-trip?

The exercise fields reuse the pair the strength probe already proved
(SQUAT / BARBELL_BACK_SQUAT) so a rejection isolates the sport type or end
condition, not the exercise vocabulary (tracked separately in issue #16).

Usage:
    python scratch/issue16_hiit_push_probe.py            # dry-run: print the payload
    python scratch/issue16_hiit_push_probe.py --confirm  # live: upload, print result, delete

The --confirm run writes to the real Garmin account (then deletes the probe
workout). It is a manual, operator-run step -- never wire it into anything.
"""

from __future__ import annotations

import argparse
import json
import sys

from garminconnect.workout import ConditionType, SportType, StepType

from garmin_coach.etl import client

HIIT_SPORT_TYPE = {
    "sportTypeId": SportType.HIIT,
    "sportTypeKey": "hiit",
    "displayOrder": 9,
}

PROBE_PAYLOAD = {
    "workoutName": "GC SPIKE hiit probe",
    "sportType": HIIT_SPORT_TYPE,
    "description": "issue-16 spike; safe to delete",
    "estimatedDurationInSecs": 600,
    "workoutSegments": [
        {
            "segmentOrder": 1,
            "sportType": HIIT_SPORT_TYPE,
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
                {
                    "type": "ExecutableStepDTO",
                    "stepOrder": 2,
                    "stepType": {
                        "stepTypeId": StepType.INTERVAL,
                        "stepTypeKey": "interval",
                        "displayOrder": 3,
                    },
                    "endCondition": {
                        "conditionTypeId": ConditionType.TIME,
                        "conditionTypeKey": "time",
                        "displayOrder": 2,
                        "displayable": True,
                    },
                    "endConditionValue": 45.0,
                    "category": "SQUAT",
                    "exerciseName": "BARBELL_BACK_SQUAT",
                },
                {
                    "type": "ExecutableStepDTO",
                    "stepOrder": 3,
                    "stepType": {
                        "stepTypeId": StepType.REST,
                        "stepTypeKey": "rest",
                        "displayOrder": 5,
                    },
                    "endCondition": {
                        "conditionTypeId": ConditionType.TIME,
                        "conditionTypeKey": "time",
                        "displayOrder": 2,
                        "displayable": True,
                    },
                    "endConditionValue": 60.0,
                },
            ],
        }
    ],
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe Garmin's HIIT workout create endpoint.")
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
        print(f"\nENDPOINT REJECTED the HIIT payload: {type(exc).__name__}: {exc}")
        print("Finding: HIIT push not feasible as-is; strength (5) remains the proven path.")
        return 1

    workout_id = created.get("workoutId")
    print(f"\nENDPOINT ACCEPTED the HIIT payload. workoutId={workout_id}")
    print("Full response:")
    print(json.dumps(created, indent=2))
    print("\nFinding: HIIT = 9 accepts the hand-built shape; check the echoed steps above")
    print("for whether time-ended work blocks and the rest step round-tripped.")
    if workout_id is not None:
        api.delete_workout(workout_id)
        print(f"Cleaned up probe workout {workout_id}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
