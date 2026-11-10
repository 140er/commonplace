"""Real local task/runner ledgers; no server, runner process or provider calls."""

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from commonplace import sessions
from commonplace.boards.service import BoardService
from commonplace.chat.coworkers import routes, runtime, store as coworker_store
from commonplace.extensions import ToolContext, ToolError
from commonplace.orch import runner_bridge, store as orch_store
from commonplace.projects import models, orchestration, seats
from commonplace.runner import jobs, registry
from tests._orch_helpers import config_toml
from tests.test_coworker_api import Request
from tests.test_coworker_runtime import env as env


@pytest.fixture
def dispatch(env):
    """Enroll only synthetic identities in the fixture's temporary control DB."""
    env.gateway.secure_cookies = False
    seats.ensure_schema(env.db)
    boards = BoardService(ToolContext(env.principal, env.gateway))
    boards.board_add(slug="work", wing="shared")
    boards.board_add(slug="plain", wing="shared")
    registry.ensure(env.db)
    enrolled = registry.enroll(
        env.db,
        env.principal,
        {"label": "fixture", "projects": ["demo"], "topics": ["worker"], "wings": ["shared"]},
    )
    machine = enrolled["machine_id"]
    registry.attest(
        env.db,
        env.principal,
        machine,
        {
            "topics": ["worker"],
            "provider_instance_id": "fixture",
            "worker_connection_id": enrolled["worker_connection_id"],
        },
    )
    models.put(
        env.db, env.principal, "work", {"worker": {"provider": "fixture", "model": "fixture"}}
    )
    runner_store = jobs.open_store(env.gateway, env.wid)
    configs = orch_store.open_store(env.gateway, env.wid)

    def configure(**changes):
        text = config_toml(board="work", machine=machine, **changes)
        return configs.write(
            env.principal,
            "work",
            text,
            connections=[],
            sha256=hashlib.sha256(text.encode()).hexdigest(),
        ).version

    configure()
    orchestration.set_enabled(env.db, env.principal, True)
    env.store._authority = routes.Authority(env.gateway, env.wid)
    member_session = sessions.start_session(env.db, env.other, env.wid)

    def conversation(**changes):
        return env.conversation(board="work", **changes)["id"]

    def post(
        cid, text="/task Inspect the fixture\nExact requested body.", key="request", user=None
    ):
        user = user or env.uid
        session = env.session if user == env.uid else member_session
        return env.store.post_message(user, cid, text, key, session_hash=sessions._hash(session))

    def runner_jobs():
        with runner_store.connection() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM runner_jobs ORDER BY id")]

    def outcome(cid, user=None):
        return env.store.list_jobs(user or env.uid, cid)[0]

    def start(origin, number, service=None):
        service = service or BoardService(ToolContext(env.principal, env.gateway))
        return service.start_chat_task(
            origin,
            number,
            board="work",
            authorize=lambda: sessions.person_principal(env.db, env.uid, env.wid),
        )

    return SimpleNamespace(
        boards=boards,
        machine=machine,
        configure=configure,
        conversation=conversation,
        post=post,
        runner_jobs=runner_jobs,
        outcome=outcome,
        start=start,
    )


def test_configured_task_queues_real_pinned_job_without_claim_or_private_context(env, dispatch):
    cid = dispatch.conversation()
    env.store.add_memory(env.uid, cid, "Private conversation fact: cobalt orchard.")
    intake = dispatch.post(cid)
    assert env.worker().run_once(env.wid)
    outcome = dispatch.outcome(cid)
    assert (outcome["state"], outcome["code"]) == ("done", "runner_queued")
    card = dispatch.boards.task_show(outcome["task"]["number"])
    assert card["created_by"] == f"user:{env.uid}"
    assert card["body"] == "Exact requested body."
    assert card["status"] == "todo" and card["assignee"] is None
    assert "cobalt orchard" not in json.dumps(card)
    (job,) = dispatch.runner_jobs()
    origin = f"coworker:{cid}:{intake['job']['id']}"
    assert job["request_key"] == "chat:" + hashlib.sha256(origin.encode()).hexdigest()
    assert job["requested_by"] == f"user:{env.uid}"
    assert job["task_number"] == card["number"]
    assert job["state"] == "queued" and job["thread_id"] is None
    assert job["config_version"] == 1
    assert (job["model_provider"], job["model"]) == ("fixture", "fixture")
    assert env.store.list_messages(env.uid, cid) == [intake["message"]]


