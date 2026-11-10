"""Direct HTTP-handler tests with real session/control authority and local stores.

These tests start no server, workers, model transports or external services.
"""

import io
import json
from email.message import Message
from pathlib import Path
from types import SimpleNamespace

import pytest

from commonplace import sessions
from commonplace.chat import coworkers
from commonplace.chat.coworkers import routes
from commonplace.chat.coworkers.store import StoreError, open_store
from commonplace.control_db import ControlDB


class Request:
    def __init__(self, method, path, headers, body):
        self.command, self.path = method, path
        self.headers = Message()
        for key, value in headers:
            self.headers[key] = value
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.close_connection = False
        self.status = None
        self.response_headers = {}

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.response_headers[name] = value

    def end_headers(self):
        pass


class API:
    def __init__(self, gateway, workspace, owner, member, outsider):
        self.gateway, self.workspace = gateway, workspace
        self.owner, self.member, self.outsider = owner, member, outsider
        self.sessions = {
            user: sessions.start_session(gateway.db, user, workspace) for user in (owner, member)
        }

    def request(self, method, path="", data=None, *, user=None, headers=None, raw=None):
        user = self.owner if user is None else user
        body = raw if raw is not None else json.dumps(data).encode() if data is not None else b""
        values = [
            ("Host", "app.test"),
            ("Content-Type", "application/json"),
            ("Content-Length", str(len(body))),
        ]
        if user in self.sessions:
            values.append(("Cookie", f"cmmn_session={self.sessions[user]}"))
        if method != "GET":
            values.append(("Origin", "http://app.test"))
        if headers:
            values = [(k, v) for k, v in values if k not in headers]
            for key, value in headers.items():
                if value is not None:
                    values.extend(
                        (key, entry) for entry in (value if isinstance(value, list) else [value])
                    )
        request = Request(method, "/api/coworkers" + path, values, body)
        routes.handle(self.gateway, request)
        assert request.status is not None
        assert request.response_headers["Cache-Control"] == "no-store"
        return request.status, json.loads(request.wfile.getvalue())

    def bot(self, **kwargs):
        status, body = self.request("POST", "/bots", {"name": "Ada", **kwargs})
        assert status == 201, body
        return body["bot"]

    def dm(self, *, user=None, bot=None):
        bot = bot or self.bot()
        status, body = self.request(
            "POST", "/conversations", {"kind": "dm", "bot_ids": [bot["id"]]}, user=user
        )
        assert status == 201, body
        return body["conversation"]

    def post(self, conversation, **kwargs):
        return self.request(
            "POST",
            f"/conversations/{conversation}/messages",
            {"text": "verbatim message", "request_id": "one", **kwargs},
        )


@pytest.fixture
def api(tmp_path):
    db = ControlDB(str(tmp_path / "control.sqlite3"))
    sessions.ensure_schema(db)
    owner = db.create_user("owner@example.test")
    member = db.create_user("member@example.test")
    outsider = db.create_user("outsider@example.test")
    workspace = db.create_workspace("private workspace", owner)
    db.add_member(workspace, member, "member", actor=owner)
    root = tmp_path / "workspaces"
    supervisor = SimpleNamespace(root=root, workspace_dir=lambda wid: root / wid)
    gateway = SimpleNamespace(db=db, supervisor=supervisor, secure_cookies=False)
    gateway.workspace_dir = lambda wid: str(root / wid)
    try:
        yield API(gateway, workspace, owner, member, outsider)
    finally:
        db.close()


def test_bootstrap_owner_only_configuration_and_explicit_serialization(api):
    bot = api.bot(instructions="  exact instructions\n ")
    assert set(bot) == {"id", "name", "instructions", "connection_id", "created_at"}
    assert bot["connection_id"] is None
    status, body = api.request("GET")
    assert status == 200
    assert body == {
        "bots": [bot],
        "conversations": [],
        "user_id": api.owner,
        "can_manage_bots": True,
    }
    status, body = api.request("GET", user=api.member)
    assert status == 200 and body["can_manage_bots"] is False
    status, _ = api.request("POST", "/bots", {"name": "admin"}, user=api.member)
    assert status == 403
    with pytest.raises(StoreError, match="owner_required"):
        open_store(api.gateway, api.workspace).create_bot(api.member, "bypass")


