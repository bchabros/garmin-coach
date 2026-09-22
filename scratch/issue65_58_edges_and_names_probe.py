"""Issues #65 and #58: probe what the account accepts for edges and for names.

Sandbox script (exempt from the docstring/lint gate). Two offline-unanswerable
questions, settled in one run against the real account:

1. #65 - does a strength (5) / HIIT (9) workout accept WARMUP (1) and COOLDOWN (2)
   steps, and does a heart-rate target on the warm-up round-trip?
2. #58 - how long a `workoutName` does the account keep, and do Polish characters
   survive the round-trip?

Everything is built through the production author + translator, so what goes up is
exactly what the coach would push - a rejection here is a finding about the shape
the code produces, not about a hand-written payload.

Usage:
    python scratch/issue65_58_edges_and_names_probe.py            # dry-run: print payloads
    python scratch/issue65_58_edges_and_names_probe.py --confirm  # live: upload, read back, delete

The --confirm run writes to the real Garmin account and deletes every workout it
created, including on failure. It is a manual, operator-run step -- never wire it
into anything.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

from garmin_coach.etl import client
from garmin_coach.workouts import author

# The name ladder for #58: our caps are 30 (label) and 80 (whole name); the rungs above
# 80 are there to find where the account itself stops keeping what it was sent.
NAME_LENGTHS = (80, 120, 200)

# What the author itself will write; longer rungs stretch the payload past it on purpose.
_MAX_AUTHORED_NAME = 80

# Polish diacritics in the place the athlete would put them.
POLISH_LABEL = "4x2 km próg, rozgrzewka łatwa"


def _tomorrow() -> str:
    return (dt.date.today() + dt.timedelta(days=1)).isoformat()


def _strength_request(label: str) -> dict:
    return {
        "sport": "strength",
        "origin": "athlete",
        "date": _tomorrow(),
        "session_type": "strength",
        "label": label,
        "structure": {
            "warmup_min": 10,
            "cooldown_min": 5,
            "exercises": [{"exercise": "back_squat", "sets": 2, "reps": 5, "weight_kg": 60}],
        },
    }


def _hiit_request(label: str) -> dict:
    return {
        "sport": "hiit",
        "origin": "athlete",
        "date": _tomorrow(),
        "session_type": "crossfit",
        "label": label,
        "structure": {
            "warmup_target": {"hr_band": [110, 140]},
            "cooldown_end": "lap",
            "exercises": [{"exercise": "wall_ball", "sets": 2, "time": {"s": 40}}],
        },
    }


def _named_request(name: str) -> dict:
    request = _strength_request("length probe")
    request.pop("label")
    request["name"] = name
    request["structure"] = {"exercises": [{"exercise": "back_squat", "sets": 1, "reps": 5}]}
    return request


def _payload(request: dict) -> dict:
    """Author the request through the production path, ignoring the plan guard."""
    context = {"zones": None, "today": dt.date.today().isoformat(), "planned_intent": "quality"}
    return author.to_garmin(author.author(request, context))


def _cases() -> list[tuple[str, dict]]:
    cases = [
        ("strength with both edges (#65)", _payload(_strength_request("edges probe"))),
        ("hiit with an HR-targeted warm-up and a lap cool-down (#65)", _payload(_hiit_request("edges probe"))),
        ("polish characters in the label (#58)", _payload(_strength_request(POLISH_LABEL[:30]))),
    ]
    # The rungs above our own cap cannot go through the validator, so they stretch the
    # authored payload's name directly: the question is the account's ceiling, not ours.
    filler = "Próba długiej nazwy ćwiczenia ŁÓDŹ " * 10
    base = _payload(_named_request(filler[:_MAX_AUTHORED_NAME]))
    for length in NAME_LENGTHS:
        payload = {**base, "workoutName": filler[:length]}
        cases.append((f"name of {length} characters (#58)", payload))
    return cases


def _report_steps(sent: dict, back: dict) -> None:
    """Compare the step types and targets we sent with what the account echoed."""
    sent_steps = sent["workoutSegments"][0]["workoutSteps"]
    back_steps = (back.get("workoutSegments") or [{}])[0].get("workoutSteps") or []
    print(f"    steps sent={len(sent_steps)} echoed={len(back_steps)}")
    for i, step in enumerate(sent_steps):
        echoed = back_steps[i] if i < len(back_steps) else {}
        sent_type = step["stepType"]["stepTypeKey"]
        back_type = (echoed.get("stepType") or {}).get("stepTypeKey")
        mark = "ok " if sent_type == back_type else "DIFF"
        target = (echoed.get("targetType") or {}).get("workoutTargetTypeKey")
        bounds = (echoed.get("targetValueOne"), echoed.get("targetValueTwo"))
        print(f"    {mark} step {i + 1}: sent {sent_type!r}, back {back_type!r}, "
              f"target {target!r} {bounds}")


def _probe(api, label: str, payload: dict) -> None:
    """Upload one case, read it back, report the findings, and delete it."""
    print(f"\n--- {label}")
    print(f"    name sent ({len(payload['workoutName'])} chars): {payload['workoutName']!r}")
    try:
        created = api.upload_workout(payload)
    except Exception as exc:  # noqa: BLE001 - the rejection IS the finding
        print(f"    REJECTED: {type(exc).__name__}: {exc}")
        return
    workout_id = created.get("workoutId")
    print(f"    ACCEPTED: workoutId={workout_id}")
    try:
        back = api.get_workout_by_id(workout_id) if workout_id else created
        name_back = back.get("workoutName")
        kept = name_back == payload["workoutName"]
        print(f"    name back ({len(name_back or '')} chars): {name_back!r} "
              f"{'(kept)' if kept else '(CHANGED)'}")
        _report_steps(payload, back)
    finally:
        if workout_id is not None:
            api.delete_workout(workout_id)
            print(f"    cleaned up {workout_id}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe exercise-sport edges and name limits.")
    parser.add_argument("--confirm", action="store_true", help="Actually upload (then delete).")
    args = parser.parse_args()

    cases = _cases()
    if not args.confirm:
        for label, payload in cases:
            print(f"\n--- {label}")
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        print("\nDry-run: nothing was uploaded. Re-run with --confirm to probe the account.")
        return 0

    api = client.login_api()
    for label, payload in cases:
        _probe(api, label, payload)
    print("\nRecord the findings in issues #65 and #58: which step types round-tripped, "
          "whether the HR target survived, and the longest name the account kept.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
