"""Deterministic coworker workers: real stores, no server or real provider."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from commonplace import events, sessions
from commonplace.chat import settings
from commonplace.chat.coworkers import runtime, store as coworker_store
from commonplace.chat.types import MAX_INPUT_CHARS, MAX_REPLY_CHARS, ModelError
from commonplace.control_db import ControlDB
from commonplace.extensions import ToolError
from commonplace.projects import orchestration
from commonplace.workers import WorkerSupervisor


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = ControlDB(str(tmp_path / "control.sqlite3"))
    sessions.ensure_schema(db)
    settings.ensure_schema(db)
    events.ensure_schema(db)
    orchestration.ensure_schema(db)
    uid = db.create_user("person@example.test")
    other = db.create_user("other@example.test")
    wid = db.create_workspace("synthetic", uid)
    db.add_member(wid, other, "member", actor=uid)
    supervisor = WorkerSupervisor(str(tmp_path))
    gateway = SimpleNamespace(
        db=db,
        supervisor=supervisor,
        workspace_dir=lambda ws: str(supervisor.workspace_dir(ws)),
        limiter=SimpleNamespace(check=lambda _limits: False),
    )
    session = sessions.start_session(db, uid, wid)
    sessions.start_session(db, other, wid)
    path = supervisor.workspace_dir(wid) / "coworkers.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    store = coworker_store.Store(path)
    monkeypatch.setattr(runtime, "open_store", lambda gateway, ws: store if ws == wid else None)

    def forbidden(*args, **kwargs):
        pytest.fail("No real provider or workspace-wide memory API is allowed")

    monkeypatch.setattr(runtime.model, "complete", forbidden)
    gateway.call_memory_tool = forbidden
    principal = sessions.person_principal(db, uid, wid)
    settings.set_front(
        db,
        principal,
        {
            "front": {
                "provider": "openai_compatible",
                "model": "fixture",
                "base_url": "http://127.0.0.1:9",
            }
        },
    )
    bot = store.create_bot(uid, "Nia", instructions="Use a calm tone.")
    workers = []

    def worker(**kwargs):
        value = runtime.CoworkerWorker(gateway, start=False, **kwargs)
        workers.append(value)
        return value

    def conversation(user=uid, **kwargs):
        return store.create_conversation(user, bot_ids=[bot["id"]], kind="dm", **kwargs)

    def post(cid, text="Hello", user=uid, key=None, bot_id=None):
        nonlocal serial
        serial += 1
        return store.post_message(user, cid, text, key or f"request-{serial}", bot_id=bot_id)

    serial = 0
    value = SimpleNamespace(
        db=db,
        gateway=gateway,
        uid=uid,
        other=other,
        wid=wid,
        session=session,
        store=store,
        bot=bot,
        worker=worker,
        conversation=conversation,
        post=post,
        principal=principal,
        path=path,
    )
    try:
        yield value
    finally:
        for value in workers:
            value.stop()
        close = getattr(store, "close", None)
        if close:
            close()
        db.close()


def jobs(env, cid, user=None):
    return env.store.list_jobs(user or env.uid, cid)


def replies(env, cid, user=None):
    return [
        m for m in env.store.list_messages(user or env.uid, cid) if m["actor"].startswith("bot:")
    ]


def test_blocked_a_leaves_b_and_store_intake_free(env, monkeypatch):
    a, b = env.conversation(), env.conversation()
    env.post(a["id"], "A blocked question")
    entered, release, b_answered = threading.Event(), threading.Event(), threading.Event()
    calls = []
    original_reply = env.store.add_bot_reply

    def add_reply(job_id, text, model=None):
        result = original_reply(job_id, text, model=model)
        if text == "B answered":
            b_answered.set()
        return result

    monkeypatch.setattr(env.store, "add_bot_reply", add_reply)

    def complete(config, key, system, turns, **kwargs):
        text = json.loads(turns[0]["content"])["message"]["text"]
        calls.append(text)
        if text.startswith("A"):
            entered.set()
            assert release.wait(5), "test must release provider A"
            return "A answered"
        return "B answered"

    monkeypatch.setattr(runtime.model, "complete", complete)
    worker = env.worker(max_workers=2).start()
    try:
        assert entered.wait(5)
        # This is the synchronous route's only scheduling operation. The real
        # Store commit and wake must finish while A's provider is still blocked.
        with ThreadPoolExecutor(max_workers=1) as pool:
            receipt = pool.submit(env.post, b["id"], "B short question").result(timeout=2)
        assert receipt["job"]["state"] == "pending"
        worker.wake()
        assert b_answered.wait(5)
        assert not release.is_set()
        assert jobs(env, a["id"])[0]["state"] == "running"
        assert replies(env, b["id"])[0]["text"] == "B answered"
        assert len(calls) == 2
    finally:
        release.set()
        worker.stop()


def test_wake_does_no_storage_or_provider_work(env, monkeypatch):
    worker = env.worker()
    monkeypatch.setattr(runtime, "open_store", lambda *args: pytest.fail("wake opened a store"))
    for _ in range(3):
        worker.wake()


def test_pair_order_and_independent_bot_claims(env, monkeypatch):
    max_bot = env.store.create_bot(env.uid, "Max")
    group = env.store.create_conversation(
        env.uid, "group", member_ids=[env.other], bot_ids=[env.bot["id"], max_bot["id"]]
    )
    first = env.post(group["id"], "first", bot_id=env.bot["id"])
    second = env.post(group["id"], "second", bot_id=env.bot["id"])
    third = env.post(group["id"], "independent", bot_id=max_bot["id"])
    worker = env.worker()
    # Recover before creating a deliberately in-flight claim.
    claimed = worker._claim(env.wid)
    assert claimed[2]["id"] == first["job"]["id"]
    calls = []

    def complete(config, key, system, turns, **kwargs):
        calls.append(json.loads(turns[0]["content"])["message"]["text"])
        return "answer"

    monkeypatch.setattr(runtime.model, "complete", complete)
    assert worker.run_once(env.wid)
    assert calls == ["independent"]
    assert (
        next(j for j in jobs(env, group["id"]) if j["id"] == third["job"]["id"])["state"] == "done"
    )
    assert not worker.run_once(env.wid)
    worker._process(*claimed)
    assert worker.run_once(env.wid)
    assert calls == ["independent", "first", "second"]
    assert (
        next(j for j in jobs(env, group["id"]) if j["id"] == second["job"]["id"])["state"] == "done"
    )


def test_recovery_never_replays_ambiguous_provider_or_task(env, monkeypatch):
    cid = env.conversation()["id"]
    env.post(cid)
    assert env.store.claim_job() is not None
    worker = env.worker()
    assert not worker.run_once(env.wid)
    job = jobs(env, cid)[0]
    assert job["state"] not in ("pending", "running", "done")
    assert job["code"] in ("interrupted", "uncertain")
    assert replies(env, cid) == []
    monkeypatch.setattr(runtime.model, "complete", lambda *args, **kwargs: "new answer")
    env.post(cid, "explicit new turn")
    assert worker.run_once(env.wid)
    assert len(replies(env, cid)) == 1


def test_cancel_before_claim_never_calls_provider(env):
    cid = env.conversation()["id"]
    job = env.post(cid)["job"]
    env.store.cancel_job(env.uid, job["id"])
    assert not env.worker().run_once(env.wid)
    assert jobs(env, cid)[0]["state"] == "cancelled"


@pytest.mark.parametrize(
    "change", ["cancel", "logout", "expire", "membership", "bot", "memory", "stop"]
)
def test_changed_authority_or_context_withholds_late_answer(env, monkeypatch, change):
    cid = env.conversation()["id"]
    memory = env.store.add_memory(env.uid, cid, "Remember this exact private fact.")
    job = env.post(cid)["job"]
    worker = env.worker()

    def complete(*args, **kwargs):
        if change == "cancel":
            env.store.cancel_job(env.uid, job["id"])
        elif change == "logout":
            sessions.end_session(env.db, env.session)
        elif change == "expire":
            with env.db._lock:
                env.db._conn.execute("UPDATE sessions SET expires_at='2000-01-01T00:00:00Z'")
        elif change == "membership":
            with env.db._lock:
                env.db._conn.execute(
                    "DELETE FROM memberships WHERE workspace_id=? AND user_id=?", (env.wid, env.uid)
                )
        elif change == "bot":
            original = env.store.get_conversation

            def removed(user_id, conversation_id):
                return {**original(user_id, conversation_id), "bot_ids": []}

            monkeypatch.setattr(env.store, "get_conversation", removed)
        elif change == "memory":
            env.store.remove_memory(env.uid, cid, memory["id"])
        elif change == "stop":
            worker.stop()
        return "A late private answer"

    monkeypatch.setattr(runtime.model, "complete", complete)
    assert worker.run_once(env.wid)
    if change == "membership":
        # Restore only for inspecting the unchanged conversation through Store.
        with env.db._lock:
            env.db._conn.execute(
                "INSERT INTO memberships(workspace_id,user_id,role,created_at) VALUES (?,?,?,?)",
                (env.wid, env.uid, "owner", "2000-01-01T00:00:00Z"),
            )
    assert replies(env, cid) == []
    assert jobs(env, cid)[0]["state"] not in ("running", "pending", "done")
    if change == "cancel":
        assert jobs(env, cid)[0]["state"] == "cancelled"


@pytest.mark.parametrize(
    "invalid", ["expired", "revoked", "disabled", "foreign", "not_member", "fake_actor"]
)
def test_invalid_authority_rejected_before_provider(env, monkeypatch, invalid):
    cid = env.conversation()["id"]
    env.post(cid)
    if invalid == "expired":
        with env.db._lock:
            env.db._conn.execute("UPDATE sessions SET expires_at='2000-01-01T00:00:00Z'")
    elif invalid == "revoked":
        sessions.end_session(env.db, env.session)
    elif invalid == "disabled":
        with env.db._lock:
            env.db._conn.execute(
                "UPDATE users SET disabled_at='2000-01-01T00:00:00Z' WHERE id=?", (env.uid,)
            )
    elif invalid == "foreign":
        user = env.db.create_user("foreign@example.test")
        workspace = env.db.create_workspace("foreign", user)
        sessions.start_session(env.db, user, workspace)
        original = env.store.claim_job

        def foreign():
            row = original()
            return {**row, "user_id": user} if row else row

        monkeypatch.setattr(env.store, "claim_job", foreign)
    elif invalid == "not_member":
        original = env.store.get_conversation
        monkeypatch.setattr(
            env.store, "get_conversation", lambda uid, cid: original(env.other, cid)
        )
    elif invalid == "fake_actor":
        original = env.store.list_messages
        monkeypatch.setattr(
            env.store,
            "list_messages",
            lambda *args, **kwargs: [
                {**row, "actor": "conn:Nia"} for row in original(*args, **kwargs)
            ],
        )
    assert env.worker().run_once(env.wid)
    assert replies(env, cid) == []
    assert jobs(env, cid)[0]["state"] == "failed"


def test_two_dms_and_group_recall_only_exact_visible_memory(env, monkeypatch):
    a = env.conversation()
    b = env.conversation(user=env.other)
    group = env.store.create_conversation(
        env.uid, "group", member_ids=[env.other], bot_ids=[env.bot["id"]]
    )
    canaries = {a["id"]: "DM_A_COBALT_47", b["id"]: "DM_B_CITRINE_62", group["id"]: "G1_COPPER_83"}
    for cid, text in canaries.items():
        user = env.other if cid == b["id"] else env.uid
        env.store.add_memory(user, cid, text + "\n  Keep my original spacing. ")
        env.post(cid, "Use everything you know about me.", user=user)
    calls = []

    def complete(config, key, system, turns, **kwargs):
        calls.append((system, json.loads(turns[0]["content"])))
        return "A scoped answer"

    monkeypatch.setattr(runtime.model, "complete", complete)
    worker = env.worker()
    for _ in range(3):
        assert worker.run_once(env.wid)
    assert len(calls) == 3
    for _system, data in calls:
        visible = data["memories"]
        assert len(visible) == 1
        assert visible[0]["text"].endswith("\n  Keep my original spacing. ")
        prompt = json.dumps(data)
        assert sum(canary in prompt for canary in canaries.values()) == 1
    assert "Use a calm tone." in calls[0][0]
    assert not worker.run_once(env.wid), "Bot replies must not open new turns"


def test_history_and_memory_context_are_bounded_without_summaries(env, monkeypatch):
    cid = env.conversation()["id"]
    env.store.add_memory(env.uid, cid, "old permitted canary DM_A_COBALT_47")
    removed = env.store.add_memory(env.uid, cid, "TOMBSTONED_CITRINE")
    env.store.remove_memory(env.uid, cid, removed["id"])
    for i in range(40):
        env.store.add_memory(env.uid, cid, f"memory {i} " + "x" * 200)
    for i in range(30):
        receipt = env.post(cid, f"history {i}")
        env.store.cancel_job(env.uid, receipt["job"]["id"])
    env.post(cid, "Please recall my old exact fact")
    reads, calls = [], []
    original = env.store.list_messages

    def list_messages(*args, **kwargs):
        reads.append(kwargs)
        assert 0 < kwargs["limit"] <= 11
        return original(*args, **kwargs)

    monkeypatch.setattr(env.store, "list_messages", list_messages)

    def complete(config, key, system, turns, **kwargs):
        calls.append(json.loads(turns[0]["content"]))
        assert len(system) + sum(len(t["content"]) for t in turns) <= MAX_INPUT_CHARS
        assert kwargs["max_tokens"] <= 300
        assert kwargs["timeout"] <= 3.5
        return "bounded answer"

    monkeypatch.setattr(runtime.model, "complete", complete)
    assert env.worker().run_once(env.wid)
    assert len(reads) == 2
    assert len(calls[0]["history"]) <= 10
    assert len(calls[0]["memories"]) <= 5
    assert sum(len(m["text"]) for m in calls[0]["memories"]) <= 3000
    assert "DM_A_COBALT_47" in json.dumps(calls[0])
    assert "TOMBSTONED_CITRINE" not in json.dumps(calls[0])


@pytest.mark.parametrize(
    "mode,code",
    [
        ("none", "needs_configuration"),
        ("disabled", "needs_configuration"),
        ("timeout", "timeout"),
        ("malformed", "bad_response"),
        ("oversized", "bad_response"),
    ],
)
def test_truthful_provider_failure_no_canned_answer(env, monkeypatch, mode, code):
    cid = env.conversation()["id"]
    env.post(cid)
    if mode in ("none", "disabled"):
        settings.set_front(
            env.db, env.principal, {"front": {"provider": "none", "enabled": mode != "disabled"}}
        )
    else:

        def complete(*args, **kwargs):
            if mode == "timeout":
                raise ModelError("timeout")
            return None if mode == "malformed" else "x" * (MAX_REPLY_CHARS + 1)

        monkeypatch.setattr(runtime.model, "complete", complete)
    assert env.worker().run_once(env.wid)
    assert jobs(env, cid)[0]["code"] == code
    assert replies(env, cid) == []


def test_atomic_quota_reservations_count_inflight_calls(env, monkeypatch):
    a, b = env.conversation(), env.conversation()
    settings.set_front(
        env.db,
        env.principal,
        {
            "front": {
                "provider": "openai_compatible",
                "model": "fixture",
                "base_url": "http://127.0.0.1:9",
                "daily_limit": 1,
                "user_daily_limit": 1,
            }
        },
    )
    env.post(a["id"])
    env.post(b["id"])
    calls = []
    worker = env.worker()

    def complete(*args, **kwargs):
        calls.append(1)
        assert worker.run_once(env.wid)
        return "first admitted answer"

    monkeypatch.setattr(runtime.model, "complete", complete)
    assert worker.run_once(env.wid)
    assert calls == [1]
    assert jobs(env, b["id"])[0]["code"] == "daily_limit"


@pytest.fixture
def dispatch(env, monkeypatch):
    """Exact canonical seam supplied by parent integration, absent at this base."""
    cards, calls = {}, []
    allowed = {(env.uid, "work")}

    class FakeBoardService:
        def __init__(self, ctx):
            self.principal = ctx.principal

        def ensure_chat_task(self, origin_key, request):
            assert set(request) == {"board", "title", "body"}
            assert self.principal.person
            assert self.principal.workspace_id == env.wid
            calls.append((self.principal.actor, origin_key, request.copy()))
            if (self.principal.user_id, request["board"]) not in allowed:
                raise ToolError("no: board unavailable")
            key = (self.principal.actor, origin_key)
            if key in cards:
                assert cards[key]["request"] == request, "changed origin payload"
                return cards[key]["card"]
            card = {"number": len(cards) + 1, "created_by": self.principal.actor, **request}
            cards[key] = {"request": request.copy(), "card": card}
            return card

    monkeypatch.setattr(runtime, "BoardService", FakeBoardService)
    return SimpleNamespace(cards=cards, calls=calls, allowed=allowed)


def test_explicit_task_is_authentic_correlated_and_does_not_publish_dm_context(env, dispatch):
    cid = env.conversation(board="work")["id"]
    env.store.add_memory(env.uid, cid, "DM_A_COBALT_47 must stay private")
    job = env.post(cid, "/task Fix the selected typo\nChange just the public title.")["job"]
    settings.set_front(env.db, env.principal, {"front": {"provider": "none"}})
    worker = env.worker()
    assert worker.run_once(env.wid)
    result = jobs(env, cid)[0]
    assert result["state"] == "done"
    assert result["task"] == {"board": "work", "number": 1}
    assert result["code"] == "task_created"
    card = next(iter(dispatch.cards.values()))["card"]
    assert card["board"] == "work"
    assert card["created_by"] == f"user:{env.uid}"
    assert card["body"] == "Change just the public title."
    assert "DM_A_COBALT_47" not in json.dumps(card)
    assert dispatch.calls[0][1] == f"coworker:{cid}:{job['id']}"
    assert replies(env, cid) == [], "A task acknowledgement is not a fabricated model answer"
    assert not worker.run_once(env.wid)
    assert len(dispatch.cards) == 1


@pytest.mark.parametrize(
    "text,board", [("/task", "work"), ("/task Do this", None), ("/task " + "x" * 201, "work")]
)
def test_unspecified_task_has_no_board_effect(env, text, board):
    cid = env.conversation(board=board)["id"]
    env.post(cid, text)
    assert env.worker().run_once(env.wid)
    assert jobs(env, cid)[0]["code"] == "task_needs_details"
    assert jobs(env, cid)[0].get("task") is None
    assert replies(env, cid) == []


def test_freeform_model_output_never_dispatches_work(env, monkeypatch):
    cid = env.conversation()["id"]
    env.post(cid, "Could you discuss tasks?")
    monkeypatch.setattr(
        runtime.model,
        "complete",
        lambda *args, **kwargs: '/task launch something\n{"tool":"task_add"}',
    )
    assert env.worker().run_once(env.wid)
    assert jobs(env, cid)[0].get("task") is None
    assert len(replies(env, cid)) == 1


def test_task_lost_ack_is_interrupted_and_never_blindly_replayed(env, dispatch, monkeypatch):
    cid = env.conversation(board="work")["id"]
    job = env.post(cid, "/task Fix the typo\nOnly this public request.")["job"]
    worker = env.worker()
    claimed = worker._claim(env.wid)
    original = env.store.finish_job
    lost = False

    def finish(job_id, state, code="", task=None):
        nonlocal lost
        if task is not None and not lost:
            lost = True
            raise RuntimeError("synthetic acknowledgement loss")
        return original(job_id, state, code=code, task=task)

    monkeypatch.setattr(env.store, "finish_job", finish)
    worker._process(*claimed)
    assert len(dispatch.cards) == 1
    assert replies(env, cid) == []
    assert dispatch.calls[0][1] == f"coworker:{cid}:{job['id']}"
    assert not env.worker().run_once(env.wid)
    assert len(dispatch.calls) == 1
    assert jobs(env, cid)[0]["code"] == "interrupted"


def test_task_denied_by_current_board_authority_has_no_effect(env, dispatch):
    cid = env.conversation(board="work")["id"]
    env.post(cid, "/task Fix a public typo")
    dispatch.allowed.clear()
    assert env.worker().run_once(env.wid)
    assert dispatch.cards == {}
    assert jobs(env, cid)[0]["code"] == "no_access"
    assert replies(env, cid) == []


def test_new_session_does_not_revive_an_inflight_revoked_session(env, monkeypatch):
    cid = env.conversation()["id"]
    env.post(cid)

    def complete(*args, **kwargs):
        sessions.start_session(env.db, env.uid, env.wid)
        sessions.end_session(env.db, env.session)
        return "must be withheld"

    monkeypatch.setattr(runtime.model, "complete", complete)
    assert env.worker().run_once(env.wid)
    assert replies(env, cid) == []
    assert jobs(env, cid)[0]["code"] == "session_expired"


def test_group_audience_change_withholds_old_context(env, monkeypatch):
    cid = env.conversation()["id"]
    env.post(cid)

    def complete(*args, **kwargs):
        original = env.store.get_conversation
        monkeypatch.setattr(
            env.store,
            "get_conversation",
            lambda *args, **kwargs: {
                **original(*args, **kwargs),
                "member_ids": [env.uid, env.other],
            },
        )
        return "old audience private context"

    monkeypatch.setattr(runtime.model, "complete", complete)
    assert env.worker().run_once(env.wid)
    assert replies(env, cid) == []
    assert jobs(env, cid)[0]["code"] == "context_changed"


def test_refused_terminal_bookkeeping_recovers_after_other_turn_returns(env, monkeypatch):
    a, b = env.conversation(), env.conversation(user=env.other)
    first = env.post(a["id"], "A")
    env.post(b["id"], "B", user=env.other)
    worker = env.worker()
    claimed_a, claimed_b = worker._claim(env.wid), worker._claim(env.wid)
    original = env.store.finish_job

    def finish(job_id, state, code="", task=None):
        if job_id == first["job"]["id"]:
            # Production Store refuses bookkeeping under revoked authority.
            raise coworker_store.StoreError("not_found", 404)
        return original(job_id, state, code=code, task=task)

    def complete(config, key, system, turns, **kwargs):
        if json.loads(turns[0]["content"])["message"]["text"] == "A":
            sessions.end_session(env.db, env.session)
        return "answer"

    monkeypatch.setattr(env.store, "finish_job", finish)
    monkeypatch.setattr(runtime.model, "complete", complete)
    worker._process(*claimed_a)
    assert jobs(env, a["id"])[0]["state"] == "running"
    assert jobs(env, b["id"], user=env.other)[0]["state"] == "running"
    worker._process(*claimed_b)
    assert jobs(env, a["id"])[0]["state"] == "interrupted"
    assert jobs(env, b["id"], user=env.other)[0]["state"] == "done"
    assert replies(env, a["id"]) == []
    assert len(replies(env, b["id"], user=env.other)) == 1


def test_provider_circuit_bounds_repeated_outage_without_fallback(env, monkeypatch):
    cid = env.conversation()["id"]
    calls = []
    clock = [100.0]
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock[0])

    def complete(config, *args, **kwargs):
        calls.append((config.provider, config.model))
        raise ModelError("timeout")

    monkeypatch.setattr(runtime.model, "complete", complete)
    worker = env.worker()
    for _ in range(4):
        env.post(cid)
        assert worker.run_once(env.wid)
    assert len(calls) == 3
    assert jobs(env, cid)[-1]["code"] == "provider_unavailable"
    clock[0] += 31
    env.post(cid)
    assert worker.run_once(env.wid)
    assert len(calls) == 4
    assert set(calls) == {("openai_compatible", "fixture")}


def test_provider_cap_leaves_room_for_another_configured_endpoint(env, monkeypatch):
    blocked = [env.conversation() for _ in range(3)]
    healthy = env.conversation(board="healthy")
    settings.set_front(
        env.db,
        env.principal,
        {
            "scope": "healthy",
            "front": {
                "provider": "openai_compatible",
                "model": "other-fixture",
                "base_url": "http://127.0.0.1:10",
            },
        },
    )
    entered = threading.Barrier(3)
    release, answered, saturated = threading.Event(), threading.Event(), threading.Event()
    original_reply, original_finish = env.store.add_bot_reply, env.store.finish_job

    def add_reply(job_id, text, model=None):
        result = original_reply(job_id, text, model=model)
        if text == "healthy answer":
            answered.set()
        return result

    def finish(job_id, state, code="", task=None):
        result = original_finish(job_id, state, code=code, task=task)
        if code == "provider_busy":
            saturated.set()
        return result

    def complete(config, *args, **kwargs):
        if config.model == "other-fixture":
            return "healthy answer"
        entered.wait(timeout=5)
        assert release.wait(5)
        return "formerly blocked answer"

    monkeypatch.setattr(runtime.model, "complete", complete)
    monkeypatch.setattr(env.store, "add_bot_reply", add_reply)
    monkeypatch.setattr(env.store, "finish_job", finish)
    for conversation in blocked[:2]:
        env.post(conversation["id"])
    worker = env.worker(max_workers=4).start()
    try:
        entered.wait(timeout=5)
        env.post(blocked[2]["id"])
        env.post(healthy["id"])
        worker.wake()
        assert answered.wait(5)
        assert saturated.wait(5)
        assert not release.is_set()
        assert replies(env, healthy["id"])[0]["text"] == "healthy answer"
        assert jobs(env, blocked[2]["id"])[0]["code"] == "provider_busy"
    finally:
        release.set()
        worker.stop()


def test_completed_reply_survives_reopen_without_replaying(env, monkeypatch):
    cid = env.conversation()["id"]
    env.post(cid)
    monkeypatch.setattr(runtime.model, "complete", lambda *args, **kwargs: "durable answer")
    assert env.worker().run_once(env.wid)
    reopened = coworker_store.Store(env.path)
    monkeypatch.setattr(runtime, "open_store", lambda *args: reopened)
    try:
        monkeypatch.setattr(
            runtime.model, "complete", lambda *args, **kwargs: pytest.fail("replayed provider")
        )
        assert not env.worker().run_once(env.wid)
        assert len(reopened.list_messages(env.uid, cid)) == 2
        assert reopened.list_jobs(env.uid, cid)[0]["state"] == "done"
    finally:
        close = getattr(reopened, "close", None)
        if close:
            close()
