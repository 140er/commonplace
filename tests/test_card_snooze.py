"""Snooze a card until a time or until another card is done, with a fake clock.

Hidden while snoozed (lists, take-next, starts, the idle nudge), a waiting reason, a wake that
happens once also after a restart, unsnooze by the owner or assignee, and every refusal."""

from datetime import datetime, timedelta, timezone

import pytest

from commonplace import schedule_time as times
from commonplace.boards import why
from commonplace.boards.service import BoardService
from commonplace.boards.store import BoardStore
from commonplace.orch import stall
from commonplace.snooze import store as snooze
from commonplace.snooze.worker import SnoozeWorker
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests._console_helpers import console_hub_fixture, service  # noqa: F401
from tests._phase_b_helpers import board, claim
from tests._split_helpers import blockers, invoke, world

START = datetime(2030, 1, 7, 8, 0, tzinfo=timezone.utc)
LATER = "2030-01-07T12:00:00Z"
NEXT_DAY = "2030-01-08T09:00:00Z"


class Clock:
    def __init__(self):
        self.t = START

    def __call__(self):
        return self.t

    def advance(self, **delta):
        self.t += timedelta(**delta)

    @property
    def epoch(self):
        return self.t.timestamp()


@pytest.fixture(autouse=True)
def fake_clock(monkeypatch):
    monkeypatch.setenv("COMMONPLACE_SNOOZE_WORKER", "off")
    clock = Clock()
    monkeypatch.setattr(snooze, "clock", clock)
    return clock


def snoozer(w, who=None):
    return BoardService(w.ctx(who or w.owner))


def snooze_card(w, card, who=None, **args):
    return snoozer(w, who).task_snooze(card, **args)


def listed(w, **kwargs):
    return [c["number"] for c in w.boards.task_list(board="acme", **kwargs)]


def events_of(w, name):
    with w.db._lock:
        rows = w.db._conn.execute(
            "SELECT subject FROM events WHERE kind=? ORDER BY seq", (name,)
        ).fetchall()
    return [r["subject"] for r in rows]


def card_log(w, card):
    with BoardStore(w.gw.workspace_dir(w.ws)).connection() as conn:
        return [
            r["text"]
            for r in conn.execute(
                "SELECT text FROM task_events WHERE task_id=? AND kind='comment' ORDER BY id",
                (card,),
            )
        ]


def snooze_rows(w):
    with w.boards.store.connection() as conn:
        return conn.execute("SELECT count(*) FROM card_snoozes").fetchone()[0]


def worker(w, clock):
    return SnoozeWorker(w.gw, start=False, now=lambda: clock.epoch)


def refused(call, *args, **kwargs):
    status, result = invoke(call, *args, **kwargs)
    assert status >= 400, result
    return result


def finish(w, card):
    """Done, as a person finishing the work would leave it (the move itself is not under test)."""
    with w.boards.store.transaction() as conn:
        conn.execute("UPDATE tasks SET status='done' WHERE id=?", (card,))


def reserve(w, card, actor):
    with w.boards.store.transaction() as conn:
        conn.execute("UPDATE tasks SET assignee=? WHERE id=?", (actor, card))


# --- hidden while snoozed, back at the time -------------------------------------------------


def test_snoozed_card_is_not_offered_and_returns_at_its_time(hub, fake_clock):
    w = world(hub)
    snoozed, other = w.card(title="Later"), w.card(title="Now")
    card = snooze_card(w, snoozed, until="2030-01-07T11:00", timezone="Europe/Amsterdam")
    assert card["snoozed"]["until"] == "2030-01-07T10:00:00Z"
    assert card["snoozed"]["text"] == "Snoozed until 2030-01-07 11:00 Europe/Amsterdam"
    # Todo lists leave it out unless asked; the board view still shows it, marked.
    assert listed(w) == [other] and listed(w, status="todo") == [other]
    assert set(listed(w, include_snoozed=True)) == {snoozed, other}
    shown = w.boards.task_list(board="acme", all_statuses=True)
    assert next(c for c in shown if c["number"] == snoozed)["snoozed"]
    # Take-next skips it and an explicit take says why, with its own code.
    assert BoardService(w.ctx(w.a)).task_take_next(board="acme")["number"] == other
    result = refused(BoardService(w.ctx(w.b)).task_take_next, board="acme", task=snoozed)
    assert result["code"] == "snoozed" and "Snoozed until 2030-01-07 11:00" in result["error"]
    assert BoardService(w.ctx(w.b)).task_take_next(board="acme") is None
    # Two hours later the card is back everywhere, with no worker running.
    fake_clock.advance(hours=2)
    assert snoozed in listed(w)
    assert "snoozed" not in w.boards.task_show(snoozed)
    assert BoardService(w.ctx(w.b)).task_take_next(board="acme")["number"] == snoozed


