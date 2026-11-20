"""A personal chat refusal stays private, actionable and on its original message."""

import json
import time
from types import SimpleNamespace

import pytest

from commonplace import events
from commonplace.chat import front
from tests._relay_helpers import relay_hub_fixture  # noqa: F401


def _personal_chat(h, monkeypatch, mode="personal"):
    now = time.time()
    monkeypatch.setattr(front, "time", SimpleNamespace(time=lambda: now))
    machine = h.machine(label="own-account")
    assert h.set_main_model("claude", "claude-haiku-5-5")[0] == 200
    assert h.configure(enabled=True)[0] == 200
    status, connection, _ = h.request(
        h.owner,
        "POST",
        "/api/me/model-connections",
        {"provider": "claude", "label": "Personal account", "machine": machine["machine_id"]},
    )
    assert status == 201
    body = {"mode": mode, "allow_person_override": mode == "hybrid"}
    assert h.request(h.owner, "PUT", "/api/settings/model-mode", body)[0] == 200
    if mode == "hybrid":
        assert h.request(h.owner, "PUT", "/api/me/model-use-own", {"use_own": True})[0] == 200
    decision = h.relay().decision_for(h.gateway, h.person(h.admin), "work")
    assert decision.refusal is None and decision.kind == "person"
    assert decision.machine_ids == (machine["machine_id"],)
    configured = h.settings(h.owner)[1]["front"]
    assert configured["enabled"] and configured["provider"] == "openai_compatible"
    return machine, connection, now


def _answers(h, message, who=None):
    return [
        reply
        for reply in h.chat(who or h.owner)[1]["replies"]
        if reply["message_id"] == message and reply["kind"] == "answer"
    ]


def _answer(h, message):
    answers = _answers(h, message)
    assert len(answers) == 1, answers
    return answers[0]


def _offline(h, machine, now, *, always=False):
    assert h.poll(machine, relay_always=always)[0] == 204
    with h.db._lock:
        h.db._conn.execute(
            "UPDATE chat_runners_seen SET seen_at=? WHERE machine_id=?",
            (h.relay()._stamp(now - h.relay().CHAT_ONLINE_S - 1), machine["machine_id"]),
        )


def _active_session(h):
    status, session = h.connect(h.owner, "work")
    assert status == 201
    h.subscribe(session["token"])
    assert h.mcp(session["token"], "space_read", board="work")[1]["ok"]
    return session["connection"]["id"]


@pytest.mark.parametrize("mode", ["personal", "hybrid"])
@pytest.mark.parametrize("availability", ["never_seen", "offline", "other_provider", "unrelated"])
def test_unavailable_personal_runner_explains_and_never_uses_hosted_fallback(
    relay_hub, monkeypatch, mode, availability
):
    h = relay_hub
    calls, attempts = h.fake_model(monkeypatch), h.no_outbound(monkeypatch)
    machine, connection, now = _personal_chat(h, monkeypatch, mode)
    if availability == "offline":
        _offline(h, machine, now)
    elif availability == "other_provider":
        assert h.poll(machine, clis=("codex",))[0] == 204
    elif availability == "unrelated":
        _offline(h, machine, now)
        unrelated = h.machine(label="shared-runner")
        assert h.poll(unrelated, relay_always=True)[0] == 204
    principal = h.person(h.admin)
    assert not h.relay().main_active(h.gateway, principal, "work", now)
    seq = h.seq()
    message = h.post(h.owner, "Which changes need attention?")
    reply = _answer(h, message)
    assert reply["code"] == "no_runner" and reply["model"] is None
    assert "runner" in reply["text"] and "Start" in reply["text"]
    assert "provider" in reply["text"] and "send this message again" in reply["text"]
    assert all(
        value not in reply["text"]
        for value in (h.admin, machine["machine_id"], machine["machine_token"], connection["id"])
    )
    assert _answers(h, message, h.member) == []

    # Re-delivery reuses the reply's existing message association and never adds a job.
    [event] = [
        event
        for event in events.after(h.db, h.workspace, seq, h.seq())
        if event["kind"] == "space_message" and event["data"]["message_id"] == message
    ]
    front.on_event(h.gateway, event)
    assert _answers(h, message) == [reply]
    second = h.post(h.owner, "Which changes need attention?")
    second_reply = _answer(h, second)
    assert second_reply["id"] != reply["id"]
    assert second_reply["code"] == "no_runner" and _answers(h, message) == [reply]
    assert h.jobs() == h.rows("chat_front_jobs") == []
    assert h.runner_jobs_count() == h.drain() == 0
    assert calls == attempts == []


@pytest.mark.parametrize("mode", ["personal", "hybrid"])
def test_online_personal_runner_still_answers_with_hosted_front_enabled(
    relay_hub, monkeypatch, mode
):
    h = relay_hub
    calls, attempts = h.fake_model(monkeypatch), h.no_outbound(monkeypatch)
    machine, _, _ = _personal_chat(h, monkeypatch, mode)
    assert h.poll(machine)[0] == 204
    message = h.post(h.owner, "Which changes need attention?")
    assert _answers(h, message) == []
    [queued] = h.jobs()
    assert queued["state"] == "queued" and queued["message_id"] == message
    assert set(json.loads(queued["account_snapshot"])) == {machine["machine_id"]}
    status, lease = h.poll(machine)
    assert status == 200
    assert (
        h.answer(machine, lease["job"], lease["lease_token"], "Review the open changes.")[0] == 200
    )
    reply = _answer(h, message)
    assert reply["code"] == "ok" and reply["text"] == "Review the open changes."
    assert h.rows("chat_front_jobs") == [] and h.drain() == 0
    assert calls == attempts == []


@pytest.mark.parametrize("availability", ["offline", "offline_always", "online", "online_always"])
def test_active_session_keeps_precedence_without_an_unavailable_reply(
    relay_hub, monkeypatch, availability
):
    h = relay_hub
    calls, attempts = h.fake_model(monkeypatch), h.no_outbound(monkeypatch)
    machine, _, now = _personal_chat(h, monkeypatch)
    always = availability.endswith("_always")
    if availability.startswith("offline"):
        _offline(h, machine, now, always=always)
    else:
        assert h.poll(machine, relay_always=always)[0] == 204
    _active_session(h)
    assert h.relay().main_active(h.gateway, h.person(h.admin), "work", now)
    message = h.post(h.owner, "Which changes need attention?")
    assert _answers(h, message) == []
    if availability == "online_always":
        [queued] = h.jobs()
        assert queued["state"] == "queued" and queued["message_id"] == message
    else:
        assert h.jobs() == []
    assert h.rows("chat_front_jobs") == [] and h.drain() == 0
    assert calls == attempts == []


@pytest.mark.parametrize("session", ["stale", "unsubscribed"])
def test_inactive_session_does_not_hide_the_runner_unavailable_reply(
    relay_hub, monkeypatch, session
):
    h = relay_hub
    machine, _, now = _personal_chat(h, monkeypatch)
    _offline(h, machine, now)
    if session == "stale":
        cid = _active_session(h)
        with h.db._lock:
            h.db._conn.execute(
                "UPDATE connections SET last_used_at=? WHERE id=?",
                (h.relay()._stamp(now - h.relay().CHAT_MAIN_FRESH_S - 1), cid),
            )
    else:
        assert h.connect(h.owner, "work")[0] == 201
    assert not h.relay().main_active(h.gateway, h.person(h.admin), "work", now)
    message = h.post(h.owner, "Which changes need attention?")
    reply = _answer(h, message)
    assert reply["code"] == "no_runner"
    assert h.jobs() == h.rows("chat_front_jobs") == []
