"""Handoff audit entries, hub events and signed delivery; no external network."""

import hashlib
import hmac
import json
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

from commonplace import control_db as control_module, events as hub_events, webhooks
from commonplace.control_db import ControlDB
from commonplace.extensions import ToolError
from commonplace.handoff import events as events_module
from commonplace.handoff.db import HandoffDB
from commonplace.handoff.events import Events
from commonplace.handoff.models import (
    EVENT_COMPLETED,
    EVENT_FAILED,
    EVENT_LAUNCH_FAILED,
    EVENT_SETTINGS,
    EVENT_STORED,
    EVENTS,
    Handoff,
    SuccessorRef,
)
from commonplace.policy import Policy

NOW = datetime(2026, 10, 30, 9, 0, 0, tzinfo=timezone.utc)
PRIVATE_TEXT = "private verbatim thread text"
HANDOFF_DATA = {
    "handoff_id": "hof_1",
    "stored_thread_id": "thr_1",
    "task_ref": "#17",
    "successor_agent_ref": "agent:2",
    "number": 17,
}


def stamp(value=NOW):
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    clock = SimpleNamespace(at=NOW)
    monkeypatch.setattr(events_module, "_now", lambda: clock.at)
    monkeypatch.setattr(control_module, "_now", lambda: clock.at)
    monkeypatch.setattr(hub_events, "_now", lambda: stamp(clock.at))
    monkeypatch.setattr(webhooks, "_now", lambda: stamp(clock.at))
    monkeypatch.setattr(webhooks.time, "time", lambda: clock.at.timestamp())
    return clock


@pytest.fixture
def hub(tmp_path):
    database = ControlDB(str(tmp_path / "hub" / "control.sqlite3"))
    try:
        owner = database.create_user("owner@example.com", is_admin=True)
        workspace = database.create_workspace("handoff", owner)
        _, token = database.create_token(
            workspace_ref=workspace,
            user_ref=owner,
            name="handoff-agent",
            client="test",
            actor=owner,
            policy=Policy.build(
                profile="full",
                trust="trusted",
                scopes=["memory:read", "private:read"],
                read_wings=["*"],
            ),
        )
        principal = database.authenticate_token(token)
        assert principal is not None
        webhooks.ensure_schema(database)
        yield SimpleNamespace(
            db=database,
            workspace=workspace,
            principal=principal,
            emitter=Events(database, HandoffDB(database)),
            audit_cursor=max(row["seq"] for row in database.audit_entries()),
            event_cursor=hub_events.latest_seq(database, workspace),
        )
    finally:
        database.close()


@pytest.fixture
def handoff(hub):
    return Handoff(
        id="hof_1",
        workspace_id=hub.workspace,
        idempotency_key="thread:source_1",
        source_thread_id="source_1",
        state="done",
        task_ref="#17",
        stored_thread_id="thr_1",
        successor=SuccessorRef(agent_ref="agent:2", url=PRIVATE_TEXT, thread_id="source_2"),
        connection_id="connection_old",
        agent=PRIVATE_TEXT,
        branch=PRIVATE_TEXT,
        pr_url=PRIVATE_TEXT,
        cwd=PRIVATE_TEXT,
        doc_markdown=PRIVATE_TEXT,
        error=PRIVATE_TEXT,
    )


def audit_rows(hub):
    return [dict(row) for row in hub.db.audit_entries() if row["seq"] > hub.audit_cursor]


def recorded_events(hub):
    return hub_events.after(hub.db, hub.workspace, hub.event_cursor)


def prepare_delivery(hub, handoff, url="http://127.0.0.1:9/wake"):
    hook = webhooks.add(hub.db, hub.principal, url, kinds=["handoff"])
    dispatcher = webhooks.Dispatcher(hub.db)
    hub.emitter.emit(
        EVENT_COMPLETED, workspace_id=hub.workspace, handoff=handoff, principal=hub.principal
    )
    (event,) = recorded_events(hub)
    dispatcher.on_event(event)
    row = dispatcher._due()
    assert row is not None
    return SimpleNamespace(hub=hub, hook=hook, dispatcher=dispatcher, row=row, event=event)


@pytest.fixture
def pending(hub, handoff):
    return prepare_delivery(hub, handoff)


