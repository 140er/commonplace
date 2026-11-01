"""Native installer acceptance checks, isolated from real client configuration."""

import base64
import copy
import json
import re
import shlex
import stat

import pytest

from commonplace import client_install as install


@pytest.fixture(autouse=True)
def isolated_url(monkeypatch):
    monkeypatch.delenv("COMMONPLACE_URL", raising=False)


def _arguments(handler, client):
    if client == "claude":
        return [handler["command"], *handler["args"]]
    command = handler["command"]
    if command.startswith(install._WINDOWS_COMMAND):
        script = base64.b64decode(command.split()[-1]).decode("utf-16-le")
        return [value.replace("''", "'") for value in re.findall(r"'((?:[^']|'')*)'", script)]
    return shlex.split(command)


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")


def _snapshot(home):
    return {path.relative_to(home): path.read_bytes() for path in home.rglob("*") if path.is_file()}


def test_fresh_install_native_formats_env_credentials_and_idempotency(tmp_path, monkeypatch):
    home = tmp_path / "home"
    project = tmp_path / "project"
    monkeypatch.setenv("COMMONPLACE_TOKEN", "never-persist-this-token")
    result = install.install_client_config(home=home, project=project)
    assert len(result["files"]) == 4
    assert {record["status"] for record in result["files"]} == {"created"}
    for client, relative in (("claude", ".claude/settings.json"), ("codex", ".codex/hooks.json")):
        config = json.loads((home / relative).read_text())
        assert set(config) == {"hooks"}
        assert set(config["hooks"]) == {"SessionStart", "PostToolUse", "Stop"}
        for event, groups in config["hooks"].items():
            assert len(groups) == 1
            assert set(groups[0]) == {"hooks"}
            handler = groups[0]["hooks"][0]
            assert handler["type"] == "command" and handler["timeout"] == 2
            arguments = _arguments(handler, client)
            assert arguments[1:4] == ["-I", "-m", "commonplace.client_hooks"]
            assert arguments[arguments.index("--client") + 1] == client
            assert arguments[arguments.index("--event") + 1] == (
                "session-start" if event == "SessionStart" else "check-local"
            )
            assert arguments[arguments.index("--project") + 1] == str(project)
    claude = json.loads((home / ".claude.json").read_text())["mcpServers"]["commonplace"]
    assert claude == {
        "type": "http",
        "url": "https://app.cmmn.dev/mcp",
        "headers": {"Authorization": "Bearer ${COMMONPLACE_TOKEN}"},
    }
    codex = install.tomllib.loads((home / ".codex/config.toml").read_text())
    assert codex == {
        "mcp_servers": {
            "commonplace": {
                "url": "https://app.cmmn.dev/mcp",
                "bearer_token_env_var": "COMMONPLACE_TOKEN",
            }
        }
    }
    before = _snapshot(home)
    rerun = install.install_client_config(home=home, project=project)
    assert {record["status"] for record in rerun["files"]} == {"unchanged"}
    assert _snapshot(home) == before
    assert not project.exists()
    assert all(b"never-persist-this-token" not in raw for raw in before.values())
    assert result["warnings"] == []
    assert "trust" in " ".join(result["notes"])


