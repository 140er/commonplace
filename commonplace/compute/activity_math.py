"""Clipped, exclusive agent time and civil-day accounting."""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .activity_schema import PHASES

ZONE = ZoneInfo("Europe/Amsterdam")


def timestamp(value):
    try:
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        if type(value) not in (int, float) or not math.isfinite(value):
            return None
        return float(value) if 946684800 <= value <= 4102444800 else None
    except (ValueError, OverflowError, OSError):
        return None


def clipped(start, end, window, *, open_at=None):
    start, end = timestamp(start), timestamp(end) if end is not None else open_at
    if start is None or end is None or end < start:
        return None
    lo, hi = max(start, window[0]), min(end, window[1])
    return (lo, hi) if hi > lo else None


def union(intervals):
    result: list[tuple[float, float]] = []
    for start, end in sorted(intervals):
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(end, result[-1][1]))
        elif end > start:
            result.append((start, end))
    return result


def length(intervals):
    return sum(b - a for a, b in union(intervals))


def percentile(samples, fraction=0.95):
    if not samples:
        return None
    return sorted(samples)[max(0, math.ceil(fraction * len(samples)) - 1)]


def peak(agents):
    events: defaultdict[float, int] = defaultdict(int)
    for intervals in agents:
        for start, end in union(intervals):
            events[start] += 1
            events[end] -= 1
    current = maximum = 0
    for _at, change in sorted(events.items()):
        current += change
        maximum = max(maximum, current)
    return maximum


def civil_parts(start, end):
    """Split by actual local midnights; a civil day can have 23 or 25 hours."""
    while start < end:
        local = datetime.fromtimestamp(start, ZONE)
        tomorrow = local.date() + timedelta(days=1)
        boundary = datetime.combine(tomorrow, datetime.min.time(), ZONE).timestamp()
        stop = min(end, boundary)
        yield local.date().isoformat(), stop - start
        start = stop


def days(window):
    at = datetime.fromtimestamp(window[0], ZONE).date()
    end = datetime.fromtimestamp(window[1] - 0.001, ZONE).date()
    while at <= end:
        yield at.isoformat()
        at += timedelta(days=1)


def phases(base, recorded):
    """Sweep overlapping events with wait > compaction > tool > unknown precedence.

    Returns exclusive wall segments within the union of this agent's started
    runs. Nested calls never multiply the agent's wall time.
    """
    events: defaultdict[float, defaultdict[str, int]] = defaultdict(lambda: defaultdict(int))
    for kind, intervals in [("base", union(base)), *recorded.items()]:
        for a, b in union(intervals):
            events[a][kind] += 1
            events[b][kind] -= 1
    active: defaultdict[str, int] = defaultdict(int)
    last = None
    result: list[tuple[float, float, str]] = []
    for at, changes in sorted(events.items()):
        if last is not None and active["base"] and at > last:
            kind = next((p for p in PHASES[:-1] if active[p]), "unknown")
            if result and result[-1][2] == kind and result[-1][1] == last:
                result[-1] = (result[-1][0], at, kind)
            else:
                result.append((last, at, kind))
        for kind, change in changes.items():
            active[kind] += change
        last = at
    return result


def model_parts(segments, runs, fallback):
    """Split phase segments at model boundaries, with the latest run winning overlaps."""
    for start, end, phase in segments:
        edges = {start, end}
        for run in runs:
            if run["interval"]:
                edges.update(at for at in run["interval"] if start < at < end)
        points = sorted(edges)
        for a, b in zip(points, points[1:]):
            candidates = [
                r for r in runs if r["interval"] and r["interval"][0] <= a < r["interval"][1]
            ]
            name = max(candidates, key=lambda r: r["ordinal"])["model"] if candidates else fallback
            yield a, b, phase, name


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()