def delivery_rows(pending):
    return webhooks.deliveries(pending.hub.db, pending.hub.principal, pending.hook["id"])


@pytest.fixture
def receiver():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            self.server.received.append((self.path, body, self.headers))
            self.send_response(self.server.reply_status)
            if 300 <= self.server.reply_status < 400:
                self.send_header("Location", self.server.redirect_url)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.received = []
    server.reply_status = 202
    server.url = f"http://127.0.0.1:{server.server_port}/wake?task=17"
    server.redirect_url = f"http://127.0.0.1:{server.server_port}/redirected"
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=1)
        server.server_close()


def test_emit_writes_real_audit_and_hub_event(hub, handoff):
    hub.emitter.emit(
        EVENT_STORED,
        workspace_id=hub.workspace,
        handoff=handoff,
        principal=hub.principal,
        transcript=PRIVATE_TEXT,
        note=PRIVATE_TEXT,
        error=PRIVATE_TEXT,
        actor=PRIVATE_TEXT,
        connection_id=PRIVATE_TEXT,
        auth=PRIVATE_TEXT,
    )
    (audit,) = audit_rows(hub)
    assert (audit["action"], audit["decision"], audit["workspace_id"]) == (
        EVENT_STORED,
        "ok",
        hub.workspace,
    )
    assert (audit["user_id"], audit["connection_id"], audit["auth"]) == (
        hub.principal.user_id,
        hub.principal.connection_id,
        hub.principal.auth,
    )
    assert audit["at"] == stamp()
    assert audit["tool"] is None
    assert audit["reason"] == ""
    assert json.loads(audit["target_ids"]) == ["hof_1", "thr_1", "agent:2"]
    (event,) = recorded_events(hub)
    assert event == {
        "seq": hub.event_cursor + 1,
        "at": stamp(),
        "workspace_id": hub.workspace,
        "kind": EVENT_STORED,
        "subject": "#17",
        "actor": hub.principal.actor,
        "data": HANDOFF_DATA,
    }
    assert hub_events.after(hub.db, hub.workspace, event["seq"]) == []
    assert PRIVATE_TEXT not in json.dumps([audit, event])


def test_emit_accepts_only_named_ids_without_handoff_or_principal(hub):
    hub.emitter.emit(
        EVENT_SETTINGS,
        workspace_id=hub.workspace,
        handoff_id="hof_2",
        stored_thread_id="thr_2",
        task_ref="#18",
        successor_agent_ref="agent:3",
        transcript=PRIVATE_TEXT,
        auth=PRIVATE_TEXT,
        actor=PRIVATE_TEXT,
        connection_id=PRIVATE_TEXT,
    )
    (audit,) = audit_rows(hub)
    (event,) = recorded_events(hub)
    assert event["data"] == {
        "handoff_id": "hof_2",
        "stored_thread_id": "thr_2",
        "task_ref": "#18",
        "successor_agent_ref": "agent:3",
        "number": 18,
    }
    assert event["subject"] == "#18"
    assert event["actor"] == ""
    assert json.loads(audit["target_ids"]) == ["hof_2", "thr_2", "agent:3"]
    assert audit["user_id"] is audit["connection_id"] is audit["auth"] is None
    assert PRIVATE_TEXT not in json.dumps([audit, event])


@pytest.mark.parametrize("with_principal", [False, True])
def test_emit_uses_handoff_connection_without_principal(hub, handoff, with_principal):
    principal = replace(hub.principal, connection_id="") if with_principal else None
    hub.emitter.emit(EVENT_STORED, workspace_id=hub.workspace, handoff=handoff, principal=principal)
    (audit,) = audit_rows(hub)
    (event,) = recorded_events(hub)
    assert audit["connection_id"] == handoff.connection_id
    assert event["actor"] == (principal.actor if principal else "")


