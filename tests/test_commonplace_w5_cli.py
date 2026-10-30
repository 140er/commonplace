"""CLI presets and the login, bridge, seed and offline-backup entrypoints."""

import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from commonplace import cli, sessions
from commonplace.backup import BackupError, data_lock
from commonplace.control_db import ControlDB
from commonplace.extensions import ToolError
from commonplace.policy import Policy
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_commonplace_cli import (
    test_serve_needs_setup as legacy_serve_needs_setup,
    test_setup_token_list_revoke_audit as legacy_setup_token_list_revoke_audit,
)


@pytest.fixture
def initialized(tmp_path, capsys):
    data = tmp_path / "data"
    assert cli.main(["--data", str(data), "init", "--admin-email", "owner@example.com"]) == 0
    capsys.readouterr()
    return data


@pytest.mark.parametrize(
    ("client", "profile", "read", "write", "pinned", "trust"),
    [
        ("claude-code", "standard", ["*"], ["project_*", "shared"], "", "trusted"),
        ("codex", "standard", ["*"], ["project_*", "shared"], "", "trusted"),
        ("t3", "standard", ["*"], ["project_*", "shared"], "", "trusted"),
        ("grok", "slim", ["shared", "inbox"], [], "inbox", "untrusted"),
        ("claude-ai", "slim", ["shared", "inbox"], [], "inbox", "standard"),
        ("chatgpt", "slim", ["shared", "inbox"], [], "inbox", "standard"),
    ],
)
def test_client_presets(initialized, capsys, client, profile, read, write, pinned, trust):
    assert (
        cli.main(
            [
                "--data",
                str(initialized),
                "token",
                "add",
                client,
                "--workspace",
                "personal",
                "--client",
                client,
            ]
        )
        == 0
    )
    output = capsys.readouterr()
    assert output.out.startswith("cmmn_") and output.out.count("\n") == 1
    assert json.loads(output.err) == {
        "scopes": ["memory:read", "memory:write"],
        "tools": profile,
        "read_wings": read,
        "write_wings": write,
        "write_wing": pinned,
        "trust": trust,
    }
    with closing(ControlDB(str(initialized / "control.sqlite3"))) as db:
        policy = Policy.from_json(db.connection(client)["policy"])
        assert "kg:read" not in policy.scopes
        assert "private:read" not in policy.scopes
        assert "private:write" not in policy.scopes
        assert policy.can_read_wing("shared")
        assert not policy.can_read_wing("private")
        assert not policy.can_write_wing("private")
        if client in {"claude-code", "codex", "t3"}:
            assert policy.can_write_wing("project_example")
            assert policy.can_write_wing("shared")
            assert not policy.can_write_wing("inbox")
        else:
            assert policy.can_write_wing("inbox")
            assert not policy.can_write_wing("shared")


def test_explicit_preset_fields_including_empty_values(initialized, capsys):
    assert (
        cli.main(
            [
                "--data",
                str(initialized),
                "token",
                "add",
                "explicit",
                "--workspace",
                "personal",
                "--client",
                "codex",
                "--profile",
                "full",
                "--tools",
                "",
                "--scopes",
                "",
                "--read-wings",
                "",
                "--write-wings",
                "",
                "--write-wing",
                "inbox",
                "--trust",
                "untrusted",
            ]
        )
        == 0
    )
    policy = json.loads(capsys.readouterr().err)
    assert policy == {
        "tools": [],
        "scopes": [],
        "read_wings": [],
        "write_wings": [],
        "write_wing": "inbox",
        "trust": "untrusted",
    }
    assert (
        cli.main(
            [
                "--data",
                str(initialized),
                "token",
                "add",
                "partial",
                "--workspace",
                "personal",
                "--client",
                "codex",
                "--read-wings",
                "shared",
                "--profile",
                "slim",
            ]
        )
        == 0
    )
    policy = json.loads(capsys.readouterr().err)
    assert policy["tools"] == "slim"
    assert policy["read_wings"] == ["shared"]
    assert policy["write_wings"] == ["project_*", "shared"]
    assert policy["trust"] == "trusted"