def test_one_second_before_the_time_it_is_still_snoozed(hub, fake_clock):
    w = world(hub)
    a = w.card()
    snooze_card(w, a, until="2030-01-07T09:00:00Z")
    fake_clock.advance(minutes=59, seconds=59)
    assert listed(w) == []
    fake_clock.advance(seconds=1)
    assert listed(w) == [a]


def test_the_idle_nudge_does_not_count_a_snoozed_card(hub):
    w = world(hub)
    a, b = w.card(title="Snoozed"), w.card(title="Open")
    row, directory = w.board_row(), w.gw.workspace_dir(w.ws)
    now = times.stamp(START)

    def assess(at=now):
        return stall.assess(directory, row, None, at, 1, None)

    assert {c["number"] for c in assess()["todo"]} == {a, b}
    snooze_card(w, a, until=LATER)
    assert [c["number"] for c in assess()["todo"]] == [b]
    # With only a snoozed card left there is no actionable work to nudge about.
    finish(w, b)
    assert assess()["stalled"] is False
    # After its time the card counts again.
    assert [c["number"] for c in assess(times.stamp(START + timedelta(hours=5)))["todo"]] == [a]


def test_a_snoozed_card_cannot_be_started_from_the_card(hub):
    w = world(hub)
    a = w.card()
    snooze_card(w, a, until=LATER)
    status, result = invoke(w.person_start, a)
    assert status >= 400 and "snoozed" in str(result)
    assert not w.jobs_for(a)


# --- the waiting reason -----------------------------------------------------------------------


def test_waiting_reason_says_snoozed_until_and_wins_over_blockers(hub, fake_clock):
    w = world(hub)
    dep, a = w.card(title="Dependency"), w.card(title="Snoozed")
    blockers(w, a, dep)
    assert w.boards.task_show(a)["waiting"]["code"] == "blocked_by"
    snooze_card(w, a, until="2030-01-07T10:00", timezone="Europe/Amsterdam")
    reason = w.boards.task_show(a)["waiting"]
    assert reason["code"] == "snoozed" and reason["text"] == why.text(reason)
    assert reason["text"] == "Snoozed until 2030-01-07 10:00 Europe/Amsterdam"
    assert board(w)["waiting"]["snoozed"] == 1
    assert [n["text"] for n in board(w)["waiting_notes"] if n["code"] == "snoozed"] == [
        "1 card snoozed: they come back by themselves."
    ]
    fake_clock.advance(hours=1)
    assert w.boards.task_show(a)["waiting"]["code"] == "blocked_by"


def test_waiting_reason_text_for_both_forms():
    base = {"status": "todo", "ask": None, "assignee": None, "ask_to": None}
    timed = {"until": "2030-01-07T10:00:00Z", "after_card": None, "text": "Snoozed until X"}
    cond = {"until": None, "after_card": 7, "text": "Snoozed until #7 is done"}
    for snoozed in (timed, cond):
        reason = why.waiting(base, snoozed=snoozed, blockers=[], released=True, usage=None)
        assert reason["code"] == "snoozed" and reason["text"] == snoozed["text"]
    assert "snoozed" in why.REFUSING and why.CODES[0] == "snoozed"
    # A card in doing or done is never described as snoozed.
    for status in ("doing", "done"):
        assert (
            why.waiting(
                {**base, "status": status},
                snoozed=timed,
                blockers=[],
                released=True,
                usage=None,
            )
            is None
        )


# --- waking: once, by itself, also after a restart --------------------------------------------


