"""Handoff fact indexing through the real gateway policy and worker."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from commonplace.handoff.models import HandoffRequest, SettingRow, ThreadMeta
from commonplace.handoff.recall import ThreadNotFound
from tests._commonplace_helpers import hub_fixture  # noqa: F401

SCOPES = [
    "memory:read",
    "memory:write",
    "kg:read",
    "kg:write",
    "private:read",
    "private:write",
    "threads:read",
    "handoff:write",
]


class _TestToken(str):
    def __repr__(self):
        return "<test credential>"


@pytest.fixture
def enabled(hub):
    token = hub.token(
        "handoff-facts",
        profile="full",
        scopes=SCOPES,
        read_wings=["*"],
        write_wings=["*"],
        trust="trusted",
    )
    principal = hub.db.authenticate_token(token)
    context = hub.gateway.handoff
    context.settings.set(
        SettingRow(workspace_id=hub.workspace, scope="workspace", ref="", enabled=True),
        actor=principal.actor,
        is_owner=True,
    )
    context.service.call_tool = None  # Most cases isolate facts from embedding/mirroring.
    try:
        yield context, principal, _TestToken(token)
    finally:
        context.close()


def request(thread="chat", lines=None):
    lines = lines or [{"role": "user", "content": "For Kestrel the deploy port is 8443."}]
    return HandoffRequest(ThreadMeta(thread), json.dumps(lines), "messages")


def query(hub, token, entity="Kestrel"):
    result, text = hub.call(token, "mempalace_kg_query", entity=entity)
    assert result is not None, text
    return result["facts"]


def test_handoff_facts_are_private_source_exact_and_idempotent(hub, enabled):
    context, principal, token = enabled
    data = request(
        lines=[
            {"role": "user", "content": "Setting up Kestrel."},
            {"role": "assistant", "content": "Kestrel deploys on port 9999."},
            {"role": "user", "content": "For Kestrel the deploy port is 8443."},
        ]
    )
    result = context.service.run(principal, data)
    assert result.action == "handoff" and result.launch
    context.service.wait_for_facts(timeout=10)
    (fact,) = query(hub, token)
    assert fact["object"] == "8443" and fact["wing"] == "private_threads"
    assert fact["sensitivity"] == "private"
    assert fact["sources"][0]["source_thread"] == result.handoff.stored_thread_id
    assert fact["sources"][0]["source_seq_start"] == fact["sources"][0]["source_seq_end"] == 2
    stored = context.store_for(hub.workspace)
    before = stored.messages(result.handoff.stored_thread_id)
    context.service.run(principal, data)
    context.service.wait_for_facts(timeout=10)
    (again,) = query(hub, token)
    assert again["id"] == fact["id"] and len(again["sources"]) == 1
    assert again["seen_count"] == 1
    assert stored.messages(result.handoff.stored_thread_id) == before
    shared = hub.token(
        "shared-reader",
        profile="full",
        scopes=["kg:read", "memory:read"],
        read_wings=["*"],
        trust="standard",
    )
    assert query(hub, shared) == []


@pytest.mark.parametrize("missing", ["kg:write", "threads:read", "private:write"])
def test_extraction_never_bypasses_a_missing_permission(hub, enabled, missing):
    context, owner, reader = enabled
    token = hub.token(
        "limited",
        profile="full",
        scopes=[s for s in SCOPES if s != missing],
        read_wings=["*"],
        write_wings=["*"],
        trust="trusted",
    )
    limited = hub.db.authenticate_token(token)
    data = request()
    result = context.service.run(limited, data)
    context.service.wait_for_facts(timeout=10)
    assert result.action == "handoff" and query(hub, reader) == []
    if missing == "threads:read":
        with pytest.raises(ThreadNotFound):
            context.recall.thread(limited, result.handoff.stored_thread_id)
    # A permitted retry resumes unfinished extraction, including after a denial.
    context.service.run(owner, data)
    context.service.wait_for_facts(timeout=10)
    assert [f["object"] for f in query(hub, reader)] == ["8443"]


def test_fact_failure_does_not_fail_handoff_and_a_retry_repairs_it(hub, enabled):
    context, principal, token = enabled
    original = context.service.save_fact
    calls = []

    def unavailable(_principal, args):
        calls.append(args)
        raise RuntimeError("do not leak an exception's chat content")

    context.service.save_fact = unavailable
    data = request()
    result = context.service.run(principal, data)
    context.service.wait_for_facts(timeout=10)
    assert result.action == "handoff" and result.launch
    assert calls and query(hub, token) == []
    context.service.save_fact = original
    context.service.run(principal, data)
    context.service.wait_for_facts(timeout=10)
    assert [f["object"] for f in query(hub, token)] == ["8443"]


def test_a_blocked_mirror_does_not_hold_up_fact_indexing(hub, enabled, monkeypatch):
    from commonplace.handoff import palace_mirror

    context, principal, token = enabled
    entered, release = threading.Event(), threading.Event()

    def blocked(*_args):
        entered.set()
        release.wait(timeout=15)
        return []

    context.service.call_tool = context.call_tool
    monkeypatch.setattr(palace_mirror, "mirror", blocked)
    try:
        result = context.service.run(principal, request())
        assert result.action == "handoff" and entered.wait(timeout=5)
        context.service.wait_for_facts(timeout=10)
        assert not release.is_set()
        assert [f["object"] for f in query(hub, token)] == ["8443"]
    finally:
        release.set()
        context.service.wait_for_mirrors(timeout=10)


def test_blocked_fact_indexing_does_not_hold_up_the_handoff(hub, enabled):
    context, principal, token = enabled
    entered, release = threading.Event(), threading.Event()
    original = context.service.save_fact

    def blocked(principal, args):
        entered.set()
        release.wait(timeout=15)
        return original(principal, args)

    context.service.save_fact = blocked
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(context.service.run, principal, request())
        try:
            result = future.result(timeout=5)
            assert result.action == "handoff" and entered.wait(timeout=5)
            assert not release.is_set()
        finally:
            release.set()
    context.service.wait_for_facts(timeout=10)
    assert [f["object"] for f in query(hub, token)] == ["8443"]


def test_off_mode_never_starts_extraction(hub, enabled):
    context, principal, _token = enabled
    context.settings.set(
        SettingRow(workspace_id=hub.workspace, scope="workspace", ref="", enabled=False),
        actor=principal.actor,
        is_owner=True,
    )
    assert context.service.run(principal, request()).action == "compact"
    assert context.service._fact_pool is None
