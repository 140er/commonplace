"""Real loopback requests against the gateway, with no worker or network services."""

import http.client
import json
import socket
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from commonplace.control_db import ControlDB
from commonplace.gateway import Gateway
from commonplace.handoff import api
from commonplace.handoff.db import HandoffDBError
from commonplace.handoff.models import (
    EVENTS,
    ContextUsage,
    Handoff,
    HandoffResult,
    HandoffSettings,
    LauncherConfig,
    RecallHit,
    SettingRow,
    StoredThread,
    ThreadMessage,
    ThreadMeta,
)
from commonplace.handoff.recall import ThreadNotFound
from commonplace.handoff.settings import SettingsError
from commonplace.handoff.thread_store import ThreadConflict
from commonplace.policy import Policy

NOT_FOUND = {"error": "not found"}
TRANSCRIPT = '  {"role":"user","content":"café 🦉\\nkeep every word"}\r\n\n'
HANDOFF_BODY = {
    "meta": {"thread_id": "source", "agent": "worker", "task_ref": "#142"},
    "transcript": TRANSCRIPT,
}

# name, method, target, body, authorization, success status
ROUTES = [
    ("create", "POST", "/api/handoff", HANDOFF_BODY, "write", 200),
    ("list", "GET", "/api/handoffs?task=%23142", None, "read", 200),
    ("handoff", "GET", "/api/handoffs/hof_local", None, "read", 200),
    (
        "successor",
        "POST",
        "/api/handoffs/hof_local/successor",
        {"agent_ref": "agent:new"},
        "write",
        200,
    ),
    (
        "launch_failure",
        "POST",
        "/api/handoffs/hof_local/launch-failed",
        {"error": "launcher exited"},
        "write",
        200,
    ),
    ("relaunch", "POST", "/api/handoffs/hof_local/relaunch", None, "owner", 200),
    ("thread", "GET", "/api/threads/thr_local", None, "read", 200),
    ("messages", "GET", "/api/threads/thr_local/messages", None, "read", 200),
    ("recall", "POST", "/api/threads/thr_local/recall", {"query": "café"}, "read", 200),
    ("chain", "GET", "/api/threads/thr_local/chain", None, "read", 200),
    ("settings", "GET", "/api/settings/handoff", None, "any", 200),
    (
        "set",
        "PUT",
        "/api/settings/handoff",
        {"scope": "task", "ref": "#142", "enabled": True},
        "any",
        200,
    ),
    (
        "clear",
        "DELETE",
        "/api/settings/handoff?scope=task&ref=%23142",
        None,
        "owner",
        200,
    ),
    ("webhooks", "GET", "/api/webhooks", None, "owner", 200),
    ("webhook_add", "POST", "/api/webhooks", {"url": "http://127.0.0.1/wake"}, "owner", 201),
    ("webhook_remove", "DELETE", "/api/webhooks/hook_local", None, "owner", 200),
]


class _Supervisor:
    def get(self, workspace):
        raise AssertionError("API tests must never start a worker")


class _Data:
    def __init__(self, local, other, calls):
        self.calls = calls
        self.records = {
            "hof_local": Handoff(
                "hof_local",
                local,
                "key",
                "source",
                "stored",
                task_ref="#142",
                connection_id="private-connection",
                doc_markdown=TRANSCRIPT,
            ),
            "hof_other": Handoff("hof_other", other, "key", "source", "stored", task_ref="#142"),
        }
        self.rows = [
            SettingRow(
                workspace,
                "task",
                "#142",
                enabled=workspace == local,
                launcher=LauncherConfig("webhook", url="http://127.0.0.1/launch", secret="hidden"),
            )
            for workspace in (local, other)
        ]
        self.hooks = {
            name: {
                "id": name,
                "workspace_id": workspace,
                "url": "http://127.0.0.1/wake",
                "events": list(EVENTS),
                "secret": "never-list-this",
            }
            for name, workspace in (("hook_local", local), ("hook_other", other))
        }

    def get(self, record_id):
        self.calls.append(("get_handoff", record_id))
        return self.records.get(record_id)

    def list(self, workspace_id, *, task_ref="", limit=50):
        self.calls.append(("list", workspace_id, task_ref))
        return [
            row
            for row in self.records.values()
            if row.workspace_id == workspace_id and (not task_ref or row.task_ref == task_ref)
        ]

    def list_settings(self, workspace_id):
        self.calls.append(("list_settings", workspace_id))
        return [row for row in self.rows if row.workspace_id == workspace_id]

    def webhooks(self, workspace_id):
        self.calls.append(("webhooks", workspace_id))
        return [dict(row) for row in self.hooks.values() if row["workspace_id"] == workspace_id]

    def add_webhook(self, workspace_id, url, secret, events, *, actor):
        self.calls.append(("add_webhook", workspace_id, url, secret, events, actor))
        self.hooks["hook_new"] = {
            "id": "hook_new",
            "workspace_id": workspace_id,
            "url": url,
            "secret": secret,
            "events": events,
        }
        return "hook_new"

    def remove_webhook(self, webhook_id, *, actor):
        self.calls.append(("remove_webhook", webhook_id, actor))
        return self.hooks.pop(webhook_id, None) is not None