@pytest.mark.parametrize("mode", ["off", "unconfigured"])
def test_off_or_unconfigured_runner_keeps_real_card(env, dispatch, mode):
    if mode == "off":
        orchestration.set_enabled(env.db, env.principal, False)
        cid = dispatch.conversation()
    else:
        cid = env.conversation(board="plain")["id"]
    dispatch.post(cid)
    assert env.worker().run_once(env.wid)
    result = dispatch.outcome(cid)
    assert result["state"] == "done"
    assert result["code"] == ("task_created" if mode == "off" else "runner_not_configured")
    assert dispatch.boards.task_show(result["task"]["number"])["status"] == "todo"
    assert dispatch.runner_jobs() == []


@pytest.mark.parametrize(
    "change,code",
    [
        ("disabled", "machine_unavailable"),
        ("revoked", "machine_unavailable"),
        ("reset", "machine_unavailable"),
        ("project", "project_not_allowed"),
        ("model", "model_unresolved"),
    ],
)
def test_unavailable_runner_returns_reason_and_preserves_card(env, dispatch, change, code):
    if change == "revoked":
        registry.revoke(env.db, env.principal, dispatch.machine)
    elif change == "model":
        models.put(env.db, env.principal, "work", {"worker": None})
    else:
        grant = {
            "disabled": {"enabled": False},
            "reset": {"topics": ["other"]},
            "project": {"projects": ["other"]},
        }[change]
        registry.patch(env.db, env.principal, dispatch.machine, grant)
    cid = dispatch.conversation()
    dispatch.post(cid)
    assert env.worker().run_once(env.wid)
    outcome = dispatch.outcome(cid)
    assert (outcome["state"], outcome["code"]) == ("done", "runner_" + code)
    assert dispatch.boards.task_show(outcome["task"]["number"])["status"] == "todo"
    assert dispatch.runner_jobs() == []


def test_persona_cannot_grant_member_runner_start_authority(env, dispatch):
    cid = dispatch.conversation(user=env.other)
    dispatch.post(cid, user=env.other)
    assert env.worker().run_once(env.wid)
    result = dispatch.outcome(cid, user=env.other)
    assert result["code"] == "runner_not_allowed"
    card = dispatch.boards.task_show(result["task"]["number"])
    assert card["created_by"] == f"user:{env.other}"
    assert card["assignee"] is None and dispatch.runner_jobs() == []


@pytest.mark.parametrize("change", ["session", "membership", "audience", "bot", "board"])
def test_authority_revoked_after_card_commit_never_admits_runner(
    env, dispatch, monkeypatch, change
):
    # Another live session must never substitute for the exact intake session.
    sessions.start_session(env.db, env.uid, env.wid)
    cid = dispatch.conversation()
    dispatch.post(cid)
    original = BoardService.ensure_chat_task

    def create_then_revoke(service, *args):
        card = original(service, *args)
        if change == "session":
            sessions.end_session(env.db, env.session)
        elif change == "membership":
            with env.db._lock:
                env.db._conn.execute(
                    "DELETE FROM memberships WHERE workspace_id=? AND user_id=?", (env.wid, env.uid)
                )
        elif change == "board":
            with dispatch.boards.store.transaction() as conn:
                conn.execute("UPDATE boards SET archived=1 WHERE slug='work'")
        else:
            with env.store._transaction() as conn:
                if change == "audience":
                    conn.execute("INSERT INTO members VALUES (?,?)", (cid, env.other))
                else:
                    conn.execute("DELETE FROM conversation_bots WHERE conversation_id=?", (cid,))
        return card

    monkeypatch.setattr(BoardService, "ensure_chat_task", create_then_revoke)
    assert env.worker().run_once(env.wid)
    assert dispatch.runner_jobs() == []
    with dispatch.boards.store.connection() as conn:
        assert conn.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1


def test_plan_guard_remains_pending_without_automatic_approval(env, dispatch, monkeypatch):
    cid = dispatch.conversation()
    dispatch.post(cid)
    original = BoardService.ensure_chat_task

    def guarded(service, *args):
        card = original(service, *args)
        dispatch.boards.task_update(card["number"], labels=["plan"])
        return card

    monkeypatch.setattr(BoardService, "ensure_chat_task", guarded)
    assert env.worker().run_once(env.wid)
    result = dispatch.outcome(cid)
    assert result["code"] == "runner_plan_pending"
    card = dispatch.boards.task_show(result["task"]["number"])
    assert "plan" in card["labels"] and card["status"] == "todo"
    assert dispatch.runner_jobs() == []


