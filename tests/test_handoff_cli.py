"""CLI adapters against signature-faithful handoff service fakes."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from commonplace import cli
from commonplace.control_db import ControlError
from commonplace.handoff import db as handoff_db, events, service, settings, thread_store
from commonplace.handoff.models import (
    Handoff,
    HandoffResult,
    HandoffSettings,
    LauncherConfig,
    RecallHit,
    StoredThread,
    SuccessorRef,
    ThreadMessage,
)
from commonplace.handoff.recall import ThreadNotFound


class FakeControl:
    def __init__(self):
        self.people = [
            {"id": "usr_owner", "email": "owner@example.test", "is_admin": True},
            {"id": "usr_member", "email": "member@example.test", "is_admin": False},
        ]
        self.spaces = [{"id": "ws_home", "name": "home"}, {"id": "ws_other", "name": "other"}]
        self.owner_checks = []

    def users(self):
        return self.people

    def user(self, user_ref):
        for person in self.people:
            if user_ref in (person["id"], person["email"]):
                return person
        raise ControlError("user not found")

    def workspace(self, ref):
        for space in self.spaces:
            if ref in (space["id"], space["name"]):
                return space
        raise ControlError("workspace not found")

    def workspaces(self):
        return self.spaces

    def role(self, workspace_id, user_id):
        if workspace_id == "ws_home":
            return "owner" if user_id == "usr_owner" else "member"
        return None

    def require_owner(self, workspace_id, actor_id):
        self.owner_checks.append((workspace_id, actor_id))
        if self.role(workspace_id, actor_id) != "owner":
            raise ControlError("only a workspace owner can do that")


def database_class(state):
    class FakeHandoffDB:
        def __init__(self, control_db, *, document_store_for=None):
            assert control_db is state.control

        def setting_rows(self, workspace_id, agent="", task_ref=""):
            keys = [(workspace_id, "workspace", "")]
            if agent:
                keys.append((workspace_id, "agent", agent))
            if task_ref:
                keys.append((workspace_id, "task", task_ref))
            return [state.rows[key] for key in keys if key in state.rows]

        def put_setting(self, row, *, actor):
            state.writes.append((row, actor))
            state.rows[(row.workspace_id, row.scope, row.ref)] = row
            return row

        def delete_setting(self, workspace_id, scope, ref, *, actor):
            state.writes.append((scope, ref, actor))
            return state.rows.pop((workspace_id, scope, ref), None) is not None

        def get(self, handoff_id):
            return state.records.get(handoff_id)

        def list(self, workspace_id, *, task_ref="", limit=50):
            return [
                record
                for record in state.records.values()
                if record.workspace_id == workspace_id
                and (not task_ref or record.task_ref == task_ref)
            ][:limit]

        def add_webhook(self, workspace_id, url, secret, events, *, actor):
            assert actor == "usr_owner"
            webhook_id = f"hook_{len(state.hooks)}"
            state.hooks[webhook_id] = {
                "id": webhook_id,
                "workspace_id": workspace_id,
                "url": url,
                "secret": secret,
                "events": events,
                "created_at": "2026-10-30T00:00:00Z",
            }
            return webhook_id

        def webhooks(self, workspace_id):
            return [hook for hook in state.hooks.values() if hook["workspace_id"] == workspace_id]

        def webhook_secret(self, webhook_id):
            raise AssertionError("CLI must never retrieve an existing secret")

        def remove_webhook(self, webhook_id, *, actor):
            state.removed.append((webhook_id, actor))
            if not state.remove_result:
                return False
            return state.hooks.pop(webhook_id, None) is not None

    return FakeHandoffDB


@pytest.fixture
def adapter(monkeypatch, tmp_path):
    control = FakeControl()
    control.path = str(tmp_path / "control.sqlite3")
    state = SimpleNamespace(
        control=control,
        rows={},
        records={},
        hooks={},
        threads={},
        messages={},
        hits=[],
        stores=[],
        writes=[],
        searches=[],
        removed=[],
        relaunches=[],
        set_error=None,
        clear_error=None,
        read_error=None,
        launch_error=None,
        remove_result=True,
        result=HandoffResult("handoff"),
    )

    FakeHandoffDB = database_class(state)

    class FakeEvents:
        def __init__(self, control_db, handoff_db):
            assert control_db is control
            assert isinstance(handoff_db, FakeHandoffDB)

    class FakeSettings:
        def __init__(self, handoff_db, events=None):
            self.db = handoff_db
            assert isinstance(events, FakeEvents)

        def effective(self, workspace_id, agent="", task_ref=""):
            effective = HandoffSettings()
            for row in self.db.setting_rows(workspace_id, agent, task_ref):
                updates = {
                    field: getattr(row, field)
                    for field in ("enabled", "threshold", "context_window", "launcher")
                    if getattr(row, field) is not None
                }
                if row.enabled is not None:
                    updates["source"] = row.scope
                effective = replace(effective, **updates)
            return effective

        def set(self, row, *, actor, is_owner):
            assert is_owner is True
            if state.set_error is not None:
                raise state.set_error
            return self.db.put_setting(row, actor=actor)

        def clear(self, workspace_id, scope, ref, *, actor, is_owner):
            assert is_owner is True
            if state.clear_error is not None:
                raise state.clear_error
            return self.db.delete_setting(workspace_id, scope, ref, actor=actor)

    class FakeStore:
        def __init__(self, path):
            self.path = Path(path)
            self.closed = False
            state.stores.append(self)

        def close(self):
            self.closed = True

        def get(self, stored_id):
            if state.read_error is not None:
                raise state.read_error
            return state.threads.get(stored_id)

        def messages(self, stored_id, start=0, end=None):
            return [
                message
                for message in state.messages.get(stored_id, [])
                if message.seq >= start and (end is None or message.seq <= end)
            ]

        def search(self, stored_ids, query, limit=8):
            state.searches.append((stored_ids, query, limit))
            return state.hits[:limit]

    class FakeService:
        def __init__(
            self,
            *,
            control_db,
            handoff_db,
            settings,
            store_for,
            events,
            call_tool=None,
            prompt_dir="",
        ):
            assert control_db is control
            assert isinstance(handoff_db, FakeHandoffDB)
            assert isinstance(settings, FakeSettings)
            assert isinstance(events, FakeEvents)
            assert call_tool is None
            assert prompt_dir == str(tmp_path / "handoff-prompts")
            self.store_for = store_for

        def relaunch(self, principal, handoff_id):
            state.relaunches.append((principal, handoff_id))
            assert self.store_for(principal.workspace_id) is self.store_for(principal.workspace_id)
            if state.launch_error is not None:
                raise state.launch_error
            return state.result

    monkeypatch.setattr(cli, "_db", lambda args: control)
    monkeypatch.setattr(handoff_db, "HandoffDB", FakeHandoffDB)
    monkeypatch.setattr(events, "Events", FakeEvents)
    monkeypatch.setattr(settings, "SettingsService", FakeSettings)
    monkeypatch.setattr(thread_store, "ThreadStore", FakeStore)
    monkeypatch.setattr(service, "HandoffService", FakeService)
    state.run = lambda *args: cli.main(["--data", str(tmp_path), *args])
    return state


def make_handoff(**changes):
    return replace(
        Handoff(
            id="hof_one",
            workspace_id="ws_home",
            idempotency_key="thread:source",
            source_thread_id="source",
            state="launch_failed",
            stored_thread_id="thr_one",
            task_ref="#42",
            doc_markdown="## Goal\n  exact words\t\n",
        ),
        **changes,
    )


def add_thread(adapter, workspace="ws_home"):
    adapter.threads["thr_one"] = StoredThread("thr_one", workspace, "source", 3, 1, "hash")
    adapter.messages["thr_one"] = [
        ThreadMessage(0, "user", "message", "  first\t\r\n", "raw one"),
        ThreadMessage(1, "assistant", "message", "\n雪\n", "raw two"),
        ThreadMessage(2, "tool", "tool_result", '{"key": "value"}', "raw three"),
    ]


@pytest.mark.parametrize(
    ("words", "handler"),
    [
        (
            ["handoff", "status", "--workspace", "home", "--agent", "a", "--task", "t"],
            cli.cmd_handoff_status,
        ),
        (["handoff", "enable", "--workspace", "home"], cli.cmd_handoff_set),
        (["handoff", "disable", "--workspace", "home", "--task", "t"], cli.cmd_handoff_set),
        (["handoff", "clear", "--workspace", "home", "--agent", "a"], cli.cmd_handoff_clear),
        (
            ["handoff", "list", "--workspace", "home", "--task", "t", "--limit", "2"],
            cli.cmd_handoff_list,
        ),
        (["handoff", "show", "hof_one"], cli.cmd_handoff_show),
        (["handoff", "relaunch", "hof_one"], cli.cmd_handoff_relaunch),
        (
            ["thread", "get", "thr_one", "--workspace", "home", "--start", "1", "--end", "2"],
            cli.cmd_thread,
        ),
        (["thread", "recall", "thr_one", "a query", "--workspace", "home"], cli.cmd_thread),
        (
            ["webhook", "add", "https://example.test/wake", "--workspace", "home"],
            cli.cmd_webhook_add,
        ),
        (["webhook", "list"], cli.cmd_webhook_read),
        (["webhook", "remove", "hook_one"], cli.cmd_webhook_read),
    ],
)
def test_parser_commands(words, handler):
    assert cli.build_parser().parse_args(words).func is handler


@pytest.mark.parametrize("action", ["enable", "disable"])
def test_optional_settings_stay_unset(action):
    args = cli.build_parser().parse_args(["handoff", action, "--workspace", "home"])
    assert args.threshold is args.window is args.launcher is args.launcher_argv is None
    assert args.launcher_url is args.launcher_secret_env is None


def test_setting_round_trip_and_inheritance(adapter, capsys):
    run = adapter.run
    assert run("handoff", "status", "--workspace", "home") == 0
    assert (
        capsys.readouterr().out
        == "handoff: off (default), threshold 0.80, window auto, launcher client\n"
    )
    assert (
        run("handoff", "enable", "--workspace", "home", "--threshold", "0.9", "--window", "100000")
        == 0
    )
    assert run("handoff", "disable", "--workspace", "home", "--agent", "agent") == 0
    assert (
        run(
            "handoff",
            "enable",
            "--workspace",
            "home",
            "--task",
            "#42",
            "--by",
            "owner@example.test",
        )
        == 0
    )
    capsys.readouterr()
    assert run("handoff", "status", "--workspace", "ws_home", "--agent", "agent") == 0
    assert (
        capsys.readouterr().out
        == "handoff: off (agent), threshold 0.90, window 100000, launcher client\n"
    )
    assert run("handoff", "status", "--workspace", "home", "--agent", "agent", "--task", "#42") == 0
    assert (
        capsys.readouterr().out
        == "handoff: on (task), threshold 0.90, window 100000, launcher client\n"
    )
    assert run("handoff", "clear", "--workspace", "home", "--task", "#42") == 0
    assert run("handoff", "clear", "--workspace", "home", "--agent", "agent") == 0
    assert run("handoff", "clear", "--workspace", "home", "--agent", "absent") == 0
    capsys.readouterr()
    assert run("handoff", "status", "--workspace", "home", "--agent", "agent", "--task", "#42") == 0
    assert (
        capsys.readouterr().out
        == "handoff: on (workspace), threshold 0.90, window 100000, launcher client\n"
    )
    assert adapter.writes[1][0].threshold is None
    assert adapter.writes[1][0].context_window is None
    assert adapter.writes[1][0].launcher is None
    assert adapter.writes[2][1] == "usr_owner"


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        (["--launcher", "client"], LauncherConfig()),
        (
            ["--launcher", "command", "--launcher-argv", '["runner", "{prompt}", "two words"]'],
            LauncherConfig("command", ("runner", "{prompt}", "two words")),
        ),
        (
            [
                "--launcher",
                "webhook",
                "--launcher-url",
                "http://localhost:4321/wake",
                "--launcher-secret-env",
                "HANDOFF_TEST_SECRET",
            ],
            LauncherConfig("webhook", (), "http://localhost:4321/wake", "secret test value"),
        ),
    ],
)
def test_launcher_settings(adapter, monkeypatch, capsys, flags, expected):
    monkeypatch.setenv("HANDOFF_TEST_SECRET", "secret test value")
    assert adapter.run("handoff", "enable", "--workspace", "home", *flags) == 0
    assert adapter.writes[-1][0].launcher == expected
    assert adapter.run("handoff", "status", "--workspace", "home") == 0
    output = capsys.readouterr()
    assert f"launcher {expected.kind}" in output.out
    assert "secret test value" not in output.out + output.err


@pytest.mark.parametrize("flag", ["--launcher-argv", "--launcher-url", "--launcher-secret-env"])
def test_launcher_options_need_kind(adapter, capsys, flag):
    value = {
        "--launcher-argv": "[]",
        "--launcher-url": "https://example.test",
        "--launcher-secret-env": "HANDOFF_TEST_SECRET",
    }[flag]
    assert adapter.run("handoff", "enable", "--workspace", "home", flag, value) == 2
    assert capsys.readouterr().err == "commonplace: --launcher is required with launcher options\n"
    assert not adapter.writes


@pytest.mark.parametrize("value", [None, ""])
def test_missing_launcher_secret(adapter, monkeypatch, capsys, value):
    if value is None:
        monkeypatch.delenv("HANDOFF_TEST_SECRET", raising=False)
    else:
        monkeypatch.setenv("HANDOFF_TEST_SECRET", value)
    assert (
        adapter.run(
            "handoff",
            "enable",
            "--workspace",
            "home",
            "--launcher",
            "webhook",
            "--launcher-secret-env",
            "HANDOFF_TEST_SECRET",
        )
        == 2
    )
    assert (
        capsys.readouterr().err
        == "commonplace: environment variable HANDOFF_TEST_SECRET is not set\n"
    )
    assert not adapter.writes


@pytest.mark.parametrize(
    "words",
    [
        ["handoff", "enable", "--workspace", "home"],
        ["handoff", "disable", "--workspace", "home", "--task", "#42"],
        ["handoff", "clear", "--workspace", "home", "--task", "#42"],
        ["handoff", "relaunch", "hof_one"],
        ["webhook", "add", "https://example.test", "--workspace", "home"],
        ["webhook", "list", "--workspace", "home"],
        ["webhook", "remove", "hook_one", "--workspace", "home"],
    ],
)
def test_owner_only_paths(adapter, capsys, words):
    adapter.records["hof_one"] = make_handoff()
    assert adapter.run(*words, "--by", "member@example.test") == 2
    assert capsys.readouterr().err == "commonplace: only a workspace owner can do that\n"
    assert adapter.control.owner_checks == [("ws_home", "usr_member")]
    assert (
        not adapter.writes and not adapter.removed and not adapter.relaunches and not adapter.hooks
    )


@pytest.mark.parametrize(
    ("words", "message"),
    [
        (
            ["handoff", "enable", "--workspace", "home", "--by", "missing@example.test"],
            "user not found",
        ),
        (["handoff", "status", "--workspace", "missing"], "workspace not found"),
    ],
)
def test_missing_control_records(adapter, capsys, words, message):
    assert adapter.run(*words) == 2
    assert capsys.readouterr().err == f"commonplace: {message}\n"


def test_actor_needs_setup(adapter, capsys):
    adapter.control.people = []
    assert adapter.run("handoff", "enable", "--workspace", "home") == 2
    assert capsys.readouterr().err == "commonplace: no admin yet: run `commonplace init` first\n"


@pytest.mark.parametrize(
    ("field", "error", "words"),
    [
        (
            "set_error",
            settings.SettingsError("invalid launcher"),
            ["handoff", "enable", "--workspace", "home"],
        ),
        (
            "set_error",
            handoff_db.HandoffDBError("invalid setting"),
            ["handoff", "disable", "--workspace", "home"],
        ),
        (
            "clear_error",
            settings.SettingsError("cannot clear"),
            ["handoff", "clear", "--workspace", "home", "--agent", "agent"],
        ),
    ],
)
def test_expected_setting_errors(adapter, capsys, field, error, words):
    setattr(adapter, field, error)
    assert adapter.run(*words) == 2
    assert capsys.readouterr().err == f"commonplace: {error}\n"
    assert not adapter.writes


def test_handoff_list_show(adapter, capsys):
    adapter.records = {
        "hof_one": make_handoff(successor=SuccessorRef("agent:new")),
        "hof_two": make_handoff(id="hof_two", task_ref="#99", stored_thread_id=""),
        "hof_other": make_handoff(id="hof_other", workspace_id="ws_other"),
    }
    assert (
        adapter.run("handoff", "list", "--workspace", "home", "--task", "#42", "--limit", "1") == 0
    )
    assert (
        capsys.readouterr().out == "hof_one  launch_failed  thread thr_one  successor agent:new\n"
    )
    assert adapter.run("handoff", "list", "--workspace", "home", "--limit", "1") == 0
    assert capsys.readouterr().out.count("\n") == 1
    assert adapter.run("handoff", "show", "hof_one") == 0
    assert (
        capsys.readouterr().out
        == "hof_one  launch_failed  thread thr_one  successor agent:new\n## Goal\n  exact words\t\n"
    )
    assert adapter.run("handoff", "show", "hof_two") == 0
    assert "thread -  successor -" in capsys.readouterr().out
    assert adapter.run("handoff", "list", "--workspace", "home", "--task", "absent") == 0
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("action", ["show", "relaunch"])
def test_missing_handoff(adapter, capsys, action):
    assert adapter.run("handoff", action, "hof_missing") == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "commonplace: handoff not found\n"


@pytest.mark.parametrize("client_launch", [True, False])
def test_relaunch(adapter, capsys, tmp_path, client_launch):
    adapter.records["hof_one"] = make_handoff()
    adapter.result = HandoffResult(
        "handoff", make_handoff(state="launched"), launch=client_launch, prompt="  start here\n雪"
    )
    assert adapter.run("handoff", "relaunch", "hof_one", "--by", "owner@example.test") == 0
    principal, handoff_id = adapter.relaunches[0]
    assert handoff_id == "hof_one"
    assert principal.user_id == "usr_owner" and principal.workspace_id == "ws_home"
    assert principal.role == "owner" and principal.policy.has_scope("handoff:write")
    assert principal.policy.can_read_wing("threads")
    assert adapter.stores[0].path == tmp_path / "workspaces" / "ws_home" / "threads.sqlite3"
    assert adapter.stores[0].closed
    expected = (
        "  start here\n雪" if client_launch else "hof_one  launched  thread thr_one  successor -\n"
    )
    assert capsys.readouterr().out == expected


@pytest.mark.parametrize(
    "result",
    [
        HandoffResult("compact", reason="off"),
        HandoffResult("handoff"),
        HandoffResult("handoff", make_handoff(state="launch_failed")),
        HandoffResult("handoff", make_handoff(state="failed")),
        HandoffResult("handoff", make_handoff(state="launch_failed", error="launcher unavailable")),
    ],
)
def test_relaunch_failure_results(adapter, capsys, result):
    adapter.records["hof_one"] = make_handoff()
    adapter.result = result
    assert adapter.run("handoff", "relaunch", "hof_one") == 2
    output = capsys.readouterr()
    assert not output.out
    expected = (
        result.reason
        or (result.handoff.error if result.handoff else "")
        or "handoff relaunch failed"
    )
    assert output.err == f"commonplace: {expected}\n"
    assert adapter.stores[0].closed


def test_relaunch_error_closes_store(adapter, capsys):
    adapter.records["hof_one"] = make_handoff()
    adapter.launch_error = handoff_db.HandoffDBError("handoff cannot be relaunched")
    assert adapter.run("handoff", "relaunch", "hof_one") == 2
    assert capsys.readouterr().err == "commonplace: handoff cannot be relaunched\n"
    assert adapter.stores[0].closed


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        ([], '  first\t\r\n\n雪\n{"key": "value"}'),
        (["--start", "1", "--end", "1"], "\n雪\n"),
        (["--start", "2"], '{"key": "value"}'),
        (["--start", "0", "--end", "0"], "  first\t\r\n"),
        (["--start", "3"], ""),
    ],
)
def test_thread_get_exact_text(adapter, capsys, flags, expected):
    add_thread(adapter)
    assert adapter.run("thread", "get", "thr_one", "--workspace", "home", *flags) == 0
    assert capsys.readouterr().out == expected
    assert adapter.stores[0].closed


def test_thread_range_order(adapter, capsys):
    assert (
        adapter.run("thread", "get", "thr_one", "--workspace", "home", "--start", "2", "--end", "1")
        == 2
    )
    assert capsys.readouterr().err == "commonplace: end must be at least start\n"
    assert not adapter.stores


@pytest.mark.parametrize("action", ["get", "recall"])
@pytest.mark.parametrize("workspace", [None, "ws_other"])
def test_thread_not_found_is_fail_closed(adapter, capsys, action, workspace):
    if workspace:
        add_thread(adapter, workspace)
    words = ["thread", action, "thr_one", "--workspace", "home"]
    if action == "recall":
        words.append("query")
    assert adapter.run(*words) == 2
    output = capsys.readouterr()
    assert not output.out
    assert output.err == "commonplace: thread not found\n"
    assert adapter.stores[0].closed


def test_thread_recall_preserves_hits(adapter, capsys):
    add_thread(adapter)
    adapter.hits = [RecallHit("thr_one", 0, 0, 1, 1.0, "[0] user message:   words\t\n雪")]
    assert adapter.run("thread", "recall", "thr_one", 'two "words"', "--workspace", "home") == 0
    assert capsys.readouterr().out == adapter.hits[0].text
    assert adapter.searches == [(["thr_one"], 'two "words"', 8)]
    adapter.hits = []
    assert adapter.run("thread", "recall", "thr_one", "absent", "--workspace", "home") == 0
    assert capsys.readouterr().out == ""


def test_thread_expected_error(adapter, capsys):
    adapter.read_error = ThreadNotFound("thread not found")
    assert adapter.run("thread", "get", "thr_one", "--workspace", "home") == 2
    assert capsys.readouterr().err == "commonplace: thread not found\n"
    assert adapter.stores[0].closed


def test_webhook_secret_once(adapter, capsys):
    assert (
        adapter.run(
            "webhook",
            "add",
            "http://localhost:4321/wake",
            "--workspace",
            "home",
            "--events",
            "handoff.stored,handoff.failed,handoff.stored",
            "--by",
            "owner@example.test",
        )
        == 0
    )
    output = capsys.readouterr()
    webhook_id, secret = output.out.split()
    assert len(secret) >= 32 and output.err == ""
    assert adapter.hooks[webhook_id]["secret"] == secret
    assert adapter.hooks[webhook_id]["events"] == ("handoff.stored", "handoff.failed")
    assert adapter.run("webhook", "list", "--workspace", "home") == 0
    assert adapter.run("webhook", "list") == 0
    listing = capsys.readouterr()
    assert webhook_id in listing.out and secret not in listing.out + listing.err
    assert adapter.run("webhook", "remove", webhook_id) == 0
    removal = capsys.readouterr()
    assert removal.out == f"{webhook_id}\n" and secret not in removal.err
    assert adapter.removed == [(webhook_id, "usr_owner")]
    assert adapter.run("webhook", "list") == 0
    assert capsys.readouterr().out == ""
    assert adapter.run("webhook", "remove", webhook_id) == 2
    assert secret not in capsys.readouterr().err


def test_webhook_default_events_and_scoped_remove(adapter, capsys):
    assert adapter.run("webhook", "add", "https://example.test", "--workspace", "home") == 0
    capsys.readouterr()
    assert adapter.hooks["hook_0"]["events"] == ("handoff.*",)
    assert adapter.run("webhook", "remove", "hook_0", "--workspace", "home") == 0
    assert capsys.readouterr().out == "hook_0\n"


def test_failed_webhook_creation_never_prints_secret(adapter, monkeypatch, capsys):
    def fail_add(self, workspace_id, url, secret, events, *, actor):
        assert secret == "new secret"
        raise handoff_db.HandoffDBError("could not save webhook")

    monkeypatch.setattr(cli.secrets, "token_urlsafe", lambda length: "new secret")
    monkeypatch.setattr(handoff_db.HandoffDB, "add_webhook", fail_add)
    assert adapter.run("webhook", "add", "https://example.test", "--workspace", "home") == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "commonplace: could not save webhook\n"
    assert not adapter.hooks


@pytest.mark.parametrize("racing_remove", [True, False])
def test_webhook_missing_or_removed(adapter, capsys, racing_remove):
    if racing_remove:
        adapter.hooks["hook_one"] = {"id": "hook_one", "workspace_id": "ws_home"}
        adapter.remove_result = False
    assert adapter.run("webhook", "remove", "hook_one", "--workspace", "home") == 2
    assert capsys.readouterr().err == "commonplace: webhook not found\n"


def test_webhooks_outside_owned_workspaces_hidden(adapter, capsys):
    adapter.hooks["hook_hidden"] = {"id": "hook_hidden", "workspace_id": "ws_other"}
    assert adapter.run("webhook", "list") == 0
    assert capsys.readouterr().out == ""
    assert adapter.run("webhook", "remove", "hook_hidden") == 2
    assert capsys.readouterr().err == "commonplace: webhook not found\n"
    assert adapter.run("webhook", "list", "--by", "member@example.test") == 0
    assert capsys.readouterr().out == ""
    assert not adapter.removed


@pytest.mark.parametrize("action", ["enable", "disable", "clear"])
def test_setting_scope_conflicts(action):
    with pytest.raises(SystemExit) as error:
        cli.build_parser().parse_args(
            ["handoff", action, "--workspace", "home", "--agent", "a", "--task", "t"]
        )
    assert error.value.code == 2


@pytest.mark.parametrize(
    "words",
    [
        ["handoff"],
        ["thread"],
        ["webhook"],
        ["handoff", "status"],
        ["handoff", "enable"],
        ["handoff", "disable"],
        ["handoff", "clear", "--workspace", "home"],
        ["handoff", "list"],
        ["handoff", "show"],
        ["handoff", "relaunch"],
        ["thread", "get", "thr_one"],
        ["thread", "recall", "thr_one", "--workspace", "home"],
        ["webhook", "add", "https://example.test"],
        ["webhook", "remove"],
    ],
)
def test_required_parser_arguments(words):
    with pytest.raises(SystemExit) as error:
        cli.build_parser().parse_args(words)
    assert error.value.code == 2


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--threshold", "0.49"),
        ("--threshold", "0.96"),
        ("--threshold", "nan"),
        ("--threshold", "inf"),
        ("--threshold", "bad"),
        ("--window", "0"),
        ("--window", "-1"),
        ("--window", "1.5"),
        ("--launcher", "other"),
        ("--launcher-argv", "{}"),
        ("--launcher-argv", "null"),
        ("--launcher-argv", '["runner", 1]'),
        ("--launcher-argv", '"runner"'),
        ("--launcher-argv", "["),
    ],
)
def test_invalid_setting_inputs(flag, value):
    with pytest.raises(SystemExit) as error:
        cli.build_parser().parse_args(["handoff", "enable", "--workspace", "home", flag, value])
    assert error.value.code == 2


@pytest.mark.parametrize("value", ["0.50", "0.95"])
def test_threshold_boundaries(value):
    args = cli.build_parser().parse_args(
        ["handoff", "enable", "--workspace", "home", "--threshold", value]
    )
    assert args.threshold == float(value)


@pytest.mark.parametrize(
    "words",
    [
        ["handoff", "list", "--workspace", "home", "--limit", "0"],
        ["handoff", "list", "--workspace", "home", "--limit", "-1"],
        ["thread", "get", "thr_one", "--workspace", "home", "--start", "-1"],
        ["thread", "get", "thr_one", "--workspace", "home", "--end", "-1"],
    ],
)
def test_invalid_ranges_and_limits(words):
    with pytest.raises(SystemExit) as error:
        cli.build_parser().parse_args(words)
    assert error.value.code == 2


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.test",
        "http:///wake",
        "http://user:secret@example.test",
        "http://example.test/#fragment",
        "http://[",
        "http://example.test:bad",
        "http://example.test:0",
        "http://example.test/a\nb",
    ],
)
def test_invalid_webhook_urls(url):
    with pytest.raises(SystemExit) as error:
        cli.build_parser().parse_args(["webhook", "add", url, "--workspace", "home"])
    assert error.value.code == 2


@pytest.mark.parametrize(
    "event_names", ["", " , ", "other.*", "handoff.unknown", "handoff.stored,other"]
)
def test_invalid_webhook_events(event_names):
    with pytest.raises(SystemExit) as error:
        cli.build_parser().parse_args(
            [
                "webhook",
                "add",
                "https://example.test",
                "--workspace",
                "home",
                "--events",
                event_names,
            ]
        )
    assert error.value.code == 2


def test_programming_errors_are_not_hidden(adapter):
    adapter.set_error = RuntimeError("bug")
    with pytest.raises(RuntimeError, match="bug"):
        adapter.run("handoff", "enable", "--workspace", "home")


def test_existing_commands_avoid_handoff_imports(monkeypatch, capsys):
    import builtins

    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        assert "handoff" not in name
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(cli, "_db", lambda args: FakeControl())
    assert cli.main(["user", "list"]) == 0
    assert "owner@example.test" in capsys.readouterr().out
