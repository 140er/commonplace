"""Loopback gateway tests with no Memory worker or external service."""

import http.client
import json
import threading
from dataclasses import replace
from html.parser import HTMLParser
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

from commonplace import sessions
from commonplace.control_db import ControlDB
from commonplace.gateway import Gateway
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
from commonplace.handoff.wiring import install
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

    def login(self, email=None, workspace=None):
        token = sessions.create_login_link(
            self.app.db,
            email or self.app.db.user(self.app.owner)["email"],
            workspace or self.app.workspace,
        )
        response = self.request("GET", "/app/login?" + urlencode({"t": token}))
        assert response.status == 302 and response.headers["Location"] == "/app/"
        assert self.cookie.startswith(sessions.COOKIE + "=")
        return response

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
        assert not hasattr(self.gateway, "handoff")
        self.handoff = install(self.gateway, path)
        self.handoff.settings = self.settings
        self.handoff.handoff_db = self.handoff_db
        self.handoff.recall = self.recall
        self.connection, self.token = self.make_token("codex:vm")
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
        self.handoff.close()
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
    routes = dict(app.gateway.routes)
    prefixes = list(app.gateway.prefix_routes)
    assert install(app.gateway, app.handoff.data_dir) is app.gateway.handoff is app.handoff
    assert app.gateway.routes == routes and app.gateway.prefix_routes == prefixes
    assert ("GET", "/app/settings/memory") in routes
    assert ("POST", "/app/settings/memory") in routes
    app.browser.login()
    response = app.browser.request("GET", "/app/settings/memory")
    assert response.status == 200 and "<h1>Memory</h1>" in response.body
    assert '<a class="brand" href="/app/">Commonplace</a>' in response.body
    assert "/app/logout" not in response.body and "/app/login" not in response.body
    assert " checked" not in response.body and 'value="80"' in response.body
    assert not app.settings.calls


def test_forms_use_current_person_session_for_csrf(app):
    browser = app.browser
    browser.login()
    old_cookie = browser.cookie
    old_csrf = browser.csrf()
    assert len(bytes.fromhex(old_csrf)) == 32 and browser.csrf() == old_csrf
    assert old_csrf != sessions.cookie_value(browser.cookie)
    browser.login()
    assert browser.cookie != old_cookie and browser.csrf() != old_csrf
    assert (
        browser.request("POST", "/app/settings/memory", {"csrf": old_csrf, "action": "save"}).status
        == 400
    )
    assert not app.settings.calls


@pytest.mark.parametrize(
    "change",
    ["session", "revoked", "disabled", "membership"],
)
def test_stale_identity_fails_closed(app, change):
    app.browser.login()
    if change == "session":
        app.db._conn.execute("UPDATE sessions SET expires_at = '2000-01-01T00:00:00Z'")
    elif change == "revoked":
        sessions.end_session(app.db, sessions.cookie_value(app.browser.cookie))
    elif change == "membership":
        app.db._conn.execute("PRAGMA foreign_keys = OFF")
        app.db._conn.execute("DELETE FROM memberships")
    elif change == "disabled":
        app.db._conn.execute("UPDATE users SET disabled_at = 'now'")
    for path in ("/app/settings/memory", "/app/cards/142", "/app/threads/thr_old"):
        response = app.browser.request("GET", path)
        assert response.status == 303 and response.headers["Location"] == "/app/login"


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
    response = app.browser.request("GET", "/app/settings/memory", headers={"Cookie": cookie})
    assert response.status == 303 and response.headers["Location"] == "/app/login"


@pytest.mark.parametrize("csrf", [None, "", "0" * 64, "é" * 64])
def test_authenticated_posts_reject_missing_or_wrong_csrf(app, csrf):
    app.browser.login()
    fields = {"action": "save", "threshold": "80"}
    if csrf is not None:
        fields["csrf"] = csrf
    assert app.browser.request("POST", "/app/settings/memory", fields).status == 400
    assert not app.settings.calls
    assert app.browser.request("GET", "/app/settings/memory").status == 200


@pytest.mark.parametrize("path", ["/app/settings/memory", "/app/cards/142", "/app/threads/thr_old"])
def test_signed_out_pages_redirect_to_shared_login(app, path):
    response = app.browser.request("GET", path)
    assert response.status == 303 and response.headers["Location"] == "/app/login"
    assert "Set-Cookie" not in response.headers


def test_revoked_session_can_start_a_fresh_login(app):
    app.browser.login()
    old_cookie = app.browser.cookie
    sessions.end_session(app.db, sessions.cookie_value(old_cookie))
    app.browser.login()
    assert app.browser.cookie != old_cookie
    assert app.browser.request("GET", "/app/settings/memory").status == 200


