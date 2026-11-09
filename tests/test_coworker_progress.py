"""Canonical synthetic ledgers only; no worker processes, providers or devices.

These cases intentionally run with the parent's grouped test selection. Progress
is a read-only per-viewer observation, not persisted disclosure into group chat.
"""

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest

from commonplace import sessions
from commonplace.chat.coworkers import routes, runtime, task_progress
from commonplace.chat.coworkers.store import Store, StoreError
from commonplace.policy import Policy
from commonplace.projects import orchestration
from commonplace.runner import jobs as runner_jobs
from tests.test_coworker_api import API
from tests.test_coworker_dispatch import dispatch as dispatch
from tests.test_coworker_runtime import env as env


@pytest.fixture
def progress(env, dispatch):
    other_session = sessions.start_session(env.db, env.other, env.wid)
    env.gateway._coworker_stores = {env.wid: env.store}

    def start(*, group=False, title="Inspect fixture"):
        if group:
            cid = env.store.create_conversation(
                env.uid, "group", member_ids=[env.other], bot_ids=[env.bot["id"]], board="work"
            )["id"]
        else:
            cid = dispatch.conversation()
        receipt = dispatch.post(cid, text="/task " + title + "\nExact requested body.")
        assert env.worker().run_once(env.wid)
        job = dispatch.outcome(cid)
        return SimpleNamespace(cid=cid, receipt=receipt, job=job, number=job["task"]["number"])

    def view(item, *, user=None, session=None, store=None):
        user = user or env.uid
        session = session or (env.session if user == env.uid else other_session)
        rows = (store or env.store).list_jobs(
            user, item.cid, progress=True, session_hash=sessions._hash(session)
        )
        return next(row for row in rows if row["id"] == item.job["id"])

    return SimpleNamespace(start=start, view=view, other_session=other_session)


def test_canonical_progress_result_and_evidence_survive_reload_without_chat_copy(
    env, dispatch, progress
):
    item = progress.start()
    admitted = progress.view(item)
    assert admitted["state"] == "done" and admitted["code"] == "runner_queued"
    assert admitted["task_progress"]["status"] == "todo"
    assert admitted["task_progress"]["runner"]["state"] == "queued"
    dispatch.boards.task_update(item.number, status="doing", status_line="Reading the fixture.")
    dispatch.boards.task_comment(item.number, "  Exact worker observation\nline two.  ")
    dispatch.boards.store.link_drawer(
        item.number,
        "artifact-one",
        env.principal.actor,
        title="Exact artifact title",
        theme="shared",
        principal=env.principal,
    )
    working = progress.view(item)["task_progress"]
    assert working["status"] == "doing" and working["status_line"] == "Reading the fixture."
    assert working["events"][-1]["text"] == "  Exact worker observation\nline two.  "
    assert working["evidence"][0]["title"] == "Exact artifact title"
    assert working["revision"] != admitted["task_progress"]["revision"]
    dispatch.boards.task_update(item.number, status="done", status_line="Exact final result.")
    reopened = Store(env.path, authority=routes.Authority(env.gateway, env.wid))
    result = progress.view(item, store=reopened)
    assert result["task_progress"]["status"] == "done"
    assert result["task_progress"]["status_line"] == "Exact final result."
    assert progress.view(item, store=reopened) == result
    assert not env.worker().run_once(env.wid)
    assert len(dispatch.runner_jobs()) == 1
    assert env.store.list_messages(env.uid, item.cid) == [item.receipt["message"]]


