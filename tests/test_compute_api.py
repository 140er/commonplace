"""Frozen REST contract against real authority and stores, without an HTTP server."""

import io
import json
import socket
import threading
from email.message import Message
from pathlib import Path

import pytest

from commonplace import sessions
from commonplace.compute import control, routes
from commonplace.compute.control import open_store
from tests.test_compute_control import (  # noqa: F401 -- shared real-store fixture
    compute_env,
    gateway_payload,
    node_payload,
    public_pin,
    scope_payload,
)


class Request:
    def __init__(self, method, path, headers, body):
        self.command, self.path = method, path
        self.headers = Message()
        for key, value in headers:
            self.headers[key] = value
        self.rfile, self.wfile = io.BytesIO(body), io.BytesIO()
        self.status = None
        self.close_connection = False
        self.response_headers = {}

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.response_headers[name] = value

    def end_headers(self):
        pass


class API:
    def __init__(self, env):
        self.env = env
        self.cookie = env.cookie

    def request(self, method="GET", path="", body=None, *, cookie=None, headers=None, raw=None):
        raw = raw if raw is not None else json.dumps(body).encode() if body is not None else b""
        values = [
            ("Host", "app.test"),
            ("Content-Type", "application/json"),
            ("Content-Length", str(len(raw))),
        ]
        if cookie is not False:
            values.append(("Cookie", f"{sessions.COOKIE}={cookie or self.cookie}"))
        if method != "GET":
            values.append(("Origin", "http://app.test"))
        for key, value in (headers or {}).items():
            values = [(k, v) for k, v in values if k != key]
            if value is not None:
                values.extend(
                    (key, entry) for entry in (value if isinstance(value, list) else [value])
                )
        request = Request(method, "/api/compute" + path, values, raw)
        routes.handle(self.env.gateway, request)
        assert request.status is not None
        assert request.response_headers["Cache-Control"] == "no-store"
        return request.status, json.loads(request.wfile.getvalue())

    def enrolled(self):
        status, result = self.request("POST", "/gateways", gateway_payload())
        assert status == 201, result
        gateway = result["gateway"]
        status, result = self.request("POST", "/nodes", node_payload(gateway["id"]))
        assert status == 201, result
        return gateway, result["node"]

    def propose(self, node, **changes):
        return self.request(
            "POST",
            "/executions",
            {
                "node_id": node["id"],
                "task_number": self.env.task,
                "operation": "system.info.v1",
                "arguments": {},
                "request_id": "one",
                **changes,
            },
        )


@pytest.fixture
def api(request):
    return API(request.getfixturevalue("compute_env"))


def test_exact_inventory_response_and_each_named_collection(api):
    expected = {
        "gateways": [],
        "nodes": [],
        "scopes": [],
        "executions": [],
        "capabilities": {"transport": False, "passive_discovery": False, "scripts": False},
        "can_manage": True,
    }
    assert api.request() == (200, expected)
    for kind in ("gateways", "nodes", "scopes", "executions"):
        assert api.request(path="/" + kind) == (200, {kind: []})


def test_frozen_rest_positive_workflow_replay_approval_scope_revoke_cancel(api):
    gateway, node = api.enrolled()
    assert gateway["host_key"] == public_pin()
    assert gateway["state"] == node["state"] == "pending_verification"
    assert api.request(path="/gateways") == (200, {"gateways": [gateway]})
    assert api.request(path="/nodes") == (200, {"nodes": [node]})
    status, result = api.request("POST", "/scopes", scope_payload(api.env, gateway["id"]))
    assert status == 201
    scope = result["scope"]
    assert api.request("POST", f"/scopes/{scope['id']}/revoke", {}) == (
        200,
        {"scope": {**scope, "revoked": True}},
    )
    status, result = api.propose(node)
    assert status == 201
    execution = result["execution"]
    assert api.propose(node) == (200, result)
    assert api.propose(node, request_id="two")[0] == 201
    status, result = api.request(
        "POST",
        f"/executions/{execution['id']}/approve",
        {"expected_digest": execution["digest"], "expires_at": control.time.time() + 60},
    )
    assert status == 200 and result["execution"]["state"] == "approved"
    status, result = api.request("POST", f"/executions/{execution['id']}/cancel", {})
    assert status == 200 and result["execution"]["state"] == "cancelled"
    assert api.request("POST", f"/executions/{execution['id']}/cancel", {}) == (200, result)
    assert api.propose(node) == (200, result)
    serialized = json.dumps(api.request()[1])
    assert api.cookie not in serialized
    assert sessions._hash(api.cookie) not in serialized
    for forbidden in (
        "session_hash",
        "approval_session_hash",
        "approved_by",
        "binding",
        "private_key",
    ):
        assert forbidden not in serialized