@pytest.mark.parametrize("empty", [False, True])
def test_handoff_ids_take_precedence_with_named_fallbacks(hub, handoff, empty):
    if empty:
        handoff = replace(handoff, id="", stored_thread_id="", task_ref="", successor=None)
    hub.emitter.emit(
        EVENT_STORED,
        workspace_id=hub.workspace,
        handoff=handoff,
        handoff_id="hof_2",
        stored_thread_id="thr_2",
        task_ref="task:日本語",
        successor_agent_ref="agent:3",
    )
    (event,) = recorded_events(hub)
    (audit,) = audit_rows(hub)
    expected = (
        {
            "handoff_id": "hof_2",
            "stored_thread_id": "thr_2",
            "task_ref": "task:日本語",
            "successor_agent_ref": "agent:3",
        }
        if empty
        else HANDOFF_DATA
    )
    assert event["data"] == expected
    assert event["subject"] == expected["task_ref"]
    assert json.loads(audit["target_ids"]) == [
        expected["handoff_id"],
        expected["stored_thread_id"],
        expected["successor_agent_ref"],
    ]


def test_subject_falls_back_to_handoff_id_without_task(hub, handoff):
    hub.emitter.emit(
        EVENT_STORED, workspace_id=hub.workspace, handoff=replace(handoff, task_ref="")
    )
    (event,) = recorded_events(hub)
    assert event["subject"] == "hof_1"
    assert event["data"] == {
        key: value for key, value in HANDOFF_DATA.items() if key not in ("task_ref", "number")
    }


@pytest.mark.parametrize("event", EVENTS)
def test_all_contract_events_are_supported(hub, handoff, event):
    hub.emitter.emit(event, workspace_id=hub.workspace, handoff=handoff)
    (audit,) = audit_rows(hub)
    (stored,) = recorded_events(hub)
    assert audit["action"] == stored["kind"] == event
    assert json.loads(audit["target_ids"]) == ["hof_1", "thr_1", "agent:2"]
    assert all(stored["data"][key] == value for key, value in HANDOFF_DATA.items())


@pytest.mark.parametrize(
    "event,decision,reason,expected",
    [
        (EVENT_FAILED, "error", PRIVATE_TEXT * 50, "thread storage failed"),
        (EVENT_LAUNCH_FAILED, "error", PRIVATE_TEXT, "successor launch failed"),
        (EVENT_FAILED, "error", "", "thread storage failed"),
        (EVENT_SETTINGS, "deny", PRIVATE_TEXT, "handoff failed"),
        (EVENT_SETTINGS, "deny", "not found", "not found"),
        (EVENT_SETTINGS, "deny", "off", "off"),
        (EVENT_SETTINGS, "deny", "invalid settings", "invalid settings"),
        (EVENT_LAUNCH_FAILED, "error", "successor launch failed", "successor launch failed"),
    ],
)
def test_failure_diagnostics_are_bounded_and_cannot_contain_thread_text(
    hub, handoff, event, decision, reason, expected
):
    hub.emitter.emit(
        event, workspace_id=hub.workspace, handoff=handoff, decision=decision, reason=reason
    )
    (audit,) = audit_rows(hub)
    (stored,) = recorded_events(hub)
    assert stored["data"] == {**HANDOFF_DATA, "decision": decision, "reason": expected}
    assert audit["decision"] == decision
    assert audit["reason"] == expected
    assert len(stored["data"]["reason"]) <= 200
    assert PRIVATE_TEXT not in json.dumps([audit, stored])


def test_unknown_event_is_not_persisted(hub, caplog):
    notices = []
    hub_events.add_listener(notices.append)
    try:
        hub.emitter.emit(PRIVATE_TEXT, workspace_id=hub.workspace)
    finally:
        hub_events.remove_listener(notices.append)
    assert audit_rows(hub) == recorded_events(hub) == notices == []
    assert "Unknown handoff event" in caplog.text
    assert PRIVATE_TEXT not in caplog.text


@pytest.mark.parametrize("broken_audit,broken_hub", [(True, False), (False, True), (True, True)])
def test_emit_isolates_database_failures(
    hub, handoff, monkeypatch, caplog, broken_audit, broken_hub
):
    attempts = []
    write_audit, write_event = hub.db.audit, hub_events.emit

    def audit(**fields):
        attempts.append("audit")
        if broken_audit:
            raise RuntimeError(PRIVATE_TEXT)
        return write_audit(**fields)

    def emit(*args, **kwargs):
        attempts.append("hub")
        if broken_hub:
            raise RuntimeError(PRIVATE_TEXT)
        return write_event(*args, **kwargs)

    monkeypatch.setattr(hub.db, "audit", audit)
    monkeypatch.setattr(hub_events, "emit", emit)
    assert hub.emitter.emit(EVENT_STORED, workspace_id=hub.workspace, handoff=handoff) is None
    assert attempts == ["audit", "hub"]
    assert len(audit_rows(hub)) == (0 if broken_audit else 1)
    assert len(recorded_events(hub)) == (0 if broken_hub else 1)
    assert ("Handoff audit write failed" in caplog.text) == broken_audit
    assert ("Handoff event write failed" in caplog.text) == broken_hub
    assert PRIVATE_TEXT not in json.dumps([audit_rows(hub), recorded_events(hub)]) + caplog.text


