"""Init must finish without reading noninteractive stdin or changing gitignore."""

import argparse
import json
from unittest.mock import Mock

import pytest

from mempalace.cli import cmd_init
from mempalace.config import DEFAULT_HALL_KEYWORDS, MempalaceConfig


@pytest.fixture
def init_project(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()
    (project / "README.md").write_text("Project reference\n", encoding="utf-8")
    config_dir = tmp_path / "config"
    monkeypatch.setenv("MEMPALACE_CONFIG_DIR", str(config_dir))
    monkeypatch.delenv("MEMPALACE_PALACE_PATH", raising=False)
    monkeypatch.setattr("mempalace.cli._run_pass_zero", lambda **kw: None)
    monkeypatch.setattr(
        "mempalace.project_scanner.discover_entities",
        lambda *a, **kw: {
            "people": [],
            "projects": [{"name": "Example", "confidence": 1.0, "signals": ["manifest"]}],
            "topics": [],
            "uncertain": [],
        },
    )
    monkeypatch.setattr("builtins.input", Mock(side_effect=AssertionError("init prompted")))
    mine = Mock()
    monkeypatch.setattr("mempalace.miner.mine", mine)
    return project, config_dir, mine


@pytest.mark.parametrize("yes,tty", [(True, True), (True, False), (False, False)])
def test_init_never_prompts_in_noninteractive_mode(init_project, monkeypatch, yes, tty):
    project, _, mine = init_project
    monkeypatch.setattr("sys.stdin.isatty", lambda: tty)
    cmd_init(argparse.Namespace(dir=str(project), yes=yes, no_llm=True))
    assert (project / "mempalace.yaml").is_file()
    assert json.loads((project / "entities.json").read_text())["projects"] == ["Example"]
    mine.assert_not_called()


@pytest.mark.parametrize("existing", [False, True])
def test_init_leaves_gitignore_untouched(init_project, monkeypatch, existing, capsys):
    project, _, _ = init_project
    gitignore = project / ".gitignore"
    if existing:
        gitignore.write_bytes(b"# user rules\n*.pyc")
    monkeypatch.setattr("mempalace.cli._maybe_run_mine_after_init", lambda *a: None)
    cmd_init(argparse.Namespace(dir=str(project), yes=True, no_llm=True))
    if existing:
        assert gitignore.read_bytes() == b"# user rules\n*.pyc"
    else:
        assert not gitignore.exists()
    assert "--update-gitignore" in capsys.readouterr().out


def test_init_can_explicitly_update_gitignore(init_project):
    project, _, _ = init_project
    cmd_init(argparse.Namespace(dir=str(project), yes=True, no_llm=True, update_gitignore=True))
    assert (project / ".gitignore").read_text().splitlines()[-2:] == [
        "mempalace.yaml",
        "entities.json",
    ]


def test_noninteractive_init_can_explicitly_mine(init_project, monkeypatch):
    project, _, mine = init_project
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    cmd_init(argparse.Namespace(dir=str(project), yes=False, no_llm=True, auto_mine=True))
    mine.assert_called_once()


@pytest.mark.parametrize("yes,tty", [(True, True), (False, False)])
def test_noninteractive_init_does_not_prompt_for_external_consent(
    init_project, monkeypatch, yes, tty, capsys
):
    project, _, _ = init_project
    provider = Mock(is_external_service=True, api_key_source="env")
    provider.check_available.return_value = (True, "ready")
    monkeypatch.setattr("mempalace.cli.get_provider", lambda **kw: provider)
    monkeypatch.setattr("sys.stdin.isatty", lambda: tty)
    consent = Mock(return_value=False)
    monkeypatch.setattr("mempalace.llm_client.require_external_consent", consent)
    cmd_init(argparse.Namespace(dir=str(project), yes=yes, no_llm=False, llm_provider="anthropic"))
    assert consent.call_args.kwargs["interactive"] is False
    assert "accept" not in consent.call_args.kwargs
    out = capsys.readouterr().out
    assert "Not asking in non-interactive mode" in out
    assert "to skip this prompt" not in out


def test_new_config_uses_neutral_halls(tmp_path):
    cfg = MempalaceConfig(config_dir=str(tmp_path))
    cfg.init()
    assert set(cfg.hall_keywords) == {"technical", "planning", "reference", "creative"}
    assert MempalaceConfig(config_dir=str(tmp_path)).hall_keywords == cfg.hall_keywords


@pytest.mark.parametrize("saved", [{}, {"hall_keywords": {"family": ["children"]}}])
def test_existing_config_keeps_hall_classification(tmp_path, saved):
    path = tmp_path / "config.json"
    original = json.dumps(saved)
    path.write_text(original, encoding="utf-8")
    cfg = MempalaceConfig(config_dir=str(tmp_path))
    cfg.init()
    assert path.read_text(encoding="utf-8") == original
    assert cfg.hall_keywords == saved.get("hall_keywords", DEFAULT_HALL_KEYWORDS)


@pytest.mark.parametrize("entries", ["mempalace.yaml\nentities.json\n", "mempalace.yaml\n"])
def test_init_gitignore_hint_only_for_missing_entries(init_project, capsys, entries):
    project, _, _ = init_project
    (project / ".gitignore").write_text(entries, encoding="utf-8")
    cmd_init(argparse.Namespace(dir=str(project), yes=True, no_llm=True))
    assert ("--update-gitignore" in capsys.readouterr().out) == ("entities.json" not in entries)
    assert (project / ".gitignore").read_text(encoding="utf-8") == entries


def test_init_checks_interactivity_once(init_project, monkeypatch):
    project, _, mine = init_project
    isatty = Mock(side_effect=[False, AssertionError("interactivity checked twice")])
    monkeypatch.setattr("sys.stdin.isatty", isatty)
    cmd_init(argparse.Namespace(dir=str(project), yes=False, no_llm=True))
    isatty.assert_called_once()
    mine.assert_not_called()


@pytest.mark.parametrize("path_source", ["init", "constructor", "env"])
def test_existing_palace_without_config_keeps_legacy_halls(tmp_path, monkeypatch, path_source):
    import chromadb

    palace = tmp_path / "existing-palace"
    client = chromadb.PersistentClient(path=str(palace))
    client.get_or_create_collection("mempalace_drawers").add(
        ids=["old-drawer"], documents=["Original words"], embeddings=[[1.0, 0.0]]
    )
    config_dir = tmp_path / "config"
    if path_source == "env":
        monkeypatch.setenv("MEMPALACE_PALACE_PATH", str(palace))
    cfg = MempalaceConfig(
        config_dir=str(config_dir),
        palace_path=str(palace) if path_source == "constructor" else None,
    )
    assert not (config_dir / "config.json").exists()
    cfg.init(palace_path=str(palace) if path_source == "init" else None)
    assert cfg.hall_keywords == DEFAULT_HALL_KEYWORDS
    assert MempalaceConfig(config_dir=str(config_dir)).hall_keywords == DEFAULT_HALL_KEYWORDS
    assert client.get_collection("mempalace_drawers").get()["documents"] == ["Original words"]


def test_empty_palace_without_config_uses_neutral_halls(tmp_path):
    palace = tmp_path / "palace"
    palace.mkdir()
    cfg = MempalaceConfig(config_dir=str(tmp_path / "config"), palace_path=str(palace))
    cfg.init()
    assert set(cfg.hall_keywords) == {"technical", "planning", "reference", "creative"}
