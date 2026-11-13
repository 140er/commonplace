"""User documentation names the product role consistently; protocol examples stay valid."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ROLE = re.compile(r"\b(?:orchestrators?|orchestration)\b", re.I)
INTERNAL_DOCS = {
    "format-coverage.md",  # Unrelated miner/test implementation coverage, not the product role.
    "modules",  # Implementation ownership and historical verification notes.
    "system",  # Internal architecture decisions and historical compatibility notes.
    "design",  # Internal proposals, audits and decision records.
    "compute",  # Internal execution contracts and build planning.
    "mvp/CONTRACT.md",  # Historical implementation contract, not current user instructions.
}


def visible_matches(text):
    # Exact schema kinds and historical handoff aliases stay valid in code examples.
    text = re.sub(
        r"`(?:orchestrator|sub-orchestrator|orchestration|orchestrators)`",
        lambda m: " " * len(m[0]),
        text,
    )
    # Link destinations are stable paths; their human-readable labels are still scanned.
    text = re.sub(r"\]\([^)]*\)", lambda m: " " * len(m[0]), text)
    # Only the literal role kind in a fenced TOML example is a protocol value.
    text = re.sub(
        r"(?ms)^```toml\s*\n.*?^```",
        lambda block: re.sub(
            r'(?m)^(\s*kind\s*=\s*")(?:orchestrator|sub-orchestrator)("\s*)$',
            lambda line: " " * len(line[0]),
            block[0],
        ),
        text,
    )
    found = []
    for match in ROLE.finditer(text):
        a, b = match.span()
        # Paths, config keys, tool names and enum values remain unchanged.
        if (
            text[a - 1 : a] in ("/", ".", "_", "-", ":")
            or text[b : b + 1] in ("/", "_", "-", ":")
            or re.match(r"\.\w", text[b:])
        ):
            continue
        found.append(match)
    return found


def test_user_docs_role_copy():
    failures = []
    for path in sorted((ROOT / "docs").rglob("*.md")):
        name = path.relative_to(ROOT / "docs").as_posix()
        if any(name == prefix or name.startswith(prefix + "/") for prefix in INTERNAL_DOCS):
            continue
        text = path.read_text()
        for match in visible_matches(text):
            failures.append(
                f"docs/{name}:{text.count(chr(10), 0, match.start()) + 1}: {match.group()}"
            )
    assert not failures, "Use Coworker in user docs:\n" + "\n".join(failures)


@pytest.mark.parametrize(
    "text",
    [
        'kind = "orchestrator"',
        '```text\nkind = "orchestrator"\n```',
        '```toml\ntopic = "ask an orchestrator"\n```',
        "Ask the Orchestrator.",
        "ORCHESTRATORS use subagents.",
        "Enable orchestration.",
        "`Released by the orchestrator`",
        "```\nOrchestrators assign cards.\n```",
    ],
)
def test_guard_catches_prose_including_instruction_examples(text):
    assert visible_matches(text)


def test_guard_keeps_protocol_examples():
    assert not visible_matches(
        '`orchestrator_inbox(board)` `orchestrator` `/api/settings/orchestration`\n```toml\nkind = "orchestrator"\n```\n[setup](orchestrator.md)'
    )