def test_lost_front_ack_recovers_canonical_origin_without_retrying_runner(
    env, dispatch, progress, monkeypatch
):
    cid = dispatch.conversation()
    receipt = dispatch.post(cid)
    original = env.store.finish_job

    def lose_ack(job_id, state, code="", task=None):
        if task is not None:
            raise RuntimeError("Synthetic front acknowledgement loss")
        return original(job_id, state, code=code, task=task)

    monkeypatch.setattr(env.store, "finish_job", lose_ack)
    assert env.worker().run_once(env.wid)
    front = dispatch.outcome(cid)
    assert front["state"] == "failed" and front["code"] == "interrupted" and "task" not in front
    item = SimpleNamespace(cid=cid, job=receipt["job"])
    first = progress.view(item)
    assert first["state"] == "failed" and first["task_progress"]["status"] == "todo"
    assert first["task_progress"]["runner"]["state"] == "queued"
    reopened = Store(env.path, authority=routes.Authority(env.gateway, env.wid))
    assert progress.view(item, store=reopened) == first
    assert not env.worker().run_once(env.wid)
    assert len(dispatch.runner_jobs()) == 1
    assert env.store.list_messages(env.uid, cid) == [receipt["message"]]


def test_restart_after_card_commit_recovers_link_but_never_launches_worker(env, dispatch, progress):
    cid = dispatch.conversation()
    receipt = dispatch.post(cid)
    claimed = env.store.claim_job()
    assert claimed["id"] == receipt["job"]["id"]
    origin = f"coworker:{cid}:{claimed['id']}"
    card = dispatch.boards.ensure_chat_task(
        origin, {"board": "work", "title": "Inspect the fixture", "body": "Exact requested body."}
    )
    # Crash checkpoint: card committed; no runner admission or front completion.
    reopened = Store(env.path, authority=routes.Authority(env.gateway, env.wid))
    assert reopened.recover_jobs() == 1
    result = progress.view(SimpleNamespace(cid=cid, job=claimed), store=reopened)
    assert result["state"] == "interrupted"
    assert result["task"] == {"board": "work", "number": card["number"]}
    assert result["task_progress"]["status"] == "todo" and result["task_progress"]["runner"] is None
    assert dispatch.runner_jobs() == []
    assert not env.worker().run_once(env.wid)


@pytest.mark.parametrize(
    "state", ["queued", "leased", "starting", "started", "failed", "uncertain", "cancelled"]
)
def test_runner_observation_uses_exact_origin_and_never_completes_task(
    env, dispatch, progress, state
):
    item = progress.start()
    ledger = runner_jobs.open_store(env.gateway, env.wid)
    with ledger.transaction() as conn:
        conn.execute(
            "UPDATE runner_jobs SET state=?,outcome_code='fixture observation' WHERE task_number=?",
            (state, item.number),
        )
    result = progress.view(item)["task_progress"]
    assert result["status"] == "todo"
    assert result["runner"]["state"] == state
    assert result["runner"]["outcome_code"] == "fixture observation"
    assert "summary" not in result and "completed" not in result
    assert len(dispatch.runner_jobs()) == 1


def test_other_runner_attempt_cannot_substitute_for_origin(env, progress):
    item = progress.start()
    ledger = runner_jobs.open_store(env.gateway, env.wid)
    with ledger.transaction() as conn:
        conn.execute("UPDATE runner_jobs SET request_key='another-request',state='uncertain'")
    result = progress.view(item)["task_progress"]
    assert result["runner"] is None and result["status"] == "todo"


def test_unconfigured_work_still_reports_real_task_progress(env, dispatch, progress):
    orchestration.set_enabled(env.db, env.principal, False)
    item = progress.start()
    dispatch.boards.task_comment(item.number, "Exact board progress without a runner.")
    result = progress.view(item)
    assert result["code"] == "task_created"
    assert result["task_progress"]["runner"] is None
    assert result["task_progress"]["events"][-1]["text"] == "Exact board progress without a runner."
    assert dispatch.runner_jobs() == []


def test_second_message_is_answered_while_canonical_work_remains_in_progress(
    env, dispatch, progress, monkeypatch
):
    item = progress.start()
    dispatch.boards.task_update(item.number, status="doing", status_line="Long task still working.")
    monkeypatch.setattr(runtime.model, "complete", lambda *args, **kwargs: "Exact front answer.")
    receipt = env.store.post_message(
        env.uid, item.cid, "A second question", "second", session_hash=sessions._hash(env.session)
    )
    assert receipt["job"]["state"] == "pending"
    assert env.worker().run_once(env.wid)
    assert env.store.list_messages(env.uid, item.cid)[-1]["text"] == "Exact front answer."
    assert progress.view(item)["task_progress"]["status"] == "doing"
    assert len(dispatch.runner_jobs()) == 1


