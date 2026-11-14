"""Live-read admission is shared by detail, tail and stream; base data stays readable."""

import pytest

from commonplace import ratelimit, sessions
from commonplace.chat.coworkers import livelog, routes
from tests.test_coworker_api import api as api
from tests.test_coworker_dispatch import dispatch as dispatch
from tests.test_coworker_livelog import live as live
from tests.test_coworker_progress import progress as progress
from tests.test_coworker_runtime import env as env


@pytest.mark.parametrize("prefix", ["", "/conversations"])
def test_detail_and_log_share_rate_budget_without_losing_base_detail(
    env, progress, live, monkeypatch, prefix
):
    item = progress.start()
    reads = livelog._reads(env.gateway)
    reads.limiter = ratelimit.TokenBuckets(clock=lambda: 0)
    monkeypatch.setattr(livelog, "READ_LIMIT", ratelimit.Limit("coworker-live", 2, 60))
    original = livelog.snapshot
    calls = []

    def snapshot(*args):
        calls.append(True)
        return original(*args)

    monkeypatch.setattr(livelog, "snapshot", snapshot)
    path = f"{prefix}/{item.cid}"
    status, healthy = live.api.request("GET", path)
    assert status == 200 and healthy["working_on"]
    assert not healthy.get("limited")
    assert live.get(item)[0] == 200
    status, limited = live.api.request("GET", path)
    assert status == 200 and limited.get("limited") is True
    assert not {"working_on", "log", "lines", "cursor"} & limited.keys()
    for field in ("conversation", "messages", "jobs", "memories"):
        assert limited[field] == healthy[field]
    assert len(calls) == 2  # The denied projection never opens its live sources.
    assert live.get(item, "log/stream")[0] == 429


@pytest.mark.parametrize("prefix", ["", "/conversations"])
def test_detail_respects_reader_cap_and_recovers_without_losing_base_detail(
    env, progress, live, monkeypatch, prefix
):
    item = progress.start()
    reads = livelog._reads(env.gateway)
    original = livelog.snapshot
    calls = []

    def snapshot(*args):
        calls.append(True)
        return original(*args)

    monkeypatch.setattr(livelog, "snapshot", snapshot)
    for _ in range(livelog.MAX_READERS):
        assert reads.slots.acquire(blocking=False)
    path = f"{prefix}/{item.cid}"
    try:
        status, body = live.api.request("GET", path)
        assert status == 200 and body.get("limited") is True
        assert not {"working_on", "log", "lines", "cursor"} & body.keys()
        assert {"conversation", "messages", "jobs", "memories"} <= body.keys()
        assert calls == []
        assert live.get(item)[0] == 429
    finally:
        for _ in range(livelog.MAX_READERS):
            reads.slots.release()
    status, body = live.api.request("GET", path)
    assert status == 200 and body["working_on"] and not body.get("limited")
    assert calls == [True]


@pytest.mark.parametrize("prefix", ["", "/conversations"])
def test_detail_holds_the_shared_reader_slot_during_source_reads(
    env, progress, live, monkeypatch, prefix
):
    item = progress.start()
    reads = livelog._reads(env.gateway)
    original = livelog.snapshot
    probing = False
    nested = []

    def snapshot(*args):
        nonlocal probing
        if not probing:
            probing = True
            nested.append(live.get(item, "log/stream")[0])
        return original(*args)

    monkeypatch.setattr(livelog, "snapshot", snapshot)
    for _ in range(livelog.MAX_READERS - 1):
        assert reads.slots.acquire(blocking=False)
    try:
        status, body = live.api.request("GET", f"{prefix}/{item.cid}")
        assert status == 200 and body["working_on"]
        assert nested == [429]  # Detail occupies the eighth slot until its read finishes.
    finally:
        for _ in range(livelog.MAX_READERS - 1):
            reads.slots.release()
    for _ in range(livelog.MAX_READERS):
        assert reads.slots.acquire(blocking=False)
    for _ in range(livelog.MAX_READERS):
        reads.slots.release()


@pytest.mark.parametrize("change", ["session", "project"])
def test_limited_detail_rechecks_current_authority(env, dispatch, progress, live, change):
    item = progress.start()

    def refuse(_policies):
        if change == "session":
            sessions.end_session(env.db, env.session)
        else:
            with dispatch.boards.store.transaction() as conn:
                conn.execute("UPDATE boards SET archived=1 WHERE slug='work'")
        return 1

    livelog._reads(env.gateway).limiter.check = refuse
    assert live.api.request("GET", f"/conversations/{item.cid}") == (
        404,
        {"error": "not_found"},
    )


def test_existing_single_segment_get_routes_are_not_conversation_aliases(api, monkeypatch):
    # Complete existing GET collection inventory beneath /api/coworkers.
    collections = {
        "": {"bots", "conversations", "user_id", "can_manage_bots"},
        "/bots": {"bots"},
        "/conversations": {"conversations"},
    }

    def unexpected(*_args):
        pytest.fail("A collection or reserved name was dispatched as a conversation")

    monkeypatch.setattr(routes, "_conversation_route", unexpected)
    for path, fields in collections.items():
        status, body = api.request("GET", path)
        assert status == 200 and set(body) == fields
    for path in ("/jobs", "/memory", "/metrics", "/conv_invalid"):
        assert api.request("GET", path) == (404, {"error": "not_found"})
