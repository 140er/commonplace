"""Composer contracts using real local stores and synthetic provider/runner ledgers."""

import json

import pytest

from commonplace import sessions
from commonplace.chat import settings
from commonplace.chat.coworkers import routes, runtime
from commonplace.chat.coworkers.model_effort import apply_effort
from commonplace.chat.coworkers.store import Store, StoreError
from commonplace.projects import models
from tests.test_coworker_api import API, api as api
from tests.test_coworker_dispatch import dispatch as dispatch
from tests.test_coworker_progress import progress as progress
from tests.test_coworker_runtime import env as env


@pytest.fixture
def configured(api):
    settings.ensure_schema(api.gateway.db)
    principal = sessions.person_principal(api.gateway.db, api.owner, api.workspace)
    settings.set_front(
        api.gateway.db,
        principal,
        {
            "front": {
                "provider": "openai_compatible",
                "model": "fixture-default",
                "base_url": "http://127.0.0.1:9",
            }
        },
    )
    models.catalog_add(
        api.gateway.db, principal, "*", {"provider": "openai_compatible", "model": "fixture-other"}
    )
    return api


def create(api, **options):
    bot = api.bot()
    return api.request("POST", "/conversations", {"kind": "dm", "bot_ids": [bot["id"]], **options})


def test_creation_detail_list_and_restart_keep_model_and_effort(configured):
    api = configured
    status, response = create(api, model="fixture-other", reasoning_effort="high")
    assert status == 201, response
    conversation = response["conversation"]
    assert conversation["model"] == "fixture-other"
    assert conversation["reasoning_effort"] == "high"
    # Directory metadata is additive to every persisted conversation field.
    listed = api.request("GET", "/conversations")[1]["conversations"]
    assert [{key: entry[key] for key in conversation} for entry in listed] == [conversation]
    assert {
        key: listed[0][key]
        for key in (
            "section",
            "private",
            "unread",
            "state",
            "summary",
            "coworker_count",
            "coworkers",
        )
    } == {
        "section": None,
        "private": True,
        "unread": 0,
        "state": "idle",
        "summary": "0 coworkers",
        "coworker_count": 0,
        "coworkers": [],
    }
    assert "owns" in listed[0]
    detail = api.request("GET", f"/conversations/{conversation['id']}")[1]["conversation"]
    assert {k: v for k, v in detail.items() if k != "owns"} == conversation
    path = api.gateway.supervisor.workspace_dir(api.workspace) / "coworkers.sqlite3"
    reopened = Store(path, authority=routes.Authority(api.gateway, api.workspace))
    assert reopened.get_conversation(api.owner, conversation["id"]) == conversation
    audit = api.gateway.db.audit_entries(api.workspace)
    row = next(row for row in audit if row["action"] == "coworker.create")
    assert json.loads(row["reason"]) == {"model": "fixture-other", "reasoning_effort": "high"}


@pytest.mark.parametrize(
    "options",
    [
        {"model": "unknown"},
        {"model": ""},
        {"model": None},
        {"model": []},
        {"reasoning_effort": "ultra"},
        {"reasoning_effort": None},
        {"reasoning_effort": True},
        {"reasoning_effort": ["high"]},
    ],
)
def test_creation_and_turn_validation_are_atomic(configured, options):
    api = configured
    assert create(api, **options)[0] == 400
    cid = api.dm()["id"]
    assert api.post(cid, mode="chat", **options)[0] == 400
    assert api.request("GET", f"/conversations/{cid}")[1]["messages"] == []


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
def test_closed_effort_enum(configured, effort):
    status, response = create(configured, reasoning_effort=effort)
    assert status == 201, response
    assert response["conversation"]["reasoning_effort"] == effort


