"""Audit and webhook delivery tests; no worker process or external network."""

import hashlib
import hmac
import json
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

from commonplace.control_db import ControlDB
from commonplace.handoff import events as events_module
from commonplace.handoff.events import Events, Outbox
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

NOW = datetime(2026, 10, 30, 9, 0, 0, tzinfo=timezone.utc)
PRIVATE_TEXT = "private verbatim thread text"
SECRET = "local-webhook-key"


def stamp(value=NOW):
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


class AuditFake:
    def __init__(self):
        self.rows = []

    def audit(self, **fields):
        self.rows.append(fields)


class HandoffDBFake:
    """The outbox contract, including committed due times and retry counts."""

    def __init__(self):
        self.hooks = []
        self.rows = []
        self.retries = []
        self.delivered = []
        self.queries = []
        self.enqueued = []
        self.queried = threading.Event()
        self.acknowledged = threading.Event()
        self.lock = threading.Lock()

    def subscribe(self, *, workspace="ws_1", events=(EVENT_STORED,), url="http://127.0.0.1/wake"):
        hook = {
            "id": f"hook_{len(self.hooks) + 1}",
            "workspace": workspace,
            "events": events,
            "url": url,
            "secret": SECRET,
        }
        self.hooks.append(hook)
        return hook

    def enqueue(self, workspace_id, event, payload):
        ids = []
        with self.lock:
            self.enqueued.append((workspace_id, event, payload))
            for hook in self.hooks:
                if hook["workspace"] != workspace_id or event not in hook["events"]:
                    continue
                row = {
                    "id": len(self.rows) + 1,
                    "webhook_id": hook["id"],
                    "workspace_id": workspace_id,
                    "event": event,
                    "payload": dict(payload),
                    "attempts": 0,
                    "url": hook["url"],
                    "next_at": payload["at"],
                    "finished": False,
                }
                self.rows.append(row)
                ids.append(row["id"])
        return ids

    def due(self, now_iso, limit=50):
        with self.lock:
            self.queries.append((now_iso, limit))
            rows = [
                dict(row) for row in self.rows if not row["finished"] and row["next_at"] <= now_iso
            ][:limit]
        self.queried.set()
        return rows

    def webhook_secret(self, webhook_id):
        return next(hook["secret"] for hook in self.hooks if hook["id"] == webhook_id)

    def mark_delivered(self, outbox_id):
        with self.lock:
            self.delivered.append(outbox_id)
            self.rows[outbox_id - 1]["finished"] = True
        self.acknowledged.set()

    def mark_retry(self, outbox_id, error, next_at_iso, *, give_up=False):
        with self.lock:
            self.retries.append((outbox_id, error, next_at_iso, give_up))
            row = self.rows[outbox_id - 1]
            row.update(attempts=row["attempts"] + 1, next_at=next_at_iso, finished=give_up)


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(events_module, "_now", lambda: NOW)


@pytest.fixture
def handoff():
    return Handoff(
        id="hof_1",
        workspace_id="ws_1",
        idempotency_key="thread:source_1",
        source_thread_id="source_1",
        state="done",
        task_ref="#17",
        stored_thread_id="thr_1",
        successor=SuccessorRef(agent_ref="agent:2", url=PRIVATE_TEXT, thread_id="source_2"),
        connection_id="connection_old",
        doc_markdown=PRIVATE_TEXT,
        error=PRIVATE_TEXT,
    )


@pytest.fixture
def db():
    database = HandoffDBFake()
    database.subscribe(events=EVENTS)
    return database


@pytest.fixture
def pending(db, handoff):
    Events(AuditFake(), db).emit(EVENT_STORED, workspace_id="ws_1", handoff=handoff)
    return db


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


