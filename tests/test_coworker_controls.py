"""Actual synthetic canonical stores/API, without providers or worker launches.

Authored for the parent's grouped run. The start ledger has no running-worker
termination protocol: task/front completion is deliberately never such evidence.
"""

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

from commonplace import sessions
from commonplace.boards.service import BoardService
from commonplace.chat.coworkers import routes, runtime, task_controls
from commonplace.chat.coworkers.store import MAX_CONTROLS, Store, StoreError
from commonplace.runner import jobs, registry
from tests.test_coworker_api import API
from tests.test_coworker_dispatch import dispatch as dispatch
from tests.test_coworker_progress import progress as progress
from tests.test_coworker_runtime import env as env


def control(
    env,
    item,
    *,
    operation="follow_up",
    text="Exact explicit follow-up.",
    key="control",
    user=None,
    session=None,
    store=None,
):
    return (store or env.store).control_task(
        user or env.uid,
        item.job["id"],
        operation,
        key,
        text if operation == "follow_up" else "",
        session_hash=sessions._hash(session or env.session),
    )["receipt"]


def comments(dispatch, item):
    with dispatch.boards.store.connection() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT 'user:' || stamp_user AS actor,text FROM task_events "
                "WHERE task_id=? AND kind='comment' ORDER BY id",
                (item.number,),
            )
        ]


def state(env, value):
    with jobs.open_store(env.gateway, env.wid).transaction() as conn:
        conn.execute(
            "UPDATE runner_jobs SET state=?,starting_at=?,lease_until=?,lease_token='fixture-lease'",
            (value, jobs._now(), jobs._after(jobs._now(), 3600)),
        )


def test_queued_stop_fences_actual_canonical_lease_and_never_starts_replacement(
    env, dispatch, progress
):
    item = progress.start()
    receipt = control(env, item, operation="stop")
    assert (receipt["state"], receipt["code"]) == ("applied", "queued_start_cancelled")
    (run,) = dispatch.runner_jobs()
    assert run["state"] == "cancelled" and run["cancel_requested_at"]
    machine = registry.get_machine(env.db, env.wid, dispatch.machine)
    assert jobs.lease(jobs.open_store(env.gateway, env.wid), machine) is None
    assert control(env, item, operation="stop") == receipt
    assert not env.worker().run_once(env.wid)
    assert len(dispatch.runner_jobs()) == 1 and comments(dispatch, item) == []
    assert dispatch.boards.task_show(item.number)["status"] == "todo"
    assert env.store.list_messages(env.uid, item.cid) == [item.receipt["message"]]


def test_leased_stop_uses_canonical_fence_before_starting(env, dispatch, progress):
    item = progress.start()
    machine = registry.get_machine(env.db, env.wid, dispatch.machine)
    ledger = jobs.open_store(env.gateway, env.wid)
    leased = jobs.lease(ledger, machine)
    assert leased and leased["state"] == "leased"
    receipt = control(env, item, operation="stop")
    assert receipt["code"] == "queued_start_cancelled"
    with pytest.raises(jobs.Conflict):
        jobs.starting(ledger, machine, leased["lease_token"], validate=lambda row: None)
    assert len(dispatch.runner_jobs()) == 1


def test_inflight_stop_is_only_received_even_after_task_and_front_are_done(env, dispatch, progress):
    item = progress.start()
    state(env, "starting")
    receipt = control(env, item, operation="stop")
    assert receipt["state"] == "received"
    assert receipt["code"] == "stop_requested_termination_unconfirmed"
    (run,) = dispatch.runner_jobs()
    assert run["state"] == "starting" and run["cancel_requested_at"]
    # Real canonical result ingestion records a LAUNCH, not worker termination.
    machine = registry.get_machine(env.db, env.wid, dispatch.machine)
    jobs.result(
        jobs.open_store(env.gateway, env.wid),
        machine,
        "fixture-lease",
        "started",
        thread_id="fixture-thread",
    )
    dispatch.boards.task_update(item.number, status="doing")
    dispatch.boards.task_update(item.number, status="done")
    assert item.job["state"] == "done"
    assert control(env, item, operation="stop") == receipt
    second = control(env, item, operation="stop", key="new-explicit-stop")
    assert (second["state"], second["code"]) == ("unsupported", "worker_stop_unsupported")
    assert "terminated" not in json.dumps(second)
    assert len(dispatch.runner_jobs()) == 1


