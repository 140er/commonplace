"""Service state transitions using only the shared dependency contracts."""

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import pytest

from commonplace.handoff import service
from commonplace.handoff.db import HandoffDBError
from commonplace.handoff.models import (
    EVENT_COMPLETED,
    EVENT_FAILED,
    EVENT_LAUNCH_FAILED,
    EVENT_STARTED,
    EVENT_STORED,
    Handoff,
    HandoffDoc,
    HandoffRequest,
    HandoffSettings,
    LauncherConfig,
    StoredThread,
    SuccessorRef,
    ThreadMessage,
    ThreadMeta,
)
from commonplace.handoff.thread_store import Chunk, ThreadConflict


class FakeDB:
    def __init__(self, control, trace):
        self.lock = control._lock
        self.trace = trace
        self.rows = {}
        self.keys = {}
        self.claims = set()
        self.created = 0

    def by_key(self, workspace_id, idempotency_key):
        with self.lock:
            found = self.keys.get((workspace_id, idempotency_key))
            return self.rows[found] if found else None

    def begin(self, workspace_id, idempotency_key, meta, *, connection_id):
        with self.lock:
            self.trace.append("begin")
            key = workspace_id, idempotency_key
            if key in self.keys:
                row = self.rows[self.keys[key]]
                if row.state != "failed":
                    return row, False
                self.created += 1
                return self.update(row.id, state="pending", error=""), True
            row = Handoff(
                id=f"hof_{len(self.rows) + 1}",
                workspace_id=workspace_id,
                idempotency_key=idempotency_key,
                source_thread_id=meta.thread_id,
                state="pending",
                agent=meta.agent,
                task_ref=meta.task_ref,
                branch=meta.branch,
                pr_url=meta.pr_url,
                cwd=meta.cwd,
                parent_handoff_id=meta.parent_handoff_id,
                connection_id=connection_id,
            )
            self.rows[row.id] = row
            self.keys[key] = row.id
            self.created += 1
            return row, True

    def update(self, handoff_id, **changes):
        with self.lock:
            row = replace(self.rows[handoff_id], **changes)
            self.rows[handoff_id] = row
            return row

    def mark_stored(self, handoff_id, stored_thread_id, doc_markdown):
        self.trace.append("mark_stored")
        return self.update(
            handoff_id, state="stored", stored_thread_id=stored_thread_id, doc_markdown=doc_markdown
        )

    def mark_failed(self, handoff_id, error):
        self.trace.append("mark_failed")
        return self.update(handoff_id, state="failed", error=error)

    def claim_launch(self, handoff_id):
        with self.lock:
            self.trace.append("claim")
            if handoff_id in self.claims:
                return False
            assert self.rows[handoff_id].state == "stored"
            self.claims.add(handoff_id)
            return True

    def mark_launched(self, handoff_id):
        self.trace.append("mark_launched")
        return self.update(handoff_id, state="launched")

    def mark_launch_failed(self, handoff_id, error):
        self.trace.append("mark_launch_failed")
        return self.update(handoff_id, state="launch_failed", error=error)

    def release_launch(self, handoff_id, *, actor):
        with self.lock:
            self.trace.append(("release", actor))
            if self.rows[handoff_id].state != "launch_failed":
                raise HandoffDBError("handoff is not ready to relaunch")
            self.claims.discard(handoff_id)
            return self.update(handoff_id, state="stored", error="")

    def set_successor(self, handoff_id, ref):
        with self.lock:
            self.trace.append("set_successor")
            row = self.rows[handoff_id]
            if row.successor is not None:
                if row.successor != ref:
                    raise HandoffDBError("a successor is already registered")
                return row
            if row.state not in ("stored", "launched"):
                raise HandoffDBError("handoff is not ready for a successor")
            return self.update(handoff_id, state="done", successor=ref)

    def get(self, handoff_id):
        with self.lock:
            return self.rows.get(handoff_id)


class FakeStore:
    def __init__(self, trace):
        self.trace = trace
        self.rows = {}
        self.contents = {}
        self.parents = {}
        self.writes = []
        self.lock = threading.RLock()
        self.on_store = None

    def store(self, workspace_id, source_thread_id, messages, *, actor, parent_stored_id=""):
        self.trace.append("store")
        if self.on_store:
            self.on_store()
        with self.lock:
            self.writes.append((workspace_id, source_thread_id, messages, actor, parent_stored_id))
            for row in self.rows.values():
                if row.workspace_id == workspace_id and row.source_thread_id == source_thread_id:
                    if self.contents[row.id] != messages:
                        raise ThreadConflict("different thread content")
                    return row
            row = StoredThread(
                id=f"thr_{len(self.rows) + 1}",
                workspace_id=workspace_id,
                source_thread_id=source_thread_id,
                message_count=len(messages),
                chunk_count=1,
                sha256="digest",
                created_by=actor,
            )
            self.rows[row.id] = row
            self.contents[row.id] = list(messages)
            self.parents[row.id] = parent_stored_id
            return row

    def get(self, stored_id):
        return self.rows.get(stored_id)

    def parent_of(self, stored_id):
        return self.parents.get(stored_id) or None

    def messages(self, stored_id, start=0, end=None):
        return [
            message
            for message in self.contents[stored_id]
            if message.seq >= start and (end is None or message.seq <= end)
        ]