def test_omission_uses_default_and_legacy_message_shape_stays_compatible(configured):
    api = configured
    cid = api.dm()["id"]
    conversation = api.request("GET", f"/conversations/{cid}")[1]["conversation"]
    assert conversation["model"] == "fixture-default"
    assert conversation["reasoning_effort"] is None
    listed = api.request("GET", "/conversations")[1]["conversations"]
    assert [(row["id"], row["model"], row["reasoning_effort"]) for row in listed] == [
        (cid, "fixture-default", None)
    ]
    status, legacy = api.post(cid)
    assert status == 201 and set(legacy) == {"message", "job"}
    status, turn = api.post(cid, mode="chat", request_id="explicit")
    assert status == 201 and turn["mode"] == "chat"
    assert turn["model"] == "fixture-default" and turn["reasoning_effort"] is None


@pytest.mark.parametrize(
    "options",
    [
        {},
        {"mode": "chat"},
        {"mode": "chat", "reasoning_effort": "low"},
        {"mode": "steer", "fallback": True},
    ],
)
def test_revoked_conversation_model_preserves_intake_but_is_fenced_at_dispatch(env, options):
    env.store._authority = routes.Authority(env.gateway, env.wid)
    env.gateway._coworker_stores = {env.wid: env.store}
    env.gateway.secure_cookies = False
    models.catalog_add(
        env.db, env.principal, "*", {"provider": "openai_compatible", "model": "alternate"}
    )
    cid = env.conversation(model="alternate", reasoning_effort="high")["id"]
    with env.db._lock:
        env.db._conn.execute("DELETE FROM project_model_catalog WHERE model='alternate'")
    api = API(env.gateway, env.wid, env.uid, env.other, "outsider")
    api.sessions[env.uid] = env.session

    status, turn = api.post(cid, **options)
    assert status == 201, turn
    if not options:
        assert set(turn) == {"message", "job"}
    else:
        assert turn["mode"] == "chat" and turn["model"] == "alternate"
        assert turn["reasoning_effort"] == options.get("reasoning_effort", "high")
    assert api.post(cid, **options) == (status, turn)
    assert api.post(cid, request_id="explicit", model="alternate") == (
        400,
        {"error": "unknown_model"},
    )
    with pytest.raises(StoreError, match="^unknown_model$") as error:
        env.store.post_message(env.uid, cid, "Explicit revoked model", "direct", model="alternate")
    assert error.value.status == 400
    assert api.post(cid, request_id="bad-effort", reasoning_effort="invalid") == (
        400,
        {"error": "invalid_reasoning_effort"},
    )
    # The runtime fixture rejects any provider call: stale inherited choices
    # must fail the queued job without losing the accepted original message.
    assert env.worker().run_once(env.wid)
    assert env.store.list_jobs(env.uid, cid)[0]["state"] == "failed"
    assert env.store.list_messages(env.uid, cid) == [turn["message"]]


@pytest.mark.parametrize(
    "mode,fallback,status,applied",
    [
        ("chat", False, 201, "chat"),
        ("steer", False, 409, None),
        ("steer", True, 201, "chat"),
        ("other", False, 400, None),
        (None, False, 400, None),
        ([], False, 400, None),
        ("steer", "yes", 400, None),
    ],
)
def test_mode_matrix_without_running_job(configured, mode, fallback, status, applied):
    api = configured
    cid = api.dm()["id"]
    code, result = api.post(cid, mode=mode, fallback=fallback)
    assert code == status, result
    if applied:
        assert result["mode"] == applied
        if mode != applied:
            assert result["reason"] == "no_running_job"
        assert api.post(cid, mode=mode, fallback=fallback) == (code, result)
    elif status == 409:
        assert result == {"error": "no_running_job"}
        assert api.request("GET", f"/conversations/{cid}")[1]["messages"] == []


def test_catalog_scope_and_provider_never_enable_an_unconfigured_endpoint(configured):
    api = configured
    principal = sessions.person_principal(api.gateway.db, api.owner, api.workspace)
    for scope, provider, name in [
        ("private", "openai_compatible", "private-model"),
        ("*", "anthropic", "other-provider"),
    ]:
        models.catalog_add(api.gateway.db, principal, scope, {"provider": provider, "model": name})
        assert create(api, model=name)[0] == 400
    other = api.gateway.db.create_workspace("other", api.owner)
    api.gateway.db._conn.execute(
        "INSERT INTO project_model_catalog VALUES (?, '*', 'openai_compatible', 'foreign', ?, 'now')",
        (other, api.owner),
    )
    assert create(api, model="foreign")[0] == 400