def test_no_cookie_and_pat_cannot_impersonate_person_even_with_cookie(api):
    assert api.request("GET", user="anonymous")[0] == 401
    _, token = api.gateway.db.create_token(
        workspace_ref=api.workspace,
        user_ref=api.owner,
        name="agent",
        client="tests",
        policy=sessions.person_policy(),
        actor=api.owner,
    )
    for method, path, body in (("GET", "", None), ("POST", "/bots", {"name": "no"})):
        assert (
            api.request(method, path, body, headers={"Authorization": "Bearer " + token})[0] == 403
        )
    assert (
        api.request("GET", headers={"Cookie": [f"cmmn_session={api.sessions[api.owner]}", "x=y"]})[
            0
        ]
        == 401
    )


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": None},
        {"Origin": "null"},
        {"Origin": "http://evil.test"},
        {"Origin": ["http://app.test", "http://evil.test"]},
        {"Content-Type": "text/plain"},
        {"Content-Type": ["application/json", "text/plain"]},
    ],
)
def test_write_csrf_origin_and_json_boundary(api, headers):
    assert api.request("POST", "/bots", {"name": "no"}, headers=headers)[0] == 403
    assert api.request("GET", "/bots")[1]["bots"] == []


def test_body_bounds_exact_fields_and_invalid_shapes(api):
    for data in (
        {"name": "x", "user_id": api.owner},
        {"name": "x", "workspace_id": api.workspace},
        {"name": 17},
        {"name": "x" * 81},
        {"name": "x", "instructions": None},
    ):
        assert api.request("POST", "/bots", data)[0] == 400
    for raw in (b"[]", b"null", b"{broken", b'{"name":NaN}'):
        assert api.request("POST", "/bots", raw=raw)[0] == 400
    assert api.request("POST", "/bots", raw=b"x" * 65_537)[0] == 413
    assert (
        api.request("POST", "/bots", {"name": "x"}, headers={"Transfer-Encoding": "chunked"})[0]
        == 400
    )


def test_member_ids_and_connection_bindings_use_current_server_authority(api):
    bot = api.bot()
    status, _ = api.request(
        "POST",
        "/conversations",
        {"kind": "group", "member_ids": [api.outsider], "bot_ids": [bot["id"]]},
    )
    assert status == 404
    db = api.gateway.db
    connection, _ = db.create_token(
        workspace_ref=api.workspace,
        user_ref=api.owner,
        name="verified",
        client="tests",
        policy=sessions.person_policy(),
        actor=api.owner,
    )
    bound = api.bot(name="Bound", connection_id=connection)
    assert bound["connection_id"] == connection
    db._conn.execute(
        "UPDATE connections SET revoked_at='2020-01-01T00:00:00Z' WHERE id=?", (connection,)
    )
    assert api.request("POST", "/bots", {"name": "revoked", "connection_id": connection})[0] == 404
    assert api.request("POST", "/conversations", {"kind": "dm", "bot_ids": [bound["id"]]})[0] == 404
    other = db.create_workspace("other workspace", api.outsider)
    foreign, _ = db.create_token(
        workspace_ref=other,
        user_ref=api.outsider,
        name="foreign",
        client="tests",
        policy=sessions.person_policy(),
        actor=api.outsider,
    )
    assert api.request("POST", "/bots", {"name": "foreign", "connection_id": foreign})[0] == 404
    assert (
        api.request("POST", "/bots", {"name": "name-is-not-id", "connection_id": "verified"})[0]
        == 404
    )


def test_private_dm_is_hidden_from_member_and_other_workspace(api):
    dm = api.dm()["id"]
    status, posted = api.post(dm)
    assert status == 201
    status, memory = api.request("POST", f"/conversations/{dm}/memory", {"text": "DM_CANARY"})
    assert status == 201
    job_id, memory_id = posted["job"]["id"], memory["memory"]["id"]
    for method, path, data in (
        ("GET", f"/conversations/{dm}", None),
        ("GET", f"/conversations/{dm}/messages", None),
        ("GET", f"/conversations/{dm}/memory", None),
        ("POST", f"/conversations/{dm}/messages", {"text": "x", "request_id": "x"}),
        ("POST", f"/conversations/{dm}/memory", {"text": "x"}),
        ("DELETE", f"/conversations/{dm}/memory/{memory_id}", None),
        ("POST", f"/jobs/{job_id}/cancel", {}),
    ):
        assert api.request(method, path, data, user=api.member) == (404, {"error": "not_found"})
    assert api.request("GET", "/conversations", user=api.member)[1] == {"conversations": []}
    # The same user has a valid owner session in another workspace. Object IDs
    # from the first workspace still convey no authority there.
    other = api.gateway.db.create_workspace("owner second", api.owner)
    first_session = api.sessions[api.owner]
    api.sessions[api.owner] = sessions.start_session(api.gateway.db, api.owner, other)
    assert api.request("GET", f"/conversations/{dm}") == (404, {"error": "not_found"})
    assert api.request("POST", f"/jobs/{job_id}/cancel", {})[0] == 404
    api.sessions[api.owner] = first_session


def test_workspace_owner_cannot_read_another_persons_dm(api):
    dm = api.dm(user=api.member)["id"]
    assert api.request("GET", f"/conversations/{dm}")[0] == 404
    assert api.request("GET", "/conversations")[1] == {"conversations": []}


