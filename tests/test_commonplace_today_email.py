"""Opt-in email about new Today items: opt-in, privacy, one mail per item, rate cap."""

import os
import threading
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from commonplace import mailer, urls
from commonplace.boards import service as board_service
from commonplace.control_db import ControlDB
from commonplace.extensions import RetryableToolError
from commonplace.today_email import schema
from commonplace.today_email.notifier import TodayEmail
from tests.phone_push_harness import (
    agent,
    api,
    cli,
    fresh as fresh,
    hub as hub,
    receiver as receiver,
    remote as remote,
    signup as signup,
)

BODY = "SECRET-CARD-BODY the full text of the card"
NOTE = "SECRET-HANDOFF-NOTE which approach should we use"
CONTEXT = "SECRET-HANDOFF-CONTEXT both approaches are ready"


class FakeMail:
    configured = True

    def __init__(self):
        self.sent = []
        self.fail = False

    def send(self, to, subject, text, html=None):
        if self.fail:
            raise mailer.MailError("mail not sent: simulated")
        self.sent.append((to, subject, text))


@pytest.fixture
def notifier(hub):
    """The ordinary install, with the mail transport replaced by a recorder."""
    result = hub.gateway.today_email
    result.stop()  # The tests drive passes by hand; the sign-up mailbox stays out of it.
    result.mailer = FakeMail()
    result.now = 1_000_000.0
    result.clock = lambda: result.now
    return result


def ask(hub, owner, worker, title="A decision to make"):
    code, card = api(
        hub,
        owner,
        "POST",
        "/api/boards/commonplace/cards",
        {"title": title, "body": BODY, "assignee": worker.id},
    )
    assert code == 201, card
    code, body = api(
        hub,
        None,
        "POST",
        f"/api/cards/{card['number']}/handoff",
        {"to": "owner", "note": NOTE, "context": CONTEXT, "options": ["Continue", "Revise"]},
        token=worker.token,
    )
    assert code == 200, body
    return card["number"]


def setting(hub, person, enabled=None):
    if enabled is None:
        return api(hub, person, "GET", "/api/today-email")
    return api(hub, person, "POST", "/api/today-email", {"enabled": enabled})


def join(hub, person, workspace, by, role):
    data = os.path.dirname(hub.db.path)
    cli("--data", data, "member", "add", workspace, person, "--role", role, "--by", by)
    link = cli("--data", data, "login-link", person, "--workspace", workspace)
    parsed = urlsplit(link)
    status, headers, body = hub.request(parsed.path + "?" + parsed.query)
    assert status == 302, (status, body)
    return headers["Set-Cookie"].split(";", 1)[0]


def test_without_a_mail_server_it_says_so_and_saves_nothing(hub, signup):
    _, _, register = signup
    owner, _ = register()
    hub.gateway.today_email.stop()
    hub.gateway.today_email.mailer = mailer.DisabledMailer()
    code, body = setting(hub, owner)
    assert code == 200
    assert body["available"] is False and body["enabled"] is False
    code, body = setting(hub, owner, True)
    assert code == 409
    assert body == {"error": "no: email is not set up on this server"}
    assert setting(hub, owner)[1]["enabled"] is False


def test_opt_in_is_off_by_default_and_reads_back(hub, signup, notifier):
    _, _, register = signup
    owner, _ = register()
    code, body = setting(hub, owner)
    assert code == 200 and body["available"] is True and body["enabled"] is False
    assert body["address"] == hub.me(owner)["email"]
    assert setting(hub, owner, True)[1]["enabled"] is True
    assert setting(hub, owner)[1]["enabled"] is True
    assert setting(hub, owner, False)[1]["enabled"] is False
    assert setting(hub, owner)[1]["enabled"] is False
    assert api(hub, owner, "POST", "/api/today-email", {"enabled": "yes"})[0] == 400
    assert api(hub, owner, "POST", "/api/today-email", {"enabled": True, "x": 1})[0] == 400
    assert api(hub, None, "GET", "/api/today-email")[0] == 401