def test_uncertain_start_is_never_resolved_retried_or_reported_terminated(env, dispatch, progress):
    item = progress.start()
    state(env, "uncertain")
    receipt = control(env, item, operation="stop")
    assert (receipt["state"], receipt["code"]) == ("uncertain", "runner_outcome_unknown")
    assert control(env, item, operation="stop") == receipt
    assert dispatch.runner_jobs()[0]["state"] == "uncertain"
    assert len(dispatch.runner_jobs()) == 1


def test_followup_exact_comment_and_receipt_share_no_private_history_memory_or_new_work(
    env, dispatch, progress
):
    item = progress.start(group=True)
    dispatch.post(item.cid, text="PRIVATE_CHAT_CANARY", key="ordinary")
    env.store.add_memory(env.uid, item.cid, "PRIVATE_MEMORY_CANARY")
    text = "  Explicit instruction <script>quoted()</script>\nSecond line.  "
    receipt = control(env, item, text=text)
    assert (receipt["state"], receipt["code"]) == (
        "unsupported",
        "comment_saved_application_unsupported",
    )
    assert receipt["text"] == text
    assert comments(dispatch, item) == [{"actor": "user:" + env.uid, "text": text}]
    reopened = Store(env.path, authority=routes.Authority(env.gateway, env.wid))
    assert control(env, item, text=text, store=reopened) == receipt
    assert len(comments(dispatch, item)) == 1 and len(dispatch.runner_jobs()) == 1
    snapshot = progress.view(item, store=reopened)["task_progress"]
    assert snapshot["controls"]["receipts"] == [receipt]
    assert "PRIVATE_" not in json.dumps(snapshot)
    # Group members may read the shared canonical comment but cannot inherit the
    # owner's control capability or private receipt from group membership.
    other = progress.view(item, user=env.other)["task_progress"]["controls"]
    assert other == {"can_follow_up": False, "can_stop": False, "receipts": []}


@pytest.mark.parametrize("operation", ["follow_up", "stop"])
def test_lost_canonical_ack_replay_and_restart_never_repeat_effect(
    env, dispatch, progress, monkeypatch, operation
):
    item = progress.start()
    apply, calls = task_controls.apply, []

    def lost(*args):
        calls.append(1)
        apply(*args)
        raise TimeoutError("synthetic acknowledgement loss")

    monkeypatch.setattr(task_controls, "apply", lost)
    receipt = control(env, item, operation=operation)
    assert receipt["state"] == "uncertain" and receipt["code"] == "acknowledgement_pending"
    reopened = Store(env.path, authority=routes.Authority(env.gateway, env.wid))
    assert control(env, item, operation=operation, store=reopened) == receipt
    assert len(calls) == 1
    assert len(comments(dispatch, item)) == (1 if operation == "follow_up" else 0)
    assert len(dispatch.runner_jobs()) == 1
    if operation == "stop":
        assert dispatch.runner_jobs()[0]["state"] == "cancelled"


def test_conflicting_text_job_and_target_under_same_request_id_never_repeat_effect(
    env, dispatch, progress
):
    item, other = progress.start(), progress.start()
    original = control(env, item)
    for target, text in ((item, "Changed body"), (other, "Exact explicit follow-up.")):
        with pytest.raises(StoreError, match="request_conflict"):
            control(env, target, text=text)
    assert control(env, item) == original
    assert len(comments(dispatch, item)) == 1 and comments(dispatch, other) == []
    with jobs.open_store(env.gateway, env.wid).transaction() as conn:
        conn.execute(
            "UPDATE runner_jobs SET request_hash='changed' WHERE task_number=?", (item.number,)
        )
    with pytest.raises(StoreError, match="not_found"):
        control(env, item)
    assert len(comments(dispatch, item)) == 1