def test_emit_writes_real_audit_and_matching_outbox_rows(tmp_path, db, handoff):
    db.subscribe(events=(EVENT_STORED,))
    db.subscribe(workspace="ws_other")
    db.subscribe(events=(EVENT_COMPLETED,))
    principal = SimpleNamespace(user_id="user_1", connection_id="connection_1", auth="pat")
    control = ControlDB(str(tmp_path / "control.sqlite3"))
    try:
        Events(control, db).emit(
            EVENT_STORED,
            workspace_id="ws_1",
            handoff=handoff,
            principal=principal,
            transcript=PRIVATE_TEXT,
            note=PRIVATE_TEXT,
            error=PRIVATE_TEXT,
        )
        entries = [dict(entry) for entry in control.audit_entries()]
    finally:
        control.close()

    assert len(entries) == 1
    audit = entries[0]
    assert (audit["action"], audit["decision"], audit["workspace_id"]) == (
        EVENT_STORED,
        "ok",
        "ws_1",
    )
    assert (audit["user_id"], audit["connection_id"], audit["auth"]) == (
        "user_1",
        "connection_1",
        "pat",
    )
    assert audit["tool"] is None
    assert audit["reason"] == ""
    assert json.loads(audit["target_ids"]) == ["hof_1", "thr_1", "agent:2"]
    assert len(db.rows) == 2
    expected = {
        "event": EVENT_STORED,
        "workspace": "ws_1",
        "handoff_id": "hof_1",
        "stored_thread_id": "thr_1",
        "task_ref": "#17",
        "successor_agent_ref": "agent:2",
        "at": stamp(),
    }
    assert [row["payload"] for row in db.rows] == [expected, expected]
    assert PRIVATE_TEXT not in json.dumps([entries, db.rows])


def test_emit_accepts_only_named_ids_without_handoff_or_principal(db):
    audit = AuditFake()
    Events(audit, db).emit(
        EVENT_SETTINGS,
        workspace_id="ws_1",
        handoff_id="hof_2",
        stored_thread_id="thr_2",
        task_ref="#18",
        successor_agent_ref="agent:3",
        transcript=PRIVATE_TEXT,
        auth=PRIVATE_TEXT,
    )
    assert db.rows[0]["payload"]["handoff_id"] == "hof_2"
    assert db.rows[0]["payload"]["task_ref"] == "#18"
    assert audit.rows[0]["target_ids"] == ["hof_2", "thr_2", "agent:3"]
    assert audit.rows[0]["user_id"] is None
    assert audit.rows[0]["connection_id"] is None
    assert audit.rows[0]["auth"] is None
    assert PRIVATE_TEXT not in json.dumps([audit.rows, db.rows])


def test_emit_uses_handoff_connection_without_principal(db, handoff):
    audit = AuditFake()
    Events(audit, db).emit(EVENT_STORED, workspace_id="ws_1", handoff=handoff)
    assert audit.rows[0]["connection_id"] == handoff.connection_id


@pytest.mark.parametrize("event", EVENTS)
def test_all_contract_events_are_supported(db, handoff, event):
    audit = AuditFake()
    Events(audit, db).emit(event, workspace_id="ws_1", handoff=handoff)
    assert audit.rows[0]["action"] == event
    assert db.rows[0]["event"] == event


@pytest.mark.parametrize(
    "event,decision,reason,expected",
    [
        (EVENT_FAILED, "error", PRIVATE_TEXT * 50, "thread storage failed"),
        (EVENT_LAUNCH_FAILED, "error", PRIVATE_TEXT, "successor launch failed"),
        (EVENT_FAILED, "error", "", "thread storage failed"),
        (EVENT_SETTINGS, "deny", PRIVATE_TEXT, "handoff failed"),
        (EVENT_SETTINGS, "deny", "not found", "not found"),
    ],
)
def test_failure_diagnostics_are_bounded_and_cannot_contain_thread_text(
    db, handoff, event, decision, reason, expected
):
    audit = AuditFake()
    Events(audit, db).emit(
        event,
        workspace_id="ws_1",
        handoff=handoff,
        decision=decision,
        reason=reason,
    )
    payload = db.rows[0]["payload"]
    assert payload["decision"] == decision
    assert payload["reason"] == audit.rows[0]["reason"] == expected
    assert len(payload["reason"]) <= 200
    assert PRIVATE_TEXT not in json.dumps([audit.rows, db.rows])


