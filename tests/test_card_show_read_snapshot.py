"""Opening a card reads on a snapshot: it does not queue behind a board write, and it still expires what is due."""

import threading

from commonplace.boards.service import BoardService
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests._split_helpers import world


def test_card_read_does_not_wait_for_a_board_write(hub):
    w = world(hub, runner=True)
    number = w.card(title="Read while a write is open")
    service = BoardService(w.ctx(w.owner))
    writing, read_done, shown = threading.Event(), threading.Event(), {}

    def read():
        shown["card"] = service.task_show(number)
        read_done.set()

    def write():
        with w.boards.store.transaction():
            writing.set()
            read_done.wait(10)  # the write stays open until the read has answered

    writer = threading.Thread(target=write)
    writer.start()
    assert writing.wait(10)
    reader = threading.Thread(target=read)
    reader.start()
    answered = read_done.wait(3)
    read_done.set()  # never leave the write open if the read is still waiting
    writer.join(10)
    reader.join(10)
    assert answered, "the card read waited for the board write"
    assert shown["card"]["number"] == number
    assert shown["card"]["events"][0]["kind"] == "add"


def test_card_read_still_expires_a_lapsed_claim(hub):
    w = world(hub, runner=True)
    number = w.card(title="Lapsed claim")
    with w.boards.store.transaction() as conn:
        conn.execute(
            "UPDATE tasks SET status='doing',assignee=?,claim_until='2000-01-01T00:00:00Z' WHERE id=?",
            (w.a.actor, number),
        )
    shown = BoardService(w.ctx(w.owner)).task_show(number)
    assert "expire" in [event["kind"] for event in shown["events"]]
    with w.boards.store.connection() as conn:
        stamp = conn.execute("SELECT claim_expired_at FROM tasks WHERE id=?", (number,)).fetchone()
    assert stamp[0] is not None