class FakeSettings:
    def __init__(self, trace):
        self.trace = trace
        self.current = HandoffSettings(enabled=True)
        self.calls = []

    def effective(self, workspace_id, agent="", task_ref=""):
        self.trace.append("settings")
        self.calls.append((workspace_id, agent, task_ref))
        return self.current


class FakeEvents:
    def __init__(self, trace):
        self.trace = trace
        self.calls = []
        self.delivered = []
        self.fail_on = ""

    def emit(
        self, event, *, workspace_id, handoff=None, principal=None, decision="ok", reason="", **ids
    ):
        self.trace.append(event)
        self.calls.append((event, workspace_id, handoff, principal, decision, reason, ids))
        if event == self.fail_on:
            raise RuntimeError("private event content")
        self.delivered.append(event)

    def count(self, event):
        return sum(call[0] == event for call in self.calls)


def make_request(thread_id="source-1", **changes):
    return replace(
        HandoffRequest(
            ThreadMeta(thread_id, task_ref="#142", branch="work", cwd="/task"),
            '[{"role":"user","content":"  Private goal\nunchanged  "}]',
            transcript_format="messages",
            note="  private note\n",
        ),
        **changes,
    )


@pytest.fixture
def rig(monkeypatch, tmp_path):
    trace = []
    control = SimpleNamespace(_lock=threading.RLock())
    value = SimpleNamespace(
        trace=trace,
        control=control,
        db=FakeDB(control, trace),
        store=FakeStore(trace),
        settings=FakeSettings(trace),
        events=FakeEvents(trace),
        principal=SimpleNamespace(
            workspace_id="ws_1",
            connection_id="conn_1",
            connection="agent-1",
            role="member",
            actor="conn:agent-1",
        ),
        parse_calls=[],
        mirror_calls=[],
        doc_calls=[],
        launch_calls=[],
        successor=None,
        launch_error=None,
        prompt_dir=str(tmp_path),
    )

    def parse(text, fmt="auto"):
        trace.append("parse")
        value.parse_calls.append((text, fmt))
        return [
            ThreadMessage(0, "user", "message", "  Private goal\nunchanged  ", text),
            ThreadMessage(1, "assistant", "message", "exact status\n", "raw status"),
        ]

    def chunk_messages(messages, max_chars=4000):
        trace.append("chunks")
        return [Chunk(0, 0, len(messages) - 1, "chunk text")]

    def mirror(call_tool, principal, stored, chunks):
        trace.append("mirror")
        value.mirror_calls.append((call_tool, principal, stored, chunks))
        return ["memory-id"]

    def build_doc(messages, meta, *, note="", stored_thread_id="", handoff_id="", chain=()):
        trace.append("build")
        value.doc_calls.append((messages, meta, note, stored_thread_id, handoff_id, chain))
        return HandoffDoc(
            goal=messages[0].text,
            status=messages[-1].text,
            task_ref=meta.task_ref,
            branch=meta.branch,
            pr_url=meta.pr_url,
            note=note,
            stored_thread_id=stored_thread_id,
            handoff_id=handoff_id,
            chain=chain,
        )

    def render_markdown(doc):
        trace.append("render")
        return json.dumps(doc.__dict__, ensure_ascii=False)

    def successor_prompt(doc):
        trace.append("prompt")
        return f"continue {doc.stored_thread_id}: {doc.goal}\n{doc.note}"

    def launch(config, handoff, prompt, *, prompt_dir):
        trace.append("launch")
        value.launch_calls.append((config, handoff, prompt, prompt_dir))
        if value.launch_error:
            raise value.launch_error
        return value.successor

    def store_for(workspace_id):
        trace.append("store_for")
        assert workspace_id == value.principal.workspace_id
        return value.store

    monkeypatch.setattr(service.transcripts, "parse", parse)
    monkeypatch.setattr(service.thread_store, "chunk_messages", chunk_messages)
    monkeypatch.setattr(service.palace_mirror, "mirror", mirror)
    monkeypatch.setattr(service.writer, "build_doc", build_doc)
    monkeypatch.setattr(service.writer, "render_markdown", render_markdown)
    monkeypatch.setattr(service.writer, "successor_prompt", successor_prompt)
    monkeypatch.setattr(service.launchers, "launch", launch)
    value.new_service = lambda: service.HandoffService(
        control_db=control,
        handoff_db=value.db,
        settings=value.settings,
        store_for=store_for,
        events=value.events,
        call_tool=object(),
        prompt_dir=value.prompt_dir,
    )
    value.service = value.new_service()
    return value


def test_off_has_no_side_effects(rig):
    rig.settings.current = HandoffSettings()
    result = rig.service.run(rig.principal, make_request())
    assert (result.action, result.reason, result.handoff, result.launch) == (
        "compact",
        "off",
        None,
        False,
    )
    assert rig.trace == ["settings"]
    assert not rig.db.rows and not rig.store.writes and not rig.events.calls


@pytest.mark.parametrize("thread_id", ["", " ", "\n"])
def test_empty_thread_id_is_rejected_even_when_off(rig, thread_id):
    rig.settings.current = HandoffSettings()
    with pytest.raises(ValueError, match="thread_id"):
        rig.service.run(rig.principal, make_request(thread_id))
    assert rig.trace == []


