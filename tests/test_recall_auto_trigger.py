"""Recall triggers on its own in every harness; no command is ever needed.

The skill description is what an agent sees in its skill list every
session, so it alone decides whether recall fires. These tests pin the
trigger wording, the repo fallback, the copies that must not drift, and
the size budget (the description costs tokens in every session).
"""

import json
import re
from pathlib import Path

from mempalace.instructions_cli import AVAILABLE, INSTRUCTIONS_DIR

REPO = Path(__file__).resolve().parent.parent
CANONICAL = REPO / "skills" / "mempalace-recall" / "SKILL.md"
CODEX_SKILL = REPO / ".codex-plugin" / "skills" / "recall" / "SKILL.md"
RULES = [
    REPO / "rules" / "mempalace-recall.mdc",
    REPO / "examples" / "cursor" / "rules" / "mempalace-recall.mdc",
    REPO / "examples" / "cursor" / "rules" / "mempalace-recall-always.mdc",
    REPO / ".antigravity-plugin" / "rules" / "mempalace-recall.md",
]
SKILLS = [
    CANONICAL,
    REPO / ".claude-plugin" / "skills" / "mempalace-recall" / "SKILL.md",
    REPO / ".antigravity-plugin" / "skills" / "mempalace-recall" / "SKILL.md",
]


def _frontmatter(path):
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), path
    return text[4 : text.index("\n---\n", 4)], text


def _description(path):
    front, _ = _frontmatter(path)
    return re.search(r"^description: (.*)$", front, re.MULTILINE).group(1)


def test_every_recall_skill_triggers_on_warning_questions_by_itself():
    for path in SKILLS:
        desc = _description(path)
        for needle in ("anything I should know", "safe way to do Y", "don't know"):
            assert needle in desc, (path, needle)
        # Firing must never depend on the user typing a command.
        for command in ("$mempalace", "/mempalace", "invoke", "when mempalace-recall is"):
            assert command not in desc, (path, command)


# Recall text is loaded into sessions, so it may only shrink. Budgets are the
# sizes in characters when this test was written; lower them when text shrinks.
DESCRIPTION_BUDGET = 366
FILE_BUDGETS = {
    "skills/mempalace-recall/SKILL.md": 4781,
    ".antigravity-plugin/skills/mempalace-recall/SKILL.md": 4918,
    "rules/mempalace-recall.mdc": 1113,
    "examples/cursor/rules/mempalace-recall.mdc": 1008,
    "examples/cursor/rules/mempalace-recall-always.mdc": 959,
    ".antigravity-plugin/rules/mempalace-recall.md": 835,
}


def test_recall_text_stays_within_its_token_budget():
    for path in SKILLS:
        assert len(_description(path)) <= DESCRIPTION_BUDGET, path
    for rel, budget in FILE_BUDGETS.items():
        size = len((REPO / rel).read_text(encoding="utf-8"))
        assert size <= budget, (rel, size, budget)


def test_codex_plugin_loads_the_recall_skill_from_the_repo_root():
    """How Codex recalls with no command: its marketplace entry points the
    plugin at the repo root, and the manifest loads ``./skills/`` from there,
    so ``skills/mempalace-recall`` sits in the skill list of every session.
    """
    market = json.loads(
        (REPO / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8")
    )
    (entry,) = [p for p in market["plugins"] if p["name"] == "mempalace"]
    assert entry["source"]["path"] == "./"
    manifest = json.loads((REPO / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["skills"] == "./skills/"
    assert CANONICAL.is_file()


def test_the_recall_commands_are_optional_extras():
    """The commands only force recall; none of them is the trigger.

    Codex 0.163 does not read .codex-plugin/skills (it builds command skills
    from the root commands/); the recall wrapper there keeps the Codex
    command set equal to the other plugins' for loaders that do.
    """
    for path in (
        CODEX_SKILL,
        REPO / ".claude-plugin" / "commands" / "recall.md",
        REPO / "commands" / "mempalace-recall.md",
    ):
        assert _description(path).startswith("Optional."), path
    assert "mempalace instructions recall" in CODEX_SKILL.read_text(encoding="utf-8")


def test_recall_instructions_are_the_skill_body():
    assert "recall" in AVAILABLE
    canonical = CANONICAL.read_text(encoding="utf-8")
    body = canonical[canonical.index("\n---\n", 4) + 5 :].lstrip("\n")
    assert (INSTRUCTIONS_DIR / "recall.md").read_text(encoding="utf-8") == body


def test_recall_rules_fall_back_to_the_repo_and_stay_short():
    for path in RULES:
        text = path.read_text(encoding="utf-8")
        assert "anything I should know" in text, path
        assert "answer from the repo" in text, path
        assert "do not fall back to guessing" not in text, path