def test_a_new_today_item_sends_exactly_one_mail_without_card_text(hub, signup, notifier):
    _, _, register = signup
    owner, _ = register()
    worker = agent(hub, owner, "worker", unrestricted=True)
    setting(hub, owner, True)
    number = ask(hub, owner, worker, "Pick the database")
    assert notifier.reconcile() == 1
    assert notifier.reconcile() == 0
    ((to, subject, text),) = notifier.mailer.sent
    assert to == setting(hub, owner)[1]["address"]
    assert "Pick the database" in subject
    assert f"#{number} Pick the database" in text
    links = urls.share_links(
        hub.gateway.db,
        hub.me(owner)["workspace"],
        [f"/app/#/card/{number}", "/app/#/", "/app/#/settings"],
    )
    for link in links.values():
        assert link.startswith("https://cp.example/") and link in text
    for secret in (BODY, NOTE, CONTEXT):
        assert secret not in subject + text


def test_opted_out_people_and_members_who_cannot_read_get_nothing(hub, signup, notifier):
    _, _, register = signup
    owner, _ = register()
    register(email="member@example.test")
    me = hub.me(owner)
    member = join(hub, "member@example.test", me["workspace"], me["user"], "member")
    worker = agent(hub, owner, "worker", unrestricted=True)
    # The member opts in but cannot read the owner's Today; the owner never opted in.
    assert setting(hub, member, True)[1]["enabled"] is True
    ask(hub, owner, worker)
    assert notifier.reconcile() == 0
    assert notifier.mailer.sent == []
    setting(hub, owner, True)
    ask(hub, owner, worker)
    assert notifier.reconcile() == 1
    assert [to for to, _, _ in notifier.mailer.sent] == [me["email"]]


def test_items_already_in_today_when_turning_on_send_nothing(hub, signup, notifier):
    _, _, register = signup
    owner, _ = register()
    worker = agent(hub, owner, "worker", unrestricted=True)
    ask(hub, owner, worker)
    setting(hub, owner, True)
    assert notifier.reconcile() == 0
    ask(hub, owner, worker)
    assert notifier.reconcile() == 1
    assert len(notifier.mailer.sent) == 1


def test_a_burst_is_one_mail_and_the_hourly_cap_holds_the_rest(hub, signup, notifier, monkeypatch):
    from commonplace.today_email import notifier as module

    # Small limits keep the test short; the rules are the same at the shipped values.
    monkeypatch.setattr(module, "MAX_LISTED", 3)
    monkeypatch.setattr(module, "MAX_PER_HOUR", 2)
    _, _, register = signup
    owner, _ = register()
    worker = agent(hub, owner, "worker", unrestricted=True)
    setting(hub, owner, True)
    for i in range(5):
        ask(hub, owner, worker, f"Item {i}")
    assert notifier.reconcile() == 1
    ((_, subject, text),) = notifier.mailer.sent
    assert subject == "5 new items in Today"
    assert text.count("/s/") == 3 + 2 and "and 2 more" in text
    assert notifier.reconcile() == 0  # All five are noted: no second mail for the same items.
    ask(hub, owner, worker, "Second arrival")
    assert notifier.reconcile() == 1
    held = ask(hub, owner, worker, "Held by the cap")
    assert notifier.reconcile() == 0 and len(notifier.mailer.sent) == 2
    notifier.now += module.WINDOW_SECONDS + 1
    assert notifier.reconcile() == 1
    assert f"#{held} Held by the cap" in notifier.mailer.sent[-1][2]


def test_a_failed_send_is_retried_later_and_counts_against_the_cap(hub, signup, notifier):
    _, _, register = signup
    owner, _ = register()
    worker = agent(hub, owner, "worker", unrestricted=True)
    setting(hub, owner, True)
    ask(hub, owner, worker)
    notifier.mailer.fail = True
    assert notifier.reconcile() == 0
    notifier.mailer.fail = False
    notifier.now += 3601
    assert notifier.reconcile() == 1
    assert len(notifier.mailer.sent) == 1