def test_preserves_native_security_other_settings_hooks_and_toml_comments(tmp_path):
    home = tmp_path / "home"
    user_hook = {
        "matcher": "Edit",
        "hooks": [{"type": "command", "command": "audit", "timeout": 4}],
    }
    claude_settings = {
        "permissions": {"deny": ["Bash(rm:*)"]},
        "disableAllHooks": False,
        "hooks": {
            "PostToolUse": [copy.deepcopy(user_hook)],
            "PreToolUse": [copy.deepcopy(user_hook)],
        },
    }
    codex_hooks = {"description": "Custom checks", "hooks": {"Stop": [copy.deepcopy(user_hook)]}}
    claude_connection = {
        "projects": {"example": {"hasTrustDialogAccepted": False}},
        "mcpServers": {
            "other": {"command": "example"},
            "commonplace": {
                "type": "http",
                "url": "https://old.example/mcp",
                "headers": {"authorization": "old-value", "X-Custom": "value"},
                "timeout": 1500,
            },
        },
    }
    toml = '# keep this comment\napproval_policy = "on-request"\nsandbox_mode = "read-only"\n[features]\nhooks = false\n[mcp_servers.commonplace]\nurl = "https://old.example/mcp"\nbearer_token_env_var = "OLD_TOKEN"\nenabled = false\ndisabled_tools = ["memory_save"]\n[mcp_servers.other]\ncommand = "other"\n[projects."example"]\ntrust_level = "untrusted"\n'
    _write(home / ".claude/settings.json", claude_settings)
    _write(home / ".codex/hooks.json", codex_hooks)
    _write(home / ".claude.json", claude_connection)
    _write(home / ".codex/config.toml", toml)
    result = install.install_client_config(
        home=home, url="http://localhost:9000/custom", token_env="WORKSPACE_TOKEN"
    )
    assert {record["status"] for record in result["files"]} == {"updated"}
    settings = json.loads((home / ".claude/settings.json").read_text())
    assert settings["permissions"] == claude_settings["permissions"]
    assert settings["disableAllHooks"] is False
    assert settings["hooks"]["PostToolUse"][0] == user_hook
    assert settings["hooks"]["PreToolUse"] == [user_hook]
    assert json.loads((home / ".codex/hooks.json").read_text())["hooks"]["Stop"][0] == user_hook
    connection = json.loads((home / ".claude.json").read_text())
    assert connection["projects"] == claude_connection["projects"]
    assert connection["mcpServers"]["other"] == {"command": "example"}
    assert connection["mcpServers"]["commonplace"]["headers"] == {
        "X-Custom": "value",
        "Authorization": "Bearer ${WORKSPACE_TOKEN}",
    }
    expected = install.tomllib.loads(toml)
    expected["mcp_servers"]["commonplace"].update(
        url="http://localhost:9000/custom", bearer_token_env_var="WORKSPACE_TOKEN"
    )
    actual_toml = (home / ".codex/config.toml").read_text()
    assert install.tomllib.loads(actual_toml) == expected
    assert actual_toml.startswith("# keep this comment\n")


def test_endpoint_change_updates_existing_hooks_without_duplicates(tmp_path):
    install.install_client_config(home=tmp_path, url="https://first.example")
    result = install.install_client_config(
        home=tmp_path, url="https://second.example/mcp", token_env="NEW_TOKEN"
    )
    assert {record["status"] for record in result["files"]} == {"updated"}
    for relative in (".claude/settings.json", ".codex/hooks.json"):
        config = json.loads((tmp_path / relative).read_text())
        assert all(
            len(groups) == 1 and len(groups[0]["hooks"]) == 1 for groups in config["hooks"].values()
        )
        assert "first.example" not in json.dumps(config)


def test_dry_run_never_creates_or_changes_files(tmp_path):
    home = tmp_path / "empty"
    result = install.install_client_config(home=home, dry_run=True)
    assert len(result["files"]) == 4 and not home.exists()
    _write(home / ".claude/settings.json", {"permissions": {"deny": ["*"]}})
    before = _snapshot(home)
    install.install_client_config(home=home, dry_run=True)
    assert _snapshot(home) == before


@pytest.mark.parametrize(
    "relative,contents",
    [
        (".claude/settings.json", '{"hooks": {"Stop": [{}]}}'),
        (".claude/settings.json", '{"permissions": {}, "permissions": {"deny": ["*"]}}'),
        (".codex/hooks.json", '{"hooks": {"Stop": "bad"}}'),
        (".claude.json", '{"mcpServers": []}'),
        (".codex/config.toml", "[broken"),
        (".codex/config.toml", 'mcp_servers = {commonplace = {url = "https://old.example"}}\n'),
        (".codex/config.toml", '[mcp_servers.commonplace]\ncommand = "existing-server"\n'),
        (
            ".codex/config.toml",
            '[mcp_servers.commonplace.http_headers]\nAuthorization = "existing-bearer"\n',
        ),
    ],
)
def test_malformed_or_conflicting_config_is_preserved(tmp_path, relative, contents):
    path = tmp_path / relative
    _write(path, contents)
    result = install.install_client_config(home=tmp_path)
    record = next(record for record in result["files"] if record["path"] == str(path))
    assert record["status"] == "refused"
    assert path.read_text() == contents
    assert "existing-bearer" not in record.get("reason", "")


