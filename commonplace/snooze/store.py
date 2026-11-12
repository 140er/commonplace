"""Card snoozes in the board database: set, clear, read and wake.

A snoozed card is open work that is deliberately not offered: ``card_snoozes`` holds one
row per snoozed card, either a UTC time (the owner's wall time and timezone are kept for
display) or one card that must be done first. A card counts as snoozed while its row
exists *and* the time has not passed or the other card is not done, so a late worker never
leaves a card hidden: ``wake_due`` only does the bookkeeping (delete the row, log the wake,
bump the version) and does it once, because the delete happens inside the board's write
transaction and only the transaction that deletes the row logs the wake.

Only ``backlog`` and ``todo`` cards can be snoozed; a card that moves on while snoozed is
no longer shown as snoozed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from commonplace import schedule_time as times
from commonplace.boards.store import CardRefused, append_event, task_row

SNOOZABLE = ("backlog", "todo")
MAX_DAYS = 366


def clock() -> datetime:
    """The server clock; tests replace this one function."""
    return datetime.now(timezone.utc)


def now_stamp() -> str:
    return times.stamp(clock())


def _has_table(conn) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE name='card_snoozes'").fetchone())


def snoozed_sql(alias: str = "tasks") -> str:
    """SQL condition (one ``?``: the current stamp) true for a card that is snoozed now."""
    return (
        f"({alias}.status IN ('backlog','todo') AND EXISTS (SELECT 1 FROM card_snoozes s "
        f"WHERE s.task_id={alias}.id AND ((s.until IS NOT NULL AND s.until > ?) "
        "OR (s.after_card IS NOT NULL AND coalesce("
        "(SELECT a.status FROM tasks a WHERE a.id=s.after_card), '') != 'done'))))"
    )


def label(until, tz, after_card) -> str:
    if after_card is not None:
        return f"#{after_card} is done"
    local = times.parse_stamp(until).astimezone(times.zone(tz or "UTC"))
    return local.strftime("%Y-%m-%d %H:%M ") + (tz or "UTC")


def active(conn, number: int, now: str | None = None) -> dict | None:
    """The snooze of card ``number`` if it is snoozed at ``now`` (default: the clock)."""
    if not _has_table(conn):
        return None
    now = now or now_stamp()
    row = conn.execute(
        "SELECT s.* FROM card_snoozes s JOIN tasks t ON t.id=s.task_id WHERE s.task_id=? AND "
        + snoozed_sql("t"),
        (number, now),
    ).fetchone()
    return view(row) if row else None


def view(row) -> dict:
    return {
        "until": row["until"],
        "timezone": row["timezone"],
        "after_card": row["after_card"],
        "by": row["by"],
        "at": row["at"],
        "text": "Snoozed until " + label(row["until"], row["timezone"], row["after_card"]),
    }


def allowed(principal, row) -> bool:
    """Who may snooze and unsnooze a card: a workspace owner (person) or its assignee."""
    return bool(
        (principal.person and principal.role == "owner")
        or (row["assignee"] and row["assignee"] == principal.actor)
    )


def _invalid(code, message):
    return CardRefused(code, message, status=400)


def resolve(until, tz, after_card, now: datetime) -> tuple:
    """``(until stamp, timezone, after_card)`` of a validated request, or ``CardRefused``."""
    if (until is None) == (after_card is None):
        raise _invalid(
            "invalid_snooze", "give exactly one of until (a time) or after_card (a card number)"
        )
    if after_card is not None:
        if tz is not None:
            raise _invalid("invalid_snooze", "a card condition takes no timezone")
        if type(after_card) is not int or not 1 <= after_card < 2**63:
            raise _invalid("invalid_condition", "after_card must be a card number")
        return None, None, after_card
    if tz is not None:
        try:
            times.zone(tz)
        except ValueError as exc:
            raise _invalid("invalid_timezone", str(exc)) from None
    try:
        moment = times.at(until, tz)
    except ValueError as exc:
        code = "timezone_required" if "needs a timezone" in str(exc) else "invalid_time"
        raise _invalid(code, str(exc)) from None
    if moment <= now:
        raise _invalid("in_past", "the wake-up time must be in the future")
    if moment > now + timedelta(days=MAX_DAYS):
        raise _invalid("too_far", f"the wake-up time can be at most {MAX_DAYS} days away")
    return times.stamp(moment), tz or "UTC", None


def set_snooze(conn, row, *, until, tz, after_card, actor: str, now: datetime) -> dict:
    """Snooze ``row`` (replacing an earlier snooze); returns the stored view."""
    if row["status"] not in SNOOZABLE:
        raise CardRefused("not_snoozable", "only a Backlog or Todo card can be snoozed", status=409)
    stamp, zone_name, after = resolve(until, tz, after_card, now)
    if after is not None:
        if after == row["id"]:
            raise _invalid("self", "a card cannot wait for itself")
        other = conn.execute("SELECT board_id,status FROM tasks WHERE id=?", (after,)).fetchone()
        if other is None or other["board_id"] != row["board_id"]:
            raise _invalid("other_board", f"#{after} is not a card on this board")
        if other["status"] == "done":
            raise CardRefused("condition_met", f"#{after} is already done", status=409)
    conn.execute("DELETE FROM card_snoozes WHERE task_id=?", (row["id"],))
    conn.execute(
        "INSERT INTO card_snoozes(task_id,until,timezone,after_card,by,at) VALUES (?,?,?,?,?,?)",
        (row["id"], stamp, zone_name, after, actor, times.stamp(now)),
    )
    _touch(conn, row["id"], actor, now)
    return view(conn.execute("SELECT * FROM card_snoozes WHERE task_id=?", (row["id"],)).fetchone())


def clear(conn, row, *, actor: str, now: datetime) -> bool:
    """Remove the snooze of ``row``; False when it had none."""
    removed = conn.execute("DELETE FROM card_snoozes WHERE task_id=?", (row["id"],)).rowcount
    if removed:
        _touch(conn, row["id"], actor, now)
    return bool(removed)


def _touch(conn, number, actor, now: datetime) -> None:
    conn.execute(
        "UPDATE tasks SET version=version+1, updated_at=?, updated_by=? WHERE id=?",
        (times.stamp(now), actor, number),
    )


_DUE = (
    "((s.until IS NOT NULL AND s.until <= ?) OR (s.after_card IS NOT NULL AND "
    "(SELECT a.status FROM tasks a WHERE a.id=s.after_card)='done'))"
)


def has_due(conn, now: datetime) -> bool:
    """Read-only check the worker runs before it takes the board's write lock."""
    return _has_table(conn) and bool(
        conn.execute(
            "SELECT 1 FROM card_snoozes s WHERE " + _DUE + " LIMIT 1", (times.stamp(now),)
        ).fetchone()
    )


