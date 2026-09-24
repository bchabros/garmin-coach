"""Issue #76: does the account keep a ``description`` (step notes) on HIIT / strength steps?

Sandbox script (exempt from the docstring/lint gate). The run-station sequence puts
each station's label into ``executable.description`` and that round-trips for a
running workout; the exercise sports hand-build their steps and never send one. Two
offline-unanswerable questions, settled in one run against the real account:

1. Does an executable step in a HIIT (9) / strength (5) workout keep ``description``
   when the step also carries a resolved ``category`` / ``exerciseName``?
2. Does a step with NO exercise at all - a lap-ended interval with only a description,
   the shape a HIIT station sequence would push - get accepted and echoed back with the
   note intact?

The payloads come from the production author + translator and are decorated with the
one field under test, so a rejection is about that field and nothing else.

Usage:
    python scratch/issue76_hiit_step_notes_probe.py            # dry-run: print payloads
    python scratch/issue76_hiit_step_notes_probe.py --confirm  # live: upload, read back, delete

The --confirm run writes to the real Garmin account and deletes every workout it
created, including on failure. It is a manual, operator-run step -- never wire it
into anything.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

from garmin_coach.workouts import author

# The notes under test: the athlete's own words, diacritics included, at the length an
# EMOM block description actually reaches.
NOTES = (
    "EMOM 1: 14 kcal row, 20 WBS, 10 burpees",
    "Run 2 km (bieżnia)",
    "Sled Push 50 m - lekko, tętno pod 160",
)

LAP_END = {
    "conditionTypeId": 7,
    "conditionTypeKey": "lap.button",
    "displayOrder": 1,
    "displayable": True,
}


def _tomorrow() -> str:
    return (dt.date.today() + dt.timedelta(days=1)).isoformat()


def _payload(request: dict) -> dict:
    """Author the request through the production path, ignoring the plan guard."""
    context = {"zones": None, "today": dt.date.today().isoformat(), "planned_intent": "quality"}
    return author.to_garmin(author.author(request, context))


def _exercises_request(sport: str, session_type: str) -> dict:
    return {
        "sport": sport,
        "origin": "athlete",
        "date": _tomorrow(),
        "session_type": session_type,
        "label": "notes probe",
        "structure": {
            "exercises": [
                # Resolved: goes up with category/exerciseName AND a description.
                {"exercise": "wall_balls", "sets": 1, "reps": 20},
                # Unresolved: no category, only the description - the T1 shape.
                {"exercise": "EMOM 1: 14 kcal row, 20 WBS, 10 burpees", "sets": 1, "reps": 1},
            ]
        },
    }


def _with_notes(payload: dict) -> dict:
    """Decorate every executable step with a description, cycling through NOTES."""
    steps = payload["workoutSegments"][0]["workoutSteps"]
    for i, step in enumerate(steps):
        step["description"] = NOTES[i % len(NOTES)]
    return payload


def _station_sequence_payload() -> dict:
    """The shape a HIIT station sequence would push: lap-ended intervals, notes, no rests."""
    payload = _payload(_exercises_request("hiit", "hyrox"))
    steps = payload["workoutSegments"][0]["workoutSteps"]
    stations = []
    for order, note in enumerate(NOTES, start=1):
        stations.append(
            {
                "type": "ExecutableStepDTO",
                "stepOrder": order,
                "stepType": steps[0]["stepType"],
                "endCondition": LAP_END,
                "endConditionValue": None,
                "description": note,
            }
        )
    # One station with a heart-rate ceiling, the way station_target would spell it.
    stations[-1]["targetType"] = {
        "workoutTargetTypeId": 4,
        "workoutTargetTypeKey": "heart.rate.zone",
        "displayOrder": 1,
    }
    stations[-1]["targetValueOne"] = 120
    stations[-1]["targetValueTwo"] = 160
    payload["workoutSegments"][0]["workoutSteps"] = stations
    payload["estimatedDurationInSecs"] = 0
    return payload


def _cases() -> list[tuple[str, dict]]:
    return [
        ("hiit exercises + description on every step", _with_notes(_payload(_exercises_request("hiit", "crossfit")))),
        ("strength exercises + description on every step", _with_notes(_payload(_exercises_request("strength", "strength")))),
        ("hiit station sequence: lap-ended, notes only, no exercise, no rests", _station_sequence_payload()),
    ]


def _report_steps(sent: dict, back: dict) -> None:
    """Compare what we sent with what the account echoed, step by step."""
    sent_steps = sent["workoutSegments"][0]["workoutSteps"]
    back_steps = (back.get("workoutSegments") or [{}])[0].get("workoutSteps") or []
    print(f"    steps sent={len(sent_steps)} echoed={len(back_steps)}")
    for i, step in enumerate(sent_steps):
        echoed = back_steps[i] if i < len(back_steps) else {}
        sent_note = step.get("description")
        back_note = echoed.get("description")
        mark = "ok " if sent_note == back_note else "DIFF"
        end = (echoed.get("endCondition") or {}).get("conditionTypeKey")
        exercise = echoed.get("exerciseName")
        target = (echoed.get("targetType") or {}).get("workoutTargetTypeKey")
        print(
            f"    {mark} step {i + 1}: note sent {sent_note!r}, back {back_note!r}; "
            f"end {end!r}, exercise {exercise!r}, target {target!r}"
        )


def _probe(api, label: str, payload: dict) -> None:
    """Upload one case, read it back, report the findings, and delete it."""
    print(f"\n--- {label}")
    try:
        created = api.upload_workout(payload)
    except Exception as exc:  # noqa: BLE001 - the rejection IS the finding
        print(f"    REJECTED: {type(exc).__name__}: {exc}")
        return
    workout_id = created.get("workoutId")
    print(f"    ACCEPTED: workoutId={workout_id}")
    try:
        back = api.get_workout_by_id(workout_id) if workout_id else created
        _report_steps(payload, back)
    finally:
        if workout_id is not None:
            api.delete_workout(workout_id)
            print(f"    cleaned up {workout_id}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe step notes on HIIT / strength steps.")
    parser.add_argument("--confirm", action="store_true", help="Actually upload (then delete).")
    args = parser.parse_args()

    cases = _cases()
    if not args.confirm:
        for label, payload in cases:
            print(f"\n--- {label}")
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        print("\nDry-run: nothing was uploaded. Re-run with --confirm to probe the account.")
        return 0

    from garmin_coach.etl import client

    api = client.login_api()
    for label, payload in cases:
        _probe(api, label, payload)
    print("\nRecord the findings in issue #76: whether the note round-trips beside an "
          "exerciseName, and whether a note-only lap-ended step is accepted at all.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
