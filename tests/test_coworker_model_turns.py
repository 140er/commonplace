"""Composer contracts using real local stores and synthetic provider/runner ledgers."""

import json

import pytest

from commonplace import sessions
from commonplace.chat import settings
from commonplace.chat.coworkers import routes, runtime
from commonplace.chat.coworkers.store import Store
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
    assert api.request("GET", "/conversations")[1]["conversations"] == [conversation]
    assert (
        api.request("GET", f"/conversations/{conversation['id']}")[1]["conversation"]
        == conversation
    )
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
    status, legacy = api.post(cid)
    assert status == 201 and set(legacy) == {"message", "job"}
    status, turn = api.post(cid, mode="chat", request_id="explicit")
    assert status == 201 and turn["mode"] == "chat"
    assert turn["model"] == "fixture-default" and turn["reasoning_effort"] is None


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
    private = progress.start()
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
