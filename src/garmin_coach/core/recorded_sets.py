"""Pure recorded-set proposals over complete source evidence and explicit identities."""

from __future__ import annotations

import copy
import logging
from typing import Any, TypedDict

logger = logging.getLogger(__name__)

UNRECORDED_REST_WEIGHT = -1


class WorkingSet(TypedDict):
    """Validated reported values and optional explicit source alignment.

    An absent category preserves the complete source recognition candidates.
    A known category with a null name explicitly selects its generic exercise.
    """

    category: str | None
    name: str | None
    reps: int | None
    weight: int | None
    captured_index: int | None
    pairing_explicit: bool


def _matches(row: WorkingSet, entry: dict[str, Any]) -> bool:
    return row["category"] is not None and any(
        exercise.get("category") == row["category"] and exercise.get("name") == row["name"]
        for exercise in entry["exercises"]
    )


def _unique_pairs(
    intended: list[WorkingSet],
    active: list[dict[str, Any]],
    paired: dict[int, tuple[int | None, str]],
    available: set[int],
    *,
    with_weight: bool,
) -> None:
    candidates = {
        i: [
            j
            for j in sorted(available)
            if _matches(row, active[j])
            and (
                not with_weight
                or row["weight"] is not None
                and row["weight"] == active[j].get("weight")
            )
        ]
        for i, row in enumerate(intended)
        if i not in paired
    }
    for i, matches in candidates.items():
        if len(matches) == 1 and sum(matches[0] in other for other in candidates.values()) == 1:
            j = matches[0]
            paired[i] = (j, "identity_and_weight" if with_weight else "identity")
            available.remove(j)


def _pair(intended: list[WorkingSet], active: list[dict[str, Any]]) -> list[tuple[int | None, str]]:
    if intended[0]["pairing_explicit"]:
        if any(
            row["captured_index"] is not None and row["captured_index"] >= len(active)
            for row in intended
        ):
            raise ValueError("captured_index must refer to an existing active set")
        return [
            (
                row["captured_index"],
                "explicit" if row["captured_index"] is not None else "new_working_set",
            )
            for row in intended
        ]
    paired: dict[int, tuple[int | None, str]] = {}
    available = set(range(len(active)))
    for with_weight in (True, False):
        _unique_pairs(intended, active, paired, available, with_weight=with_weight)
    # Repeated identities take precedence over consuming their timing for an unrelated new row.
    for i, row in enumerate(intended):
        matches = [j for j in sorted(available) if _matches(row, active[j])]
        if i not in paired and matches:
            paired[i] = (matches[0], "identity_order_review_required")
            available.remove(matches[0])
    for i in range(len(intended)):
        if i not in paired:
            j = min(available) if available else None
            paired[i] = (
                j,
                "order_fallback_review_required" if j is not None else "new_working_set",
            )
            if j is not None:
                available.remove(j)
    return [paired[i] for i in range(len(intended))]


def _display(entry: dict[str, Any]) -> dict[str, Any]:
    weight = entry.get("weight")
    return {
        "exercises": [
            {key: exercise.get(key) for key in ("category", "name")}
            for exercise in entry["exercises"]
        ],
        "reps": entry.get("repetitionCount"),
        "weight_kg": weight / 1000 if weight is not None and weight >= 0 else None,
        "start_time": entry.get("startTime"),
        "duration": entry.get("duration"),
        "timing_source": "unrecorded"
        if entry.get("duration") is None and entry.get("startTime") is None
        else "source",
    }


def _unrecorded_rest() -> dict[str, Any]:
    return {
        "setType": "REST",
        "duration": None,
        "exercises": [],
        "repetitionCount": None,
        "weight": UNRECORDED_REST_WEIGHT,
        "startTime": None,
    }


def _new_entry(row: WorkingSet) -> dict[str, Any]:
    if row["category"] is None or row["reps"] is None or row["weight"] is None:
        raise ValueError(
            "new working sets require an explicit exercise, reps and weight_kg; zero weight means bodyweight"
        )
    return {
        "exercises": [{"probability": 100, "category": row["category"], "name": row["name"]}],
        "repetitionCount": row["reps"],
        "duration": None,
        "weight": row["weight"],
        "setType": "ACTIVE",
    }


