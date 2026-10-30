"""Adversarial transitions with real SQLite and independent loopback requests."""

import errno
import http.client
import json
import os
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from commonplace.control_db import ControlDB
from commonplace.gateway import Gateway
from commonplace.handoff import db as db_module, launchers, palace_mirror, transcripts
from commonplace.handoff.db import HandoffDB, HandoffDBError
from commonplace.handoff.events import Outbox
from commonplace.handoff.models import (
    EVENT_COMPLETED,
    EVENT_FAILED,
    EVENT_LAUNCH_FAILED,
    HandoffRequest,
    LauncherConfig,
    SettingRow,
    SuccessorRef,
    ThreadMeta,
)
from commonplace.handoff.wiring import install
from commonplace.policy import Policy


class _NoWorker:
    def get(self, workspace):
        raise AssertionError("These tests must not start a Memory worker")


class Harness:
    def __init__(self, root):
        self.root = root
        self.control = ControlDB(str(root / "control.sqlite3"))
        self.owner = self.control.create_user("owner@example.test")
        self.workspace = self.control.create_workspace("home", self.owner)
        _, self.token = self.control.create_token(
            workspace_ref=self.workspace,
            user_ref=self.owner,
            name="reviewer",
            client="test",
            policy=Policy.build(
                profile="full",
                scopes=["memory:read", "threads:read", "handoff:write"],
                read_wings=["*"],
                trust="trusted",
            ),
            actor=self.owner,
        )
        self._open()
        self.configure(LauncherConfig())

    def _open(self):
        self.gateway = Gateway(self.control, _NoWorker())
        self.ctx = install(self.gateway, self.root, start_outbox=False)
        self.ctx.service.call_tool = None
        self.server = self.gateway.build_server("127.0.0.1", 0)
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        self.thread.start()
        self.principal = self.control.authenticate_token(self.token)

    def configure(self, launcher):
        self.ctx.handoff_db.put_setting(
            SettingRow(self.workspace, "workspace", "", enabled=True, launcher=launcher),
            actor=self.owner,
        )

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.ctx.close()
        self.control.close()

    def restart(self):
        self.close()
        self.control = ControlDB(str(self.root / "control.sqlite3"))
        self._open()

    def request(self, path="/api/handoff", body=None, method="POST"):
        assert self.gateway.authenticate(f"Bearer {self.token}") is not None
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_address[1], timeout=10
        )
        try:
            connection.request(
                method,
                path,
                body=json.dumps(body).encode() if body is not None else None,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                },
            )
            reply = connection.getresponse()
            return reply.status, json.loads(reply.read())
        finally:
            connection.close()

    def events(self, event):
        return [row for row in self.control.audit_entries() if row["action"] == event]

    def rows(self, table):
        connection = self.ctx.store_for(self.workspace)._conn
        return [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")]


@pytest.fixture
def harness(tmp_path):
    instance = Harness(tmp_path)
    try:
        yield instance
    finally:
        instance.close()


def request(source="source", *, parent="", texts=("Verbatim goal", "Verbatim status")):
    return HandoffRequest(
        ThreadMeta(source, parent_handoff_id=parent),
        json.dumps(
            [
                {"role": "user" if index == 0 else "assistant", "content": text}
                for index, text in enumerate(texts)
            ]
        ),
        transcript_format="messages",
    )


def prepared(harness, state="stored"):
    body = request()
    row, _ = harness.ctx.handoff_db.begin(
        harness.workspace, "thread:source", body.meta, connection_id=harness.principal.connection_id
    )
    saved = harness.ctx.store_for(harness.workspace).store(
        harness.workspace,
        "source",
        transcripts.parse(body.transcript, "messages"),
        actor=harness.principal.actor,
    )
    row = harness.ctx.handoff_db.mark_stored(row.id, saved.id, "Saved document")
    if state in ("claimed", "launched"):
        assert harness.ctx.handoff_db.claim_launch(row.id)
    if state == "launched":
        row = harness.ctx.handoff_db.mark_launched(row.id)
    return row


def test_sixteen_real_http_requests_store_and_launch_once(harness):
    barrier = threading.Barrier(16)
    body = asdict(request())

    def send(_):
        barrier.wait(timeout=10)
        return harness.request(body=body)

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(send, range(16)))
    assert all(status == 200 for status, _ in results), results
    assert sum(result["launch"] for _, result in results) == 1
    assert len(harness.rows("threads")) == 1
    assert len(harness.ctx.handoff_db.list(harness.workspace)) == 1
    handoff_id = next(result["handoff"]["id"] for _, result in results if result["launch"])
    registration = {"agent_ref": "agent:successor", "thread_id": "successor"}
    with ThreadPoolExecutor(max_workers=16) as pool:
        registrations = list(
            pool.map(
                lambda _: harness.request(f"/api/handoffs/{handoff_id}/successor", registration),
                range(16),
            )
        )
    assert all(status == 200 for status, _ in registrations)
    assert harness.ctx.handoff_db.get(handoff_id).successor == SuccessorRef(
        "agent:successor", thread_id="successor"
    )
    assert len(harness.events(EVENT_COMPLETED)) == 1


