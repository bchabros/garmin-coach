"""Normalize hand-logged circuit stations into manual set rows."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, NotRequired, TypedDict

from pydantic import ConfigDict, with_config


@with_config(ConfigDict(extra="forbid"))
class ManualStationDetails(TypedDict):
    """Optional detail accepted for one hand-logged circuit station."""

    subcategory: str
    category: NotRequired[str | None]
    reps: NotRequired[int | None]
    sets: NotRequired[int | None]
    duration_s: NotRequired[int | float | None]
    max_weight: NotRequired[int | float | None]


# A station name in Garmin's vocabulary (SCREAMING_SNAKE_CASE), after normalization.
_STATION_NAME = re.compile(r"^[A-Z0-9_]+$")


def _station_name(raw: Any, idx: int) -> str:
    """Normalize one station name to Garmin's vocabulary (``sled pull`` -> ``SLED_PULL``)."""
    name = re.sub(r"[\s\-]+", "_", str(raw).strip().upper())
    if not name:
        raise ValueError(f"station {idx}: empty name")
    if not _STATION_NAME.match(name):
        raise ValueError(
            f"station {idx}: a name may use letters, digits and underscores only (got {raw!r})"
        )
    return name


def _non_negative(name: str, value: Any, idx: int, *, integer: bool) -> int | float | None:
    """Return an optional non-negative number, or raise naming the offending field."""
    if value is None:
        return None
    kind = "integer" if integer else "number"
    ok = isinstance(value, int) if integer else isinstance(value, (int, float))
    if not ok or isinstance(value, bool) or value < 0:
        raise ValueError(f"station {idx}: {name} must be a non-negative {kind} (got {value!r})")
    return value


def _station_row(activity_id: int, idx: int, station: str | ManualStationDetails) -> dict[str, Any]:
    """Build one ``manual_activity_sets`` row from a station input."""
    detail: Mapping[str, Any] = (
        station if isinstance(station, Mapping) else {"subcategory": station}
    )
    unknown_fields = sorted(set(detail) - set(ManualStationDetails.__annotations__))
    if unknown_fields:
        raise ValueError(f"station {idx}: unsupported field(s): {', '.join(unknown_fields)}")
    if detail.get("subcategory") is None:
        raise ValueError(f"station {idx}: 'subcategory' is required")
    return {
        "activity_id": activity_id,
        "set_idx": idx,
        "category": detail.get("category"),
        "subcategory": _station_name(detail["subcategory"], idx),
        "reps": _non_negative("reps", detail.get("reps"), idx, integer=True),
        "sets": _non_negative("sets", detail.get("sets"), idx, integer=True),
        "duration_s": _non_negative("duration_s", detail.get("duration_s"), idx, integer=False),
        "max_weight": _non_negative("max_weight", detail.get("max_weight"), idx, integer=False),
    }


def normalize(activity_id: int, stations: list[str | ManualStationDetails]) -> list[dict[str, Any]]:
    """Validate ordered station inputs and return rows for the manual set overlay.

    Args:
        activity_id: Activity that owns the stations.
        stations: Bare station names or mappings with the documented optional details.

    Returns:
        One normalized row per station, retaining the input order as ``set_idx``.

    Raises:
        ValueError: If the list is empty or a station is malformed.
    """
    if not stations:
        raise ValueError("at least one station is required")
    return [_station_row(activity_id, i, station) for i, station in enumerate(stations)]