@pytest.mark.parametrize("agent", ["", "named-agent"])
@pytest.mark.parametrize("key", ["", "explicit-key"])
def test_enabled_pipeline_in_order_and_verbatim(rig, agent, key):
    request = make_request(idempotency_key=key)
    request = replace(request, meta=replace(request.meta, agent=agent))
    result = rig.service.run(rig.principal, request)
    assert (result.action, result.launch, result.handoff.state) == ("handoff", True, "launched")
    assert result.handoff.idempotency_key == (key or "thread:source-1")
    assert rig.settings.calls == [("ws_1", agent or "agent-1", "#142")]
    assert rig.parse_calls == [(request.transcript, "messages")]
    write = rig.store.writes[0]
    assert write[:2] == ("ws_1", "source-1")
    assert write[2][0].raw == request.transcript
    assert write[2][0].text == "  Private goal\nunchanged  "
    assert write[3:] == ("conn:agent-1", "")
    assert rig.doc_calls[0] == (
        write[2],
        request.meta,
        request.note,
        "thr_1",
        result.handoff.id,
        (),
    )
    assert rig.mirror_calls[0][2].id == "thr_1"
    assert request.note in result.prompt
    assert rig.launch_calls[0][2:] == (result.prompt, rig.prompt_dir)
    assert rig.trace == [
        "settings",
        "begin",
        EVENT_STARTED,
        "parse",
        "store_for",
        "store",
        "chunks",
        "mirror",
        "build",
        "render",
        "mark_stored",
        EVENT_STORED,
        "claim",
        "prompt",
        "mark_launched",
        "launch",
    ]


@pytest.mark.parametrize("state", ["launched", "done", "launch_failed"])
def test_retry_after_success_does_not_store_or_launch(rig, state):
    first = rig.service.run(rig.principal, make_request())
    expected = rig.db.update(first.handoff.id, state=state)
    second = rig.service.run(rig.principal, make_request())
    assert (second.action, second.handoff, second.launch, second.prompt) == (
        "handoff",
        expected,
        False,
        "",
    )
    assert len(rig.store.writes) == len(rig.launch_calls) == rig.db.created == 1
    assert rig.events.count(EVENT_STARTED) == rig.events.count(EVENT_STORED) == 1


def test_pending_is_compact_in_progress(rig):
    row, _ = rig.db.begin("ws_1", "thread:source-1", make_request().meta, connection_id="conn_1")
    result = rig.service.run(rig.principal, make_request())
    assert (result.action, result.handoff, result.reason, result.launch) == (
        "compact",
        row,
        "in progress",
        False,
    )
    assert not rig.store.writes and not rig.events.calls and not rig.db.claims


def test_stored_without_a_claim_is_retryable_and_keeps_its_snapshot(rig, monkeypatch):
    claim = rig.db.claim_launch
    monkeypatch.setattr(rig.db, "claim_launch", lambda handoff_id: False)
    first = rig.service.run(rig.principal, make_request())
    assert first.handoff.state == "stored" and not first.launch
    rig.settings.current = HandoffSettings(enabled=False, launcher=LauncherConfig(kind="command"))
    monkeypatch.setattr(rig.db, "claim_launch", claim)
    result = rig.service.run(rig.principal, make_request(note="replacement note"))
    assert result.launch and result.handoff.id == first.handoff.id
    assert "replacement note" not in result.prompt
    assert make_request().note in result.prompt
    assert rig.launch_calls[0][0].kind == "client"
    assert len(rig.store.writes) == len(rig.doc_calls) == 1


def test_snapshot_survives_toggle_change_during_storage(rig):
    rig.store.on_store = lambda: setattr(
        rig.settings,
        "current",
        HandoffSettings(enabled=False, launcher=LauncherConfig(kind="command")),
    )
    result = rig.service.run(rig.principal, make_request())
    assert result.launch and rig.launch_calls[0][0].kind == "client"
    assert len(rig.settings.calls) == 1


def test_eight_concurrent_runs_store_and_launch_once(rig):
    entered = threading.Event()
    release = threading.Event()
    other_requests_done = threading.Event()
    barrier = threading.Barrier(8)
    counter_lock = threading.Lock()
    count = 0

    def block_store():
        entered.set()
        assert release.wait(10)

    def run():
        nonlocal count
        barrier.wait(timeout=10)
        result = rig.service.run(rig.principal, make_request())
        with counter_lock:
            count += 1
            if count == 7:
                other_requests_done.set()
        return result

    rig.store.on_store = block_store
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(run) for _ in range(8)]
        try:
            assert entered.wait(10)
            assert other_requests_done.wait(10)
        finally:
            release.set()
        results = [future.result(timeout=10) for future in futures]
    assert sum(result.launch for result in results) == 1
    assert sum(result.reason == "in progress" for result in results) == 7
    assert len(rig.store.writes) == len(rig.launch_calls) == rig.db.created == 1
    assert rig.events.count(EVENT_STARTED) == rig.events.count(EVENT_STORED) == 1


def test_claim_is_atomic_across_service_instances(rig, monkeypatch):
    claim = rig.db.claim_launch
    monkeypatch.setattr(rig.db, "claim_launch", lambda handoff_id: False)
    rig.service.run(rig.principal, make_request())
    monkeypatch.setattr(rig.db, "claim_launch", claim)
    barrier = threading.Barrier(8)

    def run():
        other_service = rig.new_service()
        barrier.wait(timeout=10)
        return other_service.run(rig.principal, make_request())

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: run(), range(8)))
    assert all(result.action == "handoff" for result in results)
    assert sum(result.launch for result in results) == 1
    assert len(rig.store.writes) == len(rig.launch_calls) == 1