def test_dm_group_and_per_viewer_runner_visibility_are_isolated(env, dispatch, progress):
    first = progress.start(title="First private task")
    second = progress.start(title="Second private task")
    group = progress.start(group=True, title="Shared task")
    dispatch.boards.task_comment(first.number, "PRIVATE_FIRST_RESULT")
    dispatch.boards.task_comment(second.number, "PRIVATE_SECOND_RESULT")
    dispatch.boards.task_comment(group.number, "SHARED_RESULT")
    assert "PRIVATE_SECOND_RESULT" not in json.dumps(progress.view(first))
    group_view = progress.view(group, user=env.other)["task_progress"]
    assert group_view["events"][-1]["text"] == "SHARED_RESULT"
    assert group_view["runner"] is None  # Existing runner visibility: requester or owner.
    assert "PRIVATE_FIRST_RESULT" not in json.dumps(group_view)
    with pytest.raises(StoreError, match="not_found"):
        progress.view(first, user=env.other)
    # Observing canonical information adds no audience-wide message.
    assert env.store.list_messages(env.other, group.cid) == [group.receipt["message"]]


@pytest.mark.parametrize(
    "change",
    [
        "origin_session",
        "origin_member",
        "viewer_member",
        "conversation_member",
        "audience",
        "bot_removed",
        "bot_binding",
        "visibility",
        "conversation_board",
        "task_board",
        "archived",
        "origin_binding",
        "legacy_binding",
        "cancelled",
    ],
)
def test_revocation_rebind_and_cancel_withhold_before_canonical_source_reads(
    env, dispatch, progress, monkeypatch, change
):
    item = progress.start(group=True)
    if change == "origin_session":
        sessions.start_session(env.db, env.uid, env.wid)  # No substitute for original session.
        sessions.end_session(env.db, env.session)
    elif change in ("origin_member", "viewer_member"):
        user = env.uid if change == "origin_member" else env.other
        with env.db._lock:
            env.db._conn.execute(
                "DELETE FROM memberships WHERE workspace_id=? AND user_id=?", (env.wid, user)
            )
    elif change in ("task_board", "archived", "origin_binding"):
        with dispatch.boards.store.transaction() as conn:
            if change == "task_board":
                conn.execute(
                    "UPDATE tasks SET board_id=(SELECT id FROM boards WHERE slug='plain') WHERE id=?",
                    (item.number,),
                )
            elif change == "archived":
                conn.execute("UPDATE boards SET archived=1 WHERE slug='work'")
            else:
                conn.execute("DELETE FROM chat_task_origins WHERE task_id=?", (item.number,))
    else:
        with env.store._transaction() as conn:
            if change == "conversation_member":
                conn.execute(
                    "DELETE FROM members WHERE conversation_id=? AND user_id=?", (item.cid, env.uid)
                )
            elif change == "audience":
                conn.execute(
                    "DELETE FROM members WHERE conversation_id=? AND user_id=?",
                    (item.cid, env.other),
                )
            elif change == "bot_removed":
                conn.execute("DELETE FROM conversation_bots WHERE conversation_id=?", (item.cid,))
            elif change == "bot_binding":
                conn.execute(
                    "UPDATE bots SET instructions='Changed identity' WHERE id=?", (env.bot["id"],)
                )
            elif change == "visibility":
                memory = conn.execute(
                    "INSERT INTO memories(conversation_id,text,actor,created_at) VALUES (?,'hidden','fixture','now')",
                    (item.cid,),
                ).lastrowid
                conn.execute("INSERT INTO memory_tombstones VALUES (?,'fixture','now')", (memory,))
            elif change == "conversation_board":
                conn.execute("UPDATE conversations SET board='plain' WHERE id=?", (item.cid,))
            elif change == "legacy_binding":
                conn.execute("UPDATE jobs SET progress_binding=NULL WHERE id=?", (item.job["id"],))
            else:
                conn.execute("UPDATE jobs SET state='cancelled' WHERE id=?", (item.job["id"],))
    # None of these refusals may read canonical text/events/evidence.
    original = task_progress._fields
    monkeypatch.setattr(
        task_progress, "_fields", lambda *args: pytest.fail("Denied source content read")
    )
    if change in ("viewer_member", "audience"):
        with pytest.raises(StoreError, match="not_found"):
            progress.view(item, user=env.other)
    else:
        result = progress.view(item, user=env.other)
        assert "task" not in result
        assert result["task_progress"]["availability"] == "unavailable"
    monkeypatch.setattr(task_progress, "_fields", original)