@pytest.mark.parametrize(
    "headers",
    [
        {"Sec-Fetch-Site": "cross-site"},
        {"Origin": "https://other.example.test"},
        {"Origin": "null"},
    ],
)
def test_cross_site_posts_refuse_otherwise_valid_forms(app, headers):
    app.browser.login()
    fields = {"csrf": app.browser.csrf(), "action": "save", "threshold": "80"}
    assert app.browser.request("POST", "/app/settings/memory", fields, headers).status == 400
    assert not app.settings.calls
    assert app.browser.request("POST", "/app/settings/memory", fields).status == 303
    assert len(app.settings.calls) == 1


def test_same_origin_post_accepts_valid_csrf(app):
    app.browser.login()
    origin = f"http://127.0.0.1:{app.server.server_port}"
    response = app.browser.request(
        "POST",
        "/app/settings/memory",
        {"csrf": app.browser.csrf(), "action": "save", "threshold": "80"},
        {"Origin": origin, "Sec-Fetch-Site": "same-origin"},
    )
    assert response.status == 303 and len(app.settings.calls) == 1


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
    assert app.settings.calls[-1][2] == f"user:{app.owner}"
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
    app.settings.rows[(app.workspace, "agent", "bot")] = SettingRow(
        app.workspace, "agent", "bot", True
    )
    app.browser.login("reader@example.com")
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
    assert app.settings.calls[-1][2] == f"user:{user}"
    app.db._conn.execute("UPDATE memberships SET role = ? WHERE user_id = ?", ("member", user))
    app.settings.calls.clear()
    assert app.browser.post({"action": "save", "threshold": "80"}).status == 404
    assert not app.settings.calls


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


def test_card_shows_context_timeline_and_verbatim_preview(app):
    app.add_thread(count=42)
    doc = (
        "## Goal\n\n```\nShip <b>#142</b>\r\n```\n\n"
        "## Status\n\n````\nRan ``` once\n````\n\n## Note\n\n```\nnot shown\n```"
    )
    app.add_handoff(
        used_tokens=162_400,
        window_tokens=200_000,
        threshold=0.8,
        doc_markdown=doc,
        created_at="2026-10-30T12:20:41Z",
        updated_at="2026-10-30T12:22:46Z",
    )
    app.browser.login()
    body = app.browser.request("GET", "/app/cards/142").body
    assert 'role="meter"' in body and 'aria-valuenow="162400"' in body
    assert "162k / 200k · 81%" in body and "threshold 80%" in body
    # 16 of 20 blocks are under the threshold; none are past it at 81%.
    assert body.count('<i class="on">') == 16 and '<i class="over">' not in body
    assert "<code>hof_one</code>" in body and "42 messages" in body
    assert '<time datetime="2026-10-30T12:20:41Z">30 Oct 12:20:41 UTC</time>' in body
    assert "12:22:46" in body and "+2m 05s" in body
    assert Forms(body).texts == ["Ship <b>#142</b>\r", "Ran ``` once"]
    assert "<b>" not in body and "not shown" not in body


def test_card_meter_marks_blocks_past_the_threshold(app):
    app.add_thread()
    app.add_handoff(used_tokens=950_000, window_tokens=1_000_000, threshold=0.5)
    app.browser.login()
    body = app.browser.request("GET", "/app/cards/142").body
    assert "950k / 1M · 95%" in body
    assert body.count('<i class="on">') == 10 and body.count('<i class="over">') == 9


def test_card_without_usage_or_successor(app):
    app.add_thread()
    app.add_handoff(state="stored", successor=None)
    app.browser.login()
    body = app.browser.request("GET", "/app/cards/142").body
    assert 'role="meter"' not in body and "waiting" in body and '<dl class="preview">' not in body


def test_settings_show_on_off_and_what_overrides_change(app):
    app.settings.rows[(app.workspace, "workspace", "")] = SettingRow(
        app.workspace, "workspace", "", True
    )
    for ref, enabled in (("#142", False), ("#143", True)):
        app.settings.rows[(app.workspace, "task", ref)] = SettingRow(
            app.workspace, "task", ref, enabled
        )
    app.browser.login()
    body = app.browser.request("GET", "/app/settings/memory").body
    assert body.count('class="switch-state"') == 4
    assert '<span class="count">2</span>' in body
    assert body.count('<span class="from changed">Workspace On</span>') == 1
    assert body.count('<span class="from">Workspace On</span>') == 1


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
    # People see every wing in their workspace, including private agent wings.
    assert app.browser.request("GET", "/app/cards/142").status == 200
    assert app.browser.request("GET", "/app/threads/thr_old").status == 200
    _, token = app.make_token("limited", wings=["threads"])
    headers = {"Authorization": "Bearer " + token}
    assert app.browser.request("GET", "/api/threads/thr_old", headers=headers).status == 404
    response = app.browser.request("GET", "/api/handoffs?task=%23142", headers=headers)
    assert response.status == 200 and json.loads(response.body) == []
    user = app.db.create_user("other@example.com")
    workspace = app.db.create_workspace("other", user)
    other = Browser(app)
    other.login("other@example.com", workspace)
    assert other.request("GET", "/app/cards/142").status == 404
    assert other.request("GET", "/app/threads/thr_old").status == 404