def test_cookie_person_owner_only_and_pat_never_impersonates_even_with_cookie(api):
    env = api.env
    assert api.request(cookie=False) == (401, {"error": "sign_in"})
    assert api.request(cookie="invalid") == (401, {"error": "sign_in"})
    member_cookie = sessions.start_session(env.db, env.member, env.workspace)
    assert api.request(cookie=member_cookie) == (403, {"error": "owner_required"})
    _, token = env.db.create_token(
        workspace_ref=env.workspace,
        user_ref=env.owner,
        name="owner-agent",
        client="tests",
        policy=sessions.person_policy(),
        actor=env.owner,
    )
    for method, path, payload in (("GET", "", None), ("POST", "/gateways", gateway_payload())):
        assert api.request(method, path, payload, headers={"Authorization": "Bearer " + token}) == (
            403,
            {"error": "owner_required"},
        )
    assert api.request(headers={"Cookie": [f"cmmn_session={api.cookie}", "x=y"]}) == (
        401,
        {"error": "sign_in"},
    )
    assert api.request(headers={"Cookie": f"cmmn_session={api.cookie}; cmmn_session=another"}) == (
        401,
        {"error": "sign_in"},
    )
    assert not env.path.exists()


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": None},
        {"Origin": "null"},
        {"Origin": "http://evil.test"},
        {"Origin": ["http://app.test", "http://evil.test"]},
        {"Content-Type": None},
        {"Content-Type": "text/plain"},
        {"Content-Type": ["application/json", "text/plain"]},
    ],
)
def test_origin_and_non_simple_json_boundary_on_every_mutation(api, headers):
    gateway, node = api.enrolled()
    _, scope_result = api.request("POST", "/scopes", scope_payload(api.env, gateway["id"]))
    _, result = api.propose(node)
    execution = result["execution"]
    for path, payload in (
        ("/gateways", gateway_payload()),
        ("/nodes", node_payload(gateway["id"])),
        ("/scopes", scope_payload(api.env, gateway["id"])),
        (f"/scopes/{scope_result['scope']['id']}/revoke", {}),
        (
            "/executions",
            {
                "node_id": node["id"],
                "task_number": api.env.task,
                "operation": "system.info.v1",
                "arguments": {},
                "request_id": "bad",
            },
        ),
        (
            f"/executions/{execution['id']}/approve",
            {"expected_digest": execution["digest"], "expires_at": control.time.time() + 60},
        ),
        (f"/executions/{execution['id']}/cancel", {}),
    ):
        assert api.request("POST", path, payload, headers=headers) == (
            403,
            {"error": "owner_required"},
        )
    assert api.request(path="/executions")[1]["executions"][0]["state"] == "awaiting_approval"
    assert api.request(path="/scopes")[1]["scopes"][0]["revoked"] is False


@pytest.mark.parametrize(
    "raw",
    [b"[]", b"null", b"{bad", b'{"name":NaN}', b'{"name":"a","name":"b"}', b"", b"x" * 65_537],
)
def test_malformed_ambiguous_and_oversized_json_have_fixed_error_code(api, raw):
    assert api.request("POST", "/gateways", raw=raw) == (400, {"error": "invalid_request"})


@pytest.mark.parametrize(
    "headers",
    [
        {"Content-Length": None},
        {"Content-Length": "-1"},
        {"Content-Length": "bogus"},
        {"Content-Length": ["2", "2"]},
        {"Transfer-Encoding": "chunked"},
    ],
)
def test_request_framing_is_strict(api, headers):
    assert api.request("POST", "/gateways", gateway_payload(), headers=headers) == (
        400,
        {"error": "invalid_request"},
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"workspace_id": "ws_foreign"},
        {"user_id": "u_foreign"},
        {"state": "verified"},
        {"epoch": 55},
        {"private_key": "synthetic-canary"},
        {"pin_confirmed": False},
    ],
)
def test_request_fields_cannot_choose_identity_verification_or_credentials(api, changes):
    assert api.request("POST", "/gateways", gateway_payload(**changes)) == (
        400,
        {"error": "invalid_request"},
    )
    assert api.request(path="/gateways") == (200, {"gateways": []})


def test_unknown_routes_and_methods_cannot_verify_consume_or_dispatch(api):
    gateway, node = api.enrolled()
    _, result = api.propose(node)
    execution = result["execution"]
    for path in (
        f"/gateways/{gateway['id']}/verify",
        f"/nodes/{node['id']}/verify",
        f"/executions/{execution['id']}/run",
        f"/executions/{execution['id']}/consume",
        "/dispatch",
        "/observations",
        "/scripts",
    ):
        assert api.request("POST", path, {}) == (404, {"error": "not_found"})
    assert api.request("DELETE", f"/executions/{execution['id']}", {}) == (
        404,
        {"error": "not_found"},
    )
    assert api.request(path="/executions/") == (404, {"error": "not_found"})
    assert api.request(path="/executions?limit=1000000") == (400, {"error": "invalid_request"})
    assert api.request(path=f"/executions/{execution['id']}") == (404, {"error": "not_found"})