def test_current_viewer_session_is_required_even_when_origin_still_live(env, progress):
    item = progress.start(group=True)
    sessions.end_session(env.db, progress.other_session)
    result = progress.view(item, user=env.other)
    assert "task" not in result and result["task_progress"]["availability"] == "unavailable"
    assert progress.view(item)["task_progress"]["availability"] == "available"


def test_each_group_viewer_must_currently_read_the_canonical_board(env, progress, monkeypatch):
    item = progress.start(group=True)
    authority = env.store._authority
    original = authority.user

    def user(uid, **kwargs):
        result = original(uid, **kwargs)
        if uid == env.other:
            return replace(
                result,
                policy=Policy.build(
                    profile="full", scopes=["memory:read"], read_wings=["other"], trust="trusted"
                ),
            )
        return result

    monkeypatch.setattr(authority, "user", user)
    assert progress.view(item, user=env.other)["task_progress"]["availability"] == "unavailable"
    assert progress.view(item)["task_progress"]["availability"] == "available"


def test_bounded_whole_source_fields_and_redaction_without_fabricated_summary(
    env, dispatch, progress
):
    item = progress.start()
    full = "large original " * 400
    dispatch.boards.task_update(item.number, body=full, status_line="Small exact status.")
    for index in range(task_progress.EVENTS + 3):
        dispatch.boards.task_comment(item.number, f"Exact comment {index}")
    dispatch.boards.task_comment(item.number, full)
    # Synthetic credential shape, never any actual secret or credential source.
    dispatch.boards.task_comment(item.number, "Authorization: Bearer " + "fixture" * 5)
    result = progress.view(item)["task_progress"]
    assert result["omitted_fields"] == ["body"] and "body" not in result
    assert result["status_line"] == "Small exact status."
    assert len(result["events"]) == task_progress.EVENTS and result["events_omitted"]
    assert result["events"][-2]["omitted_fields"] == ["text"]
    assert "text" not in result["events"][-2]
    assert result["events"][-1]["redacted"]
    assert "fixturefixture" not in json.dumps(result)
    assert "summary" not in result


def test_embedded_nul_does_not_bypass_whole_field_size_limit(env, dispatch, progress):
    item = progress.start()
    body = "prefix\x00" + "original after nul " * 400
    dispatch.boards.task_update(item.number, body=body)
    result = progress.view(item)["task_progress"]
    assert "body" not in result and result["omitted_fields"] == ["body"]
    assert dispatch.boards.task_show(item.number)["body"] == body


def test_evidence_obeys_existing_memory_visibility_for_each_viewer(
    env, dispatch, progress, monkeypatch
):
    item = progress.start(group=True)
    for drawer_id, title, theme in (
        ("visible-artifact", "Visible evidence", "shared"),
        ("hidden-artifact", "HIDDEN_EVIDENCE_TITLE", "private"),
        ("legacy-artifact", "HIDDEN_UNKNOWN_THEME", None),
    ):
        dispatch.boards.store.link_drawer(
            item.number,
            drawer_id,
            env.principal.actor,
            title=title,
            theme=theme,
            principal=env.principal,
        )
    original = env.store._authority.user

    def scoped(uid, **kwargs):
        principal = original(uid, **kwargs)
        if uid == env.other:
            return replace(
                principal,
                policy=Policy.build(
                    profile="full", scopes=["memory:read"], read_wings=["shared"], trust="trusted"
                ),
            )
        return principal

    monkeypatch.setattr(env.store._authority, "user", scoped)
    result = progress.view(item, user=env.other)["task_progress"]
    assert [row["title"] for row in result["evidence"]] == ["Visible evidence"]
    assert "HIDDEN_" not in json.dumps(result)
    assert "hidden-artifact" not in json.dumps(result)
    assert result["evidence_bounded"] is True