@pytest.mark.parametrize("subscribed", [False, True])
def test_emit_notifies_hub_listeners_with_or_without_subscribers(hub, handoff, subscribed):
    hook = webhooks.add(
        hub.db,
        hub.principal,
        "http://127.0.0.1:9/wake",
        kinds=["handoff"] if subscribed else ["card_changed"],
    )
    dispatcher = webhooks.Dispatcher(hub.db)
    notices = []
    hub_events.add_listener(dispatcher.on_event)
    hub_events.add_listener(notices.append)
    try:
        hub.emitter.emit(EVENT_COMPLETED, workspace_id=hub.workspace, handoff=handoff)
    finally:
        hub_events.remove_listener(dispatcher.on_event)
        hub_events.remove_listener(notices.append)
    assert notices == recorded_events(hub)
    assert len(notices) == 1
    rows = webhooks.deliveries(hub.db, hub.principal, hook["id"])
    assert len(rows) == int(subscribed)
    if subscribed:
        assert rows[0]["event_seq"] == notices[0]["seq"]
        assert rows[0]["status"] == "pending"
        assert rows[0]["attempt"] == 0


def test_broken_notification_does_not_escape_emit(hub, handoff, monkeypatch, caplog):
    hook = webhooks.add(hub.db, hub.principal, "http://127.0.0.1:9/wake", kinds=["handoff"])
    dispatcher = webhooks.Dispatcher(hub.db)
    enqueue = webhooks._enqueue
    attempts, notices = [], []

    def broken(*args):
        attempts.append(True)
        raise RuntimeError(PRIVATE_TEXT)

    monkeypatch.setattr(webhooks, "_enqueue", broken)
    hub_events.add_listener(dispatcher.on_event)
    hub_events.add_listener(notices.append)
    try:
        assert (
            hub.emitter.emit(EVENT_COMPLETED, workspace_id=hub.workspace, handoff=handoff) is None
        )
    finally:
        hub_events.remove_listener(dispatcher.on_event)
        hub_events.remove_listener(notices.append)
    assert attempts == [True]
    assert len(audit_rows(hub)) == 1
    assert notices == recorded_events(hub)
    assert len(notices) == 1
    assert webhooks.deliveries(hub.db, hub.principal, hook["id"]) == []
    assert "event listener failed" in caplog.text
    assert PRIVATE_TEXT not in caplog.text
    monkeypatch.setattr(webhooks, "_enqueue", enqueue)
    dispatcher.reconcile()
    dispatcher.on_event(notices[0])
    rows = webhooks.deliveries(hub.db, hub.principal, hook["id"])
    assert len(rows) == 1
    assert rows[0]["event_seq"] == notices[0]["seq"]
    assert rows[0]["status"] == "pending"


def test_broken_handoff_does_not_escape_emit(hub, caplog):
    class BrokenHandoff:
        @property
        def id(self):
            raise RuntimeError(PRIVATE_TEXT)

    assert (
        hub.emitter.emit(EVENT_STORED, workspace_id=hub.workspace, handoff=BrokenHandoff()) is None
    )
    assert audit_rows(hub) == recorded_events(hub) == []
    assert "preparation failed" in caplog.text
    assert PRIVATE_TEXT not in caplog.text