@pytest.mark.parametrize(
    "stage", ["parse", "store_for", "parent", "store", "chain", "build", "render", "mark_stored"]
)
@pytest.mark.parametrize(
    "error_type", [ValueError, ThreadConflict, sqlite3.OperationalError, RuntimeError]
)
def test_storage_failures_compact_without_claim_or_text_leak(rig, monkeypatch, stage, error_type):
    def fail(*args, **kwargs):
        raise error_type("PRIVATE TRANSCRIPT AND NOTE")

    targets = {
        "parse": (service.transcripts, "parse"),
        "store_for": (rig.service, "store_for"),
        "parent": (rig.db, "get"),
        "store": (rig.store, "store"),
        "chain": (rig.store, "get"),
        "build": (service.writer, "build_doc"),
        "render": (service.writer, "render_markdown"),
        "mark_stored": (rig.db, "mark_stored"),
    }
    request = make_request()
    if stage in ("parent", "chain"):
        parent, _ = rig.db.begin("ws_1", "parent", ThreadMeta("older"), connection_id="conn_1")
        rig.db.update(parent.id, state="stored", stored_thread_id="older-thread")
        request = replace(request, meta=replace(request.meta, parent_handoff_id=parent.id))
    monkeypatch.setattr(*targets[stage], fail)
    result = rig.service.run(rig.principal, request)
    assert result.action == "compact" and not result.launch
    assert result.handoff.state == "failed"
    assert rig.events.count(EVENT_FAILED) == 1
    failure = rig.events.calls[-1]
    assert failure[4] == "error" and failure[5] == result.reason
    assert "PRIVATE" not in result.reason and "PRIVATE" not in result.handoff.error
    assert "claim" not in rig.trace and not rig.launch_calls


def test_retry_after_storage_failure_reuses_row(rig, monkeypatch):
    parse = service.transcripts.parse

    def fail(text, fmt="auto"):
        raise ValueError("private text")

    monkeypatch.setattr(service.transcripts, "parse", fail)
    first = rig.service.run(rig.principal, make_request())
    monkeypatch.setattr(service.transcripts, "parse", parse)
    second = rig.service.run(rig.principal, make_request())
    assert first.action == "compact" and second.action == "handoff" and second.launch
    assert first.handoff.id == second.handoff.id
    assert rig.db.created == 2 and len(rig.db.rows) == len(rig.store.writes) == 1
    assert rig.events.count(EVENT_STARTED) == 2
    assert rig.events.count(EVENT_FAILED) == rig.events.count(EVENT_STORED) == 1


@pytest.mark.parametrize("break_mark_failed", [False, True])
def test_failure_reporting_errors_do_not_escape(rig, monkeypatch, break_mark_failed):
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("private database value")

    monkeypatch.setattr(service.transcripts, "parse", fail)
    if break_mark_failed:
        monkeypatch.setattr(rig.db, "mark_failed", fail)
    rig.events.fail_on = EVENT_FAILED
    result = rig.service.run(rig.principal, make_request())
    assert result.action == "compact" and not result.launch
    assert rig.events.count(EVENT_FAILED) == 1 and "claim" not in rig.trace


def test_begin_database_failure_compacts_and_emits_safe_event(rig, monkeypatch):
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("private data")

    monkeypatch.setattr(rig.db, "begin", fail)
    result = rig.service.run(rig.principal, make_request())
    assert result.action == "compact" and result.handoff is None
    assert rig.events.calls[-1][0] == EVENT_FAILED
    assert rig.events.calls[-1][4:6] == ("error", "thread storage failed")


@pytest.mark.parametrize("stage", ["chunks", "mirror"])
def test_mirror_failures_are_best_effort(rig, monkeypatch, stage):
    def fail(*args, **kwargs):
        raise RuntimeError("private mirror content")

    target = (
        (service.thread_store, "chunk_messages")
        if stage == "chunks"
        else (service.palace_mirror, "mirror")
    )
    monkeypatch.setattr(*target, fail)
    result = rig.service.run(rig.principal, make_request())
    assert result.action == "handoff" and result.launch
    assert rig.events.count(EVENT_STORED) == 1 and rig.events.count(EVENT_FAILED) == 0


def test_optional_mirror_is_skipped(rig):
    rig.service.call_tool = None
    result = rig.service.run(rig.principal, make_request())
    assert result.launch and "chunks" not in rig.trace and "mirror" not in rig.trace


@pytest.mark.parametrize("event", [EVENT_STARTED, EVENT_STORED, EVENT_COMPLETED])
def test_event_errors_do_not_undo_saved_threads(rig, event):
    rig.events.fail_on = event
    if event == EVENT_COMPLETED:
        rig.settings.current = HandoffSettings(
            enabled=True, launcher=LauncherConfig(kind="webhook")
        )
        rig.successor = SuccessorRef("successor")
    result = rig.service.run(rig.principal, make_request())
    assert result.action == "handoff" and result.handoff.stored_thread_id
    assert result.handoff.state == ("done" if event == EVENT_COMPLETED else "launched")
    assert rig.events.count(EVENT_FAILED) == 0


@pytest.mark.parametrize("kind", ["command", "webhook"])
def test_server_launcher_registers_successor_once(rig, kind):
    rig.settings.current = HandoffSettings(enabled=True, launcher=LauncherConfig(kind=kind))
    rig.successor = SuccessorRef("new-agent", "http://localhost/agent", "successor-thread")
    result = rig.service.run(rig.principal, make_request())
    assert result.action == "handoff" and not result.launch and result.prompt == ""
    assert result.handoff.state == "done" and result.handoff.successor == rig.successor
    repeated = rig.service.register_successor(rig.principal, result.handoff.id, rig.successor)
    assert repeated == result.handoff
    assert rig.events.count(EVENT_COMPLETED) == 1