def test_unknown_event_is_not_persisted(db, caplog):
    audit = AuditFake()
    Events(audit, db).emit(PRIVATE_TEXT, workspace_id="ws_1")
    assert audit.rows == db.rows == []
    assert "Unknown handoff event" in caplog.text
    assert PRIVATE_TEXT not in caplog.text


@pytest.mark.parametrize(
    "broken_audit,broken_enqueue", [(True, False), (False, True), (True, True)]
)
def test_emit_isolates_database_failures(db, monkeypatch, caplog, broken_audit, broken_enqueue):
    audit = AuditFake()

    def broken(*args, **kwargs):
        raise RuntimeError(PRIVATE_TEXT)

    if broken_audit:
        monkeypatch.setattr(audit, "audit", broken)
    if broken_enqueue:
        monkeypatch.setattr(db, "enqueue", broken)
    notices = []
    emitter = Events(audit, db)
    emitter.outbox = SimpleNamespace(notify=lambda: notices.append(True))
    emitter.emit(EVENT_STORED, workspace_id="ws_1")
    assert len(audit.rows) == (0 if broken_audit else 1)
    assert len(db.rows) == (0 if broken_enqueue else 1)
    assert notices == ([] if broken_enqueue else [True])
    assert "failed" in caplog.text
    assert PRIVATE_TEXT not in caplog.text


@pytest.mark.parametrize("subscribed", [False, True])
def test_emit_notifies_only_when_rows_were_enqueued(db, subscribed):
    if not subscribed:
        db.hooks.clear()
    notices = []
    emitter = Events(AuditFake(), db)
    emitter.outbox = SimpleNamespace(notify=lambda: notices.append(True))
    emitter.emit(EVENT_STORED, workspace_id="ws_1")
    assert notices == ([True] if subscribed else [])


def test_broken_notification_does_not_escape_emit(db, caplog):
    def broken():
        raise RuntimeError(PRIVATE_TEXT)

    emitter = Events(AuditFake(), db)
    emitter.outbox = SimpleNamespace(notify=broken)
    emitter.emit(EVENT_STORED, workspace_id="ws_1")
    assert len(db.rows) == 1
    assert "notification failed" in caplog.text
    assert PRIVATE_TEXT not in caplog.text


def test_broken_handoff_does_not_escape_emit(db, caplog):
    class BrokenHandoff:
        @property
        def id(self):
            raise RuntimeError(PRIVATE_TEXT)

    Events(AuditFake(), db).emit(EVENT_STORED, workspace_id="ws_1", handoff=BrokenHandoff())
    assert db.rows == []
    assert "preparation failed" in caplog.text
    assert PRIVATE_TEXT not in caplog.text


def test_default_delivery_sends_exact_canonical_bytes_and_signature(db, handoff, receiver):
    db.hooks[0]["url"] = receiver.url
    handoff = replace(handoff, task_ref="task:日本語")
    Events(AuditFake(), db).emit(EVENT_STORED, workspace_id="ws_1", handoff=handoff)
    outbox = Outbox(db)
    assert outbox.deliver_once() == 1
    assert outbox.deliver_once() == 0
    assert db.delivered == [1]
    path, body, headers = receiver.received[0]
    assert path == "/wake?task=17"
    assert body == json.dumps(db.rows[0]["payload"], sort_keys=True, separators=(",", ":")).encode()
    assert headers["Content-Type"] == "application/json"
    assert headers["X-Commonplace-Event"] == EVENT_STORED
    assert headers["X-Commonplace-Delivery"] == "1"
    expected = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert hmac.compare_digest(headers["X-Commonplace-Signature"], expected)
    assert PRIVATE_TEXT.encode() not in body


@pytest.mark.parametrize("status", [200, 201, 204, 299])
def test_every_success_status_delivers(pending, status):
    outbox = Outbox(pending, post=lambda *args: status)
    assert outbox.deliver_once() == 1
    assert pending.delivered == [1]
    assert pending.retries == []


@pytest.mark.parametrize("status", [300, 301, 302, 307, 308, 400, 401, 404, 429, 500, 503, 599])
def test_other_statuses_retry(pending, status):
    outbox = Outbox(pending, post=lambda *args: status)
    assert outbox.deliver_once() == 1
    assert pending.delivered == []
    assert pending.retries == [(1, f"HTTP {status}", stamp(NOW + timedelta(seconds=1)), False)]


