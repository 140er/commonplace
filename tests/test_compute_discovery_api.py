"""Frozen owner-session REST against real canonical stores; no HTTP server."""

import io
import json
import socket
import threading
from email.message import Message
from pathlib import Path

import pytest

from commonplace import sessions
from commonplace.compute import credentials, discovery_routes, discovery_service, discovery_store
from commonplace.compute.control import StoreError
from tests.test_compute_control import compute_env as compute_env
from tests.test_compute_discovery_service import discovery_env as discovery_env


class API:
    def __init__(self, env):
        self.env = env

    def request(self, method="GET", path="", body=None, *, headers=None, cookie=None, raw=None):
        raw = raw if raw is not None else json.dumps(body).encode() if body is not None else b""
        values = {
            "Host": "app.test",
            "Content-Type": "application/json",
            "Content-Length": str(len(raw)),
            "Cookie": f"{sessions.COOKIE}={cookie or self.env.cookie}",
        }
        if method != "GET":
            values["Origin"] = "http://app.test"
        values.update(headers or {})
        h = type("Request", (), {})()
        h.command, h.path = method, "/api/compute/discoveries" + path
        h.headers = Message()
        for name, value in values.items():
            if value is not None:
                for item in value if isinstance(value, list) else [value]:
                    h.headers[name] = item
        h.rfile, h.wfile = io.BytesIO(raw), io.BytesIO()
        h.close_connection = False
        h.status = None
        h.response_headers = {}
        h.send_response = lambda status: setattr(h, "status", status)
        h.send_header = lambda name, value: h.response_headers.update({name: value})
        h.end_headers = lambda: None
        discovery_routes.handle(self.env.gateway, h)
        assert h.response_headers["Cache-Control"] == "no-store"
        return h.status, json.loads(h.wfile.getvalue())


def payload(state, **changes):
    return {"scope_id": state.scope["id"], "sources": ["linux_arp"], "request_id": "one", **changes}