def test_single_real_http_request(harness):
    status, result = harness.request(body=asdict(request()))
    assert status == 200, result
    assert result["launch"], result


@pytest.mark.parametrize("state", ["stored", "claimed", "launched"])
def test_restart_preserves_claim_and_explicit_recovery(harness, state):
    row = prepared(harness, state)
    harness.restart()
    result = harness.ctx.service.run(harness.principal, request())
    assert result.launch is (state == "stored")
    assert len(harness.rows("threads")) == 1
    if state != "stored":
        # A claim without registration is ambiguous. Never automatically spawn again.
        assert result.handoff.successor is None
        harness.ctx.service.report_launch_failure(harness.principal, row.id, "verified no launch")
        assert harness.ctx.service.relaunch(harness.principal, row.id).launch
        with pytest.raises(HandoffDBError):
            harness.ctx.service.relaunch(harness.principal, row.id)


def test_crash_after_thread_commit_before_mark_stored_can_resume(harness, monkeypatch):
    moment = datetime(2030, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(db_module, "_now", lambda: moment)
    body = request()
    row, _ = harness.ctx.handoff_db.begin(
        harness.workspace, "thread:source", body.meta, connection_id=harness.principal.connection_id
    )
    saved = harness.ctx.store_for(harness.workspace).store(
        harness.workspace,
        "source",
        transcripts.parse(body.transcript, "messages"),
        actor=harness.principal.actor,
    )
    harness.restart()
    moment += timedelta(seconds=601)
    result = harness.ctx.service.run(harness.principal, body)
    assert result.handoff.id == row.id
    assert len(harness.rows("threads")) == 1
    assert harness.ctx.store_for(harness.workspace).get(saved.id) is not None
    assert result.launch
    harness.ctx.service.register_successor(harness.principal, row.id, SuccessorRef("recovered"))
    assert not harness.ctx.service.run(harness.principal, body).launch
    assert len(harness.events(EVENT_COMPLETED)) == 1


@pytest.mark.parametrize("age", [0, 599, 600])
def test_pending_inside_lease_is_untouched_after_restart(harness, monkeypatch, age):
    moment = datetime(2030, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(db_module, "_now", lambda: moment)
    body = request()
    row, _ = harness.ctx.handoff_db.begin(
        harness.workspace, "thread:source", body.meta, connection_id=harness.principal.connection_id
    )
    harness.restart()
    moment += timedelta(seconds=age)
    result = harness.ctx.service.run(harness.principal, body)
    assert (result.action, result.reason, result.launch) == ("compact", "in progress", False)
    assert harness.ctx.handoff_db.get(row.id) == row
    assert not harness.rows("threads")


def test_only_one_connection_takes_over_expired_pending_lease(harness, monkeypatch):
    moment = datetime(2030, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(db_module, "_now", lambda: moment)
    body = request()
    row, _ = harness.ctx.handoff_db.begin(
        harness.workspace, "thread:source", body.meta, connection_id=harness.principal.connection_id
    )
    connections = [ControlDB(str(harness.root / "control.sqlite3")) for _ in range(4)]
    try:
        databases = [HandoffDB(control) for control in connections]
        moment += timedelta(seconds=601)
        barrier = threading.Barrier(4)

        def begin(database):
            barrier.wait(timeout=5)
            return database.begin(
                harness.workspace,
                "thread:source",
                body.meta,
                connection_id=harness.principal.connection_id,
            )

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(begin, databases))
        assert sum(created for _, created in results) == 1
        assert all(result.id == row.id for result, _ in results)
    finally:
        for control in connections:
            control.close()


def test_independent_database_connections_claim_and_register_once(harness):
    row = prepared(harness)
    connections = [ControlDB(str(harness.root / "control.sqlite3")) for _ in range(4)]
    try:
        databases = [HandoffDB(control) for control in connections]
        barrier = threading.Barrier(4)

        def claim(database):
            barrier.wait(timeout=5)
            return database.claim_launch(row.id)

        with ThreadPoolExecutor(max_workers=4) as pool:
            assert sum(pool.map(claim, databases)) == 1
        barrier.reset()

        def register(item):
            index, database = item
            barrier.wait(timeout=5)
            try:
                return database.set_successor(row.id, SuccessorRef(f"agent:{index}"))
            except HandoffDBError:
                return None

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(register, enumerate(databases)))
        assert sum(result is not None for result in results) == 1
        assert harness.ctx.handoff_db.get(row.id).state == "done"
    finally:
        for control in connections:
            control.close()


def test_longer_retry_keeps_original_snapshot_and_one_successor(harness):
    first = harness.ctx.service.run(harness.principal, request())
    original = harness.rows("messages")
    harness.ctx.service.register_successor(harness.principal, first.handoff.id, SuccessorRef("new"))
    harness.restart()
    status, retried = harness.request(
        body=asdict(request(texts=("Verbatim goal", "Verbatim status", "Agent kept going")))
    )
    assert status == 200
    assert retried["launch"] is False
    assert retried["handoff"]["stored_thread_id"] == first.handoff.stored_thread_id
    assert retried["handoff"]["successor"]["agent_ref"] == "new"
    assert harness.rows("messages") == original
    assert len(harness.rows("threads")) == 1


@pytest.mark.parametrize("boundary", ["parse", "messages", "chunks", "fts", "disk", "control"])
def test_storage_failure_rolls_back_preserves_existing_and_retries(harness, monkeypatch, boundary):
    existing = harness.ctx.service.run(harness.principal, request("existing"))
    baseline = {table: harness.rows(table) for table in ("threads", "messages", "chunks")}
    store = harness.ctx.store_for(harness.workspace)
    marker = harness.root / "existing-memory.txt"
    marker.write_bytes(b"existing Memory stays untouched\r\n")
    messages = request("failing", texts=("first", "second"))
    with monkeypatch.context() as patch:
        if boundary == "parse":

            def fail_parse(*args):
                raise ValueError("malformed transcript")

            patch.setattr(transcripts, "parse", fail_parse)
        elif boundary == "disk":

            def fail_store(*args, **kwargs):
                raise OSError(errno.ENOSPC, "disk full")

            patch.setattr(store, "store", fail_store)
        elif boundary == "control":

            def fail_begin(*args, **kwargs):
                raise sqlite3.OperationalError("database or disk is full")

            patch.setattr(harness.ctx.handoff_db, "begin", fail_begin)
        else:
            table = {"messages": "messages", "chunks": "chunks", "fts": "chunks_fts"}[boundary]
            # An actual abort after the thread header (and sometimes messages) was inserted.
            if boundary == "fts":

                class BrokenConnection:
                    def __getattr__(self, name):
                        return getattr(self.connection, name)

                    def __enter__(self):
                        self.connection.__enter__()
                        return self

                    def __exit__(self, *args):
                        return self.connection.__exit__(*args)

                    def executemany(self, sql, rows):
                        if sql.startswith("INSERT INTO chunks_fts"):
                            raise sqlite3.OperationalError("database or disk is full")
                        return self.connection.executemany(sql, rows)

                broken = BrokenConnection()
                broken.connection = store._conn
                patch.setattr(store, "_conn", broken)
            else:
                condition = "WHEN NEW.seq = 1" if boundary == "messages" else ""
                store._conn.execute(
                    f"CREATE TEMP TRIGGER fail_store BEFORE INSERT ON {table} {condition} "
                    "BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
                )
        try:
            result = harness.ctx.service.run(harness.principal, messages)
        finally:
            if boundary in ("messages", "chunks"):
                store._conn.execute("DROP TRIGGER fail_store")
    assert result.action == "compact"
    assert result.launch is False
    assert len(harness.events(EVENT_FAILED)) == 1
    assert {table: harness.rows(table) for table in baseline} == baseline
    assert harness.ctx.handoff_db.get(existing.handoff.id) == existing.handoff
    assert marker.read_bytes() == b"existing Memory stays untouched\r\n"
    retry = harness.ctx.service.run(harness.principal, messages)
    assert retry.launch
    assert len(harness.rows("threads")) == 2


def test_mirror_failure_is_best_effort_after_durable_storage(harness, monkeypatch):
    harness.ctx.service.call_tool = lambda *args: None

    def fail_mirror(*args):
        raise OSError(errno.ENOSPC, "mirror disk full")

    monkeypatch.setattr(palace_mirror, "mirror", fail_mirror)
    result = harness.ctx.service.run(harness.principal, request())
    assert result.launch
    assert len(harness.rows("threads")) == 1
    assert not harness.events(EVENT_FAILED)


@pytest.mark.parametrize("field", ["agent_ref", "thread_id", "url"])
def test_successor_conflict_compares_every_populated_field(harness, field):
    row = prepared(harness)
    ref = SuccessorRef("agent:first", "https://example.test/first", "first")
    harness.ctx.handoff_db.set_successor(row.id, ref)
    changed = asdict(ref)
    changed[field] += "-different"
    with pytest.raises(HandoffDBError):
        harness.ctx.handoff_db.set_successor(row.id, SuccessorRef(**changed))
    assert harness.ctx.handoff_db.get(row.id).successor == ref


def test_successor_empty_metadata_can_be_filled_once_without_completion_duplicate(harness):
    row = prepared(harness)
    harness.ctx.service.register_successor(harness.principal, row.id, SuccessorRef("agent:first"))
    ref = SuccessorRef("agent:first", "https://example.test/first", "first")
    result = harness.ctx.service.register_successor(harness.principal, row.id, ref)
    assert result.successor == ref
    assert harness.ctx.service.register_successor(harness.principal, row.id, ref) == result
    assert len(harness.events(EVENT_COMPLETED)) == 1


def test_registration_failure_after_external_launch_keeps_claim(harness, monkeypatch):
    harness.configure(LauncherConfig("webhook", url="http://127.0.0.1/launch", secret="secret"))
    launches = []

    def launched(*args, **kwargs):
        launches.append(SuccessorRef("external:already-running"))
        return launches[-1]

    def failed_registration(*args):
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(launchers, "launch", launched)
    with monkeypatch.context() as patch:
        patch.setattr(harness.ctx.handoff_db, "set_successor", failed_registration)
        result = harness.ctx.service.run(harness.principal, request())
    assert len(launches) == 1
    assert result.handoff.state == "launched"
    assert not harness.events(EVENT_LAUNCH_FAILED)
    harness.restart()
    retry = harness.ctx.service.run(harness.principal, request())
    assert retry.launch is False
    with pytest.raises(HandoffDBError):
        harness.ctx.service.relaunch(harness.principal, result.handoff.id)
    completed = harness.ctx.service.register_successor(
        harness.principal, result.handoff.id, launches[0]
    )
    assert completed.state == "done"
    assert len(launches) == 1


@pytest.mark.parametrize("kind", ["command", "webhook"])
def test_real_launcher_failure_then_concurrent_relaunch_once(harness, kind):
    mode = harness.root / "launch-mode"
    mode.write_text("fail")
    launches = harness.root / "launches"
    webhook_calls = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            webhook_calls.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            failed = mode.read_text() == "fail"
            if not failed:
                with launches.open("a") as output:
                    output.write("launched\n")
            data = json.dumps({"agent_ref": "webhook:successor"}).encode()
            self.send_response(503 if failed else 200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    receiver = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    receiver_thread = threading.Thread(
        target=receiver.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    receiver_thread.start()
    try:
        if kind == "command":
            script = harness.root / "launcher.py"
            script.write_text(
                "import pathlib, sys\n"
                "if pathlib.Path(sys.argv[1]).read_text() == 'fail': sys.exit(7)\n"
                "with pathlib.Path(sys.argv[2]).open('a') as out: out.write('launched\\n')\n"
            )
            config = LauncherConfig(
                "command", argv=(sys.executable, str(script), str(mode), str(launches))
            )
        else:
            config = LauncherConfig(
                "webhook",
                url=f"http://127.0.0.1:{receiver.server_address[1]}/launch",
                secret="local",
            )
        harness.configure(config)
        result = harness.ctx.service.run(harness.principal, request())
        assert result.handoff.state == "launch_failed"
        assert result.handoff.successor is None
        assert not launches.exists()
        assert len(harness.events(EVENT_LAUNCH_FAILED)) == 1
        assert not harness.ctx.service.run(harness.principal, request()).launch
        mode.write_text("succeed")
        # A restarted gateway reconstructs the stored prompt and uses a durable claim.
        harness.restart()
        barrier = threading.Barrier(2)

        def relaunch(_):
            barrier.wait(timeout=5)
            return harness.request(f"/api/handoffs/{result.handoff.id}/relaunch", {})

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(relaunch, range(2)))
        assert sorted(status for status, _ in results) == [200, 409], results
        row = harness.ctx.handoff_db.get(result.handoff.id)
        assert row.state == "done"
        assert row.successor is not None
        assert launches.read_text() == "launched\n"
        assert len(harness.events(EVENT_COMPLETED)) == 1
        assert len(harness.rows("threads")) == 1
        if kind == "webhook":
            assert len(webhook_calls) == 2
            assert webhook_calls[0]["handoff_id"] == webhook_calls[1]["handoff_id"]
    finally:
        receiver.shutdown()
        receiver.server_close()
        receiver_thread.join(timeout=5)


def test_three_generations_recall_original_and_reject_invalid_parents(harness):
    previous = ""
    stored_ids = []
    for generation in range(3):
        result = harness.ctx.service.run(
            harness.principal, request(f"generation-{generation}", parent=previous)
        )
        assert result.launch
        harness.ctx.service.register_successor(
            harness.principal, result.handoff.id, SuccessorRef(f"agent:{generation + 1}")
        )
        previous = result.handoff.id
        stored_ids.append(result.handoff.stored_thread_id)
    assert [
        row.id for row in harness.ctx.recall.chain(harness.principal, stored_ids[-1])
    ] == stored_ids[::-1]
    other = harness.control.create_workspace("other", harness.owner)
    foreign, _ = harness.ctx.handoff_db.begin(
        other, "foreign", ThreadMeta("foreign"), connection_id=harness.principal.connection_id
    )
    baseline = harness.rows("threads")
    for parent in ("hof_nonexistent", foreign.id):
        status, body = harness.request(body=asdict(request("forged", parent=parent)))
        assert (status, body) == (404, {"error": "not found"})
    # A retry that forges its own parent cannot mutate the existing immutable chain.
    status, retried = harness.request(body=asdict(request("generation-2", parent=previous)))
    assert status == 200
    assert retried["launch"] is False
    assert harness.rows("threads") == baseline
    assert [
        row.id for row in harness.ctx.recall.chain(harness.principal, stored_ids[-1])
    ] == stored_ids[::-1]


def test_forged_storage_cycle_cannot_loop_recall_or_handoff_doc(harness):
    first = harness.ctx.service.run(harness.principal, request("first"))
    second = harness.ctx.service.run(harness.principal, request("second", parent=first.handoff.id))
    store = harness.ctx.store_for(harness.workspace)
    # Simulate corrupt legacy data; public retries cannot rewrite stored parents.
    store._conn.execute(
        "UPDATE threads SET parent_stored_id = ? WHERE id = ?",
        (second.handoff.stored_thread_id, first.handoff.stored_thread_id),
    )
    chain = harness.ctx.recall.chain(harness.principal, second.handoff.stored_thread_id)
    assert [row.id for row in chain] == [
        second.handoff.stored_thread_id,
        first.handoff.stored_thread_id,
    ]
    third = harness.ctx.service.run(harness.principal, request("third", parent=second.handoff.id))
    assert third.launch
    assert len(harness.ctx.recall.chain(harness.principal, third.handoff.stored_thread_id)) == 3


def test_outbox_retry_survives_restart_and_delivered_marker_prevents_repeat(harness):
    harness.ctx.handoff_db.add_webhook(
        harness.workspace, "http://127.0.0.1/wake", "local", (EVENT_COMPLETED,), actor=harness.owner
    )
    result = harness.ctx.service.run(harness.principal, request())
    harness.ctx.service.register_successor(
        harness.principal, result.handoff.id, SuccessorRef("new")
    )
    deliveries = []

    def post(url, body, headers):
        deliveries.append((body, headers))
        return 503 if len(deliveries) == 1 else 204

    outbox = Outbox(harness.ctx.handoff_db, post=post)
    assert outbox.deliver_once() == 1
    harness.restart()
    outbox = Outbox(harness.ctx.handoff_db, post=post)
    outbox.clock = lambda: datetime(2100, 1, 1, tzinfo=timezone.utc)
    assert outbox.deliver_once() == 1
    assert deliveries[0] == deliveries[1]
    harness.restart()
    assert Outbox(harness.ctx.handoff_db, post=post).deliver_once() == 0
    assert len(deliveries) == 2


def test_external_acceptance_before_ack_can_redeliver_same_id(harness, monkeypatch):
    harness.ctx.handoff_db.add_webhook(
        harness.workspace, "http://127.0.0.1/wake", "local", (EVENT_COMPLETED,), actor=harness.owner
    )
    harness.ctx.handoff_db.enqueue(harness.workspace, EVENT_COMPLETED, {"event": EVENT_COMPLETED})
    deliveries = []

    def post(url, body, headers):
        deliveries.append(headers["X-Commonplace-Delivery"])
        return 200

    def fail_ack(*args):
        raise sqlite3.OperationalError("injected acknowledgement failure")

    with monkeypatch.context() as patch:
        patch.setattr(harness.ctx.handoff_db, "mark_delivered", fail_ack)
        assert Outbox(harness.ctx.handoff_db, post=post).deliver_once() == 1
    harness.restart()
    assert Outbox(harness.ctx.handoff_db, post=post).deliver_once() == 1
    assert len(deliveries) == 2
    assert deliveries[0] == deliveries[1]  # Receiver can deduplicate the stable delivery id.


def test_completion_crash_window_is_preserved_without_duplicate_successor(harness):
    row = prepared(harness)
    ref = SuccessorRef("already:running")
    # Process died after set_successor committed and before Events.emit began.
    harness.ctx.handoff_db.set_successor(row.id, ref)
    harness.restart()
    assert harness.ctx.service.register_successor(harness.principal, row.id, ref).successor == ref
    assert not harness.events(
        EVENT_COMPLETED
    )  # Known durable event gap, per orchestration decision.
    assert not harness.ctx.service.run(harness.principal, request()).launch


@pytest.mark.skipif(os.name != "posix", reason="The hook's fcntl lock is POSIX only")
def test_two_real_hook_processes_share_lock_and_server_claim(harness, tmp_path):
    home = tmp_path / "hook-home"
    settings_dir = home / ".commonplace"
    settings_dir.mkdir(parents=True)
    launches = tmp_path / "hook-launches"
    script = tmp_path / "successor.py"
    script.write_text(
        "import pathlib, sys\n"
        "with pathlib.Path(sys.argv[1]).open('a') as out: out.write('launched\\n')\n"
    )
    (settings_dir / "handoff-client.json").write_text(
        json.dumps({"successor_argv": [sys.executable, str(script), str(launches)]})
    )
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        json.dumps({"type": "user", "message": {"role": "user", "content": "Exact original"}})
        + "\n"
    )
    event = json.dumps(
        {
            "session_id": "hook-session",
            "transcript_path": str(transcript),
            "cwd": str(tmp_path),
            "hook_event_name": "PreCompact",
        }
    )
    environment = dict(os.environ)
    environment.update(
        HOME=str(home),
        COMMONPLACE_HANDOFF="on",
        COMMONPLACE_TOKEN=harness.token,
        COMMONPLACE_URL=f"http://127.0.0.1:{harness.server.server_address[1]}",
    )
    for key in ("COMMONPLACE_AGENT", "COMMONPLACE_TASK", "COMMONPLACE_HANDOFF_ID"):
        environment.pop(key, None)
    entered, release = threading.Event(), threading.Event()
    route = harness.gateway.routes[("POST", "/api/handoff")]

    def blocked(handler):
        entered.set()
        assert release.wait(timeout=10)
        route(handler)

    harness.gateway.routes[("POST", "/api/handoff")] = blocked
    processes = []
    try:
        first = subprocess.Popen(
            [sys.executable, "-m", "commonplace.handoff.hook", "--client", "claude-code"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )
        processes.append(first)
        first.stdin.write(event)
        first.stdin.close()
        first.stdin = None
        assert entered.wait(timeout=10)
        second = subprocess.Popen(
            [sys.executable, "-m", "commonplace.handoff.hook", "--client", "claude-code"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )
        processes.append(second)
        output, error = second.communicate(event, timeout=10)
        assert (second.returncode, output, error) == (0, "", "")
        release.set()
        output, error = first.communicate(timeout=10)
        assert first.returncode == 0, error
        assert json.loads(output)["continue"] is False
        assert launches.read_text() == "launched\n"
        assert len(harness.rows("threads")) == 1
        assert len(harness.events(EVENT_COMPLETED)) == 1
        status, retry = harness.request(body=asdict(request("hook-session")))
        assert status == 200
        assert retry["launch"] is False
    finally:
        release.set()
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