def test_explicit_empty_pinned_wing_and_unknown_client(initialized, capsys):
    prefix = ["--data", str(initialized), "token", "add"]
    assert (
        cli.main(
            prefix
            + ["unpinned", "--workspace", "personal", "--client", "chatgpt", "--write-wing", ""]
        )
        == 0
    )
    policy = json.loads(capsys.readouterr().err)
    assert policy["write_wing"] == ""
    assert (
        cli.main(
            prefix
            + [
                "custom",
                "--workspace",
                "personal",
                "--client",
                "my-agent",
                "--scopes",
                "memory:read",
            ]
        )
        == 0
    )
    policy = json.loads(capsys.readouterr().err)
    assert policy == {
        "tools": "slim",
        "scopes": ["memory:read"],
        "read_wings": [],
        "write_wings": [],
        "write_wing": "",
        "trust": "untrusted",
    }


@pytest.mark.parametrize(
    ("override", "environment", "app_environment", "expected"),
    [
        (None, None, None, "http://127.0.0.1:8765"),
        (None, "https://memory.example/", None, "https://memory.example"),
        (None, "https://memory.example", "https://app.example/", "https://app.example"),
        ("https://override.example/", "https://memory.example", None, "https://override.example"),
    ],
)
def test_login_link_real_hub(
    hub, capsys, monkeypatch, override, environment, app_environment, expected
):
    monkeypatch.setitem(sys.modules, "commonplace.urls", None)
    monkeypatch.delenv("COMMONPLACE_APP_URL", raising=False)
    if app_environment:
        monkeypatch.setenv("COMMONPLACE_APP_URL", app_environment)
    if environment is None:
        monkeypatch.delenv("COMMONPLACE_BASE_URL", raising=False)
    else:
        monkeypatch.setenv("COMMONPLACE_BASE_URL", environment)
    args = ["--data", str(hub.supervisor.root.parent), "login-link", "owner@example.com"]
    if override:
        args += ["--base-url", override]
    assert cli.main(args) == 0
    output = capsys.readouterr()
    assert not output.err
    assert output.out.startswith(expected + "/app/login?t=")
    assert output.out.count("\n") == 1
    token = parse_qs(urlsplit(output.out.strip()).query)["t"][0]
    session = sessions.redeem_login_link(hub.db, token)
    principal = sessions.session_principal(hub.db, session)
    assert principal.person and principal.workspace_id == hub.workspace
    assert sessions.redeem_login_link(hub.db, token) is None


def test_login_rejects_nonmember_disabled_and_bad_base(initialized, capsys, monkeypatch):
    monkeypatch.setitem(sys.modules, "commonplace.urls", None)
    monkeypatch.delenv("COMMONPLACE_APP_URL", raising=False)
    monkeypatch.delenv("COMMONPLACE_BASE_URL", raising=False)
    with closing(ControlDB(str(initialized / "control.sqlite3"))) as db:
        outsider = db.create_user("other@example.com")
        db.create_workspace("elsewhere", outsider)
        sessions.ensure_schema(db)
    prefix = ["--data", str(initialized), "login-link"]
    assert cli.main(prefix + ["other@example.com", "--workspace", "personal"]) == 2
    assert cli.main(prefix + ["owner@example.com", "--base-url", "javascript:evil"]) == 2
    with closing(ControlDB(str(initialized / "control.sqlite3"))) as db:
        db._conn.execute("UPDATE users SET disabled_at = '2026-01-01' WHERE is_admin = 1")
    assert cli.main(prefix + ["owner@example.com"]) == 2
    with closing(ControlDB(str(initialized / "control.sqlite3"))) as db:
        assert db._conn.execute("SELECT count(*) FROM login_links").fetchone()[0] == 0
    output = capsys.readouterr()
    assert not output.out and "Traceback" not in output.err


def test_login_link_is_never_a_short_link(initialized, monkeypatch, capsys):
    calls = []
    monkeypatch.setitem(
        sys.modules,
        "commonplace.urls",
        SimpleNamespace(APP_URL="https://app.example", share_link=lambda *a: calls.append(a)),
    )
    prefix = ["--data", str(initialized), "login-link", "owner@example.com"]
    assert cli.main(prefix) == 0
    out = capsys.readouterr().out
    assert out.startswith("https://app.example/app/login?t=")
    assert calls == []
    assert cli.main(prefix + ["--base-url", "https://override.example"]) == 0
    assert capsys.readouterr().out.startswith("https://override.example/app/login?t=")