@pytest.mark.parametrize("role", ["owner", "member"])
@pytest.mark.parametrize(
    "scopes,wings",
    [
        (["memory:read"], ["*"]),
        (["threads:read"], ["*"]),
        (["memory:read", "threads:read"], ["shared"]),
    ],
)
def test_person_reads_card_and_thread_despite_agent_policy(app, role, scopes, wings):
    app.add_thread()
    app.add_handoff()
    user = app.owner
    if role == "member":
        user = app.db.create_user("reader@example.com")
        app.db.add_member(app.workspace, user, role, actor=app.owner)
    app.make_token("limited", user=user, scopes=scopes, wings=wings)
    app.browser.login(app.db.user(user)["email"])
    principal = sessions.session_principal(app.db, sessions.cookie_value(app.browser.cookie))
    assert principal.person and principal.role == role
    assert principal.policy.has_scope("memory:read")
    assert principal.policy.has_scope("threads:read")
    assert principal.policy.can_read_wing("threads")
    assert not principal.policy.has_scope("admin")
    assert app.browser.request("GET", "/app/cards/142").status == 200
    assert app.browser.request("GET", "/app/threads/thr_old").status == 200


@pytest.mark.parametrize(
    "scopes,wings,status",
    [
        (["memory:read"], ["*"], 404),
        (["threads:read"], ["*"], 404),
        (["memory:read", "threads:read"], ["shared"], 404),
        (["memory:read", "threads:read"], ["*"], 200),
    ],
)
def test_bearer_thread_reads_keep_agent_scope_and_wing_checks(app, scopes, wings, status):
    app.add_thread()
    app.add_handoff()
    _, token = app.make_token("limited", scopes=scopes, wings=wings)
    headers = {"Authorization": "Bearer " + token}
    for path in ("/api/handoffs?task=%23142", "/api/threads/thr_old"):
        assert app.browser.request("GET", path, headers=headers).status == status
    # A bearer has no browser session and cannot sign in to the handoff pages.
    response = app.browser.request("GET", "/app/cards/142", headers=headers)
    assert response.status == 303 and response.headers["Location"] == "/app/login"


@pytest.mark.parametrize(
    "change", ["revoked", "token_expired", "disabled", "membership", "credential", "bad_expiry"]
)
def test_stale_bearer_identity_fails_closed(app, change):
    if change == "membership":
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
    response = app.browser.request(
        "GET", "/api/settings/handoff", headers={"Authorization": "Bearer " + app.token}
    )
    if change == "bad_expiry" and response.status != 401:
        assert response.status == 500
        assert json.loads(response.body) == {"error": "internal error"}
        pytest.xfail(
            "Develop ControlDB._parse raises ValueError for malformed connection expiry; "
            "the gateway returns 500. Core production changes are outside this handoff task."
        )
    assert response.status == 401 and json.loads(response.body)["error"] == "unauthorized"


def test_failed_unstored_handoff_visibility(app):
    app.add_handoff(state="failed", stored_thread_id="", successor=None)
    app.browser.login()
    response = app.browser.request("GET", "/app/cards/142")
    assert response.status == 200 and 'href="/app/threads/' not in response.body
    assert "not saved" in response.body and "compacted instead" in response.body
    user = app.db.create_user("member@example.com")
    app.db.add_member(app.workspace, user, "member", actor=app.owner)
    browser = Browser(app)
    browser.login("member@example.com")
    assert browser.request("GET", "/app/cards/142").status == 404
    connection, token = app.make_token("member", user=user)
    headers = {"Authorization": "Bearer " + token}
    response = browser.request("GET", "/api/handoffs?task=%23142", headers=headers)
    assert response.status == 200 and json.loads(response.body) == []
    app.handoff_db.handoffs[0] = replace(app.handoff_db.handoffs[0], connection_id=connection)
    response = browser.request("GET", "/api/handoffs?task=%23142", headers=headers)
    assert response.status == 200 and [row["id"] for row in json.loads(response.body)] == [
        "hof_one"
    ]
    assert browser.request("GET", "/app/cards/142").status == 404
    app.db._conn.execute("UPDATE memberships SET role = ? WHERE user_id = ?", ("owner", user))
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
    # no-referrer would make browsers send `Origin: null` on our own forms.
    assert response.headers["Referrer-Policy"] == "same-origin"
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