def test_unconfigured_default_stays_off(api):
    cid = api.dm()["id"]
    assert api.request("GET", f"/conversations/{cid}")[1]["conversation"]["model"] is None
    assert create(api, model="fixture-other")[0] == 400


def test_per_turn_overrides_reach_provider_and_are_not_sticky(env, monkeypatch):
    env.store._authority = routes.Authority(env.gateway, env.wid)
    models.catalog_add(
        env.db, env.principal, "*", {"provider": "openai_compatible", "model": "alternate"}
    )
    env.gateway.secure_cookies = False
    api = API(env.gateway, env.wid, env.uid, env.other, "outsider")
    api.sessions[env.uid] = env.session
    status, response = api.request(
        "POST",
        "/conversations",
        {
            "kind": "dm",
            "bot_ids": [env.bot["id"]],
            "model": "alternate",
            "reasoning_effort": "high",
        },
    )
    assert status == 201, response
    cid = response["conversation"]["id"]
    calls = []

    def complete(config, *args, **kwargs):
        calls.append((config.model, kwargs.get("reasoning_effort")))
        return "Synthetic reply"

    monkeypatch.setattr(runtime.model, "complete", complete)
    status, turn = api.post(cid, mode="chat", model="fixture", reasoning_effort="low")
    assert status == 201, turn
    assert env.worker().run_once(env.wid)
    assert api.post(cid, mode="chat", request_id="next")[0] == 201
    assert env.worker().run_once(env.wid)
    assert calls == [("fixture", "low"), ("alternate", "high")]
    assert api.post(cid, mode="chat", model="fixture", reasoning_effort="max")[0] == 409
    # Owner removal of a model fences already queued overrides before any call.
    assert api.post(cid, mode="chat", request_id="removed", model="alternate")[0] == 201
    env.db._conn.execute("DELETE FROM project_model_catalog WHERE model='alternate'")
    assert env.worker().run_once(env.wid)
    assert len(calls) == 2


def test_steer_uses_canonical_controls_and_explicit_chat_stays_chat(env, dispatch, progress):
    item = progress.start(group=True)
    api = API(env.gateway, env.wid, env.uid, env.other, "outsider")
    api.sessions[env.uid] = env.session
    kwargs = {"mode": "steer", "text": "  Exact instruction.  ", "reasoning_effort": "high"}
    status, turn = api.post(item.cid, **kwargs)
    assert status == 200, turn
    assert turn["mode"] == "steer"
    assert turn["receipt"]["code"] == "comment_saved_application_unsupported"
    assert turn["model_effort_applied"] is False
    assert api.post(item.cid, **kwargs) == (status, turn)
    assert len(env.store.list_messages(env.uid, item.cid)) == 1
    assert api.post(item.cid, mode="chat", request_id="chat")[0] == 201
    assert len(env.store.list_messages(env.uid, item.cid)) == 2
    assert len(dispatch.runner_jobs()) == 1
    # Both aliases reuse the same receipt rather than repeating the comment.
    alias = api.request(
        "POST",
        f"/jobs/{item.job['id']}/controls",
        {
            "operation": "follow_up",
            "request_id": "one",
            "text": kwargs["text"],
            "reasoning_effort": "high",
        },
    )
    assert alias[0] == 200 and alias[1]["receipt"] == turn["receipt"]
    assert api.post(item.cid, mode="chat")[0] == 409