def wake_due(conn, now: datetime, board_id: int | None = None) -> list[dict]:
    """End every snooze that is due at ``now`` and log it; returns the woken cards (rows).

    Call inside a write transaction. A snooze row is deleted by exactly one caller."""
    if not _has_table(conn):
        return []
    stamp = times.stamp(now)
    due = conn.execute(
        "SELECT s.task_id FROM card_snoozes s JOIN tasks t ON t.id=s.task_id WHERE "
        + ("t.board_id=? AND " if board_id is not None else "")
        + _DUE
        + " ORDER BY s.task_id",
        ((board_id,) if board_id is not None else ()) + (stamp,),
    ).fetchall()
    woken = []
    for found in due:
        number = found["task_id"]
        snooze = conn.execute("SELECT * FROM card_snoozes WHERE task_id=?", (number,)).fetchone()
        if (
            snooze is None
            or not conn.execute("DELETE FROM card_snoozes WHERE task_id=?", (number,)).rowcount
        ):
            continue
        row = task_row(conn, number)
        if row["status"] not in SNOOZABLE:
            continue  # the card moved on while snoozed: nothing to wake
        _touch(conn, number, "system", now)
        append_event(
            conn,
            number,
            "comment",
            "system",
            text="Snooze ended: "
            + (
                f"#{snooze['after_card']} is done"
                if snooze["after_card"] is not None
                else "the wake-up time has come"
            ),
        )
        woken.append(task_row(conn, number))
    return woken


def event_data(row, **extra) -> dict:
    """The payload shape the board's card events use."""
    return {
        "board": row["board"],
        "number": row["id"],
        "parent": row["parent_number"],
        "title": row["title"],
        "task_type": row["task_type"],
        "status": row["status"],
        "assignee": row["assignee"],
        "ask_to": row["ask_to"],
        "actor": "system",
        **extra,
    }
