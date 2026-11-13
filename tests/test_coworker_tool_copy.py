"""The product role uses one display name while protocol identifiers remain stable."""

import ast
import io
import re
import tokenize
from pathlib import Path

import pytest

from tests._projects_helpers import projects_hub_fixture  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
ROLE = re.compile(r"\b(?:orchestrators?|orchestration)\b", re.I)

# Exact protocol strings, never display labels. Keep compatibility with stored values.
WIRE_STRINGS = {
    "orchestrator",  # JSON field, route segment, role kind and handoff alias.
    "orchestrators",  # Admin response/pagination key.
    "orchestration",  # Project response key and admin source key.
    "sub-orchestrator",  # TOML role kind.
    "replaces current orchestrator",  # Connect conflict discriminator used by older clients.
}
# Each file uses these exact literals only as protocol fields, enums or discriminators.
WIRE_FILES = {
    "commonplace/orch/schema.py",  # TOML kinds.
    "commonplace/orch/durable.py",  # TOML kind comparison.
    "commonplace/web/admin.py",  # JSON source keys.
    "commonplace/boards/service.py",  # Historical handoff alias.
    "commonplace/projects/routes.py",  # JSON keys and route segments.
    "commonplace/projects/connect.py",  # Historical conflict discriminator.
    "commonplace/bots/context.py",  # JSON field.
    "commonplace/bots/tools_project.py",  # JSON field.
    "commonplace/bots/updates.py",  # Wire mark type.
    "commonplace/chat/status.py",  # JSON field.
    "commonplace/chat/routes.py",  # JSON field.
    "commonplace/runner/sessions.py",  # TOML kind comparisons.
}
# Internal logging and extraction patterns do not supply product copy.
INTERNAL_STRINGS = {
    "commonplace/handoff/facts.py",  # Recognizes historical transcript headings.
}


def python_literals(text):
    tree = ast.parse(text)
    ignored = set()
    for node in ast.walk(tree):
        # Docstrings are internal code documentation; prompts assigned to variables are scanned.
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.body and isinstance(node.body[0], ast.Expr):
                value = node.body[0].value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    ignored.update(range(value.lineno, value.end_lineno + 1))
        # Diagnostic logs do not reach UI/tool descriptions.
        if isinstance(node, ast.Call) and (
            isinstance(node.func, ast.Name)
            and node.func.id == "log_failure"
            or isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "logger"
        ):
            ignored.update(range(node.lineno, node.end_lineno + 1))
    offsets = [0]
    for line in text.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if (
            token.type in (tokenize.STRING, getattr(tokenize, "FSTRING_MIDDLE", -1))
            and token.start[0] not in ignored
        ):
            start = offsets[token.start[0] - 1] + token.start[1]
            end = offsets[token.end[0] - 1] + token.end[1]
            yield start, end, token.string


def prose_matches(text):
    # Inline code, fenced examples and link destinations contain stable command/config names.
    text = re.sub(r"(?<!`)`[^`\n]+`(?!`)|\]\([^)]*\)", lambda m: " " * len(m[0]), text, flags=re.S)
    return [
        m
        for m in ROLE.finditer(text)
        if not re.match(
            r"\s*(?:kind|schema|topic)\s*=", text[text.rfind("\n", 0, m.start()) + 1 : m.start()]
        )
    ]


def visible_matches(path, text):
    relative = path.relative_to(ROOT).as_posix()
    if relative in INTERNAL_STRINGS:
        return []
    if path.suffix == ".md":
        return [(m.start(), m.group()) for m in prose_matches(text)]
    literals = python_literals(text)
    found = []
    for start, _end, value in literals:
        raw = (
            re.sub(r"^[rubf]*(['\"]{1,3})|(['\"]{1,3})$", "", value, flags=re.I)
            if path.suffix == ".py"
            else value
        )
        # SQL contains internal schema comments; none of it is display text.
        if raw.lstrip().startswith(
            ("CREATE TABLE", "SELECT ", "UPDATE ", "INSERT ", "DELETE ", "DROP ", "ALTER ")
        ):
            continue
        if (
            raw in WIRE_STRINGS
            and relative in WIRE_FILES
            and not re.search(
                r"(?:description|label|note|help|message)\s*[:=]\s*$",
                text[max(0, start - 50) : start],
            )
        ):
            continue
        for match in ROLE.finditer(value):
            # Names embedded in paths, selectors, identifiers and enum values are stable.
            a, b = match.span()
            before, after = value[a - 1 : a], value[b : b + 1]
            if (
                before in ("/", ".", "_", "-", ":")
                or after in ("/", "_", "-")
                or (after == ":" and match.group().islower())
            ):
                continue
            found.append((start + a, match.group()))
    return found


def surfaces():
    yield from (ROOT / "commonplace").rglob("*.py")
    yield from (ROOT / "commonplace").rglob("*.md")


def test_user_visible_role_copy():
    failures = []
    for path in sorted(surfaces()):
        text = path.read_text()
        for offset, word in visible_matches(path, text):
            failures.append(
                f"{path.relative_to(ROOT)}:{text.count(chr(10), 0, offset) + 1}: {word}"
            )
    assert not failures, "Use Coworker in visible copy:\n" + "\n".join(failures)


@pytest.mark.parametrize("word", ["orchestrator", "ORCHESTRATORS", "Orchestration"])
def test_guard_catches_labels_and_parameter_descriptions(word):
    for path, text in [
        ("commonplace/boards/tools.py", f'description = "Choose the {word}"'),
        ("commonplace/bots/tools_project.py", f'description = "{word}"'),
        ("commonplace/agent-skill/SKILL.md", f"Ask the {word}."),
    ]:
        assert visible_matches(ROOT / path, text)


def test_new_workspace_default_and_existing_seat_names(projects_hub):
    h = projects_hub
    # A saved seat from before the copy change must survive reads and token rotation.
    with h.db._lock:
        h.db._conn.execute(
            "UPDATE project_orchestrators SET name=? WHERE workspace_id=? AND board='work'",
            ("orchestrator:work", h.workspace),
        )
    assert h.project(h.owner, "work")[1]["main"]["name"] == "orchestrator:work"
    code, connected = h.connect(h.owner, "work")
    assert code == 201 and connected["connection"]["name"] == "orchestrator:work"
    assert h.connect(h.owner, "work")[1]["connection"]["name"] == "orchestrator:work"
    # A different newly-created workspace uses the new default display name.
    assert h.create_board(h.other, "fresh")[0] == 201
    assert h.project(h.other, "fresh")[1]["main"]["name"] == "Coworker:fresh"
    assert h.connect(h.other, "fresh")[1]["connection"]["name"] == "Coworker:fresh"


def test_runtime_failure_copy_uses_display_names(monkeypatch):
    from types import SimpleNamespace

    from commonplace.web import admin

    def unreadable(*args):
        raise ValueError("synthetic unavailable source")

    monkeypatch.setattr(admin, "read_control", unreadable)
    result = admin.runtime(
        SimpleNamespace(db=None), "workspace", SimpleNamespace(remaining=lambda: None), None
    )
    assert result["orchestration"]["error"] == "Coworker management unavailable; try again"
    assert result["orchestrators"]["error"] == "Coworkers unavailable; try again"
    assert result["machines"]["error"] == "Machines unavailable; try again"