@pytest.mark.parametrize("mode", ["steer", "chat"])
def test_existing_authority_matrix_with_new_fields(env, dispatch, progress, mode):
    item = progress.start(group=True)
    private = progress.start()
    api = API(env.gateway, env.wid, env.uid, env.other, "outsider")
    api.sessions[env.uid] = env.session
    body = {
        "text": "Explicit turn",
        "request_id": "member",
        "mode": mode,
        "model": "fixture",
        "reasoning_effort": "high",
        "fallback": True,
    }
    path = f"/conversations/{item.cid}/messages"
    status, _ = api.request("POST", path, body, user=env.other)
    assert status == (404 if mode == "steer" else 201)
    for headers in [{"Authorization": "Bearer synthetic"}, {"Origin": None}]:
        assert api.request("POST", path, body, headers=headers)[0] == 403
    assert (
        api.request("POST", f"/conversations/{private.cid}/messages", body, user=env.other)[0]
        == 404
    )
    assert api.request("POST", "/bots", {"name": "forbidden"}, user=env.other)[0] == 403
    # Members still may create their own conversations, as before.
    assert (
        api.request(
            "POST",
            "/conversations",
            {
                "kind": "dm",
                "bot_ids": [env.bot["id"]],
                "model": "fixture",
                "reasoning_effort": "medium",
            },
            user=env.other,
        )[0]
        == 201
    )


@pytest.mark.parametrize("provider", ["anthropic", "openai_compatible"])
@pytest.mark.parametrize("effort", [None, "low", "medium", "high", "xhigh", "max"])
def test_effort_transport_payload_preserves_omitted_baseline(monkeypatch, provider, effort):
    from commonplace.chat import model
    from tests._chat_helpers import no_outbound
    from tests.test_chat_model_wire import front_settings, model_server

    connects = no_outbound(monkeypatch)
    with model_server(provider) as (origin, requests):
        monkeypatch.setattr(model, "ANTHROPIC_ORIGIN", origin)
        assert (
            model.complete(
                front_settings(provider, origin),
                None,
                "system",
                [],
                **({"reasoning_effort": effort} if effort is not None else {}),
            )
            == "hi"
        )
    assert connects == [] and len(requests) == 1
    body = requests[0]["body"]
    if effort is None:
        assert "reasoning_effort" not in body and "output_config" not in body
    elif provider == "anthropic":
        assert body["output_config"] == {"effort": effort}
    else:
        assert body["reasoning_effort"] == effort


def test_existing_canonical_binding_is_unchanged(env, dispatch, progress):
    import hashlib

    item = progress.start()
    conversation = env.store.get_conversation(env.uid, item.cid)
    old_fields = {k: v for k, v in conversation.items() if k not in ("model", "reasoning_effort")}
    expected = hashlib.sha256(
        json.dumps({"conversation": old_fields, "bot": env.bot}, sort_keys=True).encode()
    ).hexdigest()
    with env.store._transaction() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (item.job["id"],)).fetchone()
        assert row["progress_binding"] == expected
    assert progress.view(item)["task_progress"]["availability"] == "available"


def test_catalog_revoked_immediately_before_dispatch_cannot_reach_provider(env, monkeypatch):
    env.store._authority = routes.Authority(env.gateway, env.wid)
    models.catalog_add(
        env.db, env.principal, "*", {"provider": "openai_compatible", "model": "alternate"}
    )
    cid = env.store.create_conversation(env.uid, "dm", bot_ids=[env.bot["id"]], model="alternate")[
        "id"
    ]
    env.store.post_message(env.uid, cid, "Read this", "revoked")
    worker = env.worker()
    context = worker._context

    def revoke(*args):
        result = context(*args)
        env.db._conn.execute("DELETE FROM project_model_catalog WHERE model='alternate'")
        return result

    monkeypatch.setattr(worker, "_context", revoke)
    assert worker.run_once(env.wid)
    assert len(env.store.list_messages(env.uid, cid)) == 1
    assert env.store.list_jobs(env.uid, cid)[0]["state"] == "failed"


def test_legacy_chat_cannot_reinterpret_a_steer_retry(env, dispatch, progress):
    item = progress.start()
    api = API(env.gateway, env.wid, env.uid, env.other, "outsider")
    api.sessions[env.uid] = env.session
    assert api.post(item.cid, mode="steer")[0] == 200
    assert api.post(item.cid)[0] == 409
    assert len(env.store.list_messages(env.uid, item.cid)) == 1


