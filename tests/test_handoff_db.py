"""Memory handoff storage, transitions and races in the control database."""

import json
import re
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from commonplace import events as hub_events
from commonplace.control_db import SCHEMA_VERSION, ControlDB
from commonplace.handoff import db as db_module
from commonplace.handoff.db import HandoffDB, HandoffDBError
from commonplace.handoff.events import Events
from commonplace.handoff.models import (
    EVENT_STARTED,
    STATES,
    LauncherConfig,
    SettingRow,
    SuccessorRef,
    ThreadMeta,
)
from commonplace.handoff.thread_store import ThreadStore

NOW = "2026-10-30T04:00:00Z"
LATER = "2026-10-30T05:00:00Z"
META = ThreadMeta(
    "source-thread",
    agent="codex:worker",
    task_ref="#142",
    branch="feat/handoff",
    pr_url="http://127.0.0.1/review/1",
    cwd="/work/Memory café/雪",
    parent_handoff_id="hof_parent",
)
SUCCESSOR = SuccessorRef("agent:next", "http://127.0.0.1/thread/next", "next-thread")
DOCUMENT = "# handoff\n\nExact words: café 雪\n  keep spaces\r\n\n"
WAKE_URL = "http://127.0.0.1:9000/wake"

# The pre-cwd handoff table, independent of the current schema under test.
_LEGACY_HANDOFFS = """
CREATE TABLE handoffs (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    source_thread_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN ('pending', 'stored', 'launched', 'done', 'failed', 'launch_failed')
    ),
    agent TEXT NOT NULL DEFAULT '',
    task_ref TEXT NOT NULL DEFAULT '',
    branch TEXT NOT NULL DEFAULT '',
    pr_url TEXT NOT NULL DEFAULT '',
    stored_thread_id TEXT NOT NULL DEFAULT '',
    parent_handoff_id TEXT NOT NULL DEFAULT '',
    doc_markdown TEXT NOT NULL DEFAULT '',
    successor_agent_ref TEXT NOT NULL DEFAULT '',
    successor_url TEXT NOT NULL DEFAULT '',
    successor_thread_id TEXT NOT NULL DEFAULT '',
    launch_claimed INTEGER NOT NULL DEFAULT 0 CHECK (launch_claimed IN (0, 1)),
    error TEXT NOT NULL DEFAULT '',
    connection_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (workspace_id, idempotency_key)
);
"""


@pytest.fixture
def open_control(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "_now", lambda: datetime(2026, 10, 30, 4, tzinfo=timezone.utc))
    controls = []
    stores = {}

    def store_for(workspace):
        if workspace not in stores:
            stores[workspace] = ThreadStore(str(tmp_path / (workspace + "-threads.sqlite3")))
        return stores[workspace]

    def open_one():
        control = ControlDB(str(tmp_path / "control.sqlite3"))
        controls.append(control)
        control.handoff_store_for = store_for
        return control

    yield open_one
    for control in controls:
        control.close()
    for store in stores.values():
        store.close()


@pytest.fixture
def control(open_control):
    return open_control()


@pytest.fixture
def db(control):
    return _database(control)


def _database(control):
    return HandoffDB(control, document_store_for=getattr(control, "handoff_store_for", None))


def _begin(db, workspace="workspace-one", key="thread:source-thread", meta=META):
    return db.begin(workspace, key, meta, connection_id="conn_source")[0]


def _in_state(db, state):
    handoff = _begin(db)
    if state == "pending":
        return handoff
    if state == "failed":
        return db.mark_failed(handoff.id, "store error")
    handoff = db.mark_stored(handoff.id, "thr_stored", DOCUMENT)
    if state == "stored":
        return handoff
    assert db.claim_launch(handoff.id)
    handoff = db.mark_launched(handoff.id)
    if state == "launched":
        return handoff
    if state == "launch_failed":
        return db.mark_launch_failed(handoff.id, "launch error")
    assert state == "done"
    return db.set_successor(handoff.id, SUCCESSOR)


