"""Disposable HTTP fixture with 30-second stand-ins for cold dependencies."""

import threading
import time
from types import SimpleNamespace

import pytest

from commonplace import plans, sessions
from commonplace.boards.service import BoardService
from commonplace.extensions import ToolContext
from tests._commonplace_helpers import Hub
from tests.test_commonplace_boards import api


@pytest.fixture
def cold_hub(tmp_path):
    hub = Hub(tmp_path, SimpleNamespace(start=lambda *_: pytest.fail("No worker on shell reads")))
    principal = sessions.person_principal(hub.db, hub.admin, hub.workspace)
    service = BoardService(ToolContext(principal, hub.gateway))
    service.board_add(slug="project", wing="shared")
    session = sessions.redeem_login_link(
        hub.db, sessions.create_login_link(hub.db, "owner@example.com", hub.workspace)
    )
    yield hub, {"Cookie": f"{sessions.COOKIE}={session}"}, service
    hub.close()


@pytest.mark.timing
@pytest.mark.parametrize(
    "dependency", ["board-projection", "bot-project-names", "wing-list", "storage-walk"]
)
def test_slow_dependency_does_not_hold_shell_data(cold_hub, monkeypatch, dependency):
    hub, cookie, _service = cold_hub
    release = threading.Event()
    entered = threading.Event()
    completed = threading.Event()

    def slow(value):
        entered.set()
        release.wait(30)
        completed.set()
        return value

    if dependency in ("board-projection", "bot-project-names"):
        monkeypatch.setattr(
            BoardService,
            "board_list",
            lambda *_a, **_kw: slow([{"slug": "project", "name": "Project"}]),
        )
        path = "/api/boards?view=shell" if dependency == "board-projection" else "/api/bots"
    elif dependency == "wing-list":
        from commonplace.web import agents

        monkeypatch.setattr(agents, "grantable_wings", lambda *_: slow(["shared"]))
        path = "/api/connections"
    else:
        monkeypatch.setenv("COMMONPLACE_PLANS", "on")
        plans.set_plan(hub.db, hub.admin, "free", actor=hub.admin)
        monkeypatch.setattr(hub.gateway.storage_meter, "used", lambda *_: slow(42))
        path = "/api/plan"
    started = time.monotonic()
    try:
        # The fixture helper has a 10 s transport cap; use a private longer cap
        # only to measure the pinned base's actual 30 s storage/projection wait.
        import http.client
        import json

        conn = http.client.HTTPConnection("127.0.0.1", hub.port, timeout=35)
        try:
            conn.request("GET", path, headers=cookie)
            response = conn.getresponse()
            payload = json.loads(response.read())
        finally:
            conn.close()
        elapsed = time.monotonic() - started
        print(f"TIMING {dependency} {path} {elapsed:.4f}s")
        assert response.status == 200, payload
        assert elapsed < 2, f"{path} waited {elapsed:.3f}s on {dependency}"
        if dependency == "wing-list":
            assert payload["wings"] is None
            assert payload["wings_state"] == "loading"
        if dependency == "storage-walk":
            assert payload["storage"]["used"] is None
            assert payload["storage"]["state"] == "loading"
        # Main card data must also remain available beside an in-flight scan.
        assert api(hub, cookie, "GET", "/api/boards/project/cards")[0] == 200
    finally:
        release.set()
        if entered.is_set():
            assert completed.wait(2)


def test_directory_never_reuses_authority(cold_hub):
    hub, _cookie, service = cold_hub
    service.board_add(slug="private", wing="private_hidden")
    first = service.board_directory()
    assert {b["slug"] for b in first} == {"project", "private"}
    from dataclasses import replace

    from commonplace.policy import Policy

    restricted = replace(
        service.principal, policy=Policy.build(read_wings=["shared"], scopes=["memory:read"])
    )
    other = BoardService(ToolContext(restricted, hub.gateway)).board_directory()
    assert [b["slug"] for b in other] == ["project"]
    with service.store.connection() as conn:
        conn.execute("UPDATE boards SET archived=1 WHERE slug='project'")
    assert "project" not in {b["slug"] for b in service.board_directory()}


def test_rail_waiting_for_roster_does_not_hold_identity_database(cold_hub):
    """Reproduce the roster -> control / rail control -> roster lock cycle."""
    from commonplace.bots.store import open_bots
    from commonplace.chat.coworkers.taxonomy import team_bots

    hub, _cookie, service = cold_hub
    store = open_bots(hub.gateway, hub.workspace)
    store.ensure_defaults(hub.admin, [("project", "Project")])
    original = store._lock
    attempted = threading.Event()
    errors = []

    class ObservedLock:
        def acquire(self, *args, **kwargs):
            if threading.current_thread().name == "cold-rail":
                attempted.set()
            return original.acquire(*args, **kwargs)

        def release(self):
            original.release()

        def __enter__(self):
            self.acquire()
            return self

        def __exit__(self, *_):
            self.release()

    store._lock = ObservedLock()

    def rail():
        try:
            team_bots(hub.gateway, service.principal)
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=rail, name="cold-rail")
    try:
        # A roster owns this lock while checking the sharing setting in the
        # control database. Admit the rail at exactly that point, without sleep.
        with store._tx(write=False):
            thread.start()
            assert attempted.wait(2)
            started = time.monotonic()
            available = hub.db._lock.acquire(timeout=0.5)
            if available:
                hub.db._lock.release()
            elapsed = time.monotonic() - started
        thread.join(timeout=2)
        print(f"TIMING rail-lock identity-control {elapsed:.4f}s available={available}")
        assert available, "Rail waiting for the bot store blocked all identity/settings reads"
        assert not thread.is_alive()
        assert errors == []
    finally:
        thread.join(timeout=6)
        store._lock = original