def test_decision_is_exact_source_only_and_get_never_approves(env, dispatch, progress):
    item = progress.start()
    question, context = "Which fixture should I use?", "Exact decision context."
    dispatch.boards.task_update(
        item.number, status="review", question=question, options=["One", "Two"], context=context
    )
    before = dispatch.boards.task_show(item.number)
    result = progress.view(item)["task_progress"]
    assert result["status"] == "review" and result["ask"] == question
    assert result["review_context"] == context
    assert dispatch.boards.task_show(item.number) == before
    assert env.store.list_messages(env.uid, item.cid) == [item.receipt["message"]]
    dispatch.boards.decide(item.number, choice="One", reply="Exact authorized reply.", then="todo")
    decided = progress.view(item)["task_progress"]
    event = next(row for row in decided["events"] if row["kind"] == "decide")
    assert event["choice"] == "One" and event["text"] == "Exact authorized reply."


def test_legacy_schema_migration_preserves_intake_and_withholds_unbound_result(env, progress):
    item = progress.start()
    with env.store._transaction() as conn:
        conn.execute("ALTER TABLE jobs DROP COLUMN progress_binding")
    reopened = Store(env.path, authority=routes.Authority(env.gateway, env.wid))
    result = progress.view(item, store=reopened)
    assert result["task_progress"]["availability"] == "unavailable"
    assert reopened.list_messages(env.uid, item.cid) == [item.receipt["message"]]
    assert reopened.list_jobs(env.uid, item.cid)[0]["task"] == item.job["task"]


def test_response_caps_canonical_projections_and_marks_older_receipts(env, dispatch, monkeypatch):
    orchestration.set_enabled(env.db, env.principal, False)
    cid = dispatch.conversation()
    for index in range(21):
        dispatch.post(cid, text=f"/task Fixture {index}", key=f"fixture-{index}")
        assert env.worker().run_once(env.wid)
    calls, original = [], env.store._authority.task_progress

    def count(*args):
        calls.append(args[0]["id"])
        return original(*args)

    monkeypatch.setattr(env.store._authority, "task_progress", count)
    result = env.store.list_jobs(
        env.uid, cid, progress=True, session_hash=sessions._hash(env.session)
    )
    assert len(calls) == 20
    assert result[0]["task_progress"]["availability"] == "omitted" and "task" not in result[0]
    assert all(row["task_progress"]["availability"] == "available" for row in result[1:])
    assert len(env.store.list_messages(env.uid, cid)) == 21


@pytest.mark.parametrize("change", ["session", "memory", "bot", "cancel", "task_board"])
def test_changed_authority_during_source_read_cannot_publish(
    env, dispatch, progress, monkeypatch, change
):
    item = progress.start()
    original = task_progress._runner
    transaction = env.store._transaction
    active = []

    @contextmanager
    def capture_transaction():
        with transaction() as conn:
            active.append(conn)
            try:
                yield conn
            finally:
                active.pop()

    monkeypatch.setattr(env.store, "_transaction", capture_transaction)

    def change_after_source(*args):
        result = original(*args)
        if change == "session":
            sessions.end_session(env.db, env.session)
        elif change == "task_board":
            with dispatch.boards.store.transaction() as conn:
                conn.execute(
                    "UPDATE tasks SET board_id=(SELECT id FROM boards WHERE slug='plain') WHERE id=?",
                    (item.number,),
                )
        else:
            # Mutate the held transaction reentrantly to prove the publication
            # checks inspect current rows rather than the original Python row.
            conn = active[-1]
            if change == "memory":
                mid = conn.execute(
                    "INSERT INTO memories(conversation_id,text,actor,created_at) VALUES (?,'removed','fixture','now')",
                    (item.cid,),
                ).lastrowid
                conn.execute("INSERT INTO memory_tombstones VALUES (?,'fixture','now')", (mid,))
            elif change == "bot":
                conn.execute("UPDATE bots SET instructions='Rebound' WHERE id=?", (env.bot["id"],))
            else:
                conn.execute("UPDATE jobs SET state='cancelled' WHERE id=?", (item.job["id"],))
        return result

    monkeypatch.setattr(task_progress, "_runner", change_after_source)
    result = progress.view(item)
    assert "task" not in result and result["task_progress"]["availability"] == "unavailable"


