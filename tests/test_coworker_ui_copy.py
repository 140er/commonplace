"""Visible role labels stay consistent across classic and Deck screens."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ROLE = re.compile(r"\b(?:orchestrators?|orchestration)\b", re.I)
# Exact wire literals: the conflict discriminator, event mark type and pagination key.
WIRE = {
    "projects.js": {"replaces current orchestrator"},  # Older Connect clients use this code.
    "bots.js": {"orchestrator"},  # Existing event mark type.
    "admin.js": {"orchestrators"},  # Existing pagination source.
}


def js_literals(text):
    """Read quoted text and template chunks, skipping code, comments and interpolations."""

    def code(i, nested=False):
        depth = 0
        while i < len(text):
            if text.startswith("//", i):
                end = text.find("\n", i)
                i = len(text) if end < 0 else end + 1
            elif text.startswith("/*", i):
                end = text.find("*/", i + 2)
                i = len(text) if end < 0 else end + 2
            elif text[i] in "\"'`":
                i = yield from quoted(i)
            elif nested and text[i] == "}" and depth == 0:
                return i + 1
            else:
                depth += (text[i] == "{") - (text[i] == "}")
                i += 1
        return i

    def quoted(i):
        quote = text[i]
        start = i = i + 1
        while i < len(text):
            if text[i] == "\\":
                i += 2
            elif text[i] == quote:
                yield start, i, text[start:i]
                return i + 1
            elif quote == "`" and text.startswith("${", i):
                yield start, i, text[start:i]
                i = yield from code(i + 2, True)
                start = i
            else:
                i += 1
        return i

    yield from code(0)


def visible_matches(path, text):
    found = []
    for start, _end, value in js_literals(text):
        if value in WIRE.get(path.name, set()):
            continue
        for match in ROLE.finditer(value):
            a, b = match.span()
            # Route paths, CSS selectors, attributes and wire identifiers stay stable.
            if value[a - 1 : a] in ("/", ".", "_", "-", ":") or value[b : b + 1] in ("/", "_", "-"):
                continue
            found.append((start + a, match.group()))
    return found


def test_user_visible_ui_role_copy():
    failures = []
    for path in sorted((ROOT / "commonplace/web").rglob("*.js")):
        text = path.read_text()
        for offset, word in visible_matches(path, text):
            failures.append(
                f"{path.relative_to(ROOT)}:{text.count(chr(10), 0, offset) + 1}: {word}"
            )
    assert not failures, "Use Coworker in visible copy:\n" + "\n".join(failures)


@pytest.mark.parametrize("word", ["orchestrator", "ORCHESTRATORS", "Orchestration"])
def test_guard_catches_labels(word):
    assert visible_matches(Path("sample.js"), f'const label = "{word}";')


def test_guard_reads_nested_template_copy():
    text = 'const html = `<p>${ready ? "Orchestrator" : `No orchestrator yet`}</p>`;'
    assert len(visible_matches(Path("sample.js"), text)) == 2