def test_default_off_inventory_and_install_are_inert(compute_env, monkeypatch):
    env = compute_env

    def forbidden(*args, **kwargs):
        pytest.fail("disabled HTTP/installation accessed keys, sockets or threads")

    for name in ("socket", "create_connection", "getaddrinfo"):
        monkeypatch.setattr(socket, name, forbidden)
    monkeypatch.setattr(threading.Thread, "start", forbidden)
    monkeypatch.setattr(credentials.CredentialStore, "__init__", forbidden)
    monkeypatch.setattr(credentials, "initialize_wrapping_key", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    registrations = []
    env.gateway.add_route = lambda *args: registrations.append(("exact", *args))
    env.gateway.add_prefix_route = lambda *args: registrations.append(("prefix", *args))
    discovery_routes.install(env.gateway)
    assert {(kind, method, path) for kind, method, path, callback in registrations} == {
        (kind, method, path)
        for method in ("GET", "POST")
        for kind, path in (
            ("exact", "/api/compute/discoveries"),
            ("prefix", "/api/compute/discoveries/"),
        )
    }
    api = API(env)
    assert api.request() == (200, {"discoveries": [], "capabilities": {"passive_discovery": False}})
    assert api.request(
        "POST", body={"scope_id": "synthetic", "sources": ["linux_arp"], "request_id": "one"}
    ) == (503, {"error": "discovery_unavailable"})


def test_http_commits_intake_wakes_only_and_exact_replay_conflicts(discovery_env, monkeypatch):
    state = discovery_env
    api = API(state.env)

    def forbidden(*args, **kwargs):
        pytest.fail("HTTP touched transport/custody/key material")

    monkeypatch.setattr(state.service, "_custody_store", forbidden)
    monkeypatch.setattr(state.driver, "connect_gateway", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    wakes = []
    monkeypatch.setattr(state.service, "wake", lambda: wakes.append(True))
    status, body = api.request("POST", body=payload(state))
    assert status == 201
    assert body["discovery"]["state"] == "queued"
    assert wakes == [True] and state.driver.calls == []
    assert api.request("POST", body=payload(state)) == (200, body)
    assert api.request("POST", body=payload(state, sources=["ip_neigh"])) == (
        409,
        {"error": "conflict"},
    )
    assert api.request(path="/" + body["discovery"]["id"]) == (200, body)
    status, listing = api.request()
    assert status == 200 and listing["capabilities"] == {"passive_discovery": True}
    state.service.stop()
    assert api.request()[1]["capabilities"] == {"passive_discovery": False}
    assert api.request("POST", body=payload(state, request_id="new")) == (
        503,
        {"error": "discovery_unavailable"},
    )
    # A disabled service can return historical exact intake without new effects.
    assert api.request("POST", body=payload(state)) == (200, body)


@pytest.mark.parametrize(
    "headers,status,code",
    [
        ({"Cookie": None}, 401, "sign_in"),
        ({"Cookie": "cmmn_session=missing"}, 401, "sign_in"),
        ({"Cookie": ["cmmn_session=missing", "cmmn_session=another"]}, 401, "sign_in"),
        ({"Authorization": "Bearer synthetic"}, 403, "owner_required"),
    ],
)
def test_person_cookie_only_no_pat_impersonation(compute_env, headers, status, code):
    assert API(compute_env).request(headers=headers) == (status, {"error": code})


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
def test_mandatory_exact_origin_and_non_simple_json_on_each_mutation(discovery_env, headers):
    state, api = discovery_env, API(discovery_env.env)
    item, _ = state.submit()
    for path, body in (("", payload(state, request_id="two")), (f"/{item['id']}/cancel", {})):
        assert api.request("POST", path, body, headers=headers) == (
            403,
            {"error": "owner_required"},
        )
    assert state.get(item["id"])["state"] == "queued"


@pytest.mark.parametrize(
    "raw",
    [
        b"[]",
        b"null",
        b"{bad",
        b"{}",
        b'{"scope_id":NaN}',
        b'{"scope_id":"a","scope_id":"b"}',
        b"x" * 4097,
    ],
)
def test_malformed_oversized_duplicate_json_fixed_errors(discovery_env, raw):
    assert API(discovery_env.env).request("POST", raw=raw) == (400, {"error": "invalid_request"})


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
def test_strict_body_framing(discovery_env, headers):
    assert API(discovery_env.env).request("POST", body=payload(discovery_env), headers=headers) == (
        400,
        {"error": "invalid_request"},
    )


@pytest.mark.parametrize(
    "extra",
    [
        {"workspace": "foreign"},
        {"gateway_id": "foreign"},
        {"verified": True},
        {"cidrs": ["10.0.0.0/8"]},
        {"host": "foreign"},
        {"path": "/tmp/synthetic"},
        {"credential_id": "foreign"},
        {"limits": {}},
        {"grant": {}},
    ],
)
def test_intake_cannot_choose_identity_grants_credentials_or_verification(discovery_env, extra):
    state = discovery_env
    assert API(state.env).request("POST", body=payload(state, **extra)) == (
        400,
        {"error": "invalid_request"},
    )
    assert state.driver.calls == []


@pytest.mark.parametrize(
    "sources",
    [[], ["arp"], ["linux_arp", "linux_arp"], ["/proc/net/arp"], "linux_arp", ["mdns"], ["active"]],
)
def test_only_trusted_supported_source_names(discovery_env, sources):
    assert API(discovery_env.env).request("POST", body=payload(discovery_env, sources=sources)) == (
        400,
        {"error": "invalid_request"},
    )


def test_current_owner_and_object_authority_and_cross_audience_concealment(discovery_env):
    state, env = discovery_env, discovery_env.env
    item, _ = state.submit()
    api = API(env)
    foreign_cookie = sessions.start_session(env.db, env.foreign, env.other_workspace)
    assert api.request(path="/" + item["id"], cookie=foreign_cookie) == (
        404,
        {"error": "not_found"},
    )
    assert api.request("POST", f"/{item['id']}/cancel", raw=b"[]", cookie=foreign_cookie) == (
        404,
        {"error": "not_found"},
    )
    # Another current owner of the same workspace is a distinct discovery audience.
    with env.db._tx():
        env.db._conn.execute(
            "UPDATE memberships SET role='owner' WHERE workspace_id=? AND user_id=?",
            (env.workspace, env.member),
        )
    other_cookie = sessions.start_session(env.db, env.member, env.workspace)
    assert api.request(path="/" + item["id"], cookie=other_cookie) == (404, {"error": "not_found"})
    assert api.request(cookie=other_cookie)[1]["discoveries"] == []
    with env.service.store.connection() as conn:
        conn.execute(
            "UPDATE tasks SET board_id=(SELECT id FROM boards WHERE slug='elsewhere') WHERE id=?",
            (env.task,),
        )
    assert api.request(path="/" + item["id"]) == (404, {"error": "not_found"})
    assert api.request()[1]["discoveries"] == []


def test_session_revocation_during_body_read_is_rechecked(discovery_env, monkeypatch):
    state, env = discovery_env, discovery_env.env
    original = discovery_routes._body

    def revoke(h):
        body = original(h)
        sessions.end_session(env.db, env.cookie)
        return body

    monkeypatch.setattr(discovery_routes, "_body", revoke)
    assert API(env).request("POST", body=payload(state)) == (401, {"error": "sign_in"})
    with state.store._journal() as conn:
        assert conn.execute("SELECT count(*) FROM discoveries").fetchone()[0] == 0


def test_cancel_empty_body_and_public_projection(discovery_env):
    state, api = discovery_env, API(discovery_env.env)
    item, _ = state.submit()
    assert api.request("POST", f"/{item['id']}/cancel", {"verified": True}) == (
        400,
        {"error": "invalid_request"},
    )
    status, body = api.request("POST", f"/{item['id']}/cancel", {})
    assert status == 200 and body["discovery"]["state"] == "cancelled"
    raw = json.dumps(body)
    for private in (
        state.env.auth["session_hash"],
        state.credential.credential_id,
        "session_hash",
        "private_key",
        "handle",
    ):
        assert private not in raw


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/"),
        ("GET", "?limit=1000"),
        ("POST", "/run"),
        ("POST", "/scope/revoke"),
        ("POST", "/item/verify"),
        ("POST", "/item/execute"),
        ("DELETE", "/item"),
        ("GET", "/item%2fsecret"),
    ],
)
def test_unknown_paths_do_not_take_over_scope_or_execution_routes(discovery_env, method, path):
    status, body = API(discovery_env.env).request(method, path, {})
    assert status == (400 if "?" in path else 404)
    assert body == {"error": "invalid_request" if "?" in path else "not_found"}