def test_handoff_preset_filters_kinds_and_workspaces(hub, handoff):
    matching = [
        webhooks.add(hub.db, hub.principal, "http://127.0.0.1:9/wake", kinds=["handoff"])
        for _ in range(2)
    ]
    other_kind = webhooks.add(
        hub.db, hub.principal, "http://127.0.0.1:9/other", kinds=["card_changed"]
    )
    elsewhere = hub.db.create_workspace("elsewhere", hub.principal.user_id)
    other_principal = replace(hub.principal, workspace_id=elsewhere)
    other_workspace = webhooks.add(
        hub.db, other_principal, "http://127.0.0.1:9/other", kinds=["handoff"]
    )
    for kind in EVENTS:
        hub.emitter.emit(kind, workspace_id=hub.workspace, handoff=handoff)
    stored = recorded_events(hub)
    assert len(stored) == len(EVENTS)
    dispatcher = webhooks.Dispatcher(hub.db)
    dispatcher.on_event(stored[-1])
    for hook in matching:
        assert hook["kinds"] == list(EVENTS)
        rows = webhooks.deliveries(hub.db, hub.principal, hook["id"])
        assert {row["event_seq"] for row in rows} == {event["seq"] for event in stored}
        assert len(rows) == len(EVENTS)
    assert webhooks.deliveries(hub.db, hub.principal, other_kind["id"]) == []
    assert webhooks.deliveries(hub.db, other_principal, other_workspace["id"]) == []
    assert hub_events.after(hub.db, elsewhere) == []


