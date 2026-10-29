"""Codex hook format and read-only trust diagnostics, isolated from user config."""

import argparse
import json
import re
from pathlib import Path
from unittest.mock import Mock

import pytest

from mempalace.codex_hooks import codex_hook_notice

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def project(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    root = tmp_path / "project"
    (root / ".git").mkdir(parents=True)
    (root / ".codex").mkdir()
    return root


def _hooks(project):
    (project / ".codex" / "hooks.json").write_text('{"hooks": {"Stop": []}}', encoding="utf-8")


def _trust(directory, level="trusted", config_home=None):
    home = config_home or Path.home() / ".codex"
    home.mkdir(parents=True, exist_ok=True)
    path = home / "config.toml"
    path.write_text(
        f'[projects.{json.dumps(str(directory))}]\ntrust_level = "{level}"\n', encoding="utf-8"
    )
    return path


def test_no_hooks_does_not_read_or_create_global_config(project):
    assert codex_hook_notice(project) is None
    assert not (Path.home() / ".codex").exists()


@pytest.mark.parametrize("level", [None, "untrusted", "trusted"])
def test_project_trust_diagnostic_never_changes_files(project, level):
    _hooks(project)
    path = _trust(project, level) if level else None
    before = path.read_bytes() if path else None
    notice = codex_hook_notice(project)
    if level == "trusted":
        assert notice is None
    else:
        assert "project-local hooks will be skipped" in notice
        assert str(Path.home() / ".codex" / "config.toml") in notice
        assert "/hooks" in notice
    if path:
        assert path.read_bytes() == before
    else:
        assert not (Path.home() / ".codex").exists()


def test_status_checks_repository_hooks_from_subdirectory(project):
    _hooks(project)
    subdir = project / "src"
    subdir.mkdir()
    assert "will be skipped" in codex_hook_notice(subdir)
    _trust(project)
    assert codex_hook_notice(subdir) is None


def test_explicit_untrusted_subdirectory_overrides_trusted_root(project):
    _hooks(project)
    subdir = project / "src"
    subdir.mkdir()
    path = _trust(project)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(f'[projects.{json.dumps(str(subdir))}]\ntrust_level = "untrusted"\n')
    assert "will be skipped" in codex_hook_notice(subdir)


@pytest.mark.parametrize(
    "contents,has_hooks",
    [
        ('[[hooks.Stop]]\n[[hooks.Stop.hooks]]\ntype="command"\ncommand="echo ok"\n', True),
        ('[mcp_servers.mempalace]\ncommand="mempalace-mcp"\n', False),
    ],
)
def test_detects_inline_hooks_without_warning_for_mcp_only_config(project, contents, has_hooks):
    (project / ".codex" / "config.toml").write_text(contents, encoding="utf-8")
    assert bool(codex_hook_notice(project)) is has_hooks


def test_custom_codex_home_is_read_only(project, tmp_path, monkeypatch):
    _hooks(project)
    config_home = tmp_path / "codex-home"
    path = _trust(project, config_home=config_home)
    before = path.read_bytes()
    monkeypatch.setenv("CODEX_HOME", str(config_home))
    assert codex_hook_notice(project) is None
    assert path.read_bytes() == before
    assert not (Path.home() / ".codex").exists()


def test_invalid_global_config_reports_unknown_trust(project):
    _hooks(project)
    path = _trust(project)
    path.write_text("[broken", encoding="utf-8")
    assert "Cannot check Codex project trust" in codex_hook_notice(project)
    assert path.read_text(encoding="utf-8") == "[broken"


def test_status_prints_project_hook_warning(project, monkeypatch, capsys):
    from mempalace.cli import cmd_status

    _hooks(project)
    monkeypatch.chdir(project)
    monkeypatch.setattr("mempalace.miner.status", Mock())
    monkeypatch.setattr("mempalace.knowledge_graph.knowledge_graph_notice", lambda *a, **kw: None)
    cmd_status(argparse.Namespace(palace=str(project / "palace")))
    assert "project-local hooks will be skipped" in capsys.readouterr().out


def _assert_nested(config):
    for groups in config["hooks"].values():
        for group in groups:
            assert "command" not in group
            assert group["hooks"]
            for handler in group["hooks"]:
                assert handler["type"] == "command"
                assert handler["command"]


def test_plugin_hook_config_already_uses_nested_format():
    _assert_nested(json.loads((REPO_ROOT / ".codex-plugin" / "hooks.json").read_text()))


def test_codex_guide_config_uses_all_four_events_with_nested_handlers():
    guide = (REPO_ROOT / "website" / "guide" / "hooks.md").read_text(encoding="utf-8")
    section = guide.split("## Install — Codex CLI", 1)[1].split("## Configuration", 1)[0]
    example = re.search(r"```json\n(.*?)\n```", section, re.DOTALL)
    config = json.loads(example.group(1))
    assert set(config["hooks"]) == {"SessionStart", "Stop", "SessionEnd", "PreCompact"}
    _assert_nested(config)
    for event, groups in config["hooks"].items():
        handler = groups[0]["hooks"][0]
        assert "mempalace hook run --harness codex --hook " in handler["command"]
        assert handler["timeout"] > 0
        if event == "SessionEnd":
            assert handler["timeout"] <= 3


def test_codex_guide_toml_and_invocation_match_nested_json_schema():
    import shlex

    from mempalace.codex_hooks import tomllib

    guide = (REPO_ROOT / "website" / "guide" / "hooks.md").read_text(encoding="utf-8")
    section = guide.split("## Install — Codex CLI", 1)[1].split("## Configuration", 1)[0]
    inline = re.search(r"```toml\n(.*?)\n```", section, re.DOTALL)
    _assert_nested(tomllib.loads(inline.group(1)))
    invocation = re.search(r"```bash\n(.*?)\n```", section, re.DOTALL)
    arguments = shlex.split(invocation.group(1).replace("\\\n", ""))
    events = set()
    for index, argument in enumerate(arguments):
        if argument == "-c":
            config = tomllib.loads(arguments[index + 1])
            _assert_nested(config)
            events.update(config["hooks"])
    assert events == {"SessionStart", "Stop", "SessionEnd", "PreCompact"}