def test_five_backoff_delays_then_terminal_failure(pending):
    current = NOW
    outbox = Outbox(pending, post=lambda *args: 503)
    outbox.clock = lambda: current
    for delay in (1, 5, 30, 120, 600):
        assert outbox.deliver_once() == 1
        next_at = current + timedelta(seconds=delay)
        assert pending.retries[-1] == (1, "HTTP 503", stamp(next_at), False)
        assert outbox.deliver_once() == 0
        current = next_at - timedelta(seconds=1)
        assert outbox.deliver_once() == 0
        current = next_at
    assert outbox.deliver_once() == 1
    assert pending.retries[-1] == (1, "HTTP 503", stamp(current), True)
    assert pending.rows[0]["attempts"] == 6
    assert outbox.deliver_once() == 0


def test_retry_uses_clock_after_the_request_finishes(pending):
    current = NOW

    def post(*args):
        nonlocal current
        current += timedelta(seconds=4)
        return 503

    outbox = Outbox(pending, post=post)
    outbox.clock = lambda: current
    outbox.deliver_once()
    assert pending.retries[-1][2] == stamp(NOW + timedelta(seconds=5))


def test_successful_retry_keeps_delivery_id_payload_and_signature(pending):
    requests = []

    def post(*args):
        requests.append(args)
        return 503 if len(requests) == 1 else 204

    outbox = Outbox(pending, post=post)
    assert outbox.deliver_once() == 1
    outbox.clock = lambda: NOW + timedelta(seconds=1)
    assert outbox.deliver_once() == 1
    assert requests[0] == requests[1]
    assert pending.delivered == [1]
    assert outbox.deliver_once() == 0


@pytest.mark.parametrize("attempts", [0, 3, 5])
def test_gone_is_immediately_terminal(pending, attempts):
    pending.rows[0]["attempts"] = attempts
    assert Outbox(pending, post=lambda *args: 410).deliver_once() == 1
    assert pending.retries == [(1, "HTTP 410", stamp(), True)]


@pytest.mark.parametrize("error_type", [OSError, TimeoutError, RuntimeError])
def test_transport_errors_retry_without_recording_raw_error(pending, caplog, error_type):
    def broken(*args):
        raise error_type(PRIVATE_TEXT + SECRET)

    assert Outbox(pending, post=broken).deliver_once() == 1
    assert pending.retries == [
        (1, "webhook delivery failed", stamp(NOW + timedelta(seconds=1)), False)
    ]
    assert PRIVATE_TEXT not in repr(pending.retries) + caplog.text
    assert SECRET not in repr(pending.retries) + caplog.text


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
def test_invalid_urls_are_terminal_without_posting(pending, url):
    pending.rows[0]["url"] = url
    requests = []
    outbox = Outbox(pending, post=lambda *args: requests.append(args))
    assert outbox.deliver_once() == 1
    assert requests == []
    assert pending.retries == [(1, "invalid webhook URL", stamp(), True)]


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308, 410])
def test_default_http_handles_errors_without_following_redirects(db, receiver, status):
    receiver.reply_status = status
    db.hooks[0]["url"] = receiver.url
    Events(AuditFake(), db).emit(EVENT_STORED, workspace_id="ws_1")
    assert Outbox(db).deliver_once() == 1
    assert len(receiver.received) == 1
    assert receiver.received[0][0] == "/wake?task=17"
    assert db.retries[-1][3] == (status == 410)


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
            return SimpleNamespace(status=204, close=lambda: calls.append("response closed"))

        def close(self):
            calls.append("connection closed")

    name = "HTTPSConnection" if scheme == "https" else "HTTPConnection"
    monkeypatch.setattr(events_module.http.client, name, Connection)
    if failure:
        with pytest.raises(OSError):
            events_module._post(f"{scheme}://127.0.0.1:8000?x=1#fragment", b"{}", {})
    else:
        assert events_module._post(f"{scheme}://127.0.0.1:8000?x=1#fragment", b"{}", {}) == 204
        assert calls[-2] == "response closed"
    assert calls[0] == ("127.0.0.1", 8000, 5.0)
    assert calls[1] == ("POST", "/?x=1", {"body": b"{}", "headers": {}})
    assert calls[-1] == "connection closed"