def test_detached_command_without_ref_stays_launched(rig):
    rig.settings.current = HandoffSettings(enabled=True, launcher=LauncherConfig(kind="command"))
    result = rig.service.run(rig.principal, make_request())
    assert result.handoff.state == "launched" and not result.launch
    assert rig.events.count(EVENT_COMPLETED) == 0


@pytest.mark.parametrize("stage", ["prompt", "mark_launched", "launch", "set_successor"])
def test_launch_errors_keep_stored_thread_and_stop_old_agent(rig, monkeypatch, stage):
    def fail(*args, **kwargs):
        raise service.launchers.LaunchError("PRIVATE TRANSCRIPT")

    rig.settings.current = HandoffSettings(enabled=True, launcher=LauncherConfig(kind="command"))
    rig.successor = SuccessorRef("successor")
    if stage == "prompt":
        monkeypatch.setattr(service.writer, "successor_prompt", fail)
    elif stage == "launch":
        rig.launch_error = service.launchers.LaunchError("PRIVATE TRANSCRIPT")
    else:
        monkeypatch.setattr(rig.db, stage, fail)
    result = rig.service.run(rig.principal, make_request())
    assert result.action == "handoff" and not result.launch
    assert result.handoff.state == "launch_failed" and result.handoff.stored_thread_id
    assert rig.events.count(EVENT_LAUNCH_FAILED) == 1 and rig.events.count(EVENT_FAILED) == 0
    failure = rig.events.calls[-1]
    assert failure[4:6] == ("error", "successor launch failed")
    assert "PRIVATE" not in result.reason and "PRIVATE" not in result.handoff.error


def test_launch_failure_reporting_errors_still_stop_old_agent(rig, monkeypatch):
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("private database content")

    rig.launch_error = service.launchers.LaunchError("private content")
    rig.events.fail_on = EVENT_LAUNCH_FAILED
    monkeypatch.setattr(rig.db, "mark_launch_failed", fail)
    result = rig.service.run(rig.principal, make_request())
    assert result.action == "handoff" and result.handoff.stored_thread_id and not result.launch


@pytest.mark.parametrize(
    ("kind", "outcome"),
    [
        ("command", "error"),
        ("command", "different_ref"),
        ("command", "no_ref"),
        ("client", "no_ref"),
    ],
)
def test_registered_successor_survives_late_launcher_result(rig, monkeypatch, kind, outcome):
    entered = threading.Event()
    release = threading.Event()
    rig.settings.current = HandoffSettings(enabled=True, launcher=LauncherConfig(kind=kind))

    def launch(config, handoff, prompt, *, prompt_dir):
        entered.set()
        assert release.wait(10)
        if outcome == "error":
            raise service.launchers.LaunchError("private launcher error")
        return SuccessorRef("different") if outcome == "different_ref" else None

    monkeypatch.setattr(service.launchers, "launch", launch)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(rig.service.run, rig.principal, make_request())
        try:
            assert entered.wait(10)
            row = rig.db.get("hof_1")
            assert row.state == "launched"
            completed = rig.new_service().register_successor(
                rig.principal, row.id, SuccessorRef("registered")
            )
        finally:
            release.set()
        result = future.result(timeout=10)
    assert result.handoff == rig.db.get(completed.id) == completed
    assert result.action == "handoff" and not result.launch and not result.prompt
    assert rig.events.count(EVENT_COMPLETED) == 1
    assert rig.events.count(EVENT_LAUNCH_FAILED) == 0
    assert "mark_launch_failed" not in rig.trace