@pytest.mark.parametrize(
    "change",
    [
        "origin_session",
        "viewer_session",
        "viewer_role",
        "audience",
        "bot_binding",
        "bot_removed",
        "visibility",
        "conversation_board",
        "task_board",
        "archived",
        "origin_binding",
        "legacy",
    ],
)
def test_current_authority_and_binding_fence_new_controls_and_private_replays(
    env, dispatch, progress, change
):
    item = progress.start(group=True)
    # A separate current owner can control the original run, but only with their
    # own current session and still-live conversation audience.
    with env.db._lock:
        env.db._conn.execute(
            "UPDATE memberships SET role='owner' WHERE workspace_id=? AND user_id=?",
            (env.wid, env.other),
        )
    kwargs = {"user": env.other, "session": progress.other_session}
    control(env, item, text="PRIVATE_EXPLICIT_INSTRUCTION", **kwargs)
    if change == "origin_session":
        sessions.end_session(env.db, env.session)
    elif change == "viewer_session":
        sessions.end_session(env.db, progress.other_session)
    elif change == "viewer_role":
        with env.db._lock:
            env.db._conn.execute(
                "UPDATE memberships SET role='member' WHERE workspace_id=? AND user_id=?",
                (env.wid, env.other),
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
            if change == "audience":
                conn.execute(
                    "DELETE FROM members WHERE conversation_id=? AND user_id=?",
                    (item.cid, env.other),
                )
            elif change == "bot_binding":
                conn.execute(
                    "UPDATE bots SET instructions='Changed binding' WHERE id=?", (env.bot["id"],)
                )
            elif change == "bot_removed":
                conn.execute("DELETE FROM conversation_bots WHERE conversation_id=?", (item.cid,))
            elif change == "visibility":
                mid = conn.execute(
                    "INSERT INTO memories(conversation_id,text,actor,created_at) VALUES (?,'hidden','fixture','now')",
                    (item.cid,),
                ).lastrowid
                conn.execute("INSERT INTO memory_tombstones VALUES (?,'fixture','now')", (mid,))
            elif change == "conversation_board":
                conn.execute("UPDATE conversations SET board='plain' WHERE id=?", (item.cid,))
            else:
                conn.execute("UPDATE jobs SET progress_binding=NULL WHERE id=?", (item.job["id"],))
    for key in ("control", "new-control"):
        with pytest.raises(StoreError, match="not_found"):
            control(env, item, text="PRIVATE_EXPLICIT_INSTRUCTION", key=key, **kwargs)
    assert len(comments(dispatch, item)) == 1
    assert dispatch.runner_jobs()[0]["state"] == "queued"


def test_group_read_members_foreign_job_and_revoked_run_cannot_control(env, dispatch, progress):
    item = progress.start(group=True)
    with pytest.raises(StoreError, match="not_found"):
        control(env, item, user=env.other, session=progress.other_session)
    dm = progress.start()
    with pytest.raises(StoreError, match="not_found"):
        control(env, dm, user=env.other, session=progress.other_session)
    with jobs.open_store(env.gateway, env.wid).transaction() as conn:
        conn.execute(
            "UPDATE runner_jobs SET request_key='foreign-origin' WHERE task_number=?",
            (item.number,),
        )
    with pytest.raises(StoreError, match="not_found"):
        control(env, item, operation="stop")
    assert comments(dispatch, item) == [] and len(dispatch.runner_jobs()) == 2


def test_actual_requester_remains_authorized_after_role_change(env, dispatch, progress):
    item = progress.start()
    with env.db._lock:
        env.db._conn.execute(
            "UPDATE memberships SET role='member' WHERE workspace_id=? AND user_id=?",
            (env.wid, env.uid),
        )
    assert control(env, item, operation="stop")["code"] == "queued_start_cancelled"
    assert len(dispatch.runner_jobs()) == 1


def test_concurrent_same_control_has_one_effect_and_no_lock_over_canonical_work(
    env, dispatch, progress, monkeypatch
):
    item = progress.start()
    entered, release = threading.Event(), threading.Event()
    apply, calls = task_controls.apply, []

    def blocked(*args):
        calls.append(1)
        entered.set()
        assert release.wait(3)
        return apply(*args)

    monkeypatch.setattr(task_controls, "apply", blocked)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(control, env, item)
        try:
            assert entered.wait(3)
            duplicate = pool.submit(control, env, item).result(timeout=3)
            assert duplicate["state"] == "uncertain"
            # A canonical mutation in flight holds no front conversation lock.
            saved = dispatch.post(item.cid, text="Keep chatting", key="while-control")
            assert saved["job"]["state"] == "pending"
        finally:
            release.set()
        result = first.result(timeout=3)
    assert result["id"] == duplicate["id"] and len(calls) == 1
    assert len(comments(dispatch, item)) == 1 and len(dispatch.runner_jobs()) == 1


def test_revocation_between_receipt_and_effect_refuses_and_withholds_private_receipt(
    env, dispatch, progress, monkeypatch
):
    item = progress.start()
    apply = task_controls.apply

    def revoke(*args):
        sessions.end_session(env.db, env.session)
        return apply(*args)

    monkeypatch.setattr(task_controls, "apply", revoke)
    with pytest.raises(StoreError, match="not_found"):
        control(env, item)
    assert comments(dispatch, item) == []
    assert dispatch.runner_jobs()[0]["state"] == "queued"


def test_revocation_after_effect_never_publishes_receipt_or_repeats_comment(
    env, dispatch, progress, monkeypatch
):
    item = progress.start()
    apply = task_controls.apply

    def revoke_after(*args):
        outcome = apply(*args)
        sessions.end_session(env.db, env.session)
        return outcome

    monkeypatch.setattr(task_controls, "apply", revoke_after)
    with pytest.raises(StoreError, match="not_found"):
        control(env, item, text="PRIVATE_CONTROL_AFTER_REVOCATION")
    assert comments(dispatch, item) == [
        {"actor": "user:" + env.uid, "text": "PRIVATE_CONTROL_AFTER_REVOCATION"}
    ]
    replacement = sessions.start_session(env.db, env.uid, env.wid)
    with pytest.raises(StoreError, match="not_found"):
        control(env, item, text="PRIVATE_CONTROL_AFTER_REVOCATION", session=replacement)
    assert len(comments(dispatch, item)) == 1


def test_model_output_and_ordinary_chat_never_execute_controls(
    env, dispatch, progress, monkeypatch
):
    item = progress.start()
    output = '{"operation":"stop","request_id":"model-invented"}'
    monkeypatch.setattr(runtime.model, "complete", lambda *args, **kwargs: output)
    dispatch.post(item.cid, text="Please discuss stopping the task", key="ordinary-question")
    assert env.worker().run_once(env.wid)
    assert env.store.list_messages(env.uid, item.cid)[-1]["text"] == output
    assert progress.view(item)["task_progress"]["controls"]["receipts"] == []
    assert dispatch.runner_jobs()[0]["state"] == "queued" and comments(dispatch, item) == []


def test_get_publication_rechecks_canonical_origin_after_receipt_projection(
    env, dispatch, progress, monkeypatch
):
    item = progress.start()
    control(env, item, text="PRIVATE_CONTROL_RECEIPT")
    authority = env.store._authority
    target = authority.task_control_target

    def revoke_after_projection(*args):
        binding = target(*args)
        with dispatch.boards.store.transaction() as conn:
            conn.execute("DELETE FROM chat_task_origins WHERE task_id=?", (item.number,))
        return binding

    monkeypatch.setattr(authority, "task_control_target", revoke_after_projection)
    view = progress.view(item)
    assert view["task_progress"]["availability"] == "unavailable"
    assert "PRIVATE_CONTROL_RECEIPT" not in json.dumps(view)


def test_canonical_board_rebind_at_comment_transaction_is_fenced(
    env, dispatch, progress, monkeypatch
):
    item = progress.start()
    transaction = BoardService.transaction

    @contextmanager
    def rebind(service):
        with transaction(service) as conn:
            conn.execute(
                "UPDATE tasks SET board_id=(SELECT id FROM boards WHERE slug='plain') WHERE id=?",
                (item.number,),
            )
            yield conn

    monkeypatch.setattr(BoardService, "transaction", rebind)
    # The canonical transaction rolls back rather than commenting on a rebind.
    receipt = control(env, item)
    assert receipt["state"] == "uncertain" and comments(dispatch, item) == []


def test_api_controls_same_origin_cookie_person_bounded_explicit_fields_and_read_only_get(
    env, dispatch, progress
):
    item = progress.start()
    api = API(env.gateway, env.wid, env.uid, env.other, "outsider")
    api.sessions[env.uid] = env.session
    path = f"/jobs/{item.job['id']}/controls"
    body = {"operation": "follow_up", "text": "  Exact REST instruction.  ", "request_id": "rest"}
    for headers in (
        {"Origin": None},
        {"Origin": "http://foreign.test"},
        {"Authorization": "Bearer fixture"},
        {"Content-Type": "text/plain"},
    ):
        assert api.request("POST", path, body, headers=headers)[0] == 403
    for extra in (
        {"board": "plain"},
        {"run": "arbitrary"},
        {"task": item.number},
        {"user_id": env.other},
    ):
        assert api.request("POST", path, {**body, **extra})[0] == 400
    assert api.request("POST", path, {**body, "text": "x" * 4001})[0] == 400
    assert api.request("POST", path, {**body, "operation": "approve"})[0] == 400
    assert api.request("POST", path, {**body, "request_id": []})[0] == 400
    assert api.request("POST", path, {**body, "operation": "stop"})[0] == 400
    status, response = api.request("POST", path, body)
    assert status == 200 and response["receipt"]["text"] == body["text"]
    assert api.request("POST", path, body) == (status, response)
    assert api.request("POST", path, {**body, "text": "changed"})[0] == 409
    for _ in range(2):
        status, view = api.request("GET", f"/conversations/{item.cid}")
        assert status == 200
        assert view["jobs"][0]["task_progress"]["controls"]["receipts"] == [response["receipt"]]
    assert len(comments(dispatch, item)) == 1 and len(dispatch.runner_jobs()) == 1
    assert not {"target", "session_hash", "progress_binding"} & response["receipt"].keys()
    assert api.request("POST", path, body, user=env.other)[0] == 404


def test_control_history_and_count_are_bounded(env, dispatch, progress):
    item = progress.start()
    for index in range(12):
        control(env, item, key=f"bounded-{index}")
    history = progress.view(item)["task_progress"]["controls"]["receipts"]
    assert len(history) == 10 and history[0]["request_id"] == "bounded-2"
    with env.store._transaction() as conn:
        first = conn.execute("SELECT * FROM task_controls LIMIT 1").fetchone()
        for index in range(12, MAX_CONTROLS):
            conn.execute(
                "INSERT INTO task_controls(job_id,actor,operation,request_id,text,target,state,code,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    first["job_id"],
                    first["actor"],
                    first["operation"],
                    f"bounded-{index}",
                    first["text"],
                    first["target"],
                    first["state"],
                    first["code"],
                    first["created_at"],
                    first["updated_at"],
                ),
            )
    with pytest.raises(StoreError, match="at_capacity"):
        control(env, item, key="overflow")
    assert control(env, item, key="bounded-0")["request_id"] == "bounded-0"


