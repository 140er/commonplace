"""Keep the Commonplace agent instructions usable and within their budgets."""

import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "commonplace/agent-skill/SKILL.md"
SNIPPET = ROOT / "docs/mvp/AGENTS-SNIPPET.md"
INSTALLER = ROOT / "commonplace/agent-skill/install.sh"
CONTRACT = ROOT / "docs/mvp/CONTRACT.md"


def test_frontmatter_and_size():
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    front, body = text[4:].split("\n---\n", 1)
    fields = dict(line.split(": ", 1) for line in front.splitlines())
    assert fields["name"] == "commonplace"
    description = fields["description"].strip('"')
    assert 0 < len(description) < 300
    assert len(text.splitlines()) < 120
    for phrase in ("picking up work", "reporting status", "remembering", "asking the user"):
        assert phrase in description
    assert body.strip()


def _contract_tools() -> set:
    contract = CONTRACT.read_text(encoding="utf-8")
    sections = contract.split("## Boards (W1)", 1)[1].split("## Webhooks (W3)", 1)[0]
    rules = (ROOT / "docs/design/RULES.md").read_text(encoding="utf-8")
    return set(re.findall(r"^\| `([a-z][a-z0-9_]*)` \|", sections + rules, re.MULTILINE))


_TOOLISH = re.compile(r"\b(?:task|board|memory|needs|rules|ask|decide)(?:_[a-z]+)*\b")
_ARGS = {"status_line", "rules_seq"}


def test_tool_references_match_contract():
    tools = _contract_tools()
    assert {"task_take_next", "memory_save", "ask", "needs_me"} <= tools
    for path in (SKILL, SNIPPET):
        text = path.read_text(encoding="utf-8")
        named = {w for w in _TOOLISH.findall(text) if "_" in w}
        assert named - _ARGS <= tools, named - _ARGS - tools
    skill = SKILL.read_text(encoding="utf-8")
    for tool in (
        "task_list",
        "task_take_next",
        "task_update",
        "memory_search",
        "memory_save",
        "ask",
    ):
        assert f"`{tool}" in skill, tool


def test_snippet_budgets_and_slim_tools():
    text = SNIPPET.read_text(encoding="utf-8")
    blocks = re.findall(r"```text\n(.*?)\n```", text, re.DOTALL)
    assert len(blocks) == 2
    assert len(blocks[0].splitlines()) <= 15
    assert len(blocks[1].splitlines()) <= 10
    grok = {w for w in _TOOLISH.findall(blocks[1]) if "_" in w or w == "ask"}
    assert grok <= {"memory_search", "memory_save", "task_add", "task_comment", "ask"}, grok


def test_no_user_facing_jargon():
    for path in (SKILL, SNIPPET, INSTALLER):
        assert not re.search(r"\b(?:palace|wing|room|drawer|closet)s?\b", path.read_text(), re.I)


def test_installer_dry_run_is_non_mutating_and_safe(tmp_path):
    env = {**os.environ, "HOME": str(tmp_path), "COMMONPLACE_TOKEN": "secret-test-token"}
    env.pop("COMMONPLACE_URL", None)
    result = subprocess.run(
        [str(INSTALLER), "--dry-run"], env=env, text=True, capture_output=True, check=True
    )
    assert list(tmp_path.iterdir()) == []
    for root in (".claude", ".codex", ".agents"):
        assert f"{root}/skills/commonplace/SKILL.md" in result.stdout
    assert 'export COMMONPLACE_URL="${COMMONPLACE_URL:-https://cmmn.dev}"' in result.stdout
    assert '"$COMMONPLACE_URL/mcp"' in result.stdout
    assert '"Authorization: Bearer $COMMONPLACE_TOKEN"' in result.stdout
    assert 'bearer_token_env_var = "COMMONPLACE_TOKEN"' in result.stdout
    assert "secret-test-token" not in result.stdout
    bad = subprocess.run([str(INSTALLER), "--unknown"], env=env, capture_output=True)
    assert bad.returncode != 0
    assert list(tmp_path.iterdir()) == []


def test_documented_url_defaults_overrides_and_oauth(tmp_path):
    skill = SKILL.read_text(encoding="utf-8")
    snippet = SNIPPET.read_text(encoding="utf-8")
    assert "$COMMONPLACE_URL/mcp" in skill
    assert "https://cmmn.dev" in skill
    assert "`https://cmmn.dev/mcp` as a custom connector" in snippet
    assert "OAuth" in snippet
    env = {**os.environ, "HOME": str(tmp_path)}
    env.pop("COMMONPLACE_URL", None)
    output = subprocess.run(
        [str(INSTALLER), "--dry-run"], env=env, text=True, capture_output=True, check=True
    ).stdout
    export = next(
        line for line in output.splitlines() if line.startswith("export COMMONPLACE_URL=")
    )
    for override, expected in (
        (None, "https://cmmn.dev"),
        ("https://example.test", "https://example.test"),
    ):
        test_env = env.copy()
        if override is not None:
            test_env["COMMONPLACE_URL"] = override
        result = subprocess.run(
            ["sh", "-c", export + '\nprintf "%s" "$COMMONPLACE_URL/mcp"'],
            env=test_env,
            text=True,
            capture_output=True,
            check=True,
        )
        assert result.stdout == expected + "/mcp"
    assert list(tmp_path.iterdir()) == []


def test_rules_session_loop():
    text = SKILL.read_text(encoding="utf-8").split("## Rules\n", 1)[1].split("\n## ", 1)[0]
    for phrase in (
        "rules_changed since=<last seq>",
        "rules_get format=md effective=true",
        "rules_seq",
        "Commonplace-Rules-Seq",
        "call `ask` without one",
        "text",
        "owner edits win",
        "retired",
        "rules_publish",
        "replace=true",
    ):
        assert phrase in text
