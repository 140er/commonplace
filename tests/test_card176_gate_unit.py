"""Thread access at the mirror response boundary (synthetic data only)."""

import sqlite3
import threading
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from commonplace.continuity_read import PrivateReader
from commonplace.handoff.mirror_access import gate
from commonplace.handoff.recall import Recall
from commonplace.rules import checks
from tests.test_handoff_recall import MemoryStore, reader, stored


@pytest.fixture
def world():
    principal = reader(scopes=("memory:read", "private:read", "threads:read"), wings=("*",))
    store = MemoryStore()
    thread = stored()
    store.threads[thread.id] = thread
    opener = Mock(return_value=store)
    private = Mock(readable=Mock(return_value=True))
    recall = Recall(opener, private=private)
    ctx = SimpleNamespace(recall=recall, _stores={principal.workspace_id: store})
    return SimpleNamespace(
        principal=principal,
        store=store,
        thread=thread,
        opener=opener,
        private=private,
        gateway=SimpleNamespace(handoff=ctx),
    )


def hit(w, **changes):
    return dict(
        wing="private_threads", room=w.thread.id, drawer_id="drawer_one", text="words", **changes
    )


def search(w, item=None):
    return gate(w.gateway, w.principal, "search", {"results": [hit(w) if item is None else item]})[
        "results"
    ]


def test_u5_a_other_workspace(world):
    w = world
    w.store.threads[w.thread.id] = replace(w.thread, workspace_id="other")
    assert search(w) == []


def test_u5_b_grant_filer(world):
    w = world
    w.store.threads[w.thread.id] = replace(w.thread, source_thread_id="import-private:other:source")
    assert search(w) == []
    w.store.threads[w.thread.id] = replace(
        w.thread, source_thread_id=f"import-private:{w.principal.user_id}:source"
    )
    assert search(w)
    w.private.view.assert_not_called()


def test_u5_c_scope_and_invalid_id(world):
    w = world
    w.principal = reader(scopes=("memory:read", "private:read"), wings=("*",))
    assert search(w) == []
    w.opener.assert_not_called()
    assert not w.gateway.handoff.recall.can_read(reader(), "invalid")
    w.opener.assert_not_called()


def test_u5_d_missing_store_and_exception(world):
    w = world
    w.opener.return_value = None
    assert search(w) == []
    w.opener.side_effect = RuntimeError("unavailable")
    assert search(w) == []
    w.gateway.handoff = None
    assert search(w) == []


def test_u5_e_startup_cold_and_warm(world):
    w = world
    w.gateway.handoff._stores.clear()
    with checks.deadline(60):
        assert search(w) == []
    w.opener.assert_not_called()
    w.gateway.handoff._stores[w.principal.workspace_id] = w.store
    with checks.deadline(60):
        assert search(w)
    w.opener.assert_not_called()
    w.private.view.assert_not_called()


def test_u5_f_revoked_capture_connection(world):
    w = world
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE connections (id TEXT, user_id TEXT, revoked INTEGER)")
    conn.execute("INSERT INTO connections VALUES (?, ?, 1)", ("conn_old", w.principal.user_id))
    try:
        private = PrivateReader(SimpleNamespace(_lock=threading.Lock(), _conn=conn), w.opener)
        w.gateway.handoff.recall = Recall(w.opener, private=private)
        w.store.threads[w.thread.id] = replace(
            w.thread, wing="private_chats", source_thread_id="capture:conn_old:source"
        )
        assert search(w)
        w.principal = replace(w.principal, user_id="other")
        assert search(w) == []
    finally:
        conn.close()


def test_u5_g_null_filer_owner_only(world):
    w = world
    private = PrivateReader(None, w.opener)
    w.gateway.handoff.recall = Recall(w.opener, private=private)
    w.store.threads[w.thread.id] = replace(
        w.thread, wing="private_chats", source_thread_id="unknown"
    )
    assert search(w) == []
    w.principal = replace(w.principal, role="owner")
    assert search(w)


def test_u5_h_shapes_and_cache(world):
    w = world
    result = {"results": [hit(w), hit(w)]}
    assert gate(w.gateway, w.principal, "search", result) == result
    assert w.store.calls == [("get", w.thread.id)]
    w.private.readable.return_value = False
    expected = {"drawer_id": "drawer_one", "error": "Drawer not found: drawer_one"}
    assert gate(w.gateway, w.principal, "drawer_read", result) == {
        "results": [expected, expected],
        "count": 2,
        "errors": 2,
    }
    assert gate(w.gateway, w.principal, "drawer_read", hit(w)) == {"error": expected["error"]}
    plain = {"wing": "shared", "text": "words"}
    assert search(w, plain) == [plain]
    assert gate(w.gateway, w.principal, "read_wing", {"nested": hit(w)}) == {
        "error": "not found",
        "denied": True,
    }


@pytest.mark.parametrize("field", ["text", "content", "document", "snippet"])
@pytest.mark.parametrize("room", [None, "", 123])
def test_u5_a1_roomless_words_fail_closed(world, field, room):
    item = {"wing": " WING-Private-Threads ", "room": room, field: "words"}
    assert search(world, item) == []


def test_u5_a1_roomless_taxonomy_kept(world):
    item = {"wing": "private_threads", "rooms": {"thr_ab": 2}, "text": ""}
    assert search(world, item) == [item]