class _Settings:
    def __init__(self, data, calls):
        self.data = data
        self.calls = calls

    def effective(self, workspace_id, agent="", task_ref=""):
        self.calls.append(("effective", workspace_id, agent, task_ref))
        return HandoffSettings(launcher=LauncherConfig(secret="effective-secret"))

    def set(self, row, *, actor, is_owner):
        self.calls.append(("set", row, actor, is_owner))
        if not is_owner and (
            row.scope != "task"
            or any(value is not None for value in (row.threshold, row.context_window, row.launcher))
        ):
            raise SettingsError("only owners may change this setting")
        if row.threshold is not None and not 0.5 <= row.threshold <= 0.95:
            raise SettingsError("threshold must be between 0.50 and 0.95")
        return replace(row, updated_by=actor, updated_at="2026-10-30T00:00:00Z")

    def clear(self, workspace_id, scope, ref, *, actor, is_owner):
        self.calls.append(("clear", workspace_id, scope, ref, actor, is_owner))
        rows = self.data.rows
        selected = [
            row
            for row in rows
            if (row.workspace_id, row.scope, row.ref) == (workspace_id, scope, ref)
        ]
        self.data.rows = [row for row in rows if row not in selected]
        return bool(selected)


class _Service:
    def __init__(self, data, calls):
        self.data = data
        self.calls = calls

    def run(self, principal, request):
        self.calls.append(("run", principal, request))
        record = Handoff(
            "hof_new",
            principal.workspace_id,
            request.idempotency_key,
            request.meta.thread_id,
            "stored",
            connection_id=principal.connection_id,
        )
        return HandoffResult("handoff", record, launch=True, prompt=TRANSCRIPT)

    def register_successor(self, principal, handoff_id, ref):
        self.calls.append(("successor", principal, handoff_id, ref))
        return replace(self.data.records[handoff_id], successor=ref, state="done")

    def report_launch_failure(self, principal, handoff_id, error):
        self.calls.append(("launch_failure", principal, handoff_id, error))
        return replace(self.data.records[handoff_id], state="launch_failed")

    def relaunch(self, principal, handoff_id):
        self.calls.append(("relaunch", principal, handoff_id))
        return HandoffResult("handoff", self.data.records[handoff_id], launch=True)


class _Recall:
    def __init__(self, local, other, calls):
        self.calls = calls
        self.records = {
            (workspace, name): StoredThread(name, workspace, "source", 3, 1, "sha")
            for workspace, names in (
                (local, ("thr_local", "thr_same")),
                (other, ("thr_other", "thr_same")),
            )
            for name in names
        }

    def thread(self, principal, thread_id):
        self.calls.append(("thread", principal.workspace_id, thread_id))
        try:
            return self.records[(principal.workspace_id, thread_id)]
        except KeyError:
            raise ThreadNotFound() from None

    def get(self, principal, thread_id, start=0, end=None):
        self.calls.append(("messages", principal.workspace_id, thread_id, start, end))
        return [ThreadMessage(start, "user", "message", TRANSCRIPT, TRANSCRIPT)]

    def chain(self, principal, thread_id):
        self.calls.append(("chain", principal.workspace_id, thread_id))
        return [self.records[(principal.workspace_id, thread_id)]]

    def recall(self, principal, thread_id, query, *, limit=8, follow_chain=True):
        self.calls.append(("recall", principal.workspace_id, thread_id, query, limit, follow_chain))
        return [RecallHit(thread_id, 0, 0, 2, 1.5, TRANSCRIPT)]