def test_an_item_that_leaves_today_is_forgotten(hub, signup, notifier):
    _, _, register = signup
    owner, _ = register()
    worker = agent(hub, owner, "worker", unrestricted=True)
    setting(hub, owner, True)
    number = ask(hub, owner, worker)
    assert notifier.reconcile() == 1
    code, card = api(hub, owner, "GET", f"/api/cards/{number}")
    assert code == 200, card
    code, body = api(
        hub,
        owner,
        "POST",
        f"/api/cards/{number}/decide",
        {"choice": "Continue", "then": "return", "version": card["version"]},
    )
    assert code == 200, body
    assert notifier.reconcile() == 0
    with hub.gateway.db._lock:
        left = hub.gateway.db._conn.execute("SELECT COUNT(*) FROM today_email_items").fetchone()[0]
    assert left == 0


def test_a_busy_memory_is_not_an_empty_today(hub, signup, notifier, monkeypatch):
    _, _, register = signup
    owner, _ = register()
    worker = agent(hub, owner, "worker", unrestricted=True)
    ask(hub, owner, worker)
    original = board_service.BoardService.needs_me

    def busy(self, *args, **kwargs):
        raise RetryableToolError("busy", 1, "busy")

    # Turning it on while memory is busy saves nothing instead of seeding an empty Today.
    monkeypatch.setattr(board_service.BoardService, "needs_me", busy)
    assert setting(hub, owner, True)[0] == 503
    monkeypatch.setattr(board_service.BoardService, "needs_me", original)
    assert setting(hub, owner)[1]["enabled"] is False
    assert setting(hub, owner, True)[1]["enabled"] is True
    assert notifier.reconcile() == 0  # What was already waiting is not mailed.
    number = ask(hub, owner, worker)
    assert notifier.reconcile() == 1
    # A busy pass forgets nothing, so the recovered pass does not mail the same item again.
    monkeypatch.setattr(board_service.BoardService, "needs_me", busy)
    assert notifier.reconcile() == 0
    monkeypatch.setattr(board_service.BoardService, "needs_me", original)
    assert notifier.reconcile() == 0
    assert len(notifier.mailer.sent) == 1 and f"#{number}" in notifier.mailer.sent[0][2]


class ClosedConnection:
    """Records any statement run once the database is meant to be closed."""

    def __init__(self, conn):
        self.conn, self.late, self.closed = conn, [], False

    def execute(self, sql, *args):
        if self.closed:
            self.late.append(sql)
        return self.conn.execute(sql, *args)

    def close(self):
        self.conn.close()
        self.closed = True

    def __getattr__(self, name):
        return getattr(self.conn, name)


class SettlingEvent(threading.Event):
    """Says when the notifier thread is waiting out its settle period."""

    def __init__(self, settle):
        super().__init__()
        self.settle, self.settling = settle, threading.Event()

    def wait(self, timeout=None):
        if timeout == self.settle:
            self.settling.set()
        return super().wait(timeout)


def running_notifier(tmp_path):
    db = ControlDB(str(tmp_path / "control.sqlite3"))
    schema.ensure(db)
    boundary = ClosedConnection(db._conn)
    db._conn = boundary
    notifier = TodayEmail(SimpleNamespace(db=db), FakeMail(), settle_seconds=60)
    notifier._stop = SettlingEvent(60)
    db.add_pre_close_callback(notifier.stop)
    notifier.start()
    return db, boundary, notifier


def test_closing_the_database_while_the_notifier_settles_runs_no_late_sql(tmp_path):
    db, boundary, notifier = running_notifier(tmp_path)
    assert notifier._stop.settling.wait(3)
    boundary.closed = True  # The server has closed: no statement is allowed from here on.
    db.close()
    assert not notifier.thread.is_alive()
    assert boundary.late == []


def test_a_stopped_pass_issues_no_statements(tmp_path):
    db = ControlDB(str(tmp_path / "control.sqlite3"))
    schema.ensure(db)
    boundary = ClosedConnection(db._conn)
    db._conn = boundary
    notifier = TodayEmail(SimpleNamespace(db=db), FakeMail())
    boundary.closed = True  # From here on, any statement counts as late.
    halt = threading.Event()
    halt.set()
    assert notifier.reconcile(halt=halt) == 0
    assert boundary.late == []
    boundary.closed = False
    db.close()