def _source_blocks(
    entries: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[list[dict[str, Any]]]]:
    leading: list[dict[str, Any]] = []
    blocks: list[list[dict[str, Any]]] = []
    for entry in entries:
        if entry["setType"] == "ACTIVE":
            blocks.append([entry])
        elif blocks:
            blocks[-1].append(entry)
        else:
            leading.append(entry)
    return leading, blocks


def _apply(row: WorkingSet, entry: dict[str, Any]) -> None:
    if row["category"] is not None:
        exercise = copy.deepcopy(entry["exercises"][0]) if entry["exercises"] else {}
        exercise.update(category=row["category"], name=row["name"], probability=100)
        entry["exercises"] = [exercise]
    for key, value in (("repetitionCount", row["reps"]), ("weight", row["weight"])):
        if value is not None:
            entry[key] = value


def _empty_rest(entry: dict[str, Any]) -> bool:
    fixed = {"setType", "exercises", "weight"}
    return (
        entry.get("setType") == "REST"
        and not entry.get("exercises")
        and entry.get("weight") in (None, UNRECORDED_REST_WEIGHT)
        and all(value is None for key, value in entry.items() if key not in fixed)
    )


def _assemble(
    leading: list[dict[str, Any]],
    blocks: list[list[dict[str, Any]]],
    pairing: list[tuple[int | None, str]],
    new: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    selected = [index for index, _ in pairing if index is not None]
    if new and selected != sorted(selected):
        raise ValueError("adding sets requires retained captured_index values in source order")
    ordered = []
    if new:
        for i, (index, _) in enumerate(pairing):
            ordered.append(
                ([new[i], _unrecorded_rest()], True) if index is None else (blocks[index], False)
            )
    else:
        ordered = [(blocks[index], False) for index in sorted(selected)]
    entries = list(leading)
    for block, is_new in ordered:
        if is_new and entries and entries[-1]["setType"] == "ACTIVE":
            entries.append(_unrecorded_rest())
        entries.extend(block)
    if len(selected) < len(blocks) and not new:
        while entries and _empty_rest(entries[-1]):
            entries.pop()
    return entries


def _changes(
    intended: list[WorkingSet],
    pairing: list[tuple[int | None, str]],
    active: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]:
    changes: list[dict[str, Any]] = []
    new: dict[int, dict[str, Any]] = {}
    for i, (row, (index, basis)) in enumerate(zip(intended, pairing, strict=True)):
        entry = active[index] if index is not None else _new_entry(row)
        change = {
            "captured_index": index,
            "pairing_basis": basis,
            "name_matches": _matches(row, entry)
            if index is not None and row["category"] is not None
            else None,
            "identity_preserved": row["category"] is None,
            "weight_matches": row["weight"] == entry.get("weight")
            if index is not None and row["weight"] is not None
            else None,
            "before": _display(entry) if index is not None else None,
        }
        _apply(row, entry)
        if index is None:
            new[i] = entry
        changes.append({**change, "after": _display(entry)})
    return changes, new


def propose(payload: dict[str, Any], intended: list[WorkingSet]) -> dict[str, Any]:
    """Build reviewed retained/new/deleted sets with source timing left intact.

    New sets use the operator-observed nullable timing contract; no duration,
    timestamp, identifier or missing measurement is fabricated. Source recognition
    alternatives remain intact unless an explicit exercise identity replaces them.
    """
    proposed = copy.deepcopy(payload)
    leading, blocks = _source_blocks(proposed["exerciseSets"])
    active = [block[0] for block in blocks]
    pairing = _pair(intended, active)
    changes, new = _changes(intended, pairing, active)
    selected = {index for index, _ in pairing if index is not None}
    deleted = [
        {
            "captured_index": i,
            "before": _display(block[0]),
            "rests": [_display(rest) for rest in block[1:]],
        }
        for i, block in enumerate(blocks)
        if i not in selected
    ]
    proposed["exerciseSets"] = _assemble(leading, blocks, pairing, new)
    return {
        "pairing": [index for index, _ in pairing],
        "changes": changes,
        "added": [change for change in changes if change["captured_index"] is None],
        "deleted": deleted,
        "proposed": proposed,
    }