def test_default_delivery_sends_exact_canonical_bytes_and_signature(hub, handoff, receiver):
    handoff = replace(handoff, task_ref="task:日本語")
    pending = prepare_delivery(hub, handoff, receiver.url)
    pending.dispatcher._deliver(pending.row)
    assert pending.dispatcher._due() is None
    pending.dispatcher._deliver(pending.row)
    (row,) = delivery_rows(pending)
    assert (row["status"], row["attempt"], row["http_status"]) == ("delivered", 1, 202)
    assert row["delivered_at"] == stamp()
    assert len(receiver.received) == 1
    path, body, headers = receiver.received[0]
    assert path == "/wake?task=17"
    assert body == bytes(pending.row["payload"])
    payload = json.loads(body)
    assert payload["data"] == {
        **{key: value for key, value in HANDOFF_DATA.items() if key != "number"},
        "task_ref": "task:日本語",
    }
    assert (payload["kind"], payload["subject"], payload["actor"], payload["at"]) == (
        EVENT_COMPLETED,
        "task:日本語",
        hub.principal.actor,
        stamp(),
    )
    assert payload["workspace"] == hub.workspace
    assert payload["seq"] == pending.event["seq"]
    assert payload["id"] == f"evt_{pending.event['seq']}"
    assert headers["Content-Type"] == "application/json"
    assert headers["X-Commonplace-Event"] == EVENT_COMPLETED
    assert headers["X-Commonplace-Delivery"] == row["id"]
    timestamp, signature = headers["X-Commonplace-Signature"].split(",")
    assert timestamp == f"t={int(NOW.timestamp())}"
    expected = hmac.new(
        pending.hook["secret"].encode(), timestamp[2:].encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    assert hmac.compare_digest(signature, "v1=" + expected)
    assert PRIVATE_TEXT not in body.decode() + json.dumps(audit_rows(hub))
    assert pending.hook["secret"] not in body.decode() + json.dumps(row)


@pytest.mark.parametrize("status", [200, 201, 204, 299])
def test_every_success_status_delivers(pending, monkeypatch, status):
    requests = []
    monkeypatch.setattr(webhooks, "_post", lambda *args: requests.append(args) or status)
    pending.dispatcher._deliver(pending.row)
    pending.dispatcher._deliver(pending.row)
    (row,) = delivery_rows(pending)
    assert row["status"] == "delivered"
    assert row["attempt"] == 1
    assert row["http_status"] == status
    assert row["error"] == ""
    assert len(row["attempts"]) == len(requests) == 1
    assert pending.dispatcher._due() is None


@pytest.mark.parametrize(
    "status", [300, 301, 302, 307, 308, 400, 401, 404, 410, 429, 500, 503, 599]
)
def test_other_statuses_retry(pending, monkeypatch, status):
    monkeypatch.setattr(webhooks, "_post", lambda *args: status)
    pending.dispatcher._deliver(pending.row)
    (row,) = delivery_rows(pending)
    assert (row["status"], row["attempt"], row["http_status"], row["error"]) == (
        "retry",
        1,
        status,
        f"HTTP {status}",
    )
    assert row["next_at"] == NOW.timestamp() + webhooks.BACKOFF[0]
    assert row["delivered_at"] is None
    assert row["attempts"] == [
        {"attempt": 1, "at": stamp(), "http_status": status, "error": f"HTTP {status}"}
    ]
    assert pending.dispatcher._due() is None


def test_hub_backoff_delays_then_terminal_failure(pending, monkeypatch, fixed_clock):
    requests = []
    monkeypatch.setattr(webhooks, "_post", lambda *args: requests.append(args) or 503)
    row = pending.row
    for attempt in range(1, webhooks.MAX_ATTEMPTS + 1):
        pending.dispatcher._deliver(row)
        (stored,) = delivery_rows(pending)
        assert stored["attempt"] == attempt
        assert stored["error"] == "HTTP 503"
        if attempt == webhooks.MAX_ATTEMPTS:
            assert stored["status"] == "dead"
            assert pending.dispatcher._due() is None
            break
        assert stored["status"] == "retry"
        next_at = fixed_clock.at + timedelta(seconds=webhooks.BACKOFF[attempt - 1])
        assert stored["next_at"] == next_at.timestamp()
        fixed_clock.at = next_at - timedelta(seconds=1)
        assert pending.dispatcher._due() is None
        fixed_clock.at = next_at
        row = pending.dispatcher._due()
        assert row is not None
    pending.dispatcher._deliver(row)
    assert len(requests) == webhooks.MAX_ATTEMPTS
    (stored,) = delivery_rows(pending)
    assert len(stored["attempts"]) == webhooks.MAX_ATTEMPTS
    (failure,) = [
        event for event in recorded_events(pending.hub) if event["kind"] == "webhook.failed"
    ]
    assert failure["data"] == {"webhook_id": pending.hook["id"], "delivery_id": pending.row["id"]}
    assert PRIVATE_TEXT not in json.dumps([stored, failure])


def test_retry_uses_clock_after_the_request_finishes(pending, monkeypatch, fixed_clock):
    def post(*args):
        fixed_clock.at += timedelta(seconds=4)
        return 503

    monkeypatch.setattr(webhooks, "_post", post)
    pending.dispatcher._deliver(pending.row)
    (row,) = delivery_rows(pending)
    assert row["next_at"] == NOW.timestamp() + 4 + webhooks.BACKOFF[0]
    assert row["attempts"][0]["at"] == stamp(NOW + timedelta(seconds=4))


def test_successful_retry_keeps_delivery_id_payload_and_signature(
    hub, handoff, receiver, fixed_clock
):
    receiver.reply_status = 503
    pending = prepare_delivery(hub, handoff, receiver.url)
    pending.dispatcher._deliver(pending.row)
    fixed_clock.at += timedelta(seconds=webhooks.BACKOFF[0])
    row = pending.dispatcher._due()
    assert row is not None
    receiver.reply_status = 204
    pending.dispatcher._deliver(pending.row)
    assert len(receiver.received) == 1
    pending.dispatcher._deliver(row)
    assert len(receiver.received) == 2
    first, second = receiver.received
    assert first[1] == second[1]
    assert first[2]["X-Commonplace-Delivery"] == second[2]["X-Commonplace-Delivery"] == row["id"]
    for _, body, headers in receiver.received:
        timestamp, signature = headers["X-Commonplace-Signature"].split(",")
        expected = hmac.new(
            pending.hook["secret"].encode(), timestamp[2:].encode() + b"." + body, hashlib.sha256
        ).hexdigest()
        assert hmac.compare_digest(signature, "v1=" + expected)
        assert PRIVATE_TEXT.encode() not in body
    assert first[2]["X-Commonplace-Signature"] != second[2]["X-Commonplace-Signature"]
    (stored,) = delivery_rows(pending)
    assert stored["status"] == "delivered"
    assert stored["attempt"] == 2
    assert [attempt["http_status"] for attempt in stored["attempts"]] == [503, 204]
    assert pending.dispatcher._due() is None


@pytest.mark.parametrize("error_type", [OSError, TimeoutError, RuntimeError])
def test_transport_errors_retry_without_recording_raw_error(
    pending, monkeypatch, caplog, error_type
):
    def broken(*args):
        raise error_type(PRIVATE_TEXT + pending.hook["secret"])

    monkeypatch.setattr(webhooks, "_post", broken)
    pending.dispatcher._deliver(pending.row)
    (row,) = delivery_rows(pending)
    assert (row["status"], row["attempt"], row["http_status"], row["error"]) == (
        "retry",
        1,
        None,
        "delivery connection failed",
    )
    assert row["attempts"] == [
        {"attempt": 1, "at": stamp(), "http_status": None, "error": "delivery connection failed"}
    ]
    assert row["next_at"] == NOW.timestamp() + webhooks.BACKOFF[0]
    saved = json.dumps([row, recorded_events(pending.hub), audit_rows(pending.hub)]) + caplog.text
    assert PRIVATE_TEXT not in saved
    assert pending.hook["secret"] not in saved


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/thread",
        "ftp://127.0.0.1/file",
        "data:text/plain,private",
        "javascript:run()",
        "",
        "//127.0.0.1/wake",
        "http://",
        "http://owner:password@127.0.0.1/wake",
        "http://127.0.0.1:70000/wake",
        "http://127.0.0.1:0/wake",
        "http://[broken",
        "http://127.0.0.1/\r\nprivate",
        None,
    ],
)
def test_invalid_urls_are_refused_without_side_effects(hub, monkeypatch, url):
    requests = []
    monkeypatch.setattr(webhooks, "_post", lambda *args: requests.append(args) or 204)
    with pytest.raises(ToolError):
        webhooks.add(hub.db, hub.principal, url, kinds=["handoff"])
    assert webhooks.list_webhooks(hub.db, hub.principal) == []
    assert hub.db._conn.execute("SELECT count(*) FROM webhook_deliveries").fetchone()[0] == 0
    assert requests == audit_rows(hub) == recorded_events(hub) == []


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308, 410])
def test_default_http_handles_errors_without_following_redirects(hub, handoff, receiver, status):
    receiver.reply_status = status
    pending = prepare_delivery(hub, handoff, receiver.url)
    pending.dispatcher._deliver(pending.row)
    assert len(receiver.received) == 1
    assert receiver.received[0][0] == "/wake?task=17"
    (row,) = delivery_rows(pending)
    assert (row["status"], row["http_status"], row["error"]) == ("retry", status, f"HTTP {status}")