def test_completion_during_prompt_skips_launcher_and_preserves_done(rig, monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    def successor_prompt(doc):
        entered.set()
        assert release.wait(10)
        return "continue"

    monkeypatch.setattr(service.writer, "successor_prompt", successor_prompt)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(rig.service.run, rig.principal, make_request())
        try:
            assert entered.wait(10)
            row = rig.db.get("hof_1")
            assert row.state == "stored"
            completed = rig.new_service().register_successor(
                rig.principal, row.id, SuccessorRef("registered")
            )
        finally:
            release.set()
        result = future.result(timeout=10)
    assert result.handoff == rig.db.get(completed.id) == completed
    assert not result.launch and not rig.launch_calls
    assert "mark_launched" not in rig.trace and "mark_launch_failed" not in rig.trace
    assert rig.events.count(EVENT_LAUNCH_FAILED) == 0


def test_stale_stored_caller_does_not_fail_completed_row_when_claim_raises(rig, monkeypatch):
    monkeypatch.setattr(rig.db, "claim_launch", lambda handoff_id: False)
    first = rig.service.run(rig.principal, make_request())
    entered = threading.Event()
    release = threading.Event()

    def claim_launch(handoff_id):
        entered.set()
        assert release.wait(10)
        raise sqlite3.OperationalError("private claim error")

    monkeypatch.setattr(rig.db, "claim_launch", claim_launch)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(rig.new_service().run, rig.principal, make_request())
        try:
            assert entered.wait(10)
            completed = rig.service.register_successor(
                rig.principal, first.handoff.id, SuccessorRef("registered")
            )
        finally:
            release.set()
        result = future.result(timeout=10)
    assert result.handoff == rig.db.get(first.handoff.id) == completed
    assert result.action == "handoff" and not result.launch
    assert "mark_launch_failed" not in rig.trace
    assert rig.events.count(EVENT_LAUNCH_FAILED) == 0


def test_claim_error_leaves_unowned_stored_row_retryable(rig, monkeypatch):
    claim = rig.db.claim_launch

    def fail(handoff_id):
        raise sqlite3.OperationalError("private claim value")

    monkeypatch.setattr(rig.db, "claim_launch", fail)
    first = rig.service.run(rig.principal, make_request())
    assert first.action == "handoff" and first.handoff.state == "stored" and not first.launch
    assert not rig.db.claims and "mark_launch_failed" not in rig.trace
    assert rig.events.count(EVENT_LAUNCH_FAILED) == 0
    monkeypatch.setattr(rig.db, "claim_launch", claim)
    second = rig.service.run(rig.principal, make_request())
    assert second.launch and second.handoff.id == first.handoff.id
    assert len(rig.store.writes) == 1


def test_chain_of_three_handoffs(rig):
    results = []
    for index in range(3):
        request = make_request(f"source-{index}")
        if results:
            request = replace(
                request, meta=replace(request.meta, parent_handoff_id=results[-1].handoff.id)
            )
        results.append(rig.service.run(rig.principal, request))
    assert [call[-1] for call in rig.doc_calls] == [(), ("thr_1",), ("thr_2", "thr_1")]
    assert [write[-1] for write in rig.store.writes] == ["", "thr_1", "thr_2"]


@pytest.mark.parametrize("parent_kind", ["missing", "foreign", "pending"])
def test_unavailable_parent_is_not_linked(rig, parent_kind):
    parent_id = "missing-parent"
    if parent_kind != "missing":
        parent, _ = rig.db.begin(
            "ws_other" if parent_kind == "foreign" else "ws_1",
            "parent",
            ThreadMeta("parent"),
            connection_id="conn_1",
        )
        parent_id = parent.id
        if parent_kind == "foreign":
            rig.db.update(parent_id, state="stored", stored_thread_id="foreign-thread")
    request = make_request()
    request = replace(request, meta=replace(request.meta, parent_handoff_id=parent_id))
    result = rig.service.run(rig.principal, request)
    assert result.launch and rig.store.writes[-1][-1] == "" and rig.doc_calls[-1][-1] == ()


@pytest.mark.parametrize("end", ["cycle", "self", "missing", "foreign"])
def test_malformed_ancestry_is_bounded_and_workspace_scoped(rig, end):
    first = rig.service.run(rig.principal, make_request("first"))
    parent_id = first.handoff.stored_thread_id
    if end == "cycle":
        rig.store.parents[parent_id] = parent_id
    elif end == "self":
        rig.store.parents[parent_id] = "thr_2"
    elif end == "missing":
        rig.store.parents[parent_id] = "missing"
    else:
        foreign = replace(rig.store.rows[parent_id], id="foreign", workspace_id="ws_other")
        rig.store.rows[foreign.id] = foreign
        rig.store.parents[parent_id] = foreign.id
    request = make_request("second")
    request = replace(request, meta=replace(request.meta, parent_handoff_id=first.handoff.id))
    result = rig.service.run(rig.principal, request)
    assert result.launch and rig.doc_calls[-1][-1] == (parent_id,)


@pytest.mark.parametrize("state", ["stored", "launched"])
@pytest.mark.parametrize("owner", [False, True])
def test_successor_registration_allows_source_connection_or_owner(rig, state, owner):
    result = rig.service.run(rig.principal, make_request())
    rig.db.update(result.handoff.id, state=state)
    caller = (
        rig.principal
        if not owner
        else SimpleNamespace(
            **{**vars(rig.principal), "role": "owner", "connection_id": "different"}
        )
    )
    ref = SuccessorRef("new-agent", thread_id="new-thread")
    saved = rig.service.register_successor(caller, result.handoff.id, ref)
    assert saved.state == "done" and saved.successor == ref
    assert rig.service.register_successor(caller, saved.id, ref) == saved
    assert rig.events.count(EVENT_COMPLETED) == 1
    with pytest.raises(HandoffDBError):
        rig.service.register_successor(caller, saved.id, SuccessorRef("different"))


@pytest.mark.parametrize("case", ["other_connection", "other_workspace", "missing"])
def test_successor_registration_hides_unauthorized_handoffs(rig, case):
    result = rig.service.run(rig.principal, make_request())
    changes = {"connection_id": "different"} if case == "other_connection" else {}
    if case == "other_workspace":
        changes = {"workspace_id": "ws_other", "role": "owner"}
    caller = SimpleNamespace(**{**vars(rig.principal), **changes})
    with pytest.raises(LookupError, match="^handoff not found$"):
        rig.service.register_successor(
            caller, "missing" if case == "missing" else result.handoff.id, SuccessorRef("agent")
        )
    assert rig.events.count(EVENT_COMPLETED) == 0 and "set_successor" not in rig.trace


@pytest.mark.parametrize("state", ["pending", "failed", "launch_failed"])
def test_successor_registration_rejects_ineligible_states(rig, state):
    result = rig.service.run(rig.principal, make_request())
    rig.db.update(result.handoff.id, state=state)
    with pytest.raises(HandoffDBError):
        rig.service.register_successor(rig.principal, result.handoff.id, SuccessorRef("agent"))
    assert rig.events.count(EVENT_COMPLETED) == 0


def test_concurrent_successor_repeats_emit_completion_once(rig):
    result = rig.service.run(rig.principal, make_request())
    ref = SuccessorRef("same-agent")
    barrier = threading.Barrier(8)

    def register():
        other_service = rig.new_service()
        barrier.wait(timeout=10)
        return other_service.register_successor(rig.principal, result.handoff.id, ref)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: register(), range(8)))
    assert all(row.state == "done" and row.successor == ref for row in results)
    assert rig.events.count(EVENT_COMPLETED) == rig.trace.count("set_successor") == 1


