"""Copy edits retain technical spellings and the anchors linked by other guides."""

import html
import posixpath
import re
import subprocess
from collections import Counter
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASE = "origin/develop"
INTERNAL = {"modules", "system", "design", "compute"}  # Implementation/history, not user guides.
LINK = re.compile(r"\[[^\]\n]*\]\(([^\s)]+)(?:\s+\"[^\"]*\")?\)|^\s*\[[^\]]+\]:\s*(\S+)", re.M)
FENCE = re.compile(r"^\s*(`{3,}|~{3,})(\w*)")


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True)


def changed_docs():
    if not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    return [
        name
        for name in git("diff", "--name-only", BASE, "--", "docs/").splitlines()
        if name.endswith(".md") and Path(name).parts[1] not in INTERNAL
    ]


def slug(text):
    text = html.unescape(re.sub(r"<[^>]+>", "", text)).lower().replace("`", "")
    return re.sub(r"[^\w -]", "", text).replace(" ", "-")


def anchors(text):
    result = set(re.findall(r"<a\b[^>]*(?:id|name)=[\"\']([^\"\']+)[\"\']", text))
    seen = Counter()
    fenced = False
    for line in text.splitlines():
        if FENCE.match(line):
            fenced = not fenced
        elif not fenced and (heading := re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)):
            base = slug(heading[1])
            number = seen[base]
            result.add(base + (f"-{number}" if number else ""))
            seen[base] += 1
    return result


def fragment_errors(documents, targets):
    errors = []
    for name, text in documents.items():
        for match in LINK.finditer(text):
            destination = match[1] or match[2]
            link = urlsplit(destination.strip("<>"))
            if link.scheme or link.netloc or not link.fragment:
                continue
            target = (Path(name).parent / unquote(link.path)).as_posix() if link.path else name
            target = posixpath.normpath(target).lstrip("/")
            if not target.endswith(".md"):
                continue  # Source-code line fragments are not Markdown heading anchors.
            target_text = targets.get(target)
            if target_text is None and (ROOT / target).is_file():
                target_text = (ROOT / target).read_text()
            fragment = unquote(link.fragment)
            if target_text is None or fragment not in anchors(target_text):
                errors.append((name, destination))
    return errors


def test_changed_guides_preserve_existing_anchors_and_inbound_fragments():
    changed = changed_docs()
    before = {name: git("show", f"{BASE}:{name}") for name in changed}
    after = {name: (ROOT / name).read_text() for name in changed}
    # Every old heading remains usable, including bookmarks outside the repository.
    invalid = {
        name: sorted(a for a in anchors(text) if re.search(r"\s", a))
        for name, text in after.items()
    }
    assert not any(invalid.values()), invalid
    missing = {name: sorted(anchors(before[name]) - anchors(text)) for name, text in after.items()}
    assert not any(missing.values()), missing
    # Read ALL docs so unchanged inbound links to renamed headings are checked too.
    documents = {
        p.relative_to(ROOT).as_posix(): p.read_text() for p in (ROOT / "docs").rglob("*.md")
    }
    before_documents = {**documents, **before}
    old_errors = set(fragment_errors(before_documents, before_documents))
    new_errors = set(fragment_errors(documents, documents))
    # Pre-existing broken links outside this change are not new regressions.
    assert not new_errors - old_errors, sorted(new_errors - old_errors)


def technical_tokens(text):
    patterns = [
        r'(?:https?://|/api/)[^\s`"\']+',  # URLs and complete routes, including placeholders.
        r"(?<![\w])(?:[\w.-]+/)*[\w.-]+\.(?:py|js|mjs|md|toml|json|sqlite3|sh|yaml|yml|txt)\b",
        r"--[a-z][\w-]*",  # CLI flags.
        r"\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b",  # Environment variables/constants.
        r"\b[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+\b",  # Tool/config/wire identifiers.
        r"`[a-z_][a-z0-9_-]*`",  # Plain command/role/key names in code spans.
        r'["\'][A-Za-z_][\w.-]*["\']\s*:',  # JSON keys.
        r"(?m)^\s*[A-Za-z_][\w.-]*\s*=",  # Assignment/config keys.
    ]
    tokens = Counter(
        (i, m.group().strip())
        for i, pattern in enumerate(patterns)
        for m in re.finditer(pattern, text)
    )
    # Protocol role/schema/topic values are exact strings within TOML examples.
    for block in re.finditer(r"(?ms)^```toml\s*\n(.*?)^```", text):
        for line in block[1].splitlines():
            if re.match(r"\s*(?:kind|schema|topic)\s*=|\s*\[\[?[A-Za-z0-9_.-]+\]\]?\s*$", line):
                tokens[("toml", line.strip())] += 1
    for block in re.finditer(r"(?ms)^```(?:sh|bash|shell|console)\s*\n(.*?)^```", text):
        for line in block[1].splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                tokens[("command", line.strip())] += 1
    return tokens


def test_copy_edits_preserve_protocol_tokens():
    failures = {}
    for name in changed_docs():
        before = technical_tokens(git("show", f"{BASE}:{name}"))
        after = technical_tokens((ROOT / name).read_text())
        if before != after:
            failures[name] = {
                "removed": list((before - after).elements()),
                "added": list((after - before).elements()),
            }
    assert not failures, failures


@pytest.mark.parametrize(
    "old,new",
    [
        ("/api/projects/<slug>/orchestrator/connect", "/api/projects/<slug>/Coworker/connect"),
        ("[orchestrator.md](orchestrator.md)", "[Coworker.md](orchestrator.md)"),
        ("`orch_read`", "`coworker_read`"),
        ("`--main-role` COMMONPLACE_URL", "`--coworker-role` COMMONPLACE_ENDPOINT"),
        ('```toml\nkind = "orchestrator"\n```', '```toml\nkind = "Coworker"\n```'),
    ],
)
def test_token_check_rejects_identifier_renames(old, new):
    assert technical_tokens(old) != technical_tokens(new)


def test_fragment_check_catches_unchanged_inbound_link():
    documents = {"docs/runner.md": "[settings](projects.md#opt-in-agent-orchestration)"}
    assert fragment_errors(
        documents,
        {
            "docs/projects.md": '<a id="opt-in-agent-Coworker management"></a>\n## Opt-in Coworker management'
        },
    )
    assert not fragment_errors(
        documents,
        {
            "docs/projects.md": '<a id="opt-in-agent-orchestration"></a>\n## Opt-in Coworker management'
        },
    )


def test_token_check_keeps_config_sections_keys_and_commands():
    for old, new in [
        ("```toml\n[roles.main]\n```", "```toml\n[roles.coworker]\n```"),
        ('{"role": "main"}', '{"coworker_role": "main"}'),
        ("```sh\ncommonplace orch render\n```", "```sh\ncommonplace coworker render\n```"),
    ]:
        assert technical_tokens(old) != technical_tokens(new)