@pytest.mark.parametrize("scheme", ["http", "https"])
@pytest.mark.parametrize("failure", [None, "request", "response"])
def test_default_post_timeout_and_connection_cleanup(monkeypatch, scheme, failure):
    calls = []

    class Connection:
        def __init__(self, host, port, *, timeout):
            calls.append((host, port, timeout))

        def request(self, method, path, **kwargs):
            calls.append((method, path, kwargs))
            if failure == "request":
                raise OSError("request failed")

        def getresponse(self):
            if failure == "response":
                raise OSError("response failed")
            return SimpleNamespace(status=204)

        def close(self):
            calls.append("connection closed")

    name = "HTTPSConnection" if scheme == "https" else "HTTPConnection"
    monkeypatch.setattr(webhooks.http.client, name, Connection)
    args = (f"{scheme}://127.0.0.1:8000?x=1", b"{}", "test-key", "dlv_1", EVENT_COMPLETED)
    if failure:
        with pytest.raises(OSError):
            webhooks._post(*args)
    else:
        assert webhooks._post(*args) == 204
    assert calls[0] == ("127.0.0.1", 8000, webhooks.HTTP_TIMEOUT)
    assert calls[1][:2] == ("POST", "/?x=1")
    assert calls[1][2]["body"] == b"{}"
    assert calls[-1] == "connection closed"


def test_failed_handoff_delivery_keeps_only_ids_and_decision(hub, handoff, monkeypatch, caplog):
    hook = webhooks.add(hub.db, hub.principal, "http://127.0.0.1:9/wake", kinds=["handoff"])
    hub.emitter.emit(
        EVENT_FAILED,
        workspace_id=hub.workspace,
        handoff=handoff,
        decision="error",
        reason=PRIVATE_TEXT,
        transcript=PRIVATE_TEXT,
    )
    (event,) = recorded_events(hub)
    dispatcher = webhooks.Dispatcher(hub.db)
    dispatcher.on_event(event)
    row = dispatcher._due()
    assert row is not None
    requests = []
    monkeypatch.setattr(webhooks, "_post", lambda *args: requests.append(args) or 204)
    dispatcher._deliver(row)
    assert len(requests) == 1
    payload = json.loads(requests[0][1])
    assert payload["data"] == {**HANDOFF_DATA, "decision": "error"}
    assert payload["kind"] == EVENT_FAILED
    assert event["data"]["reason"] == audit_rows(hub)[0]["reason"] == "thread storage failed"
    (stored,) = webhooks.deliveries(hub.db, hub.principal, hook["id"])
    assert stored["status"] == "delivered"
    saved = json.dumps([payload, event, audit_rows(hub), stored]) + caplog.text
    assert PRIVATE_TEXT not in saved
    assert hook["secret"] not in saved