def test_missing_fence_or_unparticipating_fence_never_available(discovery_env):
    state = discovery_env
    state.service.stop()
    service = state.new_service(fence=None)
    assert not service.start(background=False)
    assert not service.available()
    state.fence.domains = frozenset({"compute"})
    service = state.new_service()
    assert not service.start(background=False)
    assert not service.available()


def test_bounded_lists_and_capacity_are_durable(discovery_env, monkeypatch):
    state = discovery_env
    monkeypatch.setattr(discovery_store, "MAX_DISCOVERIES", 2)
    monkeypatch.setattr(discovery_store, "MAX_LIST", 1)
    state.submit("one")
    state.submit("two")
    with pytest.raises(StoreError, match="discovery_capacity"):
        state.submit("three")
    assert len(API(state.env).request()[1]["discoveries"]) == 1


def test_configure_never_reads_environment_or_starts_workers(compute_env, monkeypatch):
    env = compute_env

    def forbidden(*args, **kwargs):
        pytest.fail("constructor/configuration started work")

    monkeypatch.setattr(threading.Thread, "start", forbidden)
    monkeypatch.setattr(credentials.CredentialStore, "__init__", forbidden)
    store = discovery_store.open_store(env.gateway, env.workspace, control_store=env.store)
    service = discovery_service.configure(env.gateway, env.workspace, store=store)
    assert not service.start()
    assert not service.available()
    assert not env.path.exists()


def test_owner_demotion_denies_all_reads_and_mutations(discovery_env):
    state, env = discovery_env, discovery_env.env
    item, _ = state.submit()
    env.db._conn.execute(
        "UPDATE memberships SET role='member' WHERE user_id=? AND workspace_id=?",
        (env.owner, env.workspace),
    )
    api = API(env)
    for method, path, body in (
        ("GET", "", None),
        ("GET", "/" + item["id"], None),
        ("POST", "", payload(state, request_id="two")),
        ("POST", f"/{item['id']}/cancel", {}),
    ):
        assert api.request(method, path, body) == (403, {"error": "owner_required"})
