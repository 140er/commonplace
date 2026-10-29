"""Recall triggers on its own in every harness; no command is ever needed.

The skill description is what an agent sees in its skill list every
session, so it alone decides whether recall fires. These tests pin the
trigger wording, the repo fallback, the copies that must not drift, and
the size budget (the description costs tokens in every session).
"""

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
    CODEX_SKILL,
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


def test_recall_description_stays_within_its_token_budget():
    """Listed in every session: no longer than the 410 chars it replaced."""
    for path in SKILLS:
        assert len(_description(path)) <= 410, path


def test_codex_plugin_ships_the_canonical_recall_skill():
    """Codex lists plugin skills by name and description; the body is the canonical one."""
    front, text = _frontmatter(CODEX_SKILL)
    assert re.search(r"^name: recall$", front, re.MULTILINE), "name must match its directory"
    canonical = CANONICAL.read_text(encoding="utf-8")
    assert text == canonical.replace("name: mempalace-recall\n", "name: recall\n", 1)


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
        assert len(text) <= 1200, (path, len(text))
