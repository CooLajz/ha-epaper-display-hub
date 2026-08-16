"""Tests for timezone-aware display wake planning."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from custom_components.coolajz_epaper_display_hub.scheduling import (
    next_wake,
    normalize_wake_schedule,
)

PRAGUE = ZoneInfo("Europe/Prague")


def _schedule(interval: int = 60) -> dict[str, int]:
    return {str(hour): interval for hour in range(24)}


def test_next_wake_uses_next_hours_boundary() -> None:
    """The planner searches future boundaries instead of adding the current interval."""
    schedule = _schedule()
    schedule["23"] = 15
    now = datetime(2026, 8, 15, 22, 17, 3, tzinfo=PRAGUE)

    planned, sleep_seconds = next_wake(now, PRAGUE, schedule)

    assert planned.isoformat() == "2026-08-15T23:00:00+02:00"
    assert sleep_seconds == 2577


def test_exact_boundary_is_strictly_future() -> None:
    """A check-in on a boundary schedules the following boundary."""
    schedule = _schedule()
    schedule["23"] = 15

    planned, sleep_seconds = next_wake(
        datetime(2026, 8, 15, 23, 0, tzinfo=PRAGUE), PRAGUE, schedule
    )

    assert planned.isoformat() == "2026-08-15T23:15:00+02:00"
    assert sleep_seconds == 900


def test_checkin_in_second_half_serves_the_upcoming_boundary() -> None:
    """An early wake late in the cycle skips the nearly reached boundary."""
    planned, sleep_seconds = next_wake(
        datetime(2026, 8, 15, 0, 45, tzinfo=PRAGUE), PRAGUE, _schedule()
    )

    assert planned.isoformat() == "2026-08-15T02:00:00+02:00"
    assert sleep_seconds == 75 * 60


def test_ten_minute_cycle_waking_after_eight_minutes_sleeps_twelve() -> None:
    """The half-interval rule applies equally to shorter configured intervals."""
    planned, sleep_seconds = next_wake(
        datetime(2026, 8, 15, 0, 8, tzinfo=PRAGUE), PRAGUE, _schedule(10)
    )

    assert planned.isoformat() == "2026-08-15T00:20:00+02:00"
    assert sleep_seconds == 12 * 60


def test_checkin_in_first_half_keeps_the_upcoming_boundary() -> None:
    """A genuinely early unscheduled wake still targets the current boundary."""
    planned, sleep_seconds = next_wake(
        datetime(2026, 8, 15, 0, 20, tzinfo=PRAGUE), PRAGUE, _schedule()
    )

    assert planned.isoformat() == "2026-08-15T01:00:00+02:00"
    assert sleep_seconds == 40 * 60


def test_seconds_before_boundary_do_not_trigger_an_immediate_second_refresh() -> None:
    """Clock drift just before a boundary advances to the following interval."""
    planned, sleep_seconds = next_wake(
        datetime(2026, 8, 15, 0, 59, 40, tzinfo=PRAGUE), PRAGUE, _schedule()
    )

    assert planned.isoformat() == "2026-08-15T02:00:00+02:00"
    assert sleep_seconds == 60 * 60 + 20


def test_half_interval_uses_real_gap_across_schedule_transition() -> None:
    """The threshold follows the interval ending at a boundary, not the next hour."""
    schedule = _schedule()
    schedule["23"] = 15

    planned, sleep_seconds = next_wake(
        datetime(2026, 8, 15, 22, 45, tzinfo=PRAGUE), PRAGUE, schedule
    )

    assert planned.isoformat() == "2026-08-15T23:15:00+02:00"
    assert sleep_seconds == 30 * 60


def test_daylight_saving_gap_uses_real_timeline() -> None:
    """The nonexistent spring hour is skipped by timezone conversion."""
    planned, sleep_seconds = next_wake(
        datetime(2026, 3, 29, 1, 59, 30, tzinfo=PRAGUE), PRAGUE, _schedule()
    )

    assert planned.isoformat() == "2026-03-29T04:00:00+02:00"
    assert sleep_seconds == 60 * 60 + 30


def test_daylight_saving_fold_can_schedule_repeated_hour() -> None:
    """The repeated autumn hour remains a valid future instant."""
    planned, sleep_seconds = next_wake(
        datetime(2026, 10, 25, 2, 50, tzinfo=PRAGUE, fold=0),
        PRAGUE,
        _schedule(),
    )

    assert planned.isoformat() == "2026-10-25T03:00:00+01:00"
    assert sleep_seconds == 70 * 60


def test_earlier_one_time_override_wins() -> None:
    """A future override may shorten but never extend the scheduled sleep."""
    now = datetime(2026, 8, 15, 22, 17, 3, tzinfo=PRAGUE)
    planned, sleep_seconds = next_wake(
        now,
        PRAGUE,
        _schedule(),
        earlier_override=datetime(2026, 8, 15, 22, 20, tzinfo=PRAGUE),
    )

    assert planned.isoformat() == "2026-08-15T22:20:00+02:00"
    assert sleep_seconds == 177


def test_schedule_is_completed_and_rejects_unsupported_intervals() -> None:
    """Corrupt or historic storage cannot produce an unusable schedule."""
    normalized = normalize_wake_schedule({"0": 15, "1": 17, "2": "5"})
    assert normalized["0"] == 15
    assert normalized["1"] == 30
    assert normalized["2"] == 5
    assert len(normalized) == 24


def test_naive_times_are_rejected() -> None:
    """Planning never silently guesses a timezone."""
    with pytest.raises(ValueError, match="timezone-aware"):
        next_wake(datetime(2026, 8, 15, 22, 17), PRAGUE, _schedule())