class _Harness:
    def __init__(self, root):
        self.db = ControlDB(str(root / "control.sqlite3"))
        owner = self.db.create_user("owner@example.com")
        member = self.db.create_user("member@example.com")
        self.workspace = self.db.create_workspace("home", owner)
        self.other = self.db.create_workspace("other", owner)
        self.db.add_member(self.workspace, member, "member", actor=owner)
        self.gateway = Gateway(self.db, _Supervisor())
        self.ctx = SimpleNamespace(gateway=self.gateway)
        self.reset()
        api.register(self.ctx)
        self.tokens = {}
        for name, user, workspace, scopes, wings in (
            (
                "owner",
                owner,
                self.workspace,
                ["handoff:write", "threads:read", "memory:read"],
                ["threads"],
            ),
            (
                "member",
                member,
                self.workspace,
                ["handoff:write", "threads:read", "memory:read"],
                ["threads"],
            ),
            ("wrong", member, self.workspace, ["memory:read"], ["threads"]),
            (
                "no_wing",
                member,
                self.workspace,
                ["handoff:write", "threads:read", "memory:read"],
                ["shared"],
            ),
            (
                "other",
                owner,
                self.other,
                ["handoff:write", "threads:read", "memory:read"],
                ["threads"],
            ),
            ("owner_empty", owner, self.workspace, [], []),
        ):
            self.tokens[name] = self.db.create_token(
                workspace_ref=workspace,
                user_ref=user,
                name=name,
                client="test",
                policy=Policy.build(scopes=scopes, read_wings=wings, trust="standard"),
                actor=owner,
            )[1]
        self.server = self.gateway.build_server("127.0.0.1", 0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        self.thread.start()

    def reset(self):
        self.calls = []
        self.ctx.handoff_db = _Data(self.workspace, self.other, self.calls)
        self.ctx.settings = _Settings(self.ctx.handoff_db, self.calls)
        self.ctx.service = _Service(self.ctx.handoff_db, self.calls)
        self.ctx.recall = _Recall(self.workspace, self.other, self.calls)

    def request(self, method, path, body=None, token="owner", headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        outgoing = {"Content-Type": "application/json"}
        if token is not None:
            outgoing["Authorization"] = "Bearer " + self.tokens.get(token, token)
        outgoing.update(headers or {})
        raw = (
            body
            if isinstance(body, bytes)
            else (
                json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
            )
        )
        try:
            connection.request(method, path, raw, outgoing)
            response = connection.getresponse()
            return response.status, json.loads(response.read()), dict(response.getheaders())
        finally:
            connection.close()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.db.close()

    def framed(self, headers, body=b"{}", *, half_close=False):
        """Send headers exactly as specified, including duplicate or missing lengths."""
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            connection.putrequest("POST", "/api/handoff")
            connection.putheader("Authorization", "Bearer " + self.tokens["owner"])
            for name, value in headers:
                connection.putheader(name, value)
            connection.endheaders(body)
            if half_close:
                connection.sock.shutdown(socket.SHUT_WR)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    hub = _Harness(tmp_path_factory.mktemp("handoff_api"))
    try:
        yield hub
    finally:
        hub.close()


@pytest.fixture
def hub(harness):
    harness.reset()
    return harness


@pytest.mark.parametrize("route", ROUTES, ids=[row[0] for row in ROUTES])
@pytest.mark.parametrize("token", [None, "bad", "cmmn_invalid"])
def test_every_route_authenticates_before_any_service_access(hub, route, token):
    _, method, path, _, _, _ = route
    status, body, headers = hub.request(method, path, b"broken", token=token)
    assert (status, body) == (401, {"error": "unauthorized"})
    assert headers["WWW-Authenticate"] == 'Bearer realm="commonplace"'
    assert not hub.calls


@pytest.mark.parametrize("route", ROUTES, ids=[row[0] for row in ROUTES])
def test_every_route_happy_path(hub, route):
    _, method, path, body, access, expected = route
    status, result, _ = hub.request(
        method, path, body, token="owner" if access == "owner" else "member"
    )
    assert status == expected, result
    assert "connection_id" not in json.dumps(result)


@pytest.mark.parametrize("route", ROUTES, ids=[row[0] for row in ROUTES])
def test_every_route_scope_or_role_boundary(hub, route):
    _, method, path, body, access, expected = route
    status, result, _ = hub.request(method, path, body, token="wrong")
    if access == "any":
        assert status == expected
    else:
        assert (status, result) == (404, NOT_FOUND)
        assert not hub.calls


@pytest.mark.parametrize("route", ROUTES, ids=[row[0] for row in ROUTES])
def test_every_route_uses_callers_workspace(hub, route):
    name, method, path, body, _, expected = route
    status, result, _ = hub.request(method, path, body, token="other")
    if name in {
        "handoff",
        "successor",
        "launch_failure",
        "relaunch",
        "thread",
        "messages",
        "recall",
        "chain",
        "webhook_remove",
    }:
        assert (status, result) == (404, NOT_FOUND)
        assert not any(
            call[0]
            in {
                "successor",
                "launch_failure",
                "relaunch",
                "remove_webhook",
                "messages",
                "recall",
                "chain",
            }
            for call in hub.calls
        )
    else:
        assert status == expected, result
        assert hub.workspace not in json.dumps(result)
        assert "hof_local" not in json.dumps(result)
        assert "hook_local" not in json.dumps(result)
        for call in hub.calls:
            if call[0] in {
                "list",
                "list_settings",
                "effective",
                "webhooks",
                "add_webhook",
                "clear",
            }:
                assert call[1] == hub.other
            if call[0] == "set":
                assert call[1].workspace_id == hub.other
            if call[0] == "run":
                assert call[1].workspace_id == hub.other


@pytest.mark.parametrize(
    "route",
    [row for row in ROUTES if row[3] is not None],
    ids=[row[0] for row in ROUTES if row[3] is not None],
)
@pytest.mark.parametrize("body", [b"broken", b"\xff", b"null", b"[]", b"1", b'"text"', b"{}"])
def test_body_routes_reject_invalid_json_or_missing_fields(hub, route, body):
    _, method, path, _, _, _ = route
    status, result, _ = hub.request(method, path, body)
    assert status == 400, result
    assert not any(
        call[0] in {"run", "successor", "launch_failure", "recall", "set", "add_webhook"}
        for call in hub.calls
    )


def test_handoff_request_maps_every_field_without_changing_text(hub):
    meta = {
        "thread_id": "source",
        "agent": "worker",
        "client": "codex",
        "model": "model",
        "task_ref": "#142",
        "branch": "feature",
        "pr_url": "http://127.0.0.1/pr/1",
        "cwd": " /local/dir ",
        "parent_handoff_id": "hof_local",
    }
    data = {
        "meta": meta,
        "transcript": TRANSCRIPT,
        "transcript_format": "codex",
        "usage": {"used_tokens": 80, "window_tokens": 100, "model": "model"},
        "note": "  my note\r\n",
        "idempotency_key": " key ",
    }
    status, result, _ = hub.request(
        "POST", "/api/handoff", data, headers={"Content-Type": "Application/JSON; charset=utf-8"}
    )
    assert status == 200
    request = next(call[2] for call in hub.calls if call[0] == "run")
    assert request.meta == ThreadMeta(**meta)
    assert request.transcript == TRANSCRIPT
    assert request.note == data["note"]
    assert request.idempotency_key == " key "
    assert request.usage == ContextUsage(80, 100, "model")
    assert request.transcript_format == "codex"
    assert result["launch"] is True and result["prompt"] == TRANSCRIPT
    assert "connection_id" not in result["handoff"]


@pytest.mark.parametrize(
    "patch",
    [
        {"meta": None},
        {"meta": []},
        {"meta": {}},
        {"meta": {"thread_id": " "}},
        {"meta": {"thread_id": 123}},
        {"meta": {"thread_id": "source", "agent": {}}},
        {"meta": {"thread_id": "source", "workspace_id": "forged"}},
        {"transcript": None},
        {"transcript": []},
        {"note": 1},
        {"idempotency_key": False},
        {"transcript_format": "unknown"},
        {"usage": []},
        {"usage": {}},
        {"usage": {"used_tokens": True, "window_tokens": 100}},
        {"usage": {"used_tokens": -1, "window_tokens": 100}},
        {"usage": {"used_tokens": 1, "window_tokens": 0}},
        {"usage": {"used_tokens": 1, "window_tokens": "100"}},
        {"usage": {"used_tokens": 1, "window_tokens": 100, "model": []}},
        {"workspace_id": "forged"},
    ],
)
def test_handoff_rejects_invalid_nested_fields(hub, patch):
    status, _, _ = hub.request("POST", "/api/handoff", {**HANDOFF_BODY, **patch})
    assert status == 400
    assert not hub.calls


@pytest.mark.parametrize("parent", ["hof_other", "missing"])
def test_handoff_parent_must_be_in_workspace(hub, parent):
    data = {**HANDOFF_BODY, "meta": {"thread_id": "source", "parent_handoff_id": parent}}
    assert hub.request("POST", "/api/handoff", data)[:2] == (404, NOT_FOUND)
    assert not any(call[0] == "run" for call in hub.calls)


def test_disabled_or_failed_handoff_result_is_returned_unchanged(hub, monkeypatch):
    monkeypatch.setattr(
        hub.ctx.service, "run", lambda *args: HandoffResult("compact", reason="off")
    )
    assert hub.request("POST", "/api/handoff", HANDOFF_BODY)[:2] == (
        200,
        {"action": "compact", "handoff": None, "launch": False, "prompt": "", "reason": "off"},
    )


@pytest.mark.parametrize("path", ["/api/handoffs/hof_other", "/api/handoffs/missing"])
def test_missing_and_foreign_handoff_have_identical_responses(hub, path):
    assert hub.request("GET", path)[:2] == (404, NOT_FOUND)


@pytest.mark.parametrize(
    "route",
    [row for row in ROUTES if row[4] == "read"],
    ids=[row[0] for row in ROUTES if row[4] == "read"],
)
def test_read_routes_hide_unreadable_memory(hub, route):
    _, method, path, body, _, _ = route
    assert hub.request(method, path, body, token="no_wing")[:2] == (404, NOT_FOUND)


def test_successor_fields_and_actor(hub):
    status, result, _ = hub.request(
        "POST",
        "/api/handoffs/hof_local/successor",
        {"agent_ref": " agent:new ", "url": "http://127.0.0.1/new", "thread_id": "new"},
    )
    assert status == 200
    assert result["state"] == "done"
    assert result["successor"] == {
        "agent_ref": " agent:new ",
        "url": "http://127.0.0.1/new",
        "thread_id": "new",
    }
    assert next(call[1] for call in hub.calls if call[0] == "successor").actor == "conn:owner"


@pytest.mark.parametrize(
    "body",
    [
        {"agent_ref": None},
        {"agent_ref": " "},
        {"agent_ref": "x", "url": []},
        {"agent_ref": "x", "thread_id": 1},
    ],
)
def test_invalid_successor_fields(hub, body):
    assert hub.request("POST", "/api/handoffs/hof_local/successor", body)[0] == 400


@pytest.mark.parametrize("error", ["", "  launcher failed\r\ncafé 🦉  "])
def test_launch_failure_passes_exact_error_and_sanitizes_handoff(hub, error):
    status, result, _ = hub.request(
        "POST", "/api/handoffs/hof_local/launch-failed", {"error": error}, token="member"
    )
    assert status == 200
    assert result["id"] == "hof_local" and result["state"] == "launch_failed"
    assert "connection_id" not in result and "secret" not in json.dumps(result)
    _, principal, handoff_id, supplied_error = hub.calls[-1]
    assert principal.actor == "conn:member" and principal.workspace_id == hub.workspace
    assert handoff_id == "hof_local" and supplied_error == error


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"error": None},
        {"error": 1},
        {"error": False},
        {"error": []},
        {"error": {}},
        {"error": "failure", "workspace_id": "forged"},
    ],
)
def test_launch_failure_rejects_missing_or_invalid_error(hub, body):
    assert hub.request("POST", "/api/handoffs/hof_local/launch-failed", body)[0] == 400
    assert not any(call[0] == "launch_failure" for call in hub.calls)