@pytest.mark.parametrize("level", ["file", "directory", "home"])
def test_symlink_targets_and_ancestors_are_refused(tmp_path, level):
    home = tmp_path / "home"
    outside = tmp_path / "outside"
    outside.mkdir()
    original = outside / "settings.json"
    original.write_text('{"permissions": {"deny": ["*"]}}')
    try:
        if level == "home":
            home.symlink_to(outside, target_is_directory=True)
        elif level == "directory":
            home.mkdir()
            (home / ".claude").symlink_to(outside, target_is_directory=True)
        else:
            (home / ".claude").mkdir(parents=True)
            (home / ".claude/settings.json").symlink_to(original)
    except OSError:
        pytest.skip("Symlink creation unavailable")
    before = _snapshot(outside)
    result = install.install_client_config(home=home)
    assert any(record["status"] == "refused" for record in result["files"])
    assert _snapshot(outside) == before


def test_atomic_write_failure_keeps_original(tmp_path, monkeypatch):
    path = tmp_path / ".claude/settings.json"
    _write(path, {"permissions": {"deny": ["*"]}})
    before = path.read_bytes()

    def refuse(_source, _target):
        raise PermissionError("unreported private details")

    monkeypatch.setattr("mempalace.fsio.os.replace", refuse)
    result = install.install_client_config(home=tmp_path)
    assert all(record["status"] == "refused" for record in result["files"])
    assert path.read_bytes() == before
    assert list(path.parent.iterdir()) == [path]
    assert "unreported private details" not in json.dumps(result)


@pytest.mark.parametrize(
    "url",
    [
        "https://user:secret@example.com/mcp",
        "https://example.com/mcp#private",
        "https://example.com/mcp?token=secret",
        "https://example.com:bad",
        "file:///tmp/server",
        "https://example.com/${TOKEN}",
        "https://example.com/\n",
    ],
)
def test_invalid_urls_are_sanitized_without_writes(tmp_path, url):
    home = tmp_path / "home"
    result = install.install_client_config(home=home, url=url)
    assert result["files"] == [] and result["warnings"]
    assert "secret" not in json.dumps(result)
    assert not home.exists()


def test_installer_never_reads_token_environment_value(tmp_path, monkeypatch):
    original = install.os.environ.get

    def get(name, *args):
        assert name != "CUSTOM_TOKEN"
        return original(name, *args)

    monkeypatch.setattr(install.os.environ, "get", get)
    result = install.install_client_config(home=tmp_path, token_env="CUSTOM_TOKEN")
    assert all(record["status"] == "created" for record in result["files"])


def test_executable_and_project_paths_with_shell_characters_are_literal(tmp_path, monkeypatch):
    executable = str(tmp_path / "Python tools $value `literal` 'quoted'" / "python")
    project = tmp_path / "project $value `literal` 'quoted'"
    monkeypatch.setattr(install.sys, "executable", executable)
    result = install.install_client_config(home=tmp_path / "home", project=project)
    assert all(record["status"] == "created" for record in result["files"])
    claude = json.loads((tmp_path / "home/.claude/settings.json").read_text())["hooks"][
        "SessionStart"
    ][0]["hooks"][0]
    codex = json.loads((tmp_path / "home/.codex/hooks.json").read_text())["hooks"]["SessionStart"][
        0
    ]["hooks"][0]
    assert claude["command"] == executable
    assert str(project) in claude["args"]
    assert _arguments(codex, "codex")[0] == executable
    assert str(project) in _arguments(codex, "codex")
    windows_exe = r"C:\Python tools $value `literal`\python.exe"
    encoded = install._windows_command(
        [windows_exe, "-m", "commonplace.client_hooks", str(project)]
    )
    script = base64.b64decode(encoded.split()[-1]).decode("utf-16-le")
    assert script.startswith("& '" + windows_exe + "'")
    assert "'" + str(project).replace("'", "''") + "'" in script


def test_new_configs_are_private_and_no_project_is_explicit(tmp_path):
    install.install_client_config(home=tmp_path)
    handler = json.loads((tmp_path / ".claude/settings.json").read_text())["hooks"]["Stop"][0][
        "hooks"
    ][0]
    assert "--no-project" in handler["args"] and "--project" not in handler["args"]
    if install.os.name != "nt":
        assert stat.S_IMODE((tmp_path / ".claude.json").stat().st_mode) == 0o600


def test_toml_unusual_multiline_values_cannot_change_unrelated_settings(tmp_path):
    contents = (
        '[mcp_servers.commonplace]\nurl = """https://old.example\n[features]\nhooks = false\n"""\n'
    )
    path = tmp_path / ".codex/config.toml"
    _write(path, contents)
    result = install.install_client_config(home=tmp_path)
    assert (
        next(record for record in result["files"] if record["path"] == str(path))["status"]
        == "refused"
    )
    assert path.read_text() == contents