@pytest.mark.parametrize("state", ["stored", "launched"])
@pytest.mark.parametrize("owner", [False, True])
def test_report_launch_failure_allows_source_connection_or_owner_once(rig, state, owner):
    first = rig.service.run(rig.principal, make_request())
    rig.db.update(first.handoff.id, state=state)
    caller = (
        rig.principal
        if not owner
        else SimpleNamespace(**{**vars(rig.principal), "role": "owner", "connection_id": "other"})
    )
    failed = rig.service.report_launch_failure(caller, first.handoff.id, "local command failed")
    assert (
        failed.state == "launch_failed"
        and failed.stored_thread_id == first.handoff.stored_thread_id
    )
    assert failed.error == "successor launch failed"
    assert rig.service.report_launch_failure(caller, failed.id, "a later error") == failed
    assert rig.trace.count("mark_launch_failed") == rig.events.count(EVENT_LAUNCH_FAILED) == 1
    assert rig.events.calls[-1][4:6] == ("error", "local command failed")
    assert len(rig.store.writes) == len(rig.launch_calls) == 1


@pytest.mark.parametrize("error", ["", "x" * 200, "private diagnostic: " + "é" * 250])
def test_report_launch_failure_bounds_error_and_keeps_it_out_of_row(rig, error):
    first = rig.service.run(rig.principal, make_request())
    failed = rig.service.report_launch_failure(rig.principal, first.handoff.id, error)
    assert failed.error == "successor launch failed"
    assert rig.events.calls[-1][5] == error[:200]
    assert len(rig.events.calls[-1][5]) <= 200


@pytest.mark.parametrize("case", ["other_connection", "other_workspace", "missing"])
def test_report_launch_failure_hides_unauthorized_handoffs_even_on_repeats(rig, case):
    first = rig.service.run(rig.principal, make_request())
    rig.service.report_launch_failure(rig.principal, first.handoff.id, "local failure")
    changes = {"connection_id": "other"} if case == "other_connection" else {}
    if case == "other_workspace":
        changes = {"workspace_id": "other", "role": "owner"}
    caller = SimpleNamespace(**{**vars(rig.principal), **changes})
    with pytest.raises(LookupError, match="^handoff not found$"):
        rig.service.report_launch_failure(
            caller, "missing" if case == "missing" else first.handoff.id, "private error"
        )
    assert rig.trace.count("mark_launch_failed") == rig.events.count(EVENT_LAUNCH_FAILED) == 1


@pytest.mark.parametrize("state", ["pending", "failed", "done"])
def test_report_launch_failure_rejects_other_states_and_preserves_completion(rig, state):
    first = rig.service.run(rig.principal, make_request())
    if state == "done":
        before = rig.service.register_successor(
            rig.principal, first.handoff.id, SuccessorRef("agent")
        )
    else:
        before = rig.db.update(first.handoff.id, state=state)
    with pytest.raises(HandoffDBError):
        rig.service.report_launch_failure(rig.principal, first.handoff.id, "late failure")
    assert rig.db.get(first.handoff.id) == before
    assert rig.events.count(EVENT_LAUNCH_FAILED) == 0 and "mark_launch_failed" not in rig.trace


def test_report_launch_failure_does_not_emit_when_db_transition_fails(rig, monkeypatch):
    first = rig.service.run(rig.principal, make_request())

    def fail(handoff_id, error):
        raise sqlite3.OperationalError("database unavailable")

    monkeypatch.setattr(rig.db, "mark_launch_failed", fail)
    with pytest.raises(sqlite3.OperationalError):
        rig.service.report_launch_failure(rig.principal, first.handoff.id, "local failure")
    assert rig.db.get(first.handoff.id) == first.handoff
    assert rig.events.count(EVENT_LAUNCH_FAILED) == 0


def test_report_launch_failure_contains_event_error_without_repeating_transition(rig):
    first = rig.service.run(rig.principal, make_request())
    rig.events.fail_on = EVENT_LAUNCH_FAILED
    failed = rig.service.report_launch_failure(rig.principal, first.handoff.id, "local failure")
    assert failed.state == "launch_failed"
    assert rig.service.report_launch_failure(rig.principal, failed.id, "repeat") == failed
    assert rig.trace.count("mark_launch_failed") == rig.events.count(EVENT_LAUNCH_FAILED) == 1


def test_concurrent_client_failure_reports_transition_and_emit_once(rig):
    first = rig.service.run(rig.principal, make_request())
    barrier = threading.Barrier(8)

    def report():
        other_service = rig.new_service()
        barrier.wait(timeout=10)
        return other_service.report_launch_failure(rig.principal, first.handoff.id, "local failure")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: report(), range(8)))
    assert all(row == results[0] and row.state == "launch_failed" for row in results)
    assert rig.trace.count("mark_launch_failed") == rig.events.count(EVENT_LAUNCH_FAILED) == 1