def test_the_worker_wakes_a_due_snooze_exactly_once(hub, fake_clock):
    w = world(hub)
    a = w.card()
    snooze_card(w, a, until="2030-01-07T09:00:00Z")
    first = worker(w, fake_clock)
    assert first.run_once() == 0  # not due yet
    fake_clock.advance(hours=1)
    assert first.run_once() == 1
    assert first.run_once() == 0
    assert events_of(w, "card.woke") == [f"#{a}"]
    assert card_log(w, a)[-1] == "Snooze ended: the wake-up time has come"
    assert sum(t.startswith("Snooze ended") for t in card_log(w, a)) == 1
    # A restart is a new worker over the same files: nothing is woken or announced twice.
    assert worker(w, fake_clock).run_once() == 0
    assert events_of(w, "card.woke") == [f"#{a}"] and snooze_rows(w) == 0


def test_a_snooze_that_came_due_while_the_hub_was_down_wakes_after_restart(hub, fake_clock):
    w = world(hub)
    a = w.card()
    snooze_card(w, a, until="2030-01-07T09:00:00Z")
    fake_clock.advance(days=3)  # nobody ran a worker meanwhile
    assert worker(w, fake_clock).run_once() == 1  # a fresh worker, as after a restart
    assert events_of(w, "card.woke") == [f"#{a}"] and listed(w) == [a]


def test_reading_the_board_after_the_time_wakes_it_and_the_worker_adds_nothing(hub, fake_clock):
    w = world(hub)
    a = w.card()
    snooze_card(w, a, until="2030-01-07T09:00:00Z")
    fake_clock.advance(hours=2)
    w.boards.task_show(a)  # the lazy path logs and announces it
    assert events_of(w, "card.woke") == [f"#{a}"]
    assert worker(w, fake_clock).run_once() == 0
    assert events_of(w, "card.woke") == [f"#{a}"] and len(card_log(w, a)) == 2


def test_two_workers_wake_once(hub, fake_clock):
    w = world(hub)
    a = w.card()
    snooze_card(w, a, until="2030-01-07T09:00:00Z")
    fake_clock.advance(hours=1)
    assert worker(w, fake_clock).run_once() + worker(w, fake_clock).run_once() == 1
    assert events_of(w, "card.woke") == [f"#{a}"]


def test_a_card_that_moved_on_while_snoozed_is_not_announced(hub, fake_clock):
    w = world(hub)
    a = w.card()
    snooze_card(w, a, until="2030-01-07T09:00:00Z")
    finish(w, a)
    assert "snoozed" not in w.boards.task_show(a)
    fake_clock.advance(hours=1)
    assert worker(w, fake_clock).run_once() == 0
    assert events_of(w, "card.woke") == [] and snooze_rows(w) == 0


# --- the condition form: when card #N is done -------------------------------------------------


def test_snooze_until_another_card_is_done(hub, fake_clock):
    w = world(hub)
    gate, a = w.card(title="Gate"), w.card(title="Waits")
    card = snooze_card(w, a, after_card=gate)
    assert card["snoozed"]["after_card"] == gate and card["snoozed"]["until"] is None
    assert card["snoozed"]["text"] == f"Snoozed until #{gate} is done"
    assert a not in listed(w)
    assert w.boards.task_show(a)["waiting"]["text"] == f"Snoozed until #{gate} is done"
    fake_clock.advance(days=30)  # time alone never wakes it
    assert a not in listed(w) and worker(w, fake_clock).run_once() == 0
    finish(w, gate)
    assert worker(w, fake_clock).run_once() == 1
    assert a in listed(w)  # back the moment the card is done
    assert card_log(w, a)[-1] == f"Snooze ended: #{gate} is done"
    assert worker(w, fake_clock).run_once() == 0
    assert events_of(w, "card.woke") == [f"#{a}"]


# --- unsnooze ---------------------------------------------------------------------------------


def test_owner_unsnoozes_and_the_card_is_offered_at_once(hub):
    w = world(hub)
    a = w.card()
    snooze_card(w, a, until=LATER)
    assert a not in listed(w)
    card = snoozer(w).task_unsnooze(a)
    assert "snoozed" not in card and a in listed(w)
    assert card_log(w, a)[-1] == f"Snooze ended by {w.owner.actor}"
    assert events_of(w, "card.woke") == [f"#{a}"]
    assert BoardService(w.ctx(w.a)).task_take_next(board="acme")["number"] == a