def test_origin_binding_is_required_for_runner_admission(env, dispatch):
    card = dispatch.boards.ensure_chat_task("actual-origin", {"board": "work", "title": "A task"})
    with pytest.raises(ToolError, match="origin not found"):
        dispatch.start("another-origin", card["number"])
    assert dispatch.runner_jobs() == []


def test_canonical_replay_after_reopen_returns_same_card_and_runner_job(env, dispatch):
    origin = "coworker:replay:1"
    request = {"board": "work", "title": "Replay", "body": "Exact words."}
    card = dispatch.boards.ensure_chat_task(origin, request)
    first = dispatch.start(origin, card["number"])
    reopened = BoardService(ToolContext(env.principal, env.gateway))
    again = reopened.ensure_chat_task(origin, request)
    second = dispatch.start(origin, again["number"], service=reopened)
    assert first["status"] == 201 and second["status"] == 200
    assert first["job"]["job_id"] == second["job"]["job_id"]
    assert card["number"] == again["number"]
    assert len(dispatch.runner_jobs()) == 1
    assert len(reopened.task_list(board="work")) == 1


def test_cancelled_runner_job_replay_does_not_requeue_work(env, dispatch):
    origin = "coworker:cancelled:1"
    card = dispatch.boards.ensure_chat_task(origin, {"board": "work", "title": "Cancelled"})
    first = dispatch.start(origin, card["number"])
    jobs.cancel(jobs.open_store(env.gateway, env.wid), first["job"]["job_id"], env.principal)
    replay = dispatch.start(origin, card["number"])
    assert replay["status"] == 200 and replay["job"]["state"] == "cancelled"
    assert [job["id"] for job in dispatch.runner_jobs()] == [first["job"]["job_id"]]


def test_concurrent_origin_retries_share_canonical_card_and_runner_job(env, dispatch):
    origin = "coworker:concurrent:1"
    request = {"board": "work", "title": "Concurrent request"}

    def admit():
        service = BoardService(ToolContext(env.principal, env.gateway))
        card = service.ensure_chat_task(origin, request)
        return dispatch.start(origin, card["number"], service=service)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(admit) for _ in range(2)]
        results = [future.result(timeout=5) for future in futures]
    assert sorted(result["status"] for result in results) == [200, 201]
    assert len({result["job"]["job_id"] for result in results}) == 1
    assert len(dispatch.runner_jobs()) == len(dispatch.boards.task_list(board="work")) == 1


def test_restart_after_admission_before_chat_ack_keeps_one_card_and_job(env, dispatch):
    cid = dispatch.conversation()
    receipt = dispatch.post(cid)
    # Simulate process loss after canonical admission, before chat finish_job.
    claimed = env.worker()._claim(env.wid)
    assert claimed[2]["id"] == receipt["job"]["id"]
    origin = f"coworker:{cid}:{receipt['job']['id']}"
    card = dispatch.boards.ensure_chat_task(
        origin,
        {"board": "work", "title": "Inspect the fixture", "body": "Exact requested body."},
    )
    admitted = dispatch.start(origin, card["number"])
    assert not env.worker().run_once(env.wid)
    assert dispatch.outcome(cid)["code"] == "interrupted"
    assert dispatch.post(cid)["job"]["id"] == receipt["job"]["id"]
    assert len(dispatch.boards.task_list(board="work")) == 1
    assert [job["id"] for job in dispatch.runner_jobs()] == [admitted["job"]["job_id"]]