def test_concurrent_projection_and_visibility_write_are_serialized_without_events(
    env, progress, monkeypatch
):
    item = progress.start()
    memory = env.store.add_memory(env.uid, item.cid, "Exact conversation memory.")
    entered, release, attempted = threading.Event(), threading.Event(), threading.Event()
    original = env.store._authority.task_progress

    def blocked(*args):
        entered.set()
        assert release.wait(3)
        return original(*args)

    monkeypatch.setattr(env.store._authority, "task_progress", blocked)

    def remove():
        attempted.set()
        return env.store.remove_memory(env.uid, item.cid, memory["id"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        read = pool.submit(progress.view, item)
        try:
            assert entered.wait(3)
            write = pool.submit(remove)
            assert attempted.wait(3)
            assert not write.done()
        finally:
            release.set()
        assert read.result(timeout=3)["task_progress"]["availability"] == "available"
        assert write.result(timeout=3) == {"ok": True}
    assert progress.view(item)["task_progress"]["availability"] == "unavailable"
    assert env.store.list_messages(env.uid, item.cid) == [item.receipt["message"]]


def test_api_detail_and_incremental_read_refresh_without_message_cursor_or_writes(
    env, dispatch, progress
):
    item = progress.start()
    client = API(env.gateway, env.wid, env.uid, env.other, "outsider")
    client.sessions[env.uid] = env.session
    status, detail = client.request("GET", f"/conversations/{item.cid}")
    assert status == 200 and detail["jobs"][0]["task_progress"]["runner"]["state"] == "queued"
    dispatch.boards.task_comment(item.number, "New canonical result without a chat message.")
    status, page = client.request(
        "GET", f"/conversations/{item.cid}/messages?after={item.receipt['message']['id']}&limit=1"
    )
    assert status == 200 and page["messages"] == []
    assert (
        page["jobs"][0]["task_progress"]["events"][-1]["text"]
        == "New canonical result without a chat message."
    )
    assert "session_hash" not in json.dumps(page) and "progress_binding" not in json.dumps(page)
    sessions.end_session(env.db, env.session)
    assert client.request("GET", f"/conversations/{item.cid}")[0] == 401


def test_replayed_post_and_terminal_cancel_cannot_reexpose_revoked_task(env, progress):
    item = progress.start(group=True)
    sessions.end_session(env.db, env.session)
    current_session = sessions.start_session(env.db, env.uid, env.wid)
    replay = env.store.post_message(
        env.uid,
        item.cid,
        item.receipt["message"]["text"],
        "request",
        session_hash=sessions._hash(current_session),
    )["job"]
    cancelled = env.store.cancel_job(
        env.other, item.job["id"], session_hash=sessions._hash(progress.other_session)
    )
    for result in (replay, cancelled):
        assert result["state"] == "done"  # Front cancellation cannot stop admitted work.
        assert "task" not in result and result["task_progress"]["availability"] == "unavailable"


def test_progress_source_is_read_only_and_replay_has_stable_revision(env, progress, monkeypatch):
    item = progress.start()
    original = task_progress._read
    queries = []

    @contextmanager
    def read(path):
        with original(path) as conn:
            conn.set_trace_callback(queries.append)
            yield conn

    monkeypatch.setattr(task_progress, "_read", read)
    first = progress.view(item)
    for _ in range(3):
        assert progress.view(item) == first
    assert queries
    assert not any(
        q.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "ALTER")) for q in queries
    )
    origin = f"coworker:{item.cid}:{item.job['id']}"
    assert any("chat:" + hashlib.sha256(origin.encode()).hexdigest() in q for q in queries)