@pytest.mark.parametrize(
    "state,expected",
    [
        ("queued", 200),
        ("starting", 200),
        ("started", 200),
        ("uncertain", 200),
        ("cancelled", 409),
        ("failed", 409),
    ],
)
def test_steer_matrix_uses_live_canonical_runner_state(env, dispatch, progress, state, expected):
    from commonplace.runner import jobs

    item = progress.start()
    with jobs.open_store(env.gateway, env.wid).transaction() as conn:
        conn.execute("UPDATE runner_jobs SET state=?", (state,))
    api = API(env.gateway, env.wid, env.uid, env.other, "outsider")
    api.sessions[env.uid] = env.session
    code, result = api.post(item.cid, mode="steer")
    assert code == expected, result
    assert len(env.store.list_messages(env.uid, item.cid)) == 1


def test_steer_does_not_guess_between_multiple_active_runs(env, dispatch, progress):
    item = progress.start()
    dispatch.post(item.cid, text="/task A separate request", key="another-task")
    assert env.worker().run_once(env.wid)
    api = API(env.gateway, env.wid, env.uid, env.other, "outsider")
    api.sessions[env.uid] = env.session
    assert api.post(item.cid, mode="steer", fallback=True) == (
        409,
        {"error": "ambiguous_running_job"},
    )
    assert len(env.store.list_messages(env.uid, item.cid)) == 2


def test_control_alias_validates_overrides_and_audits_selections(env, dispatch, progress):
    item = progress.start()
    api = API(env.gateway, env.wid, env.uid, env.other, "outsider")
    api.sessions[env.uid] = env.session
    path = f"/jobs/{item.job['id']}/controls"
    body = {"operation": "follow_up", "request_id": "alias", "text": "Exact instruction"}
    for options in ({"model": "unknown"}, {"reasoning_effort": "ultra"}):
        assert api.request("POST", path, {**body, **options})[0] == 400
    assert (
        api.request("POST", path, {**body, "model": "fixture", "reasoning_effort": "max"})[0] == 200
    )
    audit = next(row for row in env.db.audit_entries(env.wid) if row["action"] == "coworker.turn")
    assert json.loads(audit["reason"]) == {
        "mode": "steer",
        "model": "fixture",
        "reasoning_effort": "max",
    }


@pytest.mark.parametrize("route", ["steer", "alias"])
def test_stored_model_revocation_does_not_block_existing_controls(env, dispatch, route):
    models.catalog_add(
        env.db, env.principal, "*", {"provider": "openai_compatible", "model": "alternate"}
    )
    cid = dispatch.conversation(model="alternate", reasoning_effort="high")
    dispatch.post(cid)
    assert env.worker().run_once(env.wid)
    job = dispatch.outcome(cid)
    with env.db._lock:
        env.db._conn.execute("DELETE FROM project_model_catalog WHERE model='alternate'")
    env.gateway._coworker_stores = {env.wid: env.store}
    api = API(env.gateway, env.wid, env.uid, env.other, "outsider")
    api.sessions[env.uid] = env.session
    path = f"/conversations/{cid}/messages" if route == "steer" else f"/jobs/{job['id']}/controls"
    body = {
        "text": "Exact control",
        **({"mode": "steer"} if route == "steer" else {"operation": "follow_up"}),
    }
    assert api.request("POST", path, {**body, "request_id": "bad", "model": "alternate"}) == (
        400,
        {"error": "unknown_model"},
    )
    status, result = api.request("POST", path, {**body, "request_id": "control"})
    assert status == 200, result
    assert result["receipt"]["code"] == "comment_saved_application_unsupported"
    assert len(env.store.list_messages(env.uid, cid)) == 1
    assert len(dispatch.runner_jobs()) == 1
    audit = next(
        row
        for row in env.db.audit_entries(env.wid)
        if row["action"] == "coworker.turn" and json.loads(row["reason"])["mode"] == "steer"
    )
    assert json.loads(audit["reason"]) == {
        "mode": "steer",
        "model": "alternate",
        "reasoning_effort": "high",
    }