def test_missing_secret_gives_up_without_posting(pending):
    pending.hooks[0]["secret"] = ""
    requests = []
    Outbox(pending, post=lambda *args: requests.append(args)).deliver_once()
    assert requests == []
    assert pending.retries == [(1, "webhook secret unavailable", stamp(), True)]


def test_secret_lookup_failure_is_retried(pending, monkeypatch):
    def broken(*args):
        raise RuntimeError(PRIVATE_TEXT)

    monkeypatch.setattr(pending, "webhook_secret", broken)
    Outbox(pending, post=lambda *args: pytest.fail("must not post without a secret")).deliver_once()
    assert pending.retries[0][1] == "webhook delivery failed"
    assert pending.retries[0][3] is False


def test_unserializable_payload_is_retried_without_posting(pending):
    pending.rows[0]["payload"]["at"] = object()
    outbox = Outbox(pending, post=lambda *args: pytest.fail("must not post invalid JSON"))
    assert outbox.deliver_once() == 1
    assert pending.retries[0][1] == "webhook delivery failed"


def test_failed_due_query_returns_zero_and_logs_no_raw_error(db, monkeypatch, caplog):
    def broken(*args, **kwargs):
        raise RuntimeError(PRIVATE_TEXT)

    monkeypatch.setattr(db, "due", broken)
    assert Outbox(db).deliver_once() == 0
    assert "query failed" in caplog.text
    assert PRIVATE_TEXT not in caplog.text


@pytest.mark.parametrize("failing_method,status", [("mark_delivered", 204), ("mark_retry", 503)])
def test_failed_row_acknowledgement_does_not_block_later_rows(
    pending, monkeypatch, caplog, failing_method, status
):
    Events(AuditFake(), pending).emit(EVENT_STORED, workspace_id="ws_1")
    original = getattr(pending, failing_method)

    def flaky(outbox_id, *args, **kwargs):
        if outbox_id == 1:
            raise RuntimeError(PRIVATE_TEXT)
        return original(outbox_id, *args, **kwargs)

    monkeypatch.setattr(pending, failing_method, flaky)
    assert Outbox(pending, post=lambda *args: status).deliver_once() == 2
    if status == 204:
        assert pending.delivered == [2]
    else:
        assert pending.retries[0][0] == 2
    assert "row processing failed" in caplog.text
    assert PRIVATE_TEXT not in caplog.text


def test_malformed_row_does_not_block_later_rows(pending, monkeypatch, caplog):
    row = dict(pending.rows[0])
    monkeypatch.setattr(pending, "due", lambda *args, **kwargs: [{"id": 99}, row])
    assert Outbox(pending, post=lambda *args: 204).deliver_once() == 2
    assert pending.delivered == [1]
    assert "row processing failed" in caplog.text


def test_due_batch_limit_is_fifty(db):
    emitter = Events(AuditFake(), db)
    for _ in range(51):
        emitter.emit(EVENT_STORED, workspace_id="ws_1")
    outbox = Outbox(db, post=lambda *args: 204)
    assert outbox.deliver_once() == 50
    assert outbox.deliver_once() == 1
    assert db.queries == [(stamp(), 50), (stamp(), 50)]


@pytest.mark.parametrize("poll_s", [0, -1, float("inf"), float("nan")])
def test_invalid_poll_period_is_rejected(db, poll_s):
    with pytest.raises(ValueError, match="poll_s"):
        Outbox(db, poll_s=poll_s)


def test_start_stop_are_idempotent_and_worker_can_restart(db):
    outbox = Outbox(db, poll_s=60)
    outbox.stop()
    try:
        outbox.start()
        assert db.queried.wait(timeout=1)
        first_thread = outbox._thread
        assert first_thread.daemon
        outbox.start()
        assert outbox._thread is first_thread
        outbox.stop()
        outbox.stop()
        assert not first_thread.is_alive()
        db.queried.clear()
        outbox.start()
        assert db.queried.wait(timeout=1)
        assert outbox._thread is not first_thread
    finally:
        outbox.stop()
    assert not outbox._thread.is_alive()


