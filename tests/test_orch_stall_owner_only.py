"""#513: owner-only cards never count as stalled work for the no-progress nudge.

A card of restricted origin (or one whose proposal is still pending review) is not the
orchestrator's to move, even when its assignee is a shared agent connection with an expired
claim. It must not make the board stalled, nudge, or keep the nudge streak alive.
"""

import sqlite3

import pytest

from commonplace import events
from commonplace.boards import service as board_service, store as board_store
from commonplace.orch import stall, triggers
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests._phase_b_helpers import doing
from tests._split_helpers import world
from tests.test_orch_runner import BOARD

EXPIRED = "2029-12-31T00:00:00Z"


def at(minute: int) -> str:
    return f"2030-01-01T{minute // 60:02d}:{minute % 60:02d}:00Z"


@pytest.fixture
def clock(monkeypatch):
    class Clock:
        minute = 0

        def sweep(self, w, minute):
            self.minute = minute
            triggers.run(w.js, now=at(minute))

    c = Clock()
    for module in (board_store, board_service):
        monkeypatch.setattr(module, "now", lambda: at(c.minute))
    return c


def wakes(w):
    return [e for e in events.after(w.db, w.ws, 0, limit=1000) if e["kind"] == "board.stalled"]


def lapse(w, card, **fields):
    """An agent-held doing card whose claim ran out, with extra column values."""
    doing(w, card, w.a.actor)
    sets = ", ".join(f"{k}=?" for k in ("claim_expired_at", *fields))
    with w.boards.store.transaction() as conn:
        conn.execute(f"UPDATE tasks SET {sets} WHERE id=?", (EXPIRED, *fields.values(), card))


def picture(w, minute=60):
    with w.boards.store.connection() as conn:
        row = dict(conn.execute("SELECT * FROM boards WHERE slug=?", (BOARD,)).fetchone())
    return stall.assess(w.gw.workspace_dir(w.ws), row, None, at(minute), 20, None)


def streak(w):
    with w.st.connection() as conn:
        return stall.settings_in(conn, BOARD)["streak"]


@pytest.mark.parametrize(
    "fields",
    [{"restricted_origin": 1}, {"proposal_status": "pending"}],
    ids=["restricted-origin", "pending-proposal"],
)
def test_owner_only_expired_card_is_not_stalled(hub, clock, fields):
    w = world(hub, on=False, runner=True)
    card = w.card()
    lapse(w, card, **fields)
    got = picture(w)
    assert got["stalled"] is False
    assert got["doing"] == [] and got["expired"] == []
    for minute in (21, 42, 63):
        clock.sweep(w, minute)
    assert wakes(w) == []
    assert streak(w) == 0


def test_owner_only_card_clears_a_running_streak(hub, clock):
    w = world(hub, on=False, runner=True)
    card = w.card()
    lapse(w, card)
    clock.sweep(w, 21)
    clock.sweep(w, 42)
    assert len(wakes(w)) == 2 and streak(w) == 2
    with w.boards.store.transaction() as conn:
        conn.execute("UPDATE tasks SET restricted_origin=1 WHERE id=?", (card,))
    clock.sweep(w, 63)
    assert len(wakes(w)) == 2
    assert streak(w) == 0


def test_ordinary_expired_claim_still_stalls(hub, clock):
    w = world(hub, on=False, runner=True)
    card = w.card()
    lapse(w, card, restricted_origin=0)
    got = picture(w)
    assert got["stalled"] is True and [c["number"] for c in got["expired"]] == [card]
    clock.sweep(w, 21)
    assert len(wakes(w)) == 1


def test_owner_only_card_does_not_hide_an_ordinary_one(hub, clock):
    w = world(hub, on=False, runner=True)
    mine, shared = w.card(), w.card()
    lapse(w, mine, restricted_origin=1)
    lapse(w, shared)
    got = picture(w)
    assert got["stalled"] is True
    assert [c["number"] for c in got["expired"]] == [shared]


def test_older_schema_without_the_columns_still_works(monkeypatch):
    """A boards file that predates both columns is read without them."""
    import contextlib

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE tasks (id INTEGER PRIMARY KEY, board_id INTEGER, title TEXT, status TEXT,"
        " assignee TEXT, ask TEXT, ask_to TEXT, claim_until TEXT, claim_expired_at TEXT,"
        " updated_at TEXT);"
        "CREATE TABLE task_events (task_id INTEGER, to_status TEXT, created_at TEXT);"
        "CREATE TABLE card_snoozes (task_id INTEGER, until TEXT, after_card INTEGER);"
        "INSERT INTO tasks VALUES (7, 1, 'old', 'doing', 'conn:x', NULL, NULL, NULL, ?, ?);"
    )
    conn.execute("UPDATE tasks SET claim_expired_at=?, updated_at=?", (EXPIRED, EXPIRED))

    class Old:
        def __init__(self, *args, **kwargs):
            pass

        @contextlib.contextmanager
        def connection(self):
            yield conn

    monkeypatch.setattr(stall, "BoardStore", Old)
    got = stall.assess("unused", {"id": 1, "created_at": at(0)}, None, at(60), 20, None)
    assert got["stalled"] is True and [c["number"] for c in got["expired"]] == [7]