@pytest.fixture
def lock_order(monkeypatch):
    """Record physical lock order and forbid all model reads in writer scopes."""
    from contextlib import contextmanager
    from types import SimpleNamespace

    recorder = SimpleNamespace(transaction=0, view=0, events=[], model_reads=0)
    transaction, view = Store._transaction, Store._conversation_view
    front, catalog = settings.front, models.catalog

    def install(db):
        original = db._lock

        class RecordingLock:
            depth = 0

            def acquire(self, *args, **kwargs):
                # Existing authority checks may reenter the outer guard, but
                # a physical control-lock wait must never start in a writer.
                assert recorder.view == 0, "control lock acquired while building a view"
                assert not recorder.transaction or self.depth, "store -> control lock inversion"
                acquired = original.acquire(*args, **kwargs)
                if acquired:
                    self.depth += 1
                    recorder.events.append(("control", recorder.transaction))
                return acquired

            def release(self):
                self.depth -= 1
                original.release()

            def __enter__(self):
                self.acquire()
                return self

            def __exit__(self, *_):
                self.release()

        lock = RecordingLock()
        monkeypatch.setattr(db, "_lock", lock)

        @contextmanager
        def recorded_transaction(store, *args, **kwargs):
            with transaction(store, *args, **kwargs) as conn:
                assert lock.depth > 0, "writer acquired before control guard"
                recorder.events.append(("store", lock.depth))
                recorder.transaction += 1
                try:
                    yield conn
                finally:
                    recorder.transaction -= 1

        def recorded_view(*args, **kwargs):
            recorder.view += 1
            try:
                return view(*args, **kwargs)
            finally:
                recorder.view -= 1

        def read_front(*args, **kwargs):
            assert recorder.transaction == 0, "front resolved inside a writer"
            recorder.model_reads += 1
            return front(*args, **kwargs)

        def read_catalog(*args, **kwargs):
            assert recorder.transaction == 0, "catalog resolved inside a writer"
            recorder.model_reads += 1
            return catalog(*args, **kwargs)

        monkeypatch.setattr(Store, "_transaction", recorded_transaction)
        monkeypatch.setattr(Store, "_conversation_view", recorded_view)
        monkeypatch.setattr(settings, "front", read_front)
        monkeypatch.setattr(models, "catalog", read_catalog)
        return recorder

    return install


def test_model_lock_order_on_all_view_routes_and_runtime(
    env, dispatch, progress, lock_order, monkeypatch
):
    item = progress.start(group=True)
    api = API(env.gateway, env.wid, env.uid, env.other, "outsider")
    api.sessions[env.uid] = env.session
    recorder = lock_order(env.db)
    for method, path, body in [
        ("GET", "", None),
        ("GET", "/conversations", None),
        ("GET", f"/conversations/{item.cid}", None),
        ("GET", f"/conversations/{item.cid}/messages", None),
        ("GET", f"/{item.cid}", None),
        ("GET", f"/conversations/{item.cid}/log", None),
        ("GET", f"/conversations/{item.cid}/log/stream", None),
        ("GET", f"/{item.cid}/log", None),
        ("GET", f"/{item.cid}/log/stream", None),
        ("POST", "/conversations", {"kind": "dm", "bot_ids": [env.bot["id"]], "model": "fixture"}),
        (
            "POST",
            f"/conversations/{item.cid}/messages",
            {"text": "Exact legacy chat", "request_id": "legacy"},
        ),
        (
            "POST",
            f"/conversations/{item.cid}/messages",
            {
                "text": "Exact chat",
                "request_id": "chat",
                "mode": "chat",
                "reasoning_effort": "high",
            },
        ),
        (
            "POST",
            f"/conversations/{item.cid}/messages",
            {"text": "Exact instruction", "request_id": "steer", "mode": "steer"},
        ),
        (
            "POST",
            f"/jobs/{item.job['id']}/controls",
            {"text": "Exact alias instruction", "request_id": "alias", "operation": "follow_up"},
        ),
    ]:
        status, result = api.request(method, path, body)
        assert status in (200, 201), result
    assert recorder.model_reads > 0
    recorder.events.clear()
    monkeypatch.setattr(runtime.model, "complete", lambda *args, **kwargs: "Synthetic reply")
    assert env.worker().run_once(env.wid)
    assert recorder.events[0][0] == "control"
    assert any(event[0] == "store" for event in recorder.events)
    assert recorder.transaction == 0 and recorder.view == 0