@pytest.mark.parametrize("glyph", ["x", "é", "🦉"])
def test_launch_failure_limit_counts_entire_utf8_body(hub, glyph):
    overhead = len(b'{"error":""}')
    count, padding = divmod(api.MAX_FAILURE_BYTES - overhead, len(glyph.encode("utf-8")))
    error = glyph * count + "x" * padding
    raw = json.dumps({"error": error}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    assert len(raw) == 1024
    assert hub.request("POST", "/api/handoffs/hof_local/launch-failed", raw)[0] == 200
    assert hub.calls[-1][3] == error
    hub.calls.clear()
    oversized = raw[:-2] + b'x"}'
    assert len(oversized) == 1025
    if glyph != "x":
        assert len(oversized.decode("utf-8")) < 1024
    assert hub.request("POST", "/api/handoffs/hof_local/launch-failed", oversized)[0] == 413
    assert not any(call[0] == "launch_failure" for call in hub.calls)


@pytest.mark.parametrize("handoff_id", ["missing", "hof_other"])
def test_launch_failure_hides_missing_and_foreign_handoffs(hub, handoff_id):
    assert hub.request("POST", f"/api/handoffs/{handoff_id}/launch-failed", {"error": "failed"})[
        :2
    ] == (404, NOT_FOUND)
    assert not any(call[0] == "launch_failure" for call in hub.calls)


def test_launch_failure_does_not_allow_owner_to_bypass_write_scope(hub):
    assert hub.request(
        "POST", "/api/handoffs/hof_local/launch-failed", {"error": "failed"}, token="owner_empty"
    )[:2] == (404, NOT_FOUND)
    assert not hub.calls


def test_launch_failure_service_visibility_error_stays_not_found(hub, monkeypatch):
    def missing(*args):
        raise ThreadNotFound("private")

    monkeypatch.setattr(hub.ctx.service, "report_launch_failure", missing)
    assert hub.request("POST", "/api/handoffs/hof_local/launch-failed", {"error": "failed"})[
        :2
    ] == (404, NOT_FOUND)


def test_launch_failure_cannot_return_foreign_workspace(hub, monkeypatch):
    monkeypatch.setattr(
        hub.ctx.service,
        "report_launch_failure",
        lambda *args: hub.ctx.handoff_db.records["hof_other"],
    )
    assert hub.request("POST", "/api/handoffs/hof_local/launch-failed", {"error": "failed"})[
        :2
    ] == (404, NOT_FOUND)


def test_thread_range_and_verbatim_messages(hub):
    status, result, _ = hub.request("GET", "/api/threads/thr_local/messages?start=1&end=2")
    assert status == 200
    assert result[0]["text"] == result[0]["raw"] == TRANSCRIPT
    assert hub.calls[-1] == ("messages", hub.workspace, "thr_local", 1, 2)
    assert hub.request("GET", "/api/threads/thr_local/messages")[0] == 200
    assert hub.calls[-1][-2:] == (0, None)


@pytest.mark.parametrize(
    "query",
    [
        "start=-1",
        "start=abc",
        "start=",
        "end=1.5",
        "start=2&end=1",
        "start=1&start=2",
        "end=-1",
        "end=",
        "start=+1",
        "start=%D9%A1",
    ],
)
def test_invalid_message_ranges(hub, query):
    assert hub.request("GET", "/api/threads/thr_local/messages?" + query)[0] == 400
    assert not any(call[0] == "messages" for call in hub.calls)


def test_recall_parameters_and_defaults(hub):
    status, result, _ = hub.request(
        "POST",
        "/api/threads/thr_local/recall",
        {"query": "  café  ", "limit": 2, "follow_chain": False},
    )
    assert status == 200 and result[0]["text"] == TRANSCRIPT
    assert hub.calls[-1] == ("recall", hub.workspace, "thr_local", "  café  ", 2, False)
    hub.request("POST", "/api/threads/thr_local/recall", {"query": "fact"})
    assert hub.calls[-1][-2:] == (8, True)


@pytest.mark.parametrize(
    "body",
    [
        {"query": " "},
        {"query": []},
        {"query": "x", "limit": True},
        {"query": "x", "limit": 0},
        {"query": "x", "limit": "8"},
        {"query": "x", "follow_chain": 1},
    ],
)
def test_invalid_recall_fields(hub, body):
    assert hub.request("POST", "/api/threads/thr_local/recall", body)[0] == 400


@pytest.mark.parametrize(
    "suffix,method,body",
    [
        ("", "GET", None),
        ("/messages", "GET", None),
        ("/chain", "GET", None),
        ("/recall", "POST", {"query": "x"}),
    ],
)
def test_thread_not_found_from_any_recall_method(hub, monkeypatch, suffix, method, body):
    def missing(*args, **kwargs):
        raise ThreadNotFound("private detail")

    monkeypatch.setattr(
        hub.ctx.recall,
        {"": "thread", "/messages": "get", "/chain": "chain", "/recall": "recall"}[suffix],
        missing,
    )
    assert hub.request(method, "/api/threads/thr_local" + suffix, body)[:2] == (404, NOT_FOUND)


def test_identical_thread_ids_are_resolved_per_workspace(hub):
    for token, workspace in (("owner", hub.workspace), ("other", hub.other)):
        status, result, _ = hub.request("GET", "/api/threads/thr_same", token=token)
        assert status == 200 and result["workspace_id"] == workspace


def test_settings_rows_and_effective_are_public(hub):
    status, result, _ = hub.request(
        "GET", "/api/settings/handoff?agent=worker&task=%23142", token="wrong"
    )
    assert status == 200
    assert len(result["rows"]) == 1
    assert result["rows"][0]["workspace_id"] == hub.workspace
    assert result["effective"]["enabled"] is False
    assert "secret" not in json.dumps(result)
    assert hub.calls[-1] == ("effective", hub.workspace, "worker", "#142")


def test_settings_maps_fields_and_redacts_written_launcher(hub):
    body = {
        "scope": "agent",
        "ref": "worker",
        "enabled": True,
        "threshold": 0.85,
        "context_window": 1000,
        "launcher": {
            "kind": "webhook",
            "argv": [],
            "url": "http://127.0.0.1/launch",
            "secret": "hidden",
        },
    }
    status, result, _ = hub.request("PUT", "/api/settings/handoff", body)
    assert status == 200 and "secret" not in json.dumps(result)
    _, row, actor, is_owner = hub.calls[-1]
    assert row.workspace_id == hub.workspace
    assert row.enabled is True and row.threshold == 0.85 and row.context_window == 1000
    assert row.launcher == LauncherConfig("webhook", (), "http://127.0.0.1/launch", "hidden")
    assert actor == "conn:owner" and is_owner is True


@pytest.mark.parametrize(
    "patch",
    [
        {"scope": "no"},
        {"ref": None},
        {"enabled": 1},
        {"threshold": "0.8"},
        {"threshold": True},
        {"context_window": False},
        {"context_window": 0},
        {"launcher": []},
        {"launcher": {"kind": "unknown"}},
        {"launcher": {"argv": "command"}},
        {"launcher": {"argv": [1]}},
        {"launcher": {"secret": None}},
        {"workspace_id": "forged"},
    ],
)
def test_invalid_settings_fields(hub, patch):
    body = {"scope": "task", "ref": "#142", **patch}
    assert hub.request("PUT", "/api/settings/handoff", body)[0] == 400
    assert not hub.calls


@pytest.mark.parametrize(
    "body",
    [
        {"scope": "workspace", "enabled": True},
        {"scope": "agent", "ref": "worker", "enabled": True},
        {"scope": "task", "ref": "#142", "threshold": 0.8},
    ],
)
def test_members_setting_restrictions_are_explicit_403(hub, body):
    status, result, _ = hub.request("PUT", "/api/settings/handoff", body, token="member")
    assert status == 403 and "only owners" in result["error"]


def test_settings_validation_and_delete_errors(hub):
    assert (
        hub.request("PUT", "/api/settings/handoff", {"scope": "workspace", "threshold": 0.1})[0]
        == 400
    )
    assert hub.request("DELETE", "/api/settings/handoff?scope=invalid")[0] == 400
    assert hub.request("DELETE", "/api/settings/handoff?scope=task&ref=missing")[:2] == (
        404,
        NOT_FOUND,
    )


def test_webhook_supplied_secret_returned_only_on_creation(hub):
    status, result, _ = hub.request(
        "POST",
        "/api/webhooks",
        {"url": "http://127.0.0.1/wake", "secret": "my-secret", "events": [EVENTS[0]]},
    )
    assert (status, result) == (201, {"id": "hook_new", "secret": "my-secret"})
    assert hub.calls[-1] == (
        "add_webhook",
        hub.workspace,
        "http://127.0.0.1/wake",
        "my-secret",
        (EVENTS[0],),
        "conn:owner",
    )
    status, result, _ = hub.request("GET", "/api/webhooks")
    assert status == 200 and "secret" not in json.dumps(result)
    status, result, _ = hub.request("DELETE", "/api/webhooks/hook_new")
    assert status == 200 and "secret" not in json.dumps(result)


def test_webhook_secret_generation_and_default_events(hub, monkeypatch):
    sizes = []
    monkeypatch.setattr(
        api.secrets, "token_urlsafe", lambda size: sizes.append(size) or "generated"
    )
    status, result, _ = hub.request("POST", "/api/webhooks", {"url": "http://127.0.0.1/wake"})
    assert status == 201 and result["secret"] == "generated"
    assert sizes == [32]
    assert hub.calls[-1][4] == EVENTS


@pytest.mark.parametrize(
    "body",
    [
        {"url": "file:///tmp/wake"},
        {"url": "http://"},
        {"url": 1},
        {"url": "http://127.0.0.1:bad"},
        {"url": "http://127.0.0.1:0"},
        {"url": "http://127.0.0.1", "secret": ""},
        {"url": "http://127.0.0.1", "secret": None},
        {"url": "http://127.0.0.1", "events": "handoff.started"},
        {"url": "http://127.0.0.1", "events": []},
        {"url": "http://127.0.0.1", "events": ["unknown"]},
        {"url": "http://127.0.0.1", "events": [{}]},
    ],
)
def test_invalid_webhook_fields(hub, body):
    assert hub.request("POST", "/api/webhooks", body)[0] == 400
    assert not hub.calls


@pytest.mark.parametrize(
    "route",
    [row for row in ROUTES if row[3] is not None],
    ids=[row[0] for row in ROUTES if row[3] is not None],
)
def test_body_limits_and_media_types(hub, route):
    name, method, path, body, _, _ = route
    assert hub.request(method, path, body, headers={"Content-Type": "text/plain"})[0] == 415
    limit = {"create": api.MAX_HANDOFF_BYTES, "launch_failure": api.MAX_FAILURE_BYTES}.get(
        name, api.MAX_BODY_BYTES
    )
    assert hub.request(method, path, b"{}", headers={"Content-Length": str(limit + 1)})[0] == 413
    assert not any(
        call[0] in {"run", "successor", "launch_failure", "recall", "set", "add_webhook"}
        for call in hub.calls
    )


@pytest.mark.parametrize("length", ["-1", "bogus", "1.0", "+2", ""])
def test_invalid_content_length(hub, length):
    assert hub.request("POST", "/api/handoff", b"{}", headers={"Content-Length": length})[0] == 400
    assert not hub.calls


@pytest.mark.parametrize(
    "raw",
    [
        b'{"meta": {"thread_id": "source"}, "transcript": NaN}',
        b'{"threshold": Infinity}',
        b"[" * 1100 + b"]" * 1100,
    ],
)
def test_nonstandard_or_deep_json_is_a_parse_error(hub, raw):
    assert hub.request("POST", "/api/handoff", raw)[0] == 400


def test_handoff_accepts_larger_than_default_body_limit(hub):
    transcript = "é" * (api.MAX_BODY_BYTES // 2 + 1)
    status, _, _ = hub.request("POST", "/api/handoff", {**HANDOFF_BODY, "transcript": transcript})
    assert status == 200
    assert hub.calls[-1][2].transcript == transcript


def test_exact_handoff_body_limit(hub, monkeypatch):
    body = json.dumps(HANDOFF_BODY, ensure_ascii=False).encode("utf-8")
    monkeypatch.setattr(api, "MAX_HANDOFF_BYTES", len(body))
    assert hub.request("POST", "/api/handoff", body)[0] == 200
    monkeypatch.setattr(api, "MAX_HANDOFF_BYTES", len(body) - 1)
    assert hub.request("POST", "/api/handoff", body)[0] == 413


@pytest.mark.parametrize(
    "method,path,body,service_name,method_name,error,expected",
    [
        (
            "POST",
            "/api/handoffs/hof_local/successor",
            {"agent_ref": "new"},
            "service",
            "register_successor",
            HandoffDBError("private"),
            409,
        ),
        (
            "POST",
            "/api/handoffs/hof_local/relaunch",
            None,
            "service",
            "relaunch",
            HandoffDBError("private"),
            409,
        ),
        (
            "POST",
            "/api/handoffs/hof_local/launch-failed",
            {"error": "launcher exited"},
            "service",
            "report_launch_failure",
            HandoffDBError("private"),
            409,
        ),
        ("POST", "/api/handoff", HANDOFF_BODY, "service", "run", ThreadConflict("private"), 409),
        ("POST", "/api/handoff", HANDOFF_BODY, "service", "run", ValueError("private"), 400),
        ("POST", "/api/handoff", HANDOFF_BODY, "service", "run", RuntimeError("private"), 500),
    ],
)
def test_service_errors_are_json_and_do_not_leak_details(
    hub, monkeypatch, method, path, body, service_name, method_name, error, expected
):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(getattr(hub.ctx, service_name), method_name, fail)
    status, result, _ = hub.request(method, path, body)
    assert status == expected and "private" not in result["error"]


@pytest.mark.parametrize(
    "path,method",
    [
        ("/api/handoffs/", "GET"),
        ("/api/handoffs/hof_local/extra", "GET"),
        ("/api/handoffs/hof_local/unknown", "POST"),
        ("/api/threads/thr_local/unknown", "GET"),
        ("/api/threads/thr_local/messages/extra", "GET"),
        ("/api/threads/thr_local", "POST"),
        ("/api/webhooks/hook_local/extra", "DELETE"),
        ("/api/handoffs/hof_local%2Fextra", "GET"),
    ],
)
def test_prefix_routes_do_not_accept_unknown_suffixes(hub, path, method):
    assert hub.request(method, path)[:2] == (404, NOT_FOUND)
    assert not hub.calls


def test_output_defense_rejects_foreign_records_and_truncates_chain(hub, monkeypatch):
    other = hub.ctx.handoff_db.records["hof_other"]
    monkeypatch.setattr(hub.ctx.service, "run", lambda *args: HandoffResult("handoff", other))
    assert hub.request("POST", "/api/handoff", HANDOFF_BODY)[:2] == (404, NOT_FOUND)
    foreign_thread = hub.ctx.recall.records[(hub.other, "thr_other")]
    monkeypatch.setattr(hub.ctx.recall, "thread", lambda *args: foreign_thread)
    assert hub.request("GET", "/api/threads/thr_local")[:2] == (404, NOT_FOUND)
    local_thread = hub.ctx.recall.records[(hub.workspace, "thr_local")]
    monkeypatch.setattr(hub.ctx.recall, "thread", lambda *args: local_thread)
    monkeypatch.setattr(
        hub.ctx.recall, "chain", lambda *args: [local_thread, foreign_thread, local_thread]
    )
    status, result, _ = hub.request("GET", "/api/threads/thr_local/chain")
    assert status == 200 and [row["id"] for row in result] == ["thr_local"]


@pytest.mark.parametrize(
    "route",
    [row for row in ROUTES if row[4] == "owner"],
    ids=[row[0] for row in ROUTES if row[4] == "owner"],
)
def test_owner_routes_use_workspace_role_without_requiring_extra_scopes(hub, route):
    _, method, path, body, _, expected = route
    assert hub.request(method, path, body, token="owner_empty")[0] == expected


@pytest.mark.parametrize(
    "headers,expected",
    [
        ([("Content-Length", "2")], 415),
        ([("Content-Type", "application/json")], 400),
        (
            [
                ("Content-Type", "application/json"),
                ("Content-Length", "2"),
                ("Content-Length", "2"),
            ],
            400,
        ),
        (
            [
                ("Content-Type", "application/json"),
                ("Content-Length", "2"),
                ("Transfer-Encoding", "chunked"),
            ],
            400,
        ),
        ([("Content-Type", "application/json"), ("Content-Length", "9" * 100)], 413),
    ],
)
def test_ambiguous_or_missing_framing_is_rejected_before_reading(hub, headers, expected):
    assert hub.framed(headers)[0] == expected
    assert not hub.calls


def test_short_request_body_is_parse_error(hub):
    assert (
        hub.framed(
            [("Content-Type", "application/json"), ("Content-Length", "10")], half_close=True
        )[0]
        == 400
    )
    assert not hub.calls


def test_content_length_allows_leading_zeros_and_outer_whitespace(hub):
    raw = json.dumps(HANDOFF_BODY).encode()
    assert (
        hub.framed(
            [
                ("Content-Type", "application/json"),
                ("Content-Length", "0" * 30 + str(len(raw)) + " "),
            ],
            raw,
        )[0]
        == 200
    )


def test_bodyless_routes_close_connection_when_body_is_unread(hub):
    status, _, headers = hub.request("GET", "/api/webhooks", b"ignored")
    assert status == 200 and headers["Connection"] == "close"


@pytest.mark.parametrize(
    "body,headers,expected",
    [
        (None, {}, 200),
        (b"", {}, 200),
        (b"{}", {}, 200),
        (b"ignored", {}, 400),
        (b"[]", {}, 400),
        (b'{"unused":true}', {}, 400),
        (b"{}", {"Content-Type": "text/plain"}, 415),
        (b"{}", {"Content-Length": str(api.MAX_BODY_BYTES + 1)}, 413),
        (b"{}", {"Content-Length": "-1"}, 400),
        (b"{}", {"Transfer-Encoding": "chunked"}, 400),
    ],
)
def test_relaunch_allows_no_body_and_validates_any_supplied_body(hub, body, headers, expected):
    status, _, response_headers = hub.request(
        "POST", "/api/handoffs/hof_local/relaunch", body, headers=headers
    )
    assert status == expected
    if expected >= 400:
        assert response_headers["Connection"] == "close"
        assert not any(call[0] == "relaunch" for call in hub.calls)


def test_json_routes_keep_connection_usable_after_consuming_body(hub):
    connection = http.client.HTTPConnection("127.0.0.1", hub.port, timeout=3)
    try:
        headers = {
            "Authorization": "Bearer " + hub.tokens["owner"],
            "Content-Type": "application/json",
        }
        connection.request("POST", "/api/handoff", json.dumps(HANDOFF_BODY).encode(), headers)
        response = connection.getresponse()
        assert response.status == 200
        response.read()
        first_socket = connection.sock
        connection.request("GET", "/api/settings/handoff", headers=headers)
        response = connection.getresponse()
        assert response.status == 200
        response.read()
        assert first_socket is connection.sock
    finally:
        connection.close()


def test_exact_small_endpoint_body_limit(hub):
    prefix, suffix = b'{"query":"', b'"}'
    body = prefix + b"x" * (api.MAX_BODY_BYTES - len(prefix) - len(suffix)) + suffix
    assert hub.request("POST", "/api/threads/thr_local/recall", body)[0] == 200
    assert len(hub.calls[-1][3]) == api.MAX_BODY_BYTES - len(prefix) - len(suffix)
    assert hub.request("POST", "/api/threads/thr_local/recall", body + b" ")[0] == 413


def test_extreme_threshold_is_validation_error(hub):
    assert (
        hub.request("PUT", "/api/settings/handoff", {"scope": "workspace", "threshold": 10**1000})[
            0
        ]
        == 400
    )
    assert not hub.calls


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("POST", "/api/handoff", {**HANDOFF_BODY, "transcript": "\ud800"}),
        ("POST", "/api/threads/thr_local/recall", {"query": "\udfff"}),
        (
            "PUT",
            "/api/settings/handoff",
            {"scope": "workspace", "launcher": {"kind": "command", "argv": ["\ud800"]}},
        ),
    ],
)
def test_unpaired_unicode_surrogates_are_rejected(hub, method, path, body):
    status, _, _ = hub.request(method, path, json.dumps(body, ensure_ascii=True).encode())
    assert status == 400