def test_emit_wakes_worker_before_long_poll_interval(db):
    outbox = Outbox(db, poll_s=60, post=lambda *args: 204)
    emitter = Events(AuditFake(), db)
    emitter.outbox = outbox
    try:
        outbox.start()
        assert db.queried.wait(timeout=1)
        emitter.emit(EVENT_STORED, workspace_id="ws_1")
        assert db.acknowledged.wait(timeout=1)
        assert db.delivered == [1]
    finally:
        outbox.stop()


def test_worker_polls_without_notification(db):
    outbox = Outbox(db, poll_s=0.01, post=lambda *args: 204)
    try:
        outbox.start()
        assert db.queried.wait(timeout=1)
        Events(AuditFake(), db).emit(EVENT_STORED, workspace_id="ws_1")
        assert db.acknowledged.wait(timeout=1)
    finally:
        outbox.stop()


def test_emit_never_waits_for_http_and_delivery_cannot_overlap(pending):
    entered = threading.Event()
    release = threading.Event()
    emitted = threading.Event()

    def post(*args):
        entered.set()
        assert release.wait(timeout=5)
        return 204

    outbox = Outbox(pending, poll_s=60, post=post)
    emitter = Events(AuditFake(), pending)
    emitter.outbox = outbox

    def emit():
        emitter.emit(EVENT_STORED, workspace_id="ws_1")
        emitted.set()

    emit_thread = threading.Thread(target=emit, daemon=True)
    try:
        outbox.start()
        assert entered.wait(timeout=1)
        # The worker is blocked in post; emit and another delivery pass return.
        emit_thread.start()
        assert emitted.wait(timeout=1)
        assert len(pending.rows) == 2
        assert outbox.deliver_once() == 0
    finally:
        release.set()
        outbox.stop()
        if emit_thread.ident is not None:
            emit_thread.join(timeout=1)


def test_stop_is_bounded_during_request_and_restart_cannot_duplicate_worker(pending):
    entered = threading.Event()
    release = threading.Event()
    Events(AuditFake(), pending).emit(EVENT_STORED, workspace_id="ws_1")

    def post(*args):
        entered.set()
        assert release.wait(timeout=6)
        return 204

    outbox = Outbox(pending, poll_s=60, post=post)
    try:
        outbox.start()
        assert entered.wait(timeout=1)
        first_thread = outbox._thread
        started = time.monotonic()
        outbox.stop()
        assert time.monotonic() - started < 2.5
        assert first_thread.is_alive()
        outbox.start()
        assert outbox._thread is first_thread
        release.set()
        first_thread.join(timeout=1)
        assert not first_thread.is_alive()
        assert pending.delivered == [1]
        pending.acknowledged.clear()
        outbox.start()
        assert pending.acknowledged.wait(timeout=1)
        assert pending.delivered == [1, 2]
    finally:
        release.set()
        outbox.stop()


def test_worker_can_stop_itself_without_joining_current_thread(pending):
    def post(*args):
        outbox.stop()
        return 204

    outbox = Outbox(pending, poll_s=60, post=post)
    try:
        outbox.start()
        assert pending.acknowledged.wait(timeout=1)
        outbox._thread.join(timeout=1)
        assert not outbox._thread.is_alive()
        assert pending.delivered == [1]
    finally:
        outbox.stop()


def test_polling_continues_after_unexpected_failure(db, monkeypatch, caplog):
    outbox = Outbox(db, poll_s=0.01)
    recovered = threading.Event()
    calls = []

    def flaky():
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError(PRIVATE_TEXT)
        recovered.set()

    monkeypatch.setattr(outbox, "deliver_once", flaky)
    try:
        outbox.start()
        assert recovered.wait(timeout=1)
    finally:
        outbox.stop()
    assert "polling failed" in caplog.text
    assert PRIVATE_TEXT not in caplog.text
