"""Due-time calculation: pure functions, no clock."""

from datetime import datetime, timedelta, timezone

import pytest

from commonplace import schedule_due as sd

UTC = timezone.utc


def at(*args):
    return datetime(*args, tzinfo=UTC)


def test_interval_counts_from_anchor_and_is_strictly_after():
    s = sd.parse({"every_minutes": 90})
    anchor = at(2026, 11, 11, 8, 0)
    assert sd.next_due(s, anchor, anchor=anchor) == at(2026, 11, 11, 9, 30)
    assert sd.next_due(s, at(2026, 11, 11, 9, 30), anchor=anchor) == at(2026, 11, 11, 11, 0)
    assert sd.next_due(s, at(2026, 11, 11, 7, 0), anchor=anchor) == anchor


def test_interval_start_overrides_anchor():
    s = sd.parse({"every_minutes": 60, "start": "2026-11-12T00:00:00Z"})
    assert sd.next_due(s, at(2026, 11, 11, 8, 0), anchor=at(2026, 1, 1)) == at(2026, 11, 12, 0, 0)


def test_daily_and_weekly_in_a_zone():
    daily = sd.parse({"daily": "09:00", "tz": "Europe/Amsterdam"})
    assert sd.next_due(daily, at(2026, 11, 11, 8, 0)) == at(2026, 11, 12, 8, 0)
    assert sd.next_due(daily, at(2026, 11, 11, 7, 59)) == at(2026, 11, 11, 8, 0)
    weekly = sd.parse({"weekly": "mon", "at": "06:30"})
    assert sd.next_due(weekly, at(2026, 11, 11, 8, 0)) == at(2026, 11, 16, 6, 30)


def test_daily_across_dst_stays_local_nine():
    daily = sd.parse({"daily": "09:00", "tz": "Europe/Amsterdam"})
    summer = sd.next_due(daily, at(2026, 10, 24, 12, 0))
    winter = sd.next_due(daily, at(2026, 10, 25, 12, 0))
    assert summer == at(2026, 10, 25, 8, 0)
    assert winter == at(2026, 10, 26, 8, 0)


def test_due_between_is_bounded_and_ordered():
    s = sd.parse({"every_minutes": 60})
    anchor = at(2026, 11, 11, 0, 0)
    dues = sd.due_between(s, anchor, anchor + timedelta(hours=5), anchor=anchor, limit=3)
    assert dues == [anchor + timedelta(hours=h) for h in (1, 2, 3)]
    assert sd.due_between(s, anchor, anchor + timedelta(minutes=59), anchor=anchor) == []


@pytest.mark.parametrize(
    "spec",
    [
        None,
        {},
        {"every_minutes": 59},
        {"every_minutes": 60.5},
        {"every_minutes": True},
        {"every_minutes": 60, "daily": "09:00"},
        {"daily": "9:00"},
        {"daily": "24:00"},
        {"daily": "09:00", "tz": "Nowhere/Land"},
        {"daily": "09:00", "extra": 1},
        {"weekly": "funday", "at": "09:00"},
        {"weekly": "mon"},
        {"every_minutes": 60, "start": "2026-11-12T00:00:00"},
    ],
)
def test_parse_refuses_bad_schedules(spec):
    with pytest.raises(sd.ScheduleError):
        sd.parse(spec)