def test_default_handoff_fields_and_empty_verbatim_text(hub):
    status, _, _ = hub.request(
        "POST", "/api/handoff", {"meta": {"thread_id": "source"}, "transcript": ""}
    )
    assert status == 200
    request = hub.calls[-1][2]
    assert request.meta == ThreadMeta("source")
    assert request.transcript == "" and request.note == "" and request.idempotency_key == ""
    assert request.transcript_format == "auto" and request.usage is None


@pytest.mark.parametrize(
    "path",
    [
        "/api/handoffs?task=a&task=b",
        "/api/settings/handoff?agent=a&agent=b",
        "/api/settings/handoff?task=a&task=b",
    ],
)
def test_duplicate_text_query_arguments_are_invalid(hub, path):
    assert hub.request("GET", path)[0] == 400


def test_list_responses_drop_rows_from_other_workspaces(hub, monkeypatch):
    monkeypatch.setattr(
        hub.ctx.handoff_db,
        "list",
        lambda *args, **kwargs: list(hub.ctx.handoff_db.records.values()),
    )
    status, result, _ = hub.request("GET", "/api/handoffs")
    assert status == 200 and [row["id"] for row in result] == ["hof_local"]
    monkeypatch.setattr(hub.ctx.handoff_db, "list_settings", lambda *args: hub.ctx.handoff_db.rows)
    status, result, _ = hub.request("GET", "/api/settings/handoff")
    assert status == 200 and len(result["rows"]) == 1
    assert result["rows"][0]["workspace_id"] == hub.workspace


