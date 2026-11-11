"""Due-time calculation for recurring work, with no storage and no clock of its own.

A schedule is a small dict: ``{"every_minutes": N}`` counted from ``start``,
``{"daily": "HH:MM"}`` or ``{"weekly": "mon", "at": "HH:MM"}``, each optionally with
``"tz"`` (an IANA zone, default UTC). Callers pass the time in; this module never
reads the clock, so a fake clock drives every test.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MIN_INTERVAL_MINUTES = 60
MAX_INTERVAL_MINUTES = 60 * 24 * 366


class ScheduleError(ValueError):
    pass


def _clock(value):
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        raise ScheduleError("time must be HH:MM")
    try:
        hour, minute = int(value[:2]), int(value[3:])
    except ValueError:
        raise ScheduleError("time must be HH:MM") from None
    if not (0 <= hour < 24 and 0 <= minute < 60) or not value.replace(":", "").isdigit():
        raise ScheduleError("time must be HH:MM")
    return hour, minute


def _zone(name):
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise ScheduleError("unknown time zone") from None


def parse(spec, *, min_interval=MIN_INTERVAL_MINUTES):
    """Return the normalised schedule, or raise ScheduleError."""
    if not isinstance(spec, dict):
        raise ScheduleError("schedule must be an object")
    kinds = [k for k in ("every_minutes", "daily", "weekly") if k in spec]
    if len(kinds) != 1:
        raise ScheduleError("schedule needs exactly one of every_minutes, daily, weekly")
    kind = kinds[0]
    allowed = {
        "every_minutes": {"every_minutes", "start", "tz"},
        "daily": {"daily", "tz"},
        "weekly": {"weekly", "at", "tz"},
    }[kind]
    if set(spec) - allowed:
        raise ScheduleError("unknown schedule field")
    tz = spec.get("tz", "UTC")
    if not isinstance(tz, str):
        raise ScheduleError("unknown time zone")
    _zone(tz)
    result = {kind: spec[kind], "tz": tz}
    if kind == "every_minutes":
        minutes = spec["every_minutes"]
        if type(minutes) is not int or not min_interval <= minutes <= MAX_INTERVAL_MINUTES:
            raise ScheduleError(f"every_minutes must be a whole number from {min_interval} up")
        start = spec.get("start")
        if start is not None:
            result["start"] = _utc(start).isoformat()
    elif kind == "daily":
        _clock(spec["daily"])
    else:
        if spec["weekly"] not in DAYS:
            raise ScheduleError("weekly must be mon, tue, wed, thu, fri, sat or sun")
        if "at" not in spec:
            raise ScheduleError("weekly needs at: HH:MM")
        _clock(spec["at"])
        result["at"] = spec["at"]
    return result


def _utc(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ScheduleError("start must be an ISO time") from None
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ScheduleError("start must carry a time zone")
    return value.astimezone(timezone.utc)


def next_due(schedule, after, *, anchor=None):
    """First due time strictly after ``after`` (aware datetime), in UTC.

    ``anchor`` is where an interval schedule counts from when it carries no
    ``start`` (callers pass the routine's creation time).
    """
    after = _utc(after)
    if "every_minutes" in schedule:
        step = timedelta(minutes=schedule["every_minutes"])
        base = _utc(schedule["start"]) if "start" in schedule else _utc(anchor or after)
        if after < base:
            return base
        return base + step * ((after - base) // step + 1)
    zone = _zone(schedule.get("tz", "UTC"))
    hour, minute = _clock(schedule["daily"] if "daily" in schedule else schedule["at"])
    local = after.astimezone(zone)
    for offset in range(0, 15):
        day = local.date() + timedelta(days=offset)
        if "weekly" in schedule and DAYS[day.weekday()] != schedule["weekly"]:
            continue
        candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)
        candidate = candidate.astimezone(timezone.utc)
        if candidate > after:
            return candidate
    raise ScheduleError("no due time found")


def due_between(schedule, after, upto, *, anchor=None, limit=1000):
    """Due times in (after, upto], oldest first, at most ``limit``."""
    result, cursor, upto = [], _utc(after), _utc(upto)
    while len(result) < limit:
        cursor = next_due(schedule, cursor, anchor=anchor)
        if cursor > upto:
            break
        result.append(cursor)
    return result