def test_reported_client_failure_can_be_relaunched_by_owner(rig):
    first = rig.service.run(rig.principal, make_request())
    rig.service.report_launch_failure(rig.principal, first.handoff.id, "local failure")
    owner = SimpleNamespace(**{**vars(rig.principal), "role": "owner"})
    result = rig.service.relaunch(owner, first.handoff.id)
    assert result.launch and result.handoff.id == first.handoff.id
    assert len(rig.store.writes) == 1


@pytest.mark.parametrize("kind", ["client", "command"])
def test_owner_relaunch_releases_claim_and_uses_captured_settings(rig, kind):
    rig.settings.current = HandoffSettings(enabled=True, launcher=LauncherConfig(kind=kind))
    rig.launch_error = service.launchers.LaunchError("failed")
    first = rig.service.run(rig.principal, make_request())
    assert first.handoff.state == "launch_failed"
    rig.launch_error = None
    rig.settings.current = HandoffSettings(enabled=False, launcher=LauncherConfig(kind="webhook"))
    owner = SimpleNamespace(**{**vars(rig.principal), "role": "owner"})
    result = rig.service.relaunch(owner, first.handoff.id)
    assert result.action == "handoff" and result.handoff.state == "launched"
    assert result.launch == (kind == "client")
    assert rig.launch_calls[-1][0].kind == kind
    assert ("release", owner.actor) in rig.trace
    assert len(rig.store.writes) == 1 and len(rig.settings.calls) == 1


@pytest.mark.parametrize("case", ["member", "other_workspace", "missing"])
def test_relaunch_is_owner_only_and_workspace_scoped(rig, case):
    rig.launch_error = service.launchers.LaunchError("failed")
    result = rig.service.run(rig.principal, make_request())
    changes = {"role": "member" if case == "member" else "owner"}
    if case == "other_workspace":
        changes["workspace_id"] = "other"
    caller = SimpleNamespace(**{**vars(rig.principal), **changes})
    with pytest.raises(LookupError, match="^handoff not found$"):
        rig.service.relaunch(caller, "missing" if case == "missing" else result.handoff.id)
    assert not any(isinstance(item, tuple) and item[0] == "release" for item in rig.trace)


@pytest.mark.parametrize("state", ["pending", "stored", "launched", "done", "failed"])
def test_relaunch_rejects_other_states(rig, state):
    first = rig.service.run(rig.principal, make_request())
    rig.db.update(first.handoff.id, state=state)
    owner = SimpleNamespace(**{**vars(rig.principal), "role": "owner"})
    with pytest.raises(HandoffDBError):
        rig.service.relaunch(owner, first.handoff.id)
    assert len(rig.launch_calls) == 1


def test_restart_reconstructs_unclaimed_stored_doc_without_storing_again(rig, monkeypatch):
    claim = rig.db.claim_launch
    monkeypatch.setattr(rig.db, "claim_launch", lambda handoff_id: False)
    first = rig.service.run(rig.principal, make_request())
    monkeypatch.setattr(rig.db, "claim_launch", claim)
    result = rig.new_service().run(
        rig.principal, make_request("different", idempotency_key="thread:source-1")
    )
    assert result.launch and result.handoff.id == first.handoff.id
    assert rig.doc_calls[-1][1].thread_id == "source-1"
    assert rig.doc_calls[-1][1].cwd == make_request().meta.cwd
    assert len(rig.store.writes) == 1


def test_restart_relaunch_reconstructs_doc_and_ignores_disabled_toggle(rig):
    rig.launch_error = service.launchers.LaunchError("failed")
    first = rig.service.run(rig.principal, make_request())
    rig.launch_error = None
    rig.settings.current = HandoffSettings(enabled=False)
    owner = SimpleNamespace(**{**vars(rig.principal), "role": "owner"})
    result = rig.new_service().relaunch(owner, first.handoff.id)
    assert result.launch and result.handoff.id == first.handoff.id
    assert len(rig.store.writes) == 1 and len(rig.doc_calls) == 2


def test_cross_instance_stored_retry_honors_settings_captured_before_disable(rig, monkeypatch):
    claim = rig.db.claim_launch
    monkeypatch.setattr(rig.db, "claim_launch", lambda handoff_id: False)
    first = rig.service.run(rig.principal, make_request())
    assert first.handoff.state == "stored" and not rig.db.claims
    monkeypatch.setattr(rig.db, "claim_launch", claim)
    rig.settings.current = HandoffSettings(enabled=False)
    result = rig.new_service().run(rig.principal, make_request())
    assert rig.db.get(first.handoff.id).state == "launched"
    assert len(rig.store.writes) == 1
    assert (result.action, result.launch) == ("handoff", True)


@pytest.mark.xfail(
    strict=True,
    reason="Completion transition and audit/outbox must commit atomically for guaranteed event delivery",
)
def test_completed_event_is_not_lost_when_emit_fails_before_idempotent_retry(rig):
    first = rig.service.run(rig.principal, make_request())
    ref = SuccessorRef("registered")
    rig.events.fail_on = EVENT_COMPLETED
    completed = rig.service.register_successor(rig.principal, first.handoff.id, ref)
    assert completed.state == "done"
    assert EVENT_COMPLETED not in rig.events.delivered
    rig.events.fail_on = ""
    repeated = rig.service.register_successor(rig.principal, completed.id, ref)
    assert repeated == completed
    assert rig.events.delivered.count(EVENT_COMPLETED) == 1