@pytest.mark.parametrize("active_count,expected", [(1, 200), (2, 409)])
def test_steer_finds_active_origins_behind_over_100_finished_jobs(
    env, dispatch, progress, active_count, expected
):
    item = progress.start()
    if active_count == 2:
        dispatch.post(item.cid, text="/task Another active origin", key="another-origin")
        assert env.worker().run_once(env.wid)
    with env.store._transaction() as conn:
        for index in range(101):
            mid = conn.execute(
                "INSERT INTO messages(conversation_id,actor,bot_id,text,created_at,request_id) VALUES (?,?,?,?,?,?)",
                (
                    item.cid,
                    "user:" + env.uid,
                    env.bot["id"],
                    "Finished chat",
                    "now",
                    f"finished-{index}",
                ),
            ).lastrowid
            conn.execute(
                "INSERT INTO jobs(conversation_id,message_id,bot_id,user_id,state,created_at,updated_at) VALUES (?,?,?,?,'done','now','now')",
                (item.cid, mid, env.bot["id"], env.uid),
            )
    api = API(env.gateway, env.wid, env.uid, env.other, "outsider")
    api.sessions[env.uid] = env.session
    code, result = api.post(item.cid, mode="steer")
    assert code == expected, result
    if expected == 200:
        assert result["receipt"]["job_id"] == item.job["id"]
    else:
        assert result == {"error": "ambiguous_running_job"}


def test_steer_refuses_truncated_task_history_without_chat_fallback(
    env, dispatch, progress, monkeypatch
):
    from commonplace.chat.coworkers import turn_mode

    item = progress.start()
    monkeypatch.setattr(turn_mode, "MAX_TASK_CANDIDATES", 2)
    with env.store._transaction() as conn:
        for index in range(2):
            mid = conn.execute(
                "INSERT INTO messages(conversation_id,actor,bot_id,text,created_at,request_id) VALUES (?,?,?,?,?,?)",
                (
                    item.cid,
                    "user:" + env.uid,
                    env.bot["id"],
                    "/task Finished",
                    "now",
                    f"task-{index}",
                ),
            ).lastrowid
            conn.execute(
                "INSERT INTO jobs(conversation_id,message_id,bot_id,user_id,state,created_at,updated_at) VALUES (?,?,?,?,'done','now','now')",
                (item.cid, mid, env.bot["id"], env.uid),
            )
    api = API(env.gateway, env.wid, env.uid, env.other, "outsider")
    api.sessions[env.uid] = env.session
    original_messages = env.store.list_messages(env.uid, item.cid)
    with monkeypatch.context() as patch:
        patch.setattr(
            env.store,
            "_progress_job",
            lambda *args, **kwargs: pytest.fail(
                "truncated history must not trigger canonical projections"
            ),
        )
        for fallback in (False, True):
            assert api.post(item.cid, mode="steer", fallback=fallback) == (
                409,
                {"error": "task_history_limit"},
            )
    assert env.store.list_messages(env.uid, item.cid) == original_messages
    # Explicit job controls remain available when automatic targeting is capped.
    assert (
        api.request(
            "POST",
            f"/jobs/{item.job['id']}/controls",
            {"operation": "follow_up", "request_id": "bounded-alias", "text": "Exact instruction"},
        )[0]
        == 200
    )


@pytest.mark.parametrize(
    "provider,effort",
    [("unsupported", "high"), ("anthropic", "invalid"), ("openai_compatible", [])],
)
def test_effort_payload_failures_are_bounded_provider_errors(provider, effort):
    from commonplace.chat.types import ModelError

    with pytest.raises(ModelError, match="^bad_response$"):
        apply_effort({}, provider, effort)


def test_runtime_handles_effort_provider_failure_as_model_error(env, monkeypatch):
    cid = env.conversation()["id"]
    env.post(cid)

    def refused(*args, **kwargs):
        apply_effort({}, "unsupported", "high")

    monkeypatch.setattr(runtime.model, "complete", refused)
    assert env.worker().run_once(env.wid)
    job = env.store.list_jobs(env.uid, cid)[0]
    assert (job["state"], job["code"]) == ("failed", "bad_response")
