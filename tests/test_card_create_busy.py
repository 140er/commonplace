"""#550: a card create or board list refused by a busy workspace says why, in plain words.

Usability test, 14 Nov 2026: Add card on a phone failed once with HTTP 409 and no message, and a second
tester saw a 409 on the board list. Both were the lock-timeout refusal: it answered 409 with the bare
word "busy". Nothing is written when it happens, so the reply now says so, tells the client when to try
again, and a retry creates exactly one card.
"""

import http.client
import json
import sqlite3
import threading
from pathlib import Path

import pytest

from commonplace import sessions
from commonplace.boards import store as board_store
from commonplace.boards.store import BoardStore
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_commonplace_boards import service


def request(hub, cookie, method, path, body=None):
    connection = http.client.HTTPConnection("127.0.0.1", hub.port, timeout=15)
    try:
        connection.request(
            method,
            path,
            json.dumps(body).encode() if body is not None else None,
            {"Content-Type": "application/json", **cookie},
        )
        response = connection.getresponse()
        return response.status, json.loads(response.read()), response
    finally:
        connection.close()


@pytest.fixture
def busy(hub, monkeypatch):
    person = sessions.person_principal(hub.db, hub.admin, hub.workspace)
    service(hub, person).board_add(slug="work", wing="shared")
    session = sessions.redeem_login_link(
        hub.db, sessions.create_login_link(hub.db, "owner@example.com")
    )
    cookie = {"Cookie": f"{sessions.COOKIE}={session}"}
    path = str(Path(hub.gateway.workspace_dir(hub.workspace)) / "boards.sqlite3")
    BoardStore(Path(path).parent)
    real = board_store.connect
    monkeypatch.setattr(
        board_store, "connect", lambda target, **kw: real(target, **{**kw, "timeout": 0.2})
    )
    holder = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    holder.execute("BEGIN IMMEDIATE")
    release = threading.Event()

    def let_go():
        if not release.is_set():
            release.set()
            holder.rollback()
            holder.close()

    yield cookie, let_go
    let_go()


def test_card_create_refused_by_a_busy_workspace_explains_and_a_retry_adds_one_card(hub, busy):
    cookie, let_go = busy
    status, body, response = request(
        hub, cookie, "POST", "/api/boards/work/cards", {"title": "Pay rent"}
    )
    assert status == 409
    assert body["code"] == "busy"
    assert "did not go through" in body["error"] and "try again" in body["error"].lower()
    assert response.getheader("Retry-After") == "2"
    let_go()
    status, card, _ = request(hub, cookie, "POST", "/api/boards/work/cards", {"title": "Pay rent"})
    assert status == 201
    _, listed, _ = request(hub, cookie, "GET", "/api/boards/work/cards")
    assert [c["title"] for c in listed["cards"]] == ["Pay rent"] and card["number"] == 1


def test_board_list_refused_by_a_busy_workspace_explains(hub, busy):
    cookie, _ = busy
    status, body, response = request(hub, cookie, "GET", "/api/boards")
    assert status == 409 and body["code"] == "busy"
    assert "did not go through" in body["error"]
    assert response.getheader("Retry-After") == "2"