def _race(databases, operation):
    barrier = threading.Barrier(16)

    def run(index):
        barrier.wait(timeout=10)
        return operation(databases[index])

    with ThreadPoolExecutor(max_workers=16) as pool:
        return list(pool.map(run, range(16)))


def test_upgrade_existing_control_database(open_control):
    original = open_control()
    owner = original.create_user("owner@example.test", "Owner")
    workspace = original.create_workspace("Memory", owner)
    original.audit(
        action="memory.saved",
        decision="ok",
        workspace_id=workspace,
        user_id=owner,
        target_ids=["drawer_雪"],
    )
    existing_event = hub_events.emit(
        original,
        workspace,
        "memory.saved",
        subject="drawer_雪",
        actor=owner,
        data={"drawer_id": "drawer_雪", "nested": {"ids": ["one", "two"]}},
    )
    audit_before = [dict(row) for row in original.audit_entries()]
    assert not original._conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name LIKE 'handoff%'"
    ).fetchall()

    # Reopen the file exactly as an existing hub would, then install handoff tables.
    upgraded = open_control()
    db = _database(upgraded)
    assert db._conn is upgraded._conn
    assert db._lock is upgraded._lock
    tables = {
        row["name"]
        for row in upgraded._conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {"handoff_settings", "handoffs"} <= tables
    assert "handoff_webhooks" not in tables and "handoff_outbox" in tables
    assert dict(upgraded.user(owner)) == dict(original.user(owner))
    assert upgraded.workspace(workspace)["name"] == "Memory"
    assert upgraded._conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[
        0
    ] == str(SCHEMA_VERSION)

    saved = db.put_setting(SettingRow(workspace, "workspace", "", enabled=True), actor=owner)
    handoff = _begin(db, workspace)
    assert [dict(row) for row in upgraded.audit_entries()] == audit_before
    assert hub_events.after(upgraded, workspace) == [existing_event]

    Events(upgraded, db).emit(EVENT_STARTED, workspace_id=workspace, handoff=handoff)
    events_after = hub_events.after(upgraded, workspace)
    assert len(events_after) == 2 and events_after[0] == existing_event
    assert events_after[1]["kind"] == EVENT_STARTED
    assert events_after[1]["workspace_id"] == workspace
    assert events_after[1]["data"]["handoff_id"] == handoff.id
    audit_after = [dict(row) for row in upgraded.audit_entries()]
    assert len(audit_after) == len(audit_before) + 1
    assert audit_after[0]["action"] == EVENT_STARTED
    assert json.loads(audit_after[0]["target_ids"]) == [handoff.id]
    assert audit_after[1:] == audit_before

    reopened_control = open_control()
    reopened = _database(reopened_control)
    assert reopened.setting_rows(workspace) == [saved]
    assert reopened.get(handoff.id) == handoff
    assert hub_events.after(reopened_control, workspace) == events_after
    assert [dict(row) for row in reopened_control.audit_entries()] == audit_after


def test_upgrade_existing_handoff_table_without_cwd(open_control):
    original = open_control()
    original._conn.executescript(_LEGACY_HANDOFFS)
    original._conn.execute(
        "INSERT INTO handoffs(id, workspace_id, idempotency_key, source_thread_id, state, "
        "stored_thread_id, doc_markdown, created_at, updated_at) "
        "VALUES ('hof_legacy', 'workspace-one', 'legacy', 'old-thread', 'stored', "
        "'thr_legacy', ?, ?, ?)",
        (DOCUMENT, NOW, NOW),
    )
    before = dict(original._conn.execute("SELECT * FROM handoffs").fetchone())
    assert "cwd" not in before

    upgraded = open_control()
    db = _database(upgraded)
    after = dict(upgraded._conn.execute("SELECT * FROM handoffs").fetchone())
    assert after == {**before, "cwd": "", "lease_token": "", "doc_markdown": ""}
    assert db.get("hof_legacy").cwd == ""
    assert db.by_stored_thread("thr_legacy").doc_markdown == DOCUMENT
    assert db.list("workspace-one")[0].cwd == ""
    assert db.set_successor("hof_legacy", SUCCESSOR).cwd == ""
    assert db.by_successor_thread("workspace-one", SUCCESSOR.thread_id).cwd == ""

    # A second open must preserve data and never try to add the column twice.
    reopened = _database(open_control())
    assert reopened.get("hof_legacy") == db.get("hof_legacy")
    assert _begin(reopened, key="new").cwd == META.cwd
    columns = {row["name"]: row for row in upgraded._conn.execute("PRAGMA table_info(handoffs)")}
    assert columns["cwd"]["type"] == "TEXT"
    assert columns["cwd"]["notnull"] == 1 and columns["cwd"]["dflt_value"] == "''"


def test_concurrent_existing_handoff_table_upgrade(open_control):
    controls = [open_control() for _ in range(16)]
    controls[0]._conn.executescript(_LEGACY_HANDOFFS)
    databases = _race(controls, HandoffDB)
    assert len(databases) == 16
    columns = [row["name"] for row in controls[0]._conn.execute("PRAGMA table_info(handoffs)")]
    assert columns.count("cwd") == 1
    assert _begin(databases[0]).cwd == META.cwd


@pytest.mark.parametrize("cwd", ["", "/work/Memory café/雪", r"C:\Users\labadmin\work\Memory"])
def test_cwd_is_persisted_and_hydrated_on_all_reads(db, control, open_control, cwd):
    meta = replace(META, cwd=cwd)
    handoff = _begin(db, meta=meta)
    assert handoff.cwd == cwd
    assert (
        control._conn.execute("SELECT cwd FROM handoffs WHERE id = ?", (handoff.id,)).fetchone()[0]
        == cwd
    )
    # Retry metadata cannot replace the original working directory.
    assert _begin(db, meta=replace(meta, cwd="changed")).cwd == cwd
    assert db.mark_failed(handoff.id, "store error").cwd == cwd
    retried = _begin(db, meta=meta)
    assert retried.id == handoff.id and retried.cwd == cwd
    assert db.mark_stored(handoff.id, "thr_stored", DOCUMENT).cwd == cwd
    assert db.claim_launch(handoff.id)
    assert db.mark_launched(handoff.id).cwd == cwd
    assert db.mark_launch_failed(handoff.id, "launch error").cwd == cwd
    assert db.release_launch(handoff.id, actor="owner").cwd == cwd
    completed = db.set_successor(handoff.id, SUCCESSOR)
    assert completed.cwd == cwd
    reopened = _database(open_control())
    assert reopened.get(handoff.id) == completed
    assert reopened.list("workspace-one") == [completed]
    assert reopened.by_stored_thread("thr_stored") == completed
    assert reopened.by_successor_thread("workspace-one", SUCCESSOR.thread_id) == completed


def test_settings_round_trip_nulls_launcher_secret_and_stamps(db, control):
    launcher = LauncherConfig(
        "command", ("python", "worker.py", "{prompt}"), WAKE_URL, "local-secret"
    )
    row = SettingRow(
        "workspace-one", "workspace", "", False, 0.85, 200_000, launcher, "untrusted", "old"
    )
    saved = db.put_setting(row, actor="user:owner")
    assert saved == replace(row, updated_by="user:owner", updated_at=NOW)
    assert saved.enabled is False
    assert saved.launcher.argv == launcher.argv
    stored = control._conn.execute("SELECT * FROM handoff_settings").fetchone()
    assert stored["enabled"] == 0
    assert json.loads(stored["launcher"])["secret"] == "local-secret"
    assert db.setting_rows("workspace-one") == [saved]

    inherited = db.put_setting(SettingRow("workspace-one", "workspace", ""), actor="user:next")
    assert inherited.enabled is None and inherited.launcher is None
    assert inherited.threshold is None and inherited.context_window is None
    assert inherited.updated_by == "user:next"
    assert db.list_settings("workspace-one") == [inherited]
    assert control._conn.execute("SELECT COUNT(*) FROM handoff_settings").fetchone()[0] == 1


def test_settings_resolution_order_filters_and_empty_levels(db):
    rows = [
        SettingRow("workspace-one", "task", "#142", threshold=0.9),
        SettingRow("workspace-one", "agent", "codex:worker", enabled=True),
        SettingRow("workspace-one", "workspace", "", enabled=False),
        SettingRow("workspace-one", "agent", "another", enabled=False),
        SettingRow("workspace-one", "task", "#200", enabled=False),
        SettingRow("workspace-two", "workspace", "", enabled=True),
    ]
    saved = [db.put_setting(row, actor="owner") for row in rows]
    assert db.setting_rows("workspace-one", "codex:worker", "#142") == [
        saved[2],
        saved[1],
        saved[0],
    ]
    assert db.setting_rows("workspace-one", task_ref="#142") == [saved[2], saved[0]]
    assert db.setting_rows("workspace-one", "missing", "missing") == [saved[2]]
    assert db.setting_rows("workspace-one") == [saved[2]]
    assert db.setting_rows("missing", "codex:worker", "#142") == []
    assert db.list_settings("workspace-one") == [saved[2], saved[3], saved[1], saved[0], saved[4]]
    assert db.list_settings("missing") == []
    assert db.delete_setting("workspace-one", "workspace", "", actor="owner")
    assert not db.delete_setting("workspace-one", "workspace", "", actor="owner")
    assert not db.delete_setting("workspace-two", "agent", "codex:worker", actor="owner")
    assert db.setting_rows("workspace-one", "codex:worker", "#142") == [saved[1], saved[0]]


@pytest.mark.parametrize(
    ("scope", "ref"), [("unknown", ""), ("workspace", "agent"), ("agent", ""), ("task", "")]
)
def test_invalid_setting_scope_and_ref(db, scope, ref):
    with pytest.raises(HandoffDBError):
        db.put_setting(SettingRow("workspace-one", scope, ref), actor="owner")
    with pytest.raises(HandoffDBError):
        db.delete_setting("workspace-one", scope, ref, actor="owner")
    assert db.list_settings("workspace-one") == []


def test_invalid_enabled_rolls_back_and_database_remains_usable(db, control):
    with pytest.raises(HandoffDBError, match="invalid handoff setting"):
        db.put_setting(SettingRow("workspace-one", "workspace", "", enabled=2), actor="owner")
    assert db.list_settings("workspace-one") == []
    assert not control._conn.in_transaction
    assert db.put_setting(SettingRow("workspace-one", "workspace", "", True), actor="owner")


def test_schema_rejects_unknown_states_and_setting_values(db, control):
    handoff = _begin(db)
    with pytest.raises(sqlite3.IntegrityError):
        control._conn.execute("UPDATE handoffs SET state = 'unknown' WHERE id = ?", (handoff.id,))
    for state in STATES:
        control._conn.execute("UPDATE handoffs SET state = ? WHERE id = ?", (state, handoff.id))
    for scope, ref, enabled in [("unknown", "", 0), ("workspace", "x", 0), ("workspace", "", 2)]:
        with pytest.raises(sqlite3.IntegrityError):
            control._conn.execute(
                "INSERT INTO handoff_settings(workspace_id, scope, ref, enabled, updated_by, "
                "updated_at) VALUES ('workspace-one', ?, ?, ?, 'owner', ?)",
                (scope, ref, enabled, NOW),
            )
    indexed = [
        [column["name"] for column in control._conn.execute(f"PRAGMA index_info('{row['name']}')")]
        for row in control._conn.execute("PRAGMA index_list('handoffs')")
    ]
    for name in ("task_ref", "stored_thread_id", "successor_thread_id"):
        assert any(name in columns for columns in indexed)


def test_begin_metadata_and_workspace_scoped_idempotency(db):
    handoff, created = db.begin("workspace-one", "same-key", META, connection_id="conn_source")
    assert created
    assert re.fullmatch(r"hof_[0-9a-f]{16}", handoff.id)
    assert handoff.workspace_id == "workspace-one"
    assert handoff.idempotency_key == "same-key"
    assert handoff.state == "pending"
    assert handoff.source_thread_id == META.thread_id
    assert (handoff.agent, handoff.task_ref, handoff.branch, handoff.pr_url) == (
        META.agent,
        META.task_ref,
        META.branch,
        META.pr_url,
    )
    assert handoff.parent_handoff_id == META.parent_handoff_id
    assert handoff.connection_id == "conn_source"
    assert handoff.cwd == META.cwd
    assert handoff.created_at == NOW and handoff.updated_at == NOW
    assert handoff.successor is None and handoff.error == "" and handoff.stored_thread_id == ""
    repeated, created = db.begin(
        "workspace-one", "same-key", replace(META, agent="changed"), connection_id="changed"
    )
    assert not created and repeated == handoff
    other = _begin(db, "workspace-two", "same-key")
    assert other.id != handoff.id
    assert db.get(handoff.id) == handoff


@pytest.mark.parametrize("state", STATES)
def test_begin_retries_only_store_failures(db, state):
    before = _in_state(db, state)
    after, created = db.begin(
        "workspace-one", "thread:source-thread", META, connection_id="conn_other"
    )
    assert created is (state == "failed")
    if state == "failed":
        assert after == replace(before, state="pending", error="")
        assert not db.claim_launch(after.id)
        assert db.mark_stored(after.id, "thr_retry", DOCUMENT).state == "stored"
    else:
        assert after == before


@pytest.mark.parametrize("separate_connections", [False, True])
@pytest.mark.parametrize("retry_failed", [False, True])
def test_begin_race_16_threads(db, open_control, separate_connections, retry_failed):
    original = _in_state(db, "failed") if retry_failed else None
    databases = (
        [_database(open_control()) for _ in range(16)] if separate_connections else [db] * 16
    )
    results = _race(
        databases,
        lambda current: current.begin(
            "workspace-one", "thread:source-thread", META, connection_id="conn_source"
        ),
    )
    assert sum(created for _, created in results) == 1
    assert len({handoff.id for handoff, _ in results}) == 1
    assert all(handoff.state == "pending" and handoff.error == "" for handoff, _ in results)
    if original is not None:
        assert results[0][0].id == original.id
    assert len(db.list("workspace-one")) == 1


@pytest.mark.parametrize("separate_connections", [False, True])
def test_claim_launch_race_16_threads(db, open_control, separate_connections):
    handoff = _in_state(db, "stored")
    databases = (
        [_database(open_control()) for _ in range(16)] if separate_connections else [db] * 16
    )
    results = _race(databases, lambda current: current.claim_launch(handoff.id))
    assert results.count(True) == 1
    assert results.count(False) == 15
    assert not db.claim_launch(handoff.id)


@pytest.mark.parametrize("state", STATES)
def test_claim_launch_requires_stored(db, state):
    handoff = _in_state(db, state)
    assert db.claim_launch(handoff.id) is (state == "stored")


# Each column lists states that may take an operation, including identical retries.
OPERATIONS = [
    ("mark_stored", {"pending", "stored"}, "stored"),
    ("mark_failed", {"pending", "failed"}, "failed"),
    ("mark_launched", {"stored", "launched"}, "launched"),
    ("mark_launch_failed", {"stored", "launched", "launch_failed"}, "launch_failed"),
    ("release_launch", {"launch_failed"}, "stored"),
    ("set_successor", {"stored", "launched", "done"}, "done"),
]


def _operate(db, name, handoff_id):
    args = {
        "mark_stored": ("thr_stored", DOCUMENT),
        "mark_failed": ("store error",),
        "mark_launched": (),
        "mark_launch_failed": ("launch error",),
        "set_successor": (SUCCESSOR,),
    }
    if name == "release_launch":
        return db.release_launch(handoff_id, actor="owner")
    return getattr(db, name)(handoff_id, *args[name])


@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize(("operation", "allowed", "result_state"), OPERATIONS)
def test_state_transitions(db, state, operation, allowed, result_state):
    before = _in_state(db, state)
    if state in allowed:
        result = _operate(db, operation, before.id)
        assert result.state == result_state
        assert result.id == before.id and result.created_at == before.created_at
        assert db.get(before.id) == result
    else:
        with pytest.raises(HandoffDBError):
            _operate(db, operation, before.id)
        assert db.get(before.id) == before


@pytest.mark.parametrize(("operation", "allowed", "result_state"), OPERATIONS)
def test_mutating_missing_handoff_fails_closed(db, operation, allowed, result_state):
    with pytest.raises(HandoffDBError, match="not found"):
        _operate(db, operation, "missing")
    assert db.list("workspace-one") == []


def test_missing_and_empty_lookups(db):
    _begin(db)
    assert db.get("missing") is None
    assert not db.claim_launch("missing")
    assert db.by_stored_thread("missing") is None
    assert db.by_stored_thread("") is None
    assert db.by_successor_thread("workspace-one", "missing") is None
    assert db.by_successor_thread("workspace-one", "") is None
    assert db.list("missing") == []


def test_stored_document_is_verbatim_and_identical_retries_do_not_change_it(db):
    handoff = _in_state(db, "stored")
    assert handoff.doc_markdown == DOCUMENT
    assert db.mark_stored(handoff.id, "thr_stored", DOCUMENT) == handoff
    with pytest.raises(HandoffDBError):
        db.mark_stored(handoff.id, "thr_different", DOCUMENT)
    with pytest.raises(HandoffDBError):
        db.mark_stored(handoff.id, "thr_stored", "changed")
    assert db.get(handoff.id) == handoff


def test_empty_stored_pointer_and_successor_are_refused(db):
    handoff = _begin(db)
    with pytest.raises(HandoffDBError, match="not found"):
        db.mark_stored(handoff.id, "", DOCUMENT)
    assert db.get(handoff.id) == handoff
    db.mark_stored(handoff.id, "thr_stored", DOCUMENT)
    with pytest.raises(HandoffDBError, match="not found"):
        db.set_successor(handoff.id, SuccessorRef(""))
    assert db.get(handoff.id).successor is None


@pytest.mark.parametrize("launched", [False, True])
def test_launch_failure_explicit_retry_preserves_stored_data(db, launched):
    handoff = _in_state(db, "stored")
    assert db.claim_launch(handoff.id)
    if launched:
        db.mark_launched(handoff.id)
    failed = db.mark_launch_failed(handoff.id, "cannot start\n雪")
    assert failed.error == "cannot start\n雪"
    assert failed.doc_markdown == DOCUMENT and failed.stored_thread_id == "thr_stored"
    assert not db.claim_launch(handoff.id)
    released = db.release_launch(handoff.id, actor="owner")
    assert released.state == "stored" and released.error == ""
    assert released.doc_markdown == DOCUMENT and released.stored_thread_id == "thr_stored"
    with pytest.raises(HandoffDBError):
        db.release_launch(handoff.id, actor="owner")
    assert db.claim_launch(handoff.id)
    assert not db.claim_launch(handoff.id)
    done = db.set_successor(handoff.id, SUCCESSOR)
    assert done.state == "done" and done.successor == SUCCESSOR


def test_successor_same_ref_is_idempotent_and_conflicting_metadata_refused(db):
    handoff = _in_state(db, "done")
    assert db.set_successor(handoff.id, SUCCESSOR) == handoff
    with pytest.raises(HandoffDBError, match="different successor"):
        db.set_successor(handoff.id, replace(SUCCESSOR, url="changed", thread_id="changed"))
    with pytest.raises(HandoffDBError, match="different successor"):
        db.set_successor(handoff.id, replace(SUCCESSOR, agent_ref="other-agent"))
    assert db.get(handoff.id) == handoff
    assert not db.claim_launch(handoff.id)


def test_immediate_successor_follows_documented_state_graph(db, control):
    handoff = _in_state(db, "stored")
    control._conn.executescript(
        "CREATE TRIGGER require_launched_before_done BEFORE UPDATE OF state ON handoffs "
        "WHEN NEW.state = 'done' AND OLD.state != 'launched' "
        "BEGIN SELECT RAISE(ABORT, 'handoff must be launched before done'); END;"
    )
    completed = db.set_successor(handoff.id, SUCCESSOR)
    assert completed.state == "done" and completed.successor == SUCCESSOR
    assert db.get(handoff.id) == completed


def test_concurrent_successor_registration_keeps_one_agent(db, open_control):
    handoff = _in_state(db, "stored")
    databases = [_database(open_control()) for _ in range(16)]

    def register(current):
        ref = SuccessorRef(f"agent:{id(current)}")
        try:
            return current.set_successor(handoff.id, ref)
        except HandoffDBError:
            return None

    results = _race(databases, register)
    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert db.get(handoff.id) == winners[0]


def test_lists_and_thread_indexes_are_scoped_and_deterministic(db, control):
    first = _in_state(db, "done")
    second = _begin(db, key="second", meta=replace(META, thread_id="second", task_ref="#200"))
    third = _begin(db, key="third", meta=replace(META, thread_id="third"))
    other = _begin(db, "workspace-two")
    db.mark_stored(other.id, "thr_other", "other document")
    db.set_successor(other.id, SUCCESSOR)
    assert db.by_stored_thread("thr_stored") == first
    assert db.by_successor_thread("workspace-one", SUCCESSOR.thread_id) == first
    assert db.by_successor_thread("workspace-two", SUCCESSOR.thread_id).id == other.id
    assert db.by_successor_thread("missing", SUCCESSOR.thread_id) is None
    assert db.list("workspace-one") == [third, second, first]
    assert db.list("workspace-one", task_ref="#142") == [third, first]
    assert db.list("workspace-one", task_ref="missing") == []
    assert db.list("workspace-one", limit=1) == [third]
    assert db.list("workspace-one", limit=0) == []
    assert db.list("workspace-one", limit=-1) == []
    # Timestamp order takes precedence over insertion order.
    control._conn.execute("UPDATE handoffs SET created_at = ? WHERE id = ?", (LATER, first.id))
    assert [item.id for item in db.list("workspace-one")] == [first.id, third.id, second.id]


def test_database_operations_do_not_write_audit_entries(db, control):
    handoff = _in_state(db, "done")
    db.put_setting(SettingRow("workspace-one", "workspace", "", True), actor="owner")
    db.delete_setting("workspace-one", "workspace", "", actor="owner")
    assert db.get(handoff.id) == handoff
    assert control.audit_entries() == []


def test_by_key_is_read_only(tmp_path):
    from commonplace.control_db import ControlDB
    from commonplace.handoff.models import ThreadMeta

    db = _database(ControlDB(str(tmp_path / "control.sqlite3")))
    assert db.by_key("ws", "thread:a") is None
    assert db.list("ws") == []
    made, created = db.begin("ws", "thread:a", ThreadMeta(thread_id="a"), connection_id="c")
    assert created and db.by_key("ws", "thread:a") == made
    assert db.by_key("other", "thread:a") is None