def test_assignee_can_snooze_and_unsnooze_its_reserved_card(hub):
    w = world(hub)
    a = w.card()
    reserve(w, a, w.a.actor)
    assert snooze_card(w, a, w.a, until=LATER)["snoozed"]
    taken = refused(BoardService(w.ctx(w.a)).task_take_next, board="acme", task=a)
    assert taken["code"] == "snoozed"
    assert "snoozed" not in snoozer(w, w.a).task_unsnooze(a)


def test_someone_else_cannot_snooze_or_unsnooze(hub):
    w = world(hub)
    a = w.card()
    reserve(w, a, w.a.actor)
    result = refused(snoozer(w, w.b).task_snooze, a, until=LATER)
    assert "only a workspace owner or the card's assignee" in result["error"]
    snooze_card(w, a, until=LATER)
    assert "assignee" in refused(snoozer(w, w.b).task_unsnooze, a)["error"]
    assert w.boards.task_show(a)["snoozed"]  # still snoozed
    # An unreserved card has no assignee: only the owner may snooze it.
    assert refused(snoozer(w, w.a).task_snooze, w.card(), until=LATER)


def test_unsnoozing_a_card_that_is_not_snoozed_is_refused(hub, fake_clock):
    w = world(hub)
    a = w.card()
    assert refused(snoozer(w).task_unsnooze, a)["code"] == "not_snoozed"
    snooze_card(w, a, until="2030-01-07T09:00:00Z")
    fake_clock.advance(hours=1)
    assert refused(snoozer(w).task_unsnooze, a)["code"] == "not_snoozed"


def test_snoozing_again_replaces_the_earlier_snooze(hub):
    w = world(hub)
    a = w.card()
    snooze_card(w, a, until="2030-01-07T09:00:00Z")
    card = snooze_card(w, a, until=NEXT_DAY)
    assert card["snoozed"]["until"] == NEXT_DAY and snooze_rows(w) == 1


# --- refusals ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("args", "code"),
    [
        ({}, "invalid_snooze"),
        ({"until": NEXT_DAY, "after_card": 5}, "invalid_snooze"),
        ({"after_card": 5, "timezone": "UTC"}, "invalid_snooze"),
        ({"until": "2030-01-08T09:00"}, "timezone_required"),
        ({"until": "2030-01-08T09:00", "timezone": "Mars/Base"}, "invalid_timezone"),
        ({"until": "tomorrow morning", "timezone": "UTC"}, "invalid_time"),
        ({"until": "2030-01-07T08:00:00Z"}, "in_past"),
        ({"until": "2029-12-31T09:00", "timezone": "UTC"}, "in_past"),
        ({"until": "2032-01-01T09:00:00Z"}, "too_far"),
        ({"until": "2030-03-31T02:30", "timezone": "Europe/Amsterdam"}, "invalid_time"),
        ({"after_card": "7"}, "invalid_condition"),
        ({"after_card": True}, "invalid_condition"),
        ({"after_card": 0}, "invalid_condition"),
        ({"after_card": 9999}, "other_board"),
    ],
)
def test_invalid_requests_are_refused_and_store_nothing(hub, args, code):
    w = world(hub)
    a = w.card()
    assert refused(snoozer(w).task_snooze, a, **args)["code"] == code
    assert snooze_rows(w) == 0 and "snoozed" not in w.boards.task_show(a)


def test_condition_refusals_name_the_reason(hub):
    w = world(hub)
    a, done, elsewhere = w.card(), w.card(), w.card(board="plain")
    finish(w, done)
    assert refused(snoozer(w).task_snooze, a, after_card=a)["code"] == "self"
    assert refused(snoozer(w).task_snooze, a, after_card=elsewhere)["code"] == "other_board"
    result = refused(snoozer(w).task_snooze, a, after_card=done)
    assert result["code"] == "condition_met" and f"#{done}" in result["error"]


@pytest.mark.parametrize("status", ["doing", "review", "done"])
def test_only_backlog_and_todo_cards_can_be_snoozed(hub, status):
    w = world(hub)
    a = w.card()
    with w.boards.store.transaction() as conn:
        if status == "doing":
            conn.execute("UPDATE tasks SET status='doing',assignee=? WHERE id=?", (w.a.actor, a))
        elif status == "review":
            conn.execute(
                "UPDATE tasks SET status='review',ask='Which?',ask_to=? WHERE id=?",
                (w.owner.actor, a),
            )
        else:
            conn.execute("UPDATE tasks SET status='done' WHERE id=?", (a,))
    assert refused(snoozer(w).task_snooze, a, until=NEXT_DAY)["code"] == "not_snoozable"