def test_control_journal_uses_workspace_crypto_and_additive_migration(tmp_path, monkeypatch):
    pytest.importorskip("sqlcipher3")
    from mempalace import sqlite_crypto

    directory = tmp_path / "workspaces" / "ws_synthetic"
    directory.mkdir(parents=True)
    (directory / "workspace.json").write_text(
        json.dumps(
            {
                "encryption": {
                    "version": 1,
                    "cipher": "sqlcipher",
                    "workspace_id": "ws_synthetic",
                    "key_id": "a" * 32,
                    "database_paths": [],
                }
            }
        )
    )
    monkeypatch.setattr(sqlite_crypto, "workspace_key", lambda *_: b"k" * 32)
    path = directory / "coworkers.sqlite3"
    store = Store(path)
    bot = store.create_bot("alice", "Fixture")
    dm = store.create_conversation("alice", "dm", bot_ids=[bot["id"]])["id"]
    intake = store.post_message("alice", dm, "Exact original intake", "one")
    with store._transaction() as conn:
        conn.execute("DROP TABLE task_controls")
    reopened = Store(path)
    with reopened._transaction() as conn:
        conn.execute(
            "INSERT INTO task_controls(job_id,actor,operation,request_id,text,target,state,code,created_at,updated_at) VALUES (?,'user:alice','follow_up','fixture','ENCRYPTED_CONTROL_CANARY','{}','uncertain','acknowledgement_pending','now','now')",
            (intake["job"]["id"],),
        )
    assert Store(path).list_messages("alice", dm) == [intake["message"]]
    assert b"ENCRYPTED_CONTROL_CANARY" not in path.read_bytes()
    with sqlite3.connect(path) as conn:
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("SELECT * FROM task_controls").fetchall()