def test_foreign_workspace_ids_are_concealed_before_action_body_validation(api):
    gateway, node = api.enrolled()
    _, scope_result = api.request("POST", "/scopes", scope_payload(api.env, gateway["id"]))
    _, result = api.propose(node)
    execution = result["execution"]
    foreign_cookie = sessions.start_session(api.env.db, api.env.foreign, api.env.other_workspace)
    for path in (
        f"/scopes/{scope_result['scope']['id']}/revoke",
        f"/executions/{execution['id']}/cancel",
        f"/executions/{execution['id']}/approve",
    ):
        assert api.request("POST", path, raw=b"[]", cookie=foreign_cookie) == (
            404,
            {"error": "not_found"},
        )
    assert api.request("POST", "/nodes", node_payload(gateway["id"]), cookie=foreign_cookie) == (
        404,
        {"error": "not_found"},
    )
    assert api.propose(node)[0] == 200
    assert api.request(path="/nodes", cookie=foreign_cookie) == (200, {"nodes": []})


@pytest.mark.parametrize(
    "action", ["approve", "cancel", "revoke", "propose", "node", "gateway", "scope"]
)
def test_current_owner_demotion_denies_each_operation_despite_valid_cookie(api, action):
    gateway, node = api.enrolled()
    _, scope_result = api.request("POST", "/scopes", scope_payload(api.env, gateway["id"]))
    _, result = api.propose(node)
    execution = result["execution"]
    paths = {
        "approve": (
            f"/executions/{execution['id']}/approve",
            {"expected_digest": execution["digest"], "expires_at": control.time.time() + 60},
        ),
        "cancel": (f"/executions/{execution['id']}/cancel", {}),
        "revoke": (f"/scopes/{scope_result['scope']['id']}/revoke", {}),
        "propose": (
            "/executions",
            {
                "node_id": node["id"],
                "task_number": api.env.task,
                "operation": "system.info.v1",
                "arguments": {},
                "request_id": "two",
            },
        ),
        "node": ("/nodes", node_payload(gateway["id"])),
        "gateway": ("/gateways", gateway_payload()),
        "scope": ("/scopes", scope_payload(api.env, gateway["id"])),
    }
    api.env.db._conn.execute(
        "UPDATE memberships SET role='member' WHERE user_id=? AND workspace_id=?",
        (api.env.owner, api.env.workspace),
    )
    path, payload = paths[action]
    assert api.request("POST", path, payload) == (403, {"error": "owner_required"})


def test_revoked_session_and_deleted_task_deny_after_intake(api):
    _, node = api.enrolled()
    _, result = api.propose(node)
    execution = result["execution"]
    with api.env.service.store.connection() as conn:
        # Change to another board, keeping the same wing and canonical number.
        conn.execute(
            "UPDATE tasks SET board_id=(SELECT id FROM boards WHERE slug='elsewhere') WHERE id=?",
            (api.env.task,),
        )
    assert api.request(
        "POST",
        f"/executions/{execution['id']}/approve",
        {"expected_digest": execution["digest"], "expires_at": control.time.time() + 60},
    ) == (404, {"error": "not_found"})
    sessions.end_session(api.env.db, api.cookie)
    assert api.request("POST", f"/executions/{execution['id']}/cancel", {}) == (
        401,
        {"error": "sign_in"},
    )


def test_boundary_rechecks_current_session_after_body_read(api, monkeypatch):
    original = routes._body

    def revoke_during_body(handler):
        body = original(handler)
        sessions.end_session(api.env.db, api.cookie)
        return body

    monkeypatch.setattr(routes, "_body", revoke_during_body)
    assert api.request("POST", "/gateways", gateway_payload()) == (401, {"error": "sign_in"})
    assert not api.env.path.exists()


def test_routes_and_constructors_have_no_custody_transport_network_or_thread_effects(
    api, monkeypatch
):
    from commonplace.compute import credentials, transport

    def forbidden(*args, **kwargs):
        pytest.fail("compute management touched an effectful integration")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(threading.Thread, "start", forbidden)
    monkeypatch.setattr(credentials.CredentialStore, "__init__", forbidden)
    monkeypatch.setattr(credentials, "initialize_wrapping_key", forbidden)
    monkeypatch.setattr(transport.SSHTransport, "__init__", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    registered = []
    api.env.gateway.add_route = lambda *args: registered.append(args)
    api.env.gateway.add_prefix_route = lambda *args: registered.append(args)
    routes.install(api.env.gateway)
    lazy = open_store(api.env.gateway, api.env.workspace)
    assert lazy is not None and len(registered) == 8
    assert not api.env.path.exists()
    _, node = api.enrolled()
    _, result = api.propose(node)
    execution = result["execution"]
    assert (
        api.request(
            "POST",
            f"/executions/{execution['id']}/approve",
            {"expected_digest": execution["digest"], "expires_at": control.time.time() + 60},
        )[0]
        == 200
    )
    assert not (api.env.path.parent / "compute-credentials.sqlite3").exists()


def test_authority_fence_timeout_returns_bounded_unavailable(api, monkeypatch):
    from commonplace.compute.fence import FenceDenied

    def unavailable(*args):
        raise FenceDenied("authority_timeout")

    monkeypatch.setattr(control.Store, "_owner", unavailable)
    assert api.request() == (503, {"error": "authority_unavailable"})