def test_group_membership_creator_is_server_added_and_only_selected_bot_runs(api):
    ada, other = api.bot(), api.bot(name="Other")
    status, body = api.request(
        "POST",
        "/conversations",
        {"kind": "group", "member_ids": [api.member], "bot_ids": [ada["id"], other["id"]]},
    )
    assert status == 201
    group = body["conversation"]
    assert set(group["member_ids"]) == {api.owner, api.member}
    assert api.request("GET", f"/conversations/{group['id']}", user=api.member)[0] == 200
    assert api.post(group["id"])[1]["job"] is None
    status, selected = api.post(group["id"], request_id="two", bot_id=ada["id"])
    assert status == 201 and selected["job"]["bot_id"] == ada["id"]
    jobs = api.request("GET", f"/conversations/{group['id']}/messages")[1]["jobs"]
    assert len(jobs) == 1


def test_message_intake_persists_before_wake_without_model_call(api, monkeypatch):
    from commonplace.chat import model

    def forbidden(*args, **kwargs):
        pytest.fail("model transport called in HTTP request")

    monkeypatch.setattr(model, "complete", forbidden)
    conversation = api.dm()["id"]
    wakes = []

    def wake():
        # A separate open can observe both records before the callback fires.
        current = open_store(api.gateway, api.workspace)
        wakes.append(
            (
                current.list_messages(api.owner, conversation),
                current.list_jobs(api.owner, conversation),
            )
        )

    api.gateway.coworker_worker = SimpleNamespace(wake=wake)
    status, posted = api.post(conversation, text="<script>exact & verbatim</script>")
    assert status == 201
    assert posted["job"]["state"] == "pending"
    assert wakes == [([posted["message"]], [posted["job"]])]
    assert api.post(conversation, text="<script>exact & verbatim</script>")[1] == posted
    assert api.post(conversation, text="changed")[0] == 409
    page = api.request("GET", f"/conversations/{conversation}")[1]
    assert page["messages"] == [posted["message"]]
    assert page["messages"][0]["text"] == "<script>exact & verbatim</script>"
    # Rendering is the UI's responsibility; the JSON API never emits HTML.
    with api.gateway.db._lock:
        dump = "\n".join(api.gateway.db._conn.iterdump())
    assert "<script>exact & verbatim</script>" not in dump
    assert (
        Path(api.gateway.supervisor.workspace_dir(api.workspace)) / "coworkers.sqlite3"
    ).is_file()


@pytest.mark.parametrize(
    "suffix",
    [
        "?limit=101",
        "?limit=0",
        "?after=-1",
        "?after=true",
        "?after=9223372036854775808",
        "?after=0&after=1",
        "?unknown=1",
    ],
)
def test_http_pagination_rejects_unbounded_or_ambiguous_parameters(api, suffix):
    conversation = api.dm()["id"]
    assert api.request("GET", f"/conversations/{conversation}/messages{suffix}")[0] == 400


def test_memory_visibility_tombstones_and_foreign_memory_concealment(api):
    conversation = api.dm()["id"]
    other = api.dm()["id"]
    status, body = api.request(
        "POST", f"/conversations/{conversation}/memory", {"text": "  exact memory\n☃  "}
    )
    assert status == 201
    memory = body["memory"]
    assert api.request("GET", f"/conversations/{conversation}/memory")[1] == {"memories": [memory]}
    assert api.request("DELETE", f"/conversations/{other}/memory/{memory['id']}")[0] == 404
    assert api.request("DELETE", f"/conversations/{conversation}/memory/{memory['id']}") == (
        200,
        {"ok": True},
    )
    assert api.request("GET", f"/conversations/{conversation}/memory")[1] == {"memories": []}


@pytest.mark.parametrize(
    "change", ["revoke_session", "expire_session", "disable_user", "remove_member"]
)
def test_revoked_or_expired_identity_cannot_read_or_publish_pending_result(api, change):
    conversation = api.dm()["id"]
    posted = api.post(conversation)[1]
    store = open_store(api.gateway, api.workspace)
    store.claim_job()
    db = api.gateway.db
    with db._lock:
        if change == "revoke_session":
            sessions.end_session(db, api.sessions[api.owner])
        elif change == "expire_session":
            db._conn.execute("UPDATE sessions SET expires_at='2000-01-01T00:00:00Z'")
        elif change == "disable_user":
            db._conn.execute(
                "UPDATE users SET disabled_at='2000-01-01T00:00:00Z' WHERE id=?", (api.owner,)
            )
        else:
            db._conn.execute(
                "DELETE FROM memberships WHERE workspace_id=? AND user_id=?",
                (api.workspace, api.owner),
            )
    assert api.request("GET", f"/conversations/{conversation}")[0] == 401
    with pytest.raises(StoreError):
        store.add_bot_reply(posted["job"]["id"], "late private response")
    with store._transaction() as conn:
        assert conn.execute("SELECT count(*) FROM messages").fetchone()[0] == 1


