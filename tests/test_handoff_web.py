"""Loopback gateway tests with no Memory worker or external service."""

import hashlib
import http.client
import json
import threading
import time
from dataclasses import replace
from html.parser import HTMLParser
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

from commonplace.control_db import ControlDB
from commonplace.gateway import Gateway
from commonplace.handoff import web
from commonplace.handoff.models import (
    Handoff,
    HandoffSettings,
    LauncherConfig,
    SettingRow,
    StoredThread,
    SuccessorRef,
    ThreadMessage,
)
from commonplace.handoff.recall import ThreadNotFound
from commonplace.handoff.settings import SettingsError
from commonplace.policy import Policy


class Forms(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.forms = []
        self.current = None
        self.texts = []
        self.in_pre = False
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.current = {"action": attrs["action"], "fields": [], "buttons": []}
            self.forms.append(self.current)
        if self.current is not None and tag == "input" and "disabled" not in attrs:
            if attrs.get("type") != "checkbox" or "checked" in attrs:
                self.current["fields"].append((attrs["name"], attrs.get("value", "")))
        if self.current is not None and tag == "button" and "name" in attrs:
            self.current["buttons"].append((attrs["name"], attrs["value"]))
        if tag == "pre":
            self.in_pre = True
            self.texts.append("")

    def handle_endtag(self, tag):
        if tag == "form":
            self.current = None
        if tag == "pre":
            self.in_pre = False

    def handle_data(self, text):
        if self.in_pre:
            self.texts[-1] += text


class FakeSettings:
    def __init__(self):
        self.rows = {}
        self.calls = []
        self.fail = False

    def effective(self, workspace, agent="", task_ref=""):
        result = HandoffSettings()
        for scope, ref in (("workspace", ""), ("agent", agent), ("task", task_ref)):
            row = self.rows.get((workspace, scope, ref))
            if row:
                result = replace(
                    result,
                    enabled=result.enabled if row.enabled is None else row.enabled,
                    threshold=result.threshold if row.threshold is None else row.threshold,
                )
        return result

    def set(self, row, *, actor, is_owner):
        assert is_owner
        if self.fail:
            raise SettingsError("private backend detail")
        self.calls.append(("set", row, actor))
        self.rows[(row.workspace_id, row.scope, row.ref)] = row
        return row

    def clear(self, workspace, scope, ref, *, actor, is_owner):
        assert is_owner
        self.calls.append(("clear", workspace, scope, ref, actor))
        return self.rows.pop((workspace, scope, ref), None) is not None


class FakeHandoffs:
    def __init__(self, settings):
        self.settings = settings
        self.handoffs = []
        self.calls = []

    def list_settings(self, workspace):
        return [row for row in self.settings.rows.values() if row.workspace_id == workspace]

    def list(self, workspace, *, task_ref="", limit=50):
        self.calls.append((workspace, task_ref))
        return [
            row
            for row in self.handoffs
            if row.workspace_id == workspace and row.task_ref == task_ref
        ]


class FakeRecall:
    def __init__(self):
        self.threads = {}
        self.messages = {}
        self.calls = []

    def thread(self, principal, identifier):
        thread = self.threads.get(identifier)
        if (
            thread is None
            or principal.workspace_id != thread.workspace_id
            or not principal.policy.has_scope("threads:read")
            or not principal.policy.can_read_wing(thread.wing)
        ):
            raise ThreadNotFound(identifier)
        return thread

    def get(self, principal, identifier, start=0, end=None):
        self.thread(principal, identifier)
        self.calls.append((identifier, start, end))
        return [message for message in self.messages[identifier] if start <= message.seq <= end]


class NoWorkers:
    def get(self, workspace):
        raise AssertionError("UI must not start a Memory worker")


class Browser:
    def __init__(self, app):
        self.app = app
        self.cookie = ""

    def request(self, method, path, data=None, headers=None):
        outgoing = {"Cookie": self.cookie}
        if data is not None:
            data = urlencode(data) if not isinstance(data, (str, bytes)) else data
            outgoing["Content-Type"] = "application/x-www-form-urlencoded"
        outgoing.update(headers or {})
        conn = http.client.HTTPConnection("127.0.0.1", self.app.server.server_port, timeout=3)
        try:
            conn.request(method, path, body=data, headers=outgoing)
            response = conn.getresponse()
            result = SimpleNamespace(
                status=response.status,
                headers=dict(response.getheaders()),
                body=response.read().decode("utf-8", errors="replace"),
            )
        finally:
            conn.close()
        if "Set-Cookie" in result.headers:
            self.cookie = result.headers["Set-Cookie"].split(";", 1)[0]
        return result

    def login(self, token=None, headers=None):
        response = self.request("GET", "/app/login", headers=headers)
        fields = Forms(response.body).forms[-1]["fields"]
        fields = [(key, value) for key, value in fields if key != "token"]
        return self.request(
            "POST", "/app/login", fields + [("token", token or self.app.token)], headers=headers
        )

    def csrf(self):
        response = self.request("GET", "/app/settings/memory")
        return dict(Forms(response.body).forms[0]["fields"])["csrf"]

    def post(self, fields, path="/app/settings/memory"):
        return self.request("POST", path, {"csrf": self.csrf(), **fields})


class App:
    def __init__(self, path):
        self.db = ControlDB(str(path / "control.sqlite3"))
        self.owner = self.db.create_user("jeffrey@example.com")
        self.workspace = self.db.create_workspace("commonplace", self.owner)
        self.settings = FakeSettings()
        self.handoff_db = FakeHandoffs(self.settings)
        self.recall = FakeRecall()
        self.gateway = Gateway(self.db, NoWorkers())
        self.control_db = self.db
        self.connection, self.token = self.make_token("codex:vm")
        web.register(self)
        self.server = self.gateway.build_server("127.0.0.1", 0)
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        self.thread.start()
        self.browser = Browser(self)

    def make_token(self, name, user=None, workspace=None, scopes=None, wings=None):
        return self.db.create_token(
            workspace_ref=workspace or self.workspace,
            user_ref=user or self.owner,
            name=name,
            client="test",
            actor=self.owner,
            policy=Policy.build(
                profile="full",
                scopes=scopes if scopes is not None else ["memory:read", "threads:read"],
                read_wings=wings if wings is not None else ["*"],
                trust="trusted",
            ),
        )

    def add_thread(self, count=1, identifier="thr_old", workspace=None, texts=None, wing="threads"):
        messages = [
            ThreadMessage(
                seq, "assistant", "message", texts[seq] if texts else f"message {seq}", "raw"
            )
            for seq in range(count)
        ]
        self.recall.messages[identifier] = messages
        self.recall.threads[identifier] = StoredThread(
            identifier, workspace or self.workspace, "source", count, 1, "digest", wing=wing
        )
        return identifier

    def add_handoff(self, **changes):
        row = Handoff(
            "hof_one",
            self.workspace,
            "key",
            "source",
            "done",
            agent="codex:vm",
            task_ref="#142",
            branch="feat/context-handoff",
            pr_url="https://example.test/pull/142",
            stored_thread_id="thr_old",
            successor=SuccessorRef("claude:next", "https://example.test/agents/next"),
            connection_id=self.connection,
        )
        row = replace(row, **changes)
        self.handoff_db.handoffs.append(row)
        return row

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.db.close()


@pytest.fixture
def app(tmp_path):
    instance = App(tmp_path)
    try:
        yield instance
    finally:
        instance.close()


def test_registration_and_default_off_have_no_side_effects(app):
    assert not app.settings.calls and not app.handoff_db.handoffs
    assert len(app.gateway.routes) == 8
    assert app.browser.login().status == 303
    response = app.browser.request("GET", "/app/settings/memory")
    assert response.status == 200 and "<h1>Memory</h1>" in response.body
    assert " checked" not in response.body and 'value="80"' in response.body
    assert not app.settings.calls


def test_session_rotation_hash_storage_and_cookie(app):
    browser = app.browser
    first = browser.request("GET", "/app/login")
    old_cookie = browser.cookie
    old_csrf = dict(Forms(first.body).forms[-1]["fields"])["csrf"]
    response = browser.login()
    assert response.status == 303 and browser.cookie != old_cookie
    cookie = response.headers["Set-Cookie"]
    assert all(
        value in cookie for value in ("HttpOnly", "SameSite=Strict", "Path=/app", "Max-Age=43200")
    )
    assert "Secure" not in cookie
    raw_id = browser.cookie.partition("=")[2]
    assert len(bytes.fromhex(raw_id)) == 32
    rows = app.db._conn.execute("SELECT * FROM web_sessions").fetchall()
    assert len(rows) == 1 and rows[0]["id_hash"] == hashlib.sha256(raw_id.encode()).hexdigest()
    assert rows[0]["connection_id"] == app.connection
    assert rows[0]["csrf"] != old_csrf
    assert raw_id not in json.dumps([dict(row) for row in rows])
    assert app.token not in json.dumps([dict(row) for row in rows])
    assert 43190 < rows[0]["expires_at"] - time.time() <= 43200
    assert (
        browser.request("POST", "/app/settings/memory", {"csrf": old_csrf, "action": "save"}).status
        == 400
    )
    assert (
        browser.request("GET", "/app/settings/memory", headers={"Cookie": old_cookie}).status == 404
    )
    assert browser.request("GET", "/app/login").status == 303


def test_secure_cookie_forwarded_https_and_tls(app):
    response = app.browser.login(headers={"X-Forwarded-Proto": "https"})
    assert "; Secure" in response.headers["Set-Cookie"]
    # Exercise the TLS branch without requiring a certificate fixture.
    request = SimpleNamespace(connection=object(), headers={})
    from unittest.mock import patch

    with patch.object(web.ssl, "SSLSocket", object):
        assert "; Secure" in web._Pages(app)._cookie(request, "value", 0)


@pytest.mark.parametrize("token", ["wrong", "cmmn_invalid", "cmmn_\r\nX-Forged: true", ""])
def test_invalid_login_never_authenticates(app, token):
    response = app.browser.request("GET", "/app/login")
    fields = dict(Forms(response.body).forms[-1]["fields"])
    fields["token"] = token
    assert app.browser.request("POST", "/app/login", fields).status == 404
    assert app.browser.request("GET", "/app/settings/memory").status == 404


@pytest.mark.parametrize("csrf", [None, "", "0" * 64, "é" * 64])
def test_login_requires_anonymous_csrf(app, csrf):
    app.browser.request("GET", "/app/login")
    fields = {"token": app.token}
    if csrf is not None:
        fields["csrf"] = csrf
    assert app.browser.request("POST", "/app/login", fields).status == 400


def test_login_requires_prelogin_session_and_it_expires(app):
    assert app.browser.request("POST", "/app/login", {"token": app.token}).status == 404
    app.browser.request("GET", "/app/login")
    row = app.db._conn.execute("SELECT * FROM web_sessions").fetchone()
    assert 590 < row["expires_at"] - time.time() <= 600
    app.db._conn.execute("UPDATE web_sessions SET expires_at = 0")
    assert (
        app.browser.request("POST", "/app/login", {"token": app.token, "csrf": row["csrf"]}).status
        == 404
    )


@pytest.mark.parametrize(
    "change",
    ["session", "revoked", "token_expired", "disabled", "membership", "credential", "bad_expiry"],
)
def test_stale_identity_fails_closed(app, change):
    app.browser.login()
    if change == "session":
        app.db._conn.execute("UPDATE web_sessions SET expires_at = 0")
    elif change == "membership":
        app.db._conn.execute("PRAGMA foreign_keys = OFF")
        app.db._conn.execute("DELETE FROM memberships")
    elif change == "disabled":
        app.db._conn.execute("UPDATE users SET disabled_at = 'now'")
    else:
        column, value = {
            "revoked": ("revoked_at", "now"),
            "token_expired": ("expires_at", "2000-01-01T00:00:00Z"),
            "credential": ("token_hash", "changed"),
            "bad_expiry": ("expires_at", "invalid"),
        }[change]
        app.db._conn.execute(f"UPDATE connections SET {column} = ?", (value,))
    for path in ("/app/settings/memory", "/app/cards/142", "/app/threads/thr_old"):
        assert app.browser.request("GET", path).status == 404


@pytest.mark.parametrize(
    "cookie",
    [
        "",
        "cmmn_session=bad",
        "cmmn_session=' OR 1=1",
        "cmmn_session=" + "0" * 64,
        "cmmn_session=" + "a" * 64 + "; cmmn_session=" + "b" * 64,
    ],
)
def test_malformed_cookie_is_not_found(app, cookie):
    assert (
        app.browser.request("GET", "/app/settings/memory", headers={"Cookie": cookie}).status == 404
    )


@pytest.mark.parametrize("path", ["/app/logout", "/app/settings/memory", "/app/login"])
def test_authenticated_posts_reject_missing_or_wrong_csrf(app, path):
    app.browser.login()
    for fields in ({}, {"csrf": "0" * 64}):
        assert app.browser.request("POST", path, fields).status == 400
    assert not app.settings.calls
    assert app.browser.request("GET", "/app/settings/memory").status == 200


def test_logout_clears_cookie_and_server_session(app):
    app.browser.login()
    cookie = app.browser.cookie
    response = app.browser.post({}, "/app/logout")
    assert response.status == 303 and response.headers["Location"] == "/app/login"
    assert "Path=/app; Max-Age=0" in response.headers["Set-Cookie"]
    assert app.db._conn.execute("SELECT COUNT(*) FROM web_sessions").fetchone()[0] == 0
    assert (
        app.browser.request("GET", "/app/settings/memory", headers={"Cookie": cookie}).status == 404
    )


def test_revoked_session_can_start_a_fresh_login(app):
    app.browser.login()
    old_cookie = app.browser.cookie
    app.db.revoke(app.connection, actor=app.owner)
    _, token = app.make_token("replacement")
    assert app.browser.login(token).status == 303
    assert app.browser.cookie != old_cookie
    assert app.browser.request("GET", "/app/settings/memory").status == 200


def test_ui_settings_updates_preserve_other_configured_fields(app):
    launcher = LauncherConfig(kind="command", argv=("agent", "{prompt}"))
    for scope, ref in (("workspace", ""), ("agent", "codex:vm")):
        app.settings.rows[(app.workspace, scope, ref)] = SettingRow(
            app.workspace, scope, ref, True, 0.9, 100000, launcher
        )
    app.browser.login()
    for scope, ref in (("workspace", ""), ("agent", "codex:vm")):
        assert (
            app.browser.post(
                {"action": "save", "scope": scope, "ref": ref, "threshold": "80"}
            ).status
            == 303
        )
        row = app.settings.calls[-1][1]
        assert not row.enabled and row.launcher == launcher and row.context_window == 100000
        assert row.threshold == (0.8 if scope == "workspace" else 0.9)


def test_owner_saves_toggle_threshold_and_adds_updates_removes_override(app):
    browser = app.browser
    browser.login()
    response = browser.post(
        {
            "action": "save",
            "scope": "workspace",
            "enabled": "on",
            "threshold": "87.5",
            "workspace": "forged",
            "actor": "forged",
        }
    )
    assert response.status == 303
    assert app.settings.calls[-1][1] == SettingRow(app.workspace, "workspace", "", True, 0.875)
    assert app.settings.calls[-1][2] == "conn:codex:vm"
    assert (
        browser.post({"action": "save", "scope": "task", "ref": "142", "enabled": "on"}).status
        == 303
    )
    assert app.settings.calls[-1][1].ref == "#142"
    response = browser.request("GET", "/app/settings/memory")
    override = next(
        form for form in Forms(response.body).forms if ("scope", "task") in form["fields"]
    )
    fields = [(key, value) for key, value in override["fields"] if key != "enabled"]
    assert browser.request("POST", override["action"], fields + [("action", "save")]).status == 303
    assert not app.settings.calls[-1][1].enabled
    assert (
        browser.request("POST", override["action"], fields + [("action", "remove")]).status == 303
    )
    assert app.settings.calls[-1][:4] == ("clear", app.workspace, "task", "#142")
    assert browser.post({"action": "save", "scope": "workspace", "threshold": "80"}).status == 303
    assert not app.settings.effective(app.workspace).enabled


def test_member_reads_but_direct_mutations_are_hidden(app):
    user = app.db.create_user("reader@example.com")
    app.db.add_member(app.workspace, user, "member", actor=app.owner)
    _, token = app.make_token("reader", user=user)
    app.settings.rows[(app.workspace, "agent", "bot")] = SettingRow(
        app.workspace, "agent", "bot", True
    )
    app.browser.login(token)
    response = app.browser.request("GET", "/app/settings/memory")
    assert response.status == 200 and " disabled" in response.body
    assert (
        "Add override" not in response.body
        and ">Save<" not in response.body
        and ">Remove<" not in response.body
    )
    for fields in (
        {"action": "save", "scope": "workspace", "threshold": "80"},
        {"action": "save", "scope": "task", "ref": "#142"},
        {"action": "remove", "scope": "agent", "ref": "bot"},
    ):
        assert app.browser.post(fields).status == 404
    assert not app.settings.calls
    app.db._conn.execute("UPDATE memberships SET role = ? WHERE user_id = ?", ("owner", user))
    assert app.browser.post({"action": "save", "threshold": "80"}).status == 303


@pytest.mark.parametrize(
    "fields",
    [{"threshold": value} for value in ("", "nan", "inf", "49", "96", "oops", "1e999")]
    + [
        {"scope": "other"},
        {"action": "delete"},
        {"scope": "workspace", "ref": "foreign"},
        {"scope": "agent", "ref": ""},
        {"scope": "agent", "ref": "x" * 201},
        {"scope": "agent", "ref": "x\ny"},
        {"scope": "task", "ref": "bad"},
        {"scope": "workspace", "action": "remove"},
        {"enabled": "yes"},
    ],
)
def test_bad_settings_inputs_have_no_side_effects(app, fields):
    app.browser.login()
    assert app.browser.post({"action": "save", "threshold": "80", **fields}).status == 400
    assert not app.settings.calls


def test_settings_error_and_malformed_forms_are_safe(app):
    app.browser.login()
    csrf = app.browser.csrf()
    for body, headers in (
        ("csrf=" + csrf + "&csrf=" + csrf, {}),
        ("csrf=" + csrf, {"Content-Type": "application/json"}),
        (b"\xff", {}),
        ("x=" + "a" * 8200, {}),
        ("&".join("x=1" for _ in range(17)), {}),
    ):
        assert app.browser.request("POST", "/app/settings/memory", body, headers).status == 400
    app.settings.fail = True
    response = app.browser.post({"action": "save", "threshold": "80"})
    assert response.status == 400 and "private backend detail" not in response.body


@pytest.mark.parametrize(
    "state", ["done", "launched", "failed", "launch_failed", "pending", "stored"]
)
def test_card_states_and_links(app, state):
    app.add_thread()
    app.add_handoff(state=state)
    app.browser.login()
    response = app.browser.request("GET", "/app/cards/142")
    assert response.status == 200 and "<h1>#142</h1>" in response.body
    assert app.handoff_db.calls[-1] == (app.workspace, "#142")
    assert 'href="/app/threads/thr_old"' in response.body
    assert 'href="https://example.test/agents/next"' in response.body
    assert "feat/context-handoff" in response.body
    if state in ("done", "launched"):
        assert "handed off" in response.body and "compacted instead" not in response.body
    elif state in ("failed", "launch_failed"):
        assert "handoff failed" in response.body and "compacted instead" in response.body


@pytest.mark.parametrize(
    "url",
    [
        "",
        "javascript:alert(1)",
        "data:text/html,hi",
        "//example.test",
        "https://user:pass@example.test",
        "https://example.test\n/evil",
        "https://[invalid",
        "https://example.test\\evil",
    ],
)
def test_unsafe_successor_urls_are_text(app, url):
    app.add_thread()
    app.add_handoff(successor=SuccessorRef('next<script>"', url), pr_url=url)
    app.browser.login()
    response = app.browser.request("GET", "/app/cards/142")
    assert response.status == 200 and "next&lt;script&gt;&quot;" in response.body
    assert 'rel="noreferrer"' not in response.body and "<script>" not in response.body


def test_all_dynamic_attributes_and_metadata_are_escaped(app):
    app.add_thread()
    app.add_handoff(
        agent="<img src=x onerror=1>",
        branch="<script>x</script>",
        pr_url='https://example.test/"><script>',
        successor=SuccessorRef("<next>", 'https://example.test/?a="&b=<'),
    )
    app.settings.rows[(app.workspace, "agent", 'bot"<')] = SettingRow(
        app.workspace, "agent", 'bot"<', True
    )
    app.browser.login()
    response = app.browser.request("GET", "/app/cards/142")
    assert "<img" not in response.body and "<script>" not in response.body
    assert 'href="https://example.test/&quot;&gt;&lt;script&gt;"' in response.body
    response = app.browser.request("GET", "/app/settings/memory")
    assert 'value="bot&quot;&lt;"' in response.body


@pytest.mark.parametrize(
    "path",
    [
        "/app/cards/999",
        "/app/cards/bad",
        "/app/cards/142/extra",
        "/app/threads/missing",
        "/app/threads/",
        "/app/threads/a%2Fb",
        "/app/static/../web.py",
    ],
)
def test_missing_resources(app, path):
    app.browser.login()
    response = app.browser.request("GET", path)
    assert response.status == 404 and "<h1>Not found</h1>" in response.body


def test_workspace_and_thread_visibility(app):
    app.add_thread(workspace="ws_other")
    app.add_handoff()
    app.add_handoff(id="hof_other", workspace_id="ws_other", stored_thread_id="")
    app.browser.login()
    assert app.browser.request("GET", "/app/cards/142").status == 404
    assert app.browser.request("GET", "/app/threads/thr_old").status == 404
    assert app.handoff_db.calls == [(app.workspace, "#142")]
    app.add_thread(wing="private")
    _, token = app.make_token("limited", wings=["threads"])
    other = Browser(app)
    other.login(token)
    assert other.request("GET", "/app/cards/142").status == 404
    assert other.request("GET", "/app/threads/thr_old").status == 404


@pytest.mark.parametrize(
    "scopes,wings",
    [
        (["memory:read"], ["*"]),
        (["threads:read"], ["*"]),
        (["memory:read", "threads:read"], ["shared"]),
    ],
)
def test_card_and_thread_need_scopes_and_readable_memory(app, scopes, wings):
    app.add_thread()
    app.add_handoff()
    _, token = app.make_token("limited", scopes=scopes, wings=wings)
    app.browser.login(token)
    assert app.browser.request("GET", "/app/cards/142").status == 404
    assert app.browser.request("GET", "/app/threads/thr_old").status == 404


def test_failed_unstored_handoff_visibility(app):
    app.add_handoff(state="failed", stored_thread_id="", successor=None)
    app.browser.login()
    response = app.browser.request("GET", "/app/cards/142")
    assert response.status == 200 and "Old thread" not in response.body
    user = app.db.create_user("member@example.com")
    app.db.add_member(app.workspace, user, "member", actor=app.owner)
    connection, token = app.make_token("member", user=user)
    browser = Browser(app)
    browser.login(token)
    assert browser.request("GET", "/app/cards/142").status == 404
    app.handoff_db.handoffs[0] = replace(app.handoff_db.handoffs[0], connection_id=connection)
    assert browser.request("GET", "/app/cards/142").status == 200


def test_verbatim_thread_text_roles_kind_names_and_headers(app):
    texts = ['\n<script>alert("x")</script>\r\n\t  trailing  \n', "a & b < c\rnext", ""]
    app.add_thread(count=3, texts=texts)
    app.recall.messages["thr_old"][1] = replace(
        app.recall.messages["thr_old"][1], role="tool", kind="tool_result", name='<tool>"'
    )
    app.browser.login()
    response = app.browser.request("GET", "/app/threads/thr_old")
    assert response.status == 200 and "<script>" not in response.body
    assert Forms(response.body).texts == texts
    assert "<pre><span>\n&lt;script&gt;" in response.body
    assert "&lt;tool&gt;&quot;" in response.body and "tool_result" in response.body
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


@pytest.mark.parametrize("count", [0, 1, 99, 100, 101, 200, 201])
def test_thread_pages_are_exactly_100_messages(app, count):
    app.add_thread(count=count)
    app.browser.login()
    response = app.browser.request("GET", "/app/threads/thr_old")
    assert response.status == 200 and len(Forms(response.body).texts) == min(count, 100)
    assert (">Next<" in response.body) == (count > 100)
    assert ">Previous<" not in response.body
    assert app.recall.calls[-1] == ("thr_old", 0, 99)
    response = app.browser.request("GET", "/app/threads/thr_old?page=2")
    if count <= 100:
        assert response.status == 404
    else:
        assert response.status == 200 and len(Forms(response.body).texts) == min(count - 100, 100)
        assert ">Previous<" in response.body
        assert (">Next<" in response.body) == (count > 200)
        assert app.recall.calls[-1] == ("thr_old", 100, 199)


@pytest.mark.parametrize(
    "query",
    [
        "page=0",
        "page=-1",
        "page=",
        "page=nan",
        "page=9999999999",
        "page=1&page=2",
        "a=1&a=2&a=3&a=4&a=5",
    ],
)
def test_invalid_page_input_is_400(app, query):
    app.add_thread()
    app.browser.login()
    assert app.browser.request("GET", "/app/threads/thr_old?" + query).status == 400


def test_static_allowlist_serves_local_css_font_and_license(app):
    for path, media in (
        ("handoff.css", "text/css"),
        ("fonts/geist-mono-latin.woff2", "font/woff2"),
        ("fonts/OFL.txt", "text/plain"),
    ):
        response = app.browser.request("GET", "/app/static/" + path)
        assert response.status == 200 and response.headers["Content-Type"].startswith(media)
    assert app.browser.request("GET", "/app/static/fonts/../../web.py").status == 404