def test_unknown_card_is_refused(hub):
    w = world(hub)
    assert "card not found" in refused(snoozer(w).task_snooze, 4242, until=NEXT_DAY)["error"]
    assert "card not found" in refused(snoozer(w).task_unsnooze, 4242)["error"]


def test_a_backlog_card_can_be_snoozed_and_wakes_to_backlog(hub, fake_clock):
    w = world(hub)
    a = w.card(status="backlog")
    snooze_card(w, a, until="2030-01-07T09:00:00Z")
    assert w.boards.task_show(a)["waiting"]["code"] == "snoozed"
    fake_clock.advance(hours=1)
    assert worker(w, fake_clock).run_once() == 1
    assert w.boards.task_show(a)["status"] == "backlog"


def test_moving_a_snoozed_card_to_doing_is_refused(hub):
    w = world(hub)
    a = w.card()
    snooze_card(w, a, until=LATER)
    result = refused(w.boards.task_update, a, status="doing")
    assert result["code"] == "snoozed"


def test_orchestration_off_still_hides_the_card(hub):
    w = world(hub, on=False)
    a, b = w.card(), w.card()
    snooze_card(w, a, until=LATER)
    assert listed(w) == [b]
    assert "waiting" not in w.boards.task_show(a)  # that block is the orchestration feature's
    assert w.boards.task_show(a)["snoozed"]["text"].startswith("Snoozed until ")
    assert claim(w, b)[0] == 200


# --- the REST routes --------------------------------------------------------------------------


def test_rest_routes_snooze_show_and_unsnooze(console_hub, fake_clock):
    h = console_hub
    n = service(h.hub, h.person(h.hub.admin)).task_add("Later", board="work")["number"]
    path = f"/api/card-snooze/{n}"
    assert h.get(h.owner, path)[1] == {"snoozed": None}
    status, body, _ = h.request(h.owner, "POST", path, {"until": "2099-01-01T09:00:00Z"})
    assert status == 400 and body["code"] == "too_far"
    until = times.stamp(START + timedelta(days=2))
    status, body, _ = h.request(h.owner, "POST", path, {"until": until})
    assert status == 200 and body["snoozed"]["until"] == until
    assert h.get(h.owner, path)[1]["snoozed"]["until"] == until
    assert h.request(h.owner, "POST", path, {"until": until, "extra": 1})[0] == 400
    # Agents and members are refused (agents use the tools); another workspace's owner finds no card.
    assert h.request(h.t2, "POST", path + "/cancel", {})[0] == 403
    assert h.request(h.member, "POST", path, {"until": until})[0] == 403
    assert h.request(h.other, "POST", path + "/cancel", {})[0] in (403, 404)
    status, body, _ = h.request(h.owner, "POST", path + "/cancel", {})
    assert status == 200 and "snoozed" not in body
    status, body, _ = h.request(h.owner, "POST", path + "/cancel", {})
    assert status == 409 and body["code"] == "not_snoozed"
    assert h.get(h.owner, "/api/card-snooze/999")[0] == 404


# --- the shared time helper -------------------------------------------------------------------


def utc(*parts):
    return datetime(*parts, tzinfo=timezone.utc)


def test_schedule_time_at_resolves_wall_times_and_offsets():
    ams = "Europe/Amsterdam"
    assert times.at("2030-07-01T09:00", ams) == utc(2030, 7, 1, 7, 0)
    assert times.at("2030-01-01T09:00:30", ams) == utc(2030, 1, 1, 8, 0, 30)
    assert times.at("2030-01-01T09:00+05:00", ams) == utc(2030, 1, 1, 4, 0)
    assert times.at("2030-01-01T09:00Z") == utc(2030, 1, 1, 9, 0)
    assert times.at("2030-01-01", "UTC") == utc(2030, 1, 1)
    # A repeated wall time (fall-back) is its first occurrence.
    assert times.at("2030-10-27T02:30", ams) == utc(2030, 10, 27, 0, 30)
    for bad in ("2030-03-31T02:30", "soon", "", None, 5, "x" * 50):
        with pytest.raises(ValueError):
            times.at(bad, ams)
    with pytest.raises(ValueError):
        times.at("2030-01-01T09:00")