def test_revoked_session_pending_job_is_failed_without_claim(api):
    conversation = api.dm()["id"]
    api.post(conversation)
    sessions.end_session(api.gateway.db, api.sessions[api.owner])
    store = open_store(api.gateway, api.workspace)
    assert store.claim_job() is None
    assert store.list_jobs(api.owner, conversation)[0]["code"] == "no_access"


def test_connection_revocation_after_claim_fences_reply(api):
    connection, _ = api.gateway.db.create_token(
        workspace_ref=api.workspace,
        user_ref=api.owner,
        name="live",
        client="tests",
        policy=sessions.person_policy(),
        actor=api.owner,
    )
    conversation = api.dm(bot=api.bot(connection_id=connection))["id"]
    post = api.post(conversation)[1]
    store = open_store(api.gateway, api.workspace)
    store.claim_job()
    api.gateway.db._conn.execute(
        "UPDATE connections SET revoked_at='2000-01-01T00:00:00Z' WHERE id=?", (connection,)
    )
    with pytest.raises(StoreError):
        store.add_bot_reply(post["job"]["id"], "late")
    assert len(store.list_messages(api.owner, conversation)) == 1


def test_only_public_routes_installed_and_runtime_is_optional(monkeypatch):
    callbacks, registered = [], []
    gateway = SimpleNamespace(
        db=SimpleNamespace(add_pre_close_callback=callbacks.append),
        add_route=lambda *args: registered.append(args),
        add_prefix_route=lambda *args: registered.append(args),
    )
    original_import = coworkers.importlib.import_module

    def missing(name):
        if name == "commonplace.chat.coworkers.runtime":
            raise ModuleNotFoundError(name=name)
        return original_import(name)

    monkeypatch.setattr(coworkers.importlib, "import_module", missing)
    coworkers.install(gateway)
    coworkers.install(gateway)
    assert len(registered) == 6 and callbacks == []
    assert not hasattr(gateway, "coworker_worker")
    assert all("claim" not in route[1] and "finish" not in route[1] for route in registered)


def test_runtime_worker_is_installed_once_and_stopped_with_gateway(monkeypatch):
    callbacks, started = [], []
    gateway = SimpleNamespace(
        db=SimpleNamespace(add_pre_close_callback=callbacks.append),
        add_route=lambda *_: None,
        add_prefix_route=lambda *_: None,
    )
    worker = SimpleNamespace(stop=lambda: None)

    def factory(gateway, *, start):
        started.append(gateway)
        return worker

    monkeypatch.setattr(
        coworkers.importlib, "import_module", lambda _: SimpleNamespace(CoworkerWorker=factory)
    )
    coworkers.install(gateway)
    coworkers.install(gateway)
    assert started == [gateway]
    assert gateway.coworker_worker is worker
    assert callbacks == [worker.stop]


def test_board_binding_reuses_board_service_and_never_copies_private_text(api):
    from commonplace import events
    from commonplace.boards.service import BoardService
    from commonplace.extensions import ToolContext

    events.ensure_schema(api.gateway.db)
    principal = sessions.person_principal(api.gateway.db, api.owner, api.workspace)
    service = BoardService(ToolContext(principal=principal, gateway=api.gateway))
    service.board_add("work", name="Work", wing="project_work")
    bot = api.bot()
    status, body = api.request(
        "POST", "/conversations", {"kind": "dm", "bot_ids": [bot["id"]], "board": "work"}
    )
    assert status == 201
    conversation = body["conversation"]["id"]
    api.post(conversation, text="PRIVATE_DM_NEVER_SPACE")
    api.request(
        "POST", f"/conversations/{conversation}/memory", {"text": "PRIVATE_MEMORY_NEVER_SPACE"}
    )
    assert service.space_read("work")["messages"] == []
    assert (
        api.request(
            "POST", "/conversations", {"kind": "dm", "bot_ids": [bot["id"]], "board": "missing"}
        )[0]
        == 404
    )
    with service.store.transaction() as conn:
        conn.execute("UPDATE boards SET archived=1 WHERE slug='work'")
    assert (
        api.request(
            "POST", "/conversations", {"kind": "dm", "bot_ids": [bot["id"]], "board": "work"}
        )[0]
        == 404
    )


def test_internal_worker_methods_have_no_public_http_alias(api):
    for path in (
        "/jobs/1/claim",
        "/jobs/1/finish",
        "/jobs/recover",
        "/conversations/unknown/add_bot_reply",
    ):
        assert api.request("POST", path, {})[0] == 404
