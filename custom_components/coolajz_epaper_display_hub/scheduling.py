"""Timezone-aware wake scheduling for sleeping displays."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any

from .const import (
    DEFAULT_REFRESH_INTERVAL_MINUTES,
    DEFAULT_WAKE_TIME_CORRECTION_SECONDS,
    MAX_WAKE_TIME_CORRECTION_SECONDS,
    MIN_WAKE_TIME_CORRECTION_SECONDS,
    WAKE_INTERVAL_OPTIONS,
)

_MAX_SEARCH_MINUTES = 3 * 24 * 60


def normalize_wake_schedule(value: Any) -> dict[str, int]:
    """Return a complete and safe 24-hour wake schedule."""
    source = value if isinstance(value, Mapping) else {}
    result: dict[str, int] = {}
    for hour in range(24):
        raw = source.get(str(hour), DEFAULT_REFRESH_INTERVAL_MINUTES)
        try:
            interval = int(raw)
        except (TypeError, ValueError):
            interval = DEFAULT_REFRESH_INTERVAL_MINUTES
        if interval not in WAKE_INTERVAL_OPTIONS:
            interval = DEFAULT_REFRESH_INTERVAL_MINUTES
        result[str(hour)] = interval
    return result


def normalize_wake_time_correction(value: Any) -> int:
    """Return a safe signed correction for a planned wake boundary."""
    if isinstance(value, bool):
        return DEFAULT_WAKE_TIME_CORRECTION_SECONDS
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return DEFAULT_WAKE_TIME_CORRECTION_SECONDS
    if not numeric.is_integer():
        return DEFAULT_WAKE_TIME_CORRECTION_SECONDS
    correction = int(numeric)
    if not (
        MIN_WAKE_TIME_CORRECTION_SECONDS
        <= correction
        <= MAX_WAKE_TIME_CORRECTION_SECONDS
    ):
        return DEFAULT_WAKE_TIME_CORRECTION_SECONDS
    return correction


def next_wake(
    now: datetime,
    timezone: tzinfo,
    schedule: Any,
    *,
    correction_seconds: Any = DEFAULT_WAKE_TIME_CORRECTION_SECONDS,
    earlier_override: datetime | None = None,
) -> tuple[datetime, int]:
    """Find the next boundary, treating a late-cycle check-in as already served."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now_utc = now.astimezone(UTC).replace(microsecond=0)
    normalized = normalize_wake_schedule(schedule)
    minute_floor_utc = now_utc.replace(second=0)
    previous_boundary_utc: datetime | None = None
    for _ in range(_MAX_SEARCH_MINUTES):
        local_candidate = minute_floor_utc.astimezone(timezone)
        if _is_boundary(local_candidate, normalized):
            previous_boundary_utc = minute_floor_utc
            break
        minute_floor_utc -= timedelta(minutes=1)
    if previous_boundary_utc is None:
        raise ValueError("wake schedule has no future boundary")

    scheduled: datetime | None = None
    candidate_utc = now_utc.replace(second=0) + timedelta(minutes=1)
    for _ in range(_MAX_SEARCH_MINUTES):
        local_candidate = candidate_utc.astimezone(timezone)
        if _is_boundary(local_candidate, normalized):
            boundary_interval = candidate_utc - previous_boundary_utc
            remaining = candidate_utc - now_utc
            if remaining > boundary_interval / 2:
                scheduled = local_candidate
                break
            previous_boundary_utc = candidate_utc
        candidate_utc += timedelta(minutes=1)
    if scheduled is None:
        raise ValueError("wake schedule has no future boundary")

    correction = normalize_wake_time_correction(correction_seconds)
    scheduled = (
        scheduled.astimezone(UTC) + timedelta(seconds=correction)
    ).astimezone(timezone)

    if earlier_override is not None:
        if earlier_override.tzinfo is None:
            raise ValueError("earlier_override must be timezone-aware")
        override = earlier_override.astimezone(timezone).replace(microsecond=0)
        if now_utc < override.astimezone(UTC) < scheduled.astimezone(UTC):
            scheduled = override

    sleep_seconds = int((scheduled.astimezone(UTC) - now_utc).total_seconds())
    return scheduled, sleep_seconds


def _is_boundary(candidate: datetime, schedule: Mapping[str, int]) -> bool:
    """Return whether one local minute is a configured wake boundary."""
    interval = schedule[str(candidate.hour)]
    return candidate.minute % interval == 0