def test_lost_runner_ack_and_intake_retry_never_queue_second_worker(env, dispatch, monkeypatch):
    cid = dispatch.conversation()
    receipt = dispatch.post(cid)
    original = runner_bridge.fenced_start

    def lose_ack(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("synthetic lost admission acknowledgement")

    monkeypatch.setattr(runner_bridge, "fenced_start", lose_ack)
    assert env.worker().run_once(env.wid)
    result = dispatch.outcome(cid)
    assert result["code"] == "runner_interrupted" and result["task"] is not None
    assert len(dispatch.runner_jobs()) == 1
    assert dispatch.post(cid)["job"]["id"] == receipt["job"]["id"]
    reopened = coworker_store.Store(env.path, authority=routes.Authority(env.gateway, env.wid))
    monkeypatch.setattr(runtime, "open_store", lambda *args: reopened)
    assert not env.worker().run_once(env.wid)
    monkeypatch.setattr(runner_bridge, "fenced_start", original)
    replay = dispatch.start(f"coworker:{cid}:{receipt['job']['id']}", result["task"]["number"])
    assert replay["status"] == 200
    assert replay["job"]["job_id"] == dispatch.runner_jobs()[0]["id"]
    assert len(dispatch.runner_jobs()) == 1


def test_replay_with_changed_config_conflicts_instead_of_launching_again(env, dispatch):
    origin = "coworker:config-replay:1"
    card = dispatch.boards.ensure_chat_task(origin, {"board": "work", "title": "Replay"})
    first = dispatch.start(origin, card["number"])
    dispatch.configure(prompt="Changed owner-approved fixture instructions.")
    with pytest.raises(runner_bridge.StartRefused) as error:
        dispatch.start(origin, card["number"])
    assert error.value.code == "request_conflict"
    assert [job["id"] for job in dispatch.runner_jobs()] == [first["job"]["job_id"]]


def test_config_changed_between_snapshot_and_fence_refuses_admission(env, dispatch, monkeypatch):
    cid = dispatch.conversation()
    dispatch.post(cid)
    original = runner_bridge.fenced_start

    def change_config(*args, **kwargs):
        dispatch.configure(prompt="Changed before durable admission.")
        return original(*args, **kwargs)

    monkeypatch.setattr(runner_bridge, "fenced_start", change_config)
    assert env.worker().run_once(env.wid)
    assert dispatch.outcome(cid)["code"] == "runner_config_changed"
    assert dispatch.outcome(cid)["task"] is not None
    assert dispatch.runner_jobs() == []


@pytest.mark.parametrize("change,code", [("card", "card_changed"), ("machine", "config_changed")])
def test_final_authority_check_also_refreshes_card_and_runner_binding(env, dispatch, change, code):
    origin = "coworker:fresh-binding:1"
    card = dispatch.boards.ensure_chat_task(origin, {"board": "work", "title": "Fresh"})
    checks = 0

    def authorize():
        nonlocal checks
        checks += 1
        if checks == 2:
            if change == "card":
                dispatch.boards.task_update(card["number"], labels=["plan"])
            else:
                registry.patch(env.db, env.principal, dispatch.machine, {"enabled": False})
        return env.principal

    with pytest.raises(runner_bridge.StartRefused) as error:
        dispatch.boards.start_chat_task(origin, card["number"], board="work", authorize=authorize)
    assert error.value.code == code and dispatch.runner_jobs() == []


def test_blocked_admission_leaves_http_intake_and_other_conversation_available(
    env, dispatch, monkeypatch
):
    a, b = dispatch.conversation(), dispatch.conversation()
    dispatch.post(a)
    entered, release, answered, admitted = (threading.Event() for _ in range(4))
    original_start = runner_bridge.fenced_start
    original_reply, original_finish = env.store.add_bot_reply, env.store.finish_job

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(5), "test must release synthetic admission"
        return original_start(*args, **kwargs)

    def reply(*args, **kwargs):
        result = original_reply(*args, **kwargs)
        answered.set()
        return result

    def finish(*args, **kwargs):
        result = original_finish(*args, **kwargs)
        if kwargs.get("code") == "runner_queued":
            admitted.set()
        return result

    monkeypatch.setattr(runner_bridge, "fenced_start", blocked)
    monkeypatch.setattr(runtime.model, "complete", lambda *args, **kwargs: "Fixture answer")
    monkeypatch.setattr(env.store, "add_bot_reply", reply)
    monkeypatch.setattr(env.store, "finish_job", finish)
    worker = env.worker(max_workers=2).start()
    env.gateway.coworker_worker = worker

    def http_post(cid):
        body = json.dumps({"text": "Follow-up question", "request_id": "follow-up"}).encode()
        request = Request(
            "POST",
            f"/api/coworkers/conversations/{cid}/messages",
            [
                ("Host", "app.test"),
                ("Origin", "http://app.test"),
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(body))),
                ("Cookie", f"cmmn_session={env.session}"),
            ],
            body,
        )
        routes.handle(env.gateway, request)
        return request.status, json.loads(request.wfile.getvalue())

    try:
        assert entered.wait(5)
        with ThreadPoolExecutor(max_workers=1) as pool:
            for cid in (a, b):
                status, receipt = pool.submit(http_post, cid).result(timeout=2)
                assert status == 201 and receipt["job"]["state"] == "pending"
        assert answered.wait(5)
        assert not release.is_set()
        assert dispatch.runner_jobs() == []
        release.set()
        assert admitted.wait(5)
    finally:
        release.set()
        worker.stop()