def test_connect_delegates_without_touching_data(tmp_path, monkeypatch):
    from commonplace import bridge

    calls = []
    monkeypatch.setattr(bridge, "run_bridge", lambda *args: calls.append(args) or 7)
    data = tmp_path / "untouched"
    prefix = ["--data", str(data), "connect", "--url", "https://hub.example/mcp"]
    assert cli.main(prefix) == 7
    assert cli.main(prefix + ["--token-env", "MY_TOKEN"]) == 7
    assert calls == [
        ("https://hub.example/mcp", "COMMONPLACE_TOKEN"),
        ("https://hub.example/mcp", "MY_TOKEN"),
    ]
    assert not data.exists()


def test_seed_uses_local_owner_person_context(initialized, tmp_path, capsys, monkeypatch):
    from commonplace import gateway, seed

    monkeypatch.setattr(gateway, "Gateway", lambda *args: pytest.fail("Gateway constructed"))
    path = tmp_path / "cards.json"
    path.write_text("[]", encoding="utf-8")
    calls = []

    def seed_file(ctx, file):
        assert ctx.principal.person and ctx.principal.role == "owner"
        assert ctx.principal.actor.startswith("user:")
        assert ctx.db.workspace("personal")["id"] == ctx.principal.workspace_id
        assert Path(ctx.workspace_dir()) == initialized / "workspaces" / ctx.principal.workspace_id
        calls.append(file)
        return {"created": 2, "skipped": 1}

    monkeypatch.setattr(seed, "seed_file", seed_file)
    assert cli.main(["--data", str(initialized), "seed", str(path), "--workspace", "personal"]) == 0
    assert calls == [str(path)]
    assert json.loads(capsys.readouterr().out) == {"created": 2, "skipped": 1}


@pytest.mark.parametrize(
    "error",
    [
        ToolError("no: refused"),
        OSError("unreadable"),
        ValueError("bad JSON"),
        sqlite3.OperationalError("busy"),
    ],
)
def test_seed_expected_errors_have_no_traceback(initialized, capsys, monkeypatch, error):
    from commonplace import seed

    def fail(*args):
        raise error

    monkeypatch.setattr(seed, "seed_file", fail)
    assert (
        cli.main(["--data", str(initialized), "seed", "cards.json", "--workspace", "personal"]) == 2
    )
    output = capsys.readouterr()
    assert not output.out and "commonplace:" in output.err and "Traceback" not in output.err


def test_backup_cli_snapshots_control_database(initialized, tmp_path, capsys):
    destination = tmp_path / "backup"
    assert cli.main(["--data", str(initialized), "backup", str(destination)]) == 0
    assert capsys.readouterr().out == str(destination / "manifest.json") + "\n"
    manifest = json.loads((destination / "manifest.json").read_text())
    assert "control.sqlite3" in {entry["path"] for entry in manifest["files"]}
    with closing(ControlDB(str(destination / "control.sqlite3"))) as db:
        assert db.user("owner@example.com")["is_admin"]


def test_commands_lock_before_database_and_serve_holds_lock(initialized, monkeypatch):
    from commonplace import gateway

    original_db = cli._db

    def checked_db(args):
        with pytest.raises(BackupError), data_lock(initialized, exclusive=True):
            pytest.fail("CLI did not hold the data lock before opening the DB")
        return original_db(args)

    class FakeGateway:
        def __init__(self, db, supervisor):
            pass

        def serve(self, host, port):
            with pytest.raises(BackupError), data_lock(initialized, exclusive=True):
                pytest.fail("serve released its data lock")

    monkeypatch.setattr(cli, "_db", checked_db)
    monkeypatch.setattr(gateway, "Gateway", FakeGateway)
    assert cli.main(["--data", str(initialized), "serve"]) == 0
    with data_lock(initialized, exclusive=True):
        pass


def test_legacy_cli_flow(tmp_path, capsys):
    legacy_setup_token_list_revoke_audit(tmp_path, capsys)


def test_legacy_serve_setup(tmp_path, capsys):
    legacy_serve_needs_setup(tmp_path, capsys)
