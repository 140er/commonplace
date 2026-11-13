"""The meeting lifecycle as data: every state, every event that may change it, and what each may
become.

``meetings.state`` is written by exactly one function, ``MeetingStore._transition``. It reads the
row, asks :func:`plan` what the table allows for ``(state, event)`` and applies the answer with a
compare-and-set on the state it read, inside one write transaction. The claim, the worker's steps,
the inviter's leave, the owner's stop, the kill switch, a normal end and crash recovery are all
events of this one table, so a rule is stated once and cannot be missed by a new writer:

* an ended state never changes;
* ``removed`` (a stop was requested) is left only by a discarding end: a stop always discards;
* ``ending`` and ``processing`` never go back to joining, announcing or live;
* a stop or a leave that lands in any non-ended state is recorded and cannot be undone by a step
  that was already in flight (that step's compare-and-set is refused);
* capture opens only from ``live``, which is entered only from ``announcing`` with the
  adapter-confirmed announcement stamped.

Nothing here touches a database, a clock or a provider.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

REQUESTED, JOINING, LOBBY, ANNOUNCING, LIVE = "requested", "joining", "lobby", "announcing", "live"
ENDING, PROCESSING, REMOVED = "ending", "processing", "removed"
DONE, CANCELLED, TIMEOUT, DISCARDED, INTERRUPTED = (
    "done",
    "cancelled",
    "timeout",
    "discarded",
    "interrupted",
)
FAILED = "failed:*"  # any ``failed:<code>``

INITIAL = REQUESTED
ENDED = (DONE, CANCELLED, TIMEOUT, DISCARDED, INTERRUPTED)  # plus every ``failed:<code>``
# A meeting a worker holds: it has a lease and may hold a join link and captured segments.
RUNNING = (JOINING, LOBBY, ANNOUNCING, LIVE, ENDING, PROCESSING, REMOVED)
ACTIVE = (REQUESTED, *RUNNING)
# What a meeting holds besides its row (participants, working segments) is only written while it is
# in one of these, so an end that discards cannot be followed by a late write that survives it.
CAPTURING = (LIVE,)
# Transcribed segments are written while the meeting listens and while the capture drains what it
# still holds at ``ending``; never after the meeting is removed, processing or ended.
SEGMENTING = (LIVE, ENDING)

# The events: one per kind of writer.
CLAIM = "claim"  # a worker takes a requested meeting
ADVANCE = "advance"  # the worker's next normal step, and the ``ending``/``processing`` of an end
LEAVE = "leave"  # the inviter's leave: stop listening, keep what was captured
STOP = "stop"  # the owner's stop: stop listening, discard what was captured
FINISH_KEEP = "finish_keep"  # the end of a normal end: what was captured is kept
FINISH_DISCARD = "finish_discard"  # an end that discards (removed, bot stop, failure, cancel)
RECOVER = "recover"  # the beat ending a meeting whose worker is gone
KILL = "kill"  # the kill switch: everything active is cancelled and discarded
EVENTS = (CLAIM, ADVANCE, LEAVE, STOP, FINISH_KEEP, FINISH_DISCARD, RECOVER, KILL)

_DISCARDS = (DISCARDED, CANCELLED, FAILED)
_RUNNING_STEP = {  # what any non-ending state may do besides its own forward step
    LEAVE: (ENDING,),
    STOP: (REMOVED,),
    FINISH_DISCARD: _DISCARDS,
    RECOVER: (INTERRUPTED,),
    KILL: (CANCELLED,),
}

# state -> event -> the states that event may write. A state or event that is absent is illegal
# (the compare-and-set is refused). An ended state has no row: it never changes.
TABLE: dict[str, dict[str, tuple[str, ...]]] = {
    REQUESTED: {CLAIM: (JOINING,), LEAVE: (CANCELLED,), STOP: (CANCELLED,), KILL: (CANCELLED,)},
    JOINING: {ADVANCE: (LOBBY, ANNOUNCING, ENDING), **_RUNNING_STEP},
    LOBBY: {ADVANCE: (ANNOUNCING, ENDING), **_RUNNING_STEP},
    ANNOUNCING: {ADVANCE: (LIVE, ENDING), **_RUNNING_STEP},
    LIVE: {ADVANCE: (ENDING,), **_RUNNING_STEP},
    # a leave landing here is already what is happening; a stop still wins
    ENDING: {
        ADVANCE: (ENDING, PROCESSING),
        STOP: (REMOVED,),
        FINISH_DISCARD: _DISCARDS,
        RECOVER: (INTERRUPTED,),
        KILL: (CANCELLED,),
    },
    PROCESSING: {
        FINISH_KEEP: (DONE, TIMEOUT),
        STOP: (REMOVED,),
        FINISH_DISCARD: _DISCARDS,
        RECOVER: (INTERRUPTED,),
        KILL: (CANCELLED,),
    },
    # only a discarding end leaves ``removed``; whatever ends it, it ends ``discarded``
    REMOVED: {FINISH_DISCARD: (DISCARDED,), RECOVER: (DISCARDED,), KILL: (DISCARDED,)},
}

# The ends that keep what was captured, and the one write that needs an extra fact.
_KEEPS = (FINISH_KEEP, RECOVER)
NEEDS = {LIVE: "announced_at"}  # capture opens only after the announcement was stamped


def is_ended(state: str) -> bool:
    return state in ENDED or state.startswith("failed:")


def is_active(state: str) -> bool:
    return state in TABLE


def _listed(targets, target) -> bool:
    return target in targets or (FAILED in targets and target.startswith("failed:"))


@dataclass(frozen=True)
class Plan:
    state: str  # the state to write
    ends: bool  # an ended state: the link is wiped, queued posts are cancelled
    discard: bool  # an end that deletes what was captured
    reason: Optional[str] = None  # a reason that replaces any stored one
    overwrite: bool = False  # the caller's reason replaces the stored one (else the first wins)


def plan(state: str, event: str, target: Optional[str] = None, have=()) -> Optional[Plan]:
    """What ``event`` may do to a meeting in ``state`` (``target`` is the state the caller wants
    for an event that has a choice, ``have`` the facts the write carries). ``None``: refused."""
    allowed = TABLE.get(state, {}).get(event)
    if allowed is None:
        return None
    if state == REMOVED:  # a stop wins over whatever ended it: it ends discarded
        if target is not None and not _listed(_DISCARDS, target):
            return None  # but a keeping end is not a way to end it
        target = DISCARDED
    elif target is None:
        if len(allowed) != 1:
            return None
        target = allowed[0]
    if not _listed(allowed, target):
        return None
    if NEEDS.get(target) and NEEDS[target] not in have:
        return None
    ends = is_ended(target)
    discard = ends and (event not in _KEEPS or state == REMOVED)
    reason = None
    if state == REQUESTED and event in (LEAVE, STOP, KILL):
        reason = CANCELLED
    elif event == STOP:
        reason = "stop_requested"
    elif event == KILL and state != REMOVED:
        reason = CANCELLED
    # A discarding end shows its own cause (a removal after the inviter left is a removal); every
    # other write keeps the first cause recorded. ``removed`` always keeps its stop.
    return Plan(target, ends, discard, reason, event == FINISH_DISCARD and state != REMOVED)


def edges() -> set[tuple[str, str]]:
    """Every ``(from, to)`` pair the table allows, ``failed:*`` standing for any failure."""
    return {
        (state, target)
        for state, cells in TABLE.items()
        for targets in cells.values()
        for target in targets
    }