def test_reconcile_recovers_missed_notifications_without_duplicate_deliveries(hub, handoff):
    hook = webhooks.add(hub.db, hub.principal, "http://127.0.0.1:9/wake", kinds=["handoff"])
    for kind in (EVENT_STORED, EVENT_COMPLETED):
        hub.emitter.emit(kind, workspace_id=hub.workspace, handoff=handoff)
    stored = recorded_events(hub)
    assert len(stored) == 2
    assert webhooks.deliveries(hub.db, hub.principal, hook["id"]) == []
    reopened = ControlDB(hub.db.path)
    try:
        dispatcher = webhooks.Dispatcher(reopened)
        dispatcher.reconcile()
        dispatcher.on_event(stored[-1])
        dispatcher.on_event(stored[0])
        rows = webhooks.deliveries(reopened, hub.principal, hook["id"])
        assert len(rows) == 2
        assert {row["event_seq"] for row in rows} == {event["seq"] for event in stored}
        assert all(row["status"] == "pending" and row["attempt"] == 0 for row in rows)
        assert reopened._conn.execute(
            "SELECT value FROM meta WHERE key='webhook_dispatch_seq'"
        ).fetchone()[0] == str(stored[-1]["seq"])
    finally:
        reopened.close()


def test_restart_resumes_retry_without_duplicate_delivery(pending, monkeypatch, fixed_clock):
    requests = []
    monkeypatch.setattr(
        webhooks,
        "_post",
        lambda *args: requests.append(args) or (503 if len(requests) == 1 else 204),
    )
    pending.dispatcher._deliver(pending.row)
    fixed_clock.at += timedelta(seconds=webhooks.BACKOFF[0])
    reopened = ControlDB(pending.hub.db.path)
    try:
        dispatcher = webhooks.Dispatcher(reopened)
        dispatcher.reconcile()
        row = dispatcher._due()
        assert row is not None
        assert row["id"] == pending.row["id"]
        dispatcher._deliver(row)
        dispatcher.on_event(pending.event)
        assert dispatcher._due() is None
    finally:
        reopened.close()
    assert requests[0] == requests[1]
    (row,) = delivery_rows(pending)
    assert (row["status"], row["attempt"]) == ("delivered", 2)
    assert [attempt["http_status"] for attempt in row["attempts"]] == [503, 204]


def test_emit_never_waits_for_http(hub, handoff, monkeypatch):
    entered, release, emitted = threading.Event(), threading.Event(), threading.Event()
    requests = []

    def post(*args):
        requests.append(args)
        entered.set()
        assert release.wait(timeout=5)
        return 204

    monkeypatch.setattr(webhooks, "_post", post)
    hook = webhooks.add(hub.db, hub.principal, "http://127.0.0.1:9/wake", kinds=["handoff"])
    dispatcher = webhooks.Dispatcher(hub.db)

    def emit():
        hub.emitter.emit(EVENT_COMPLETED, workspace_id=hub.workspace, handoff=handoff)
        emitted.set()

    thread = threading.Thread(target=emit, daemon=True)
    try:
        dispatcher.start()
        hub.emitter.emit(EVENT_STORED, workspace_id=hub.workspace, handoff=handoff)
        assert entered.wait(timeout=1)
        thread.start()
        assert emitted.wait(timeout=1)
        rows = webhooks.deliveries(hub.db, hub.principal, hook["id"])
        assert len(rows) == len(audit_rows(hub)) == len(recorded_events(hub)) == 2
        assert all(row["status"] == "pending" and row["attempt"] == 0 for row in rows)
        assert len(requests) == 1
    finally:
        release.set()
        if thread.ident is not None:
            thread.join(timeout=1)
        dispatcher.stop()
    assert not dispatcher.thread.is_alive()