@pytest.mark.parametrize("path", ["/api/webhooks/missing", "/api/webhooks/hook_other"])
def test_delete_missing_and_foreign_webhooks_are_identical(hub, path):
    assert hub.request("DELETE", path)[:2] == (404, NOT_FOUND)
    assert not any(call[0] == "remove_webhook" for call in hub.calls)


def test_webhook_disappearing_before_delete_stays_not_found(hub, monkeypatch):
    monkeypatch.setattr(hub.ctx.handoff_db, "remove_webhook", lambda *args, **kwargs: False)
    assert hub.request("DELETE", "/api/webhooks/hook_local")[:2] == (404, NOT_FOUND)


def test_successor_and_setting_responses_cannot_return_foreign_workspace(hub, monkeypatch):
    monkeypatch.setattr(
        hub.ctx.service, "register_successor", lambda *args: hub.ctx.handoff_db.records["hof_other"]
    )
    assert hub.request("POST", "/api/handoffs/hof_local/successor", {"agent_ref": "new"})[:2] == (
        404,
        NOT_FOUND,
    )
    monkeypatch.setattr(
        hub.ctx.settings, "set", lambda *args, **kwargs: SettingRow(hub.other, "task", "#142")
    )
    assert hub.request("PUT", "/api/settings/handoff", {"scope": "task", "ref": "#142"})[:2] == (
        404,
        NOT_FOUND,
    )
