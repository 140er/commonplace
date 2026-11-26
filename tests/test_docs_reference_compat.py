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
BROAD_TEST_BLOCK = (
    "```bash\n"
    "python -m pytest tests/ -q --ignore=tests/benchmarks \\\n"
    "  --cov=mempalace --cov-report=term\n"
    "ruff check .\n"
    "ruff format --check .\n"
    "```"
)
AFFECTED_TEST_BLOCK = (
    "```bash\npython -m commonplace.guards.prepush --manual --base origin/develop\n```"
)


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
    seen: Counter[str] = Counter()
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
    tokens: Counter[tuple[int | str, str]] = Counter(
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


def fenced_blocks(text):
    fence = None
    offset = 0
    start = 0
    for line in text.splitlines(keepends=True):
        if fence is None:
            if opening := FENCE.match(line):
                fence = opening[1]
                start = offset
        elif re.fullmatch(rf"[ \t]*{re.escape(fence[0])}{{{len(fence)},}}[ \t]*\n?", line):
            end = offset + len(line.removesuffix("\n"))
            yield start, end, text[start:end]
            fence = None
        offset += len(line)


def copy_edit_tokens(before, after):
    old_blocks = list(fenced_blocks(before))
    new_blocks = list(fenced_blocks(after))
    old_texts = [block for _, _, block in old_blocks]
    new_texts = [block for _, _, block in new_blocks]
    if (
        old_texts.count(BROAD_TEST_BLOCK) == 1
        and AFFECTED_TEST_BLOCK not in old_texts
        and BROAD_TEST_BLOCK not in new_texts
        and new_texts.count(AFFECTED_TEST_BLOCK) == 1
    ):
        start, end, _ = next(span for span in old_blocks if span[2] == BROAD_TEST_BLOCK)
        before = before[:start] + AFFECTED_TEST_BLOCK + before[end:]
    return technical_tokens(before), technical_tokens(after)


def test_copy_edits_preserve_protocol_tokens():
    failures = {}
    for name in changed_docs():
        before, after = copy_edit_tokens(git("show", f"{BASE}:{name}"), (ROOT / name).read_text())
        if before != after:
            failures[name] = {
                "removed": list((before - after).elements()),
                "added": list((after - before).elements()),
            }
    assert not failures, failures


def guide_change(monkeypatch, tmp_path, before, after, name="docs/reference.md"):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(after)
    monkeypatch.setitem(globals(), "ROOT", tmp_path)
    monkeypatch.setitem(globals(), "changed_docs", lambda: [name])
    monkeypatch.setitem(globals(), "git", lambda *args: before)


@pytest.mark.parametrize("name", ["docs/reference.md", "docs/nested/other-guide.md"])
def test_copy_edit_guard_allows_exact_affected_command_migration(monkeypatch, tmp_path, name):
    guide_change(monkeypatch, tmp_path, BROAD_TEST_BLOCK, AFFECTED_TEST_BLOCK, name)
    test_copy_edits_preserve_protocol_tokens()


@pytest.mark.parametrize(
    "after",
    [
        "",
        AFFECTED_TEST_BLOCK.replace("commonplace.guards.prepush", "commonplace.guards.bypass"),
        AFFECTED_TEST_BLOCK.replace("python -m", "python3 -m"),
        AFFECTED_TEST_BLOCK.replace(" --manual", ""),
        AFFECTED_TEST_BLOCK.replace(" --base origin/develop", ""),
        AFFECTED_TEST_BLOCK.replace("origin/develop", "origin/main"),
        AFFECTED_TEST_BLOCK.replace("--manual", "--manual --full"),
        AFFECTED_TEST_BLOCK.replace("--manual", "--manual --base origin/develop"),
        AFFECTED_TEST_BLOCK.replace("origin/develop", "origin/develop; echo changed"),
        AFFECTED_TEST_BLOCK.replace("origin/develop", "origin/develop\nruff check ."),
        AFFECTED_TEST_BLOCK.replace("```bash", "```sh"),
        AFFECTED_TEST_BLOCK + "\n" + AFFECTED_TEST_BLOCK,
        AFFECTED_TEST_BLOCK + "\n" + BROAD_TEST_BLOCK,
        AFFECTED_TEST_BLOCK + "\n```bash\npython -m pytest tests/ -q\n```",
    ],
)
def test_copy_edit_guard_rejects_unsupported_command_migrations(monkeypatch, tmp_path, after):
    guide_change(monkeypatch, tmp_path, BROAD_TEST_BLOCK, after)
    with pytest.raises(AssertionError):
        test_copy_edits_preserve_protocol_tokens()


@pytest.mark.parametrize(
    "context,changed",
    [
        ("https://example.invalid/guide", "https://example.invalid/other"),
        ("/api/projects/<slug>/connect", "/api/projects/<slug>/remove"),
        ("COMMONPLACE_URL", "COMMONPLACE_ENDPOINT"),
        ("`orch_read`", "`coworker_read`"),
        ("`--main-role`", "`--other-role`"),
        ("`settings.toml`", "`other.toml`"),
        ("```toml\n[roles.main]\n```", "```toml\n[roles.coworker]\n```"),
        ('```toml\nkind = "orchestrator"\n```', '```toml\nkind = "coworker"\n```'),
        ('```toml\nschema = "v1"\n```', '```toml\nschema = "v2"\n```'),
        ('```toml\ntopic = "main"\n```', '```toml\ntopic = "other"\n```'),
        ('{"role": "main"}', '{"other_role": "main"}'),
        ("port = 1234", "other_port = 1234"),
        ("```sh\ncommonplace orch render\n```", "```sh\ncommonplace orch remove\n```"),
        ("```sh\ncommonplace orch render\n```", ""),
    ],
)
@pytest.mark.parametrize("position", ["before", "after"])
def test_migration_keeps_adjacent_technical_tokens(
    monkeypatch, tmp_path, context, changed, position
):
    before = [context, BROAD_TEST_BLOCK] if position == "before" else [BROAD_TEST_BLOCK, context]
    after = (
        [changed, AFFECTED_TEST_BLOCK] if position == "before" else [AFFECTED_TEST_BLOCK, changed]
    )
    guide_change(monkeypatch, tmp_path, "\n".join(before), "\n".join(after))
    with pytest.raises(AssertionError):
        test_copy_edits_preserve_protocol_tokens()


@pytest.mark.parametrize(
    "before,after",
    [
        (BROAD_TEST_BLOCK.replace("```bash", "```sh"), AFFECTED_TEST_BLOCK),
        ("````md\n" + BROAD_TEST_BLOCK + "\n````", "````md\n" + AFFECTED_TEST_BLOCK + "\n````"),
        ("~~~md\n" + BROAD_TEST_BLOCK + "\n~~~", "~~~md\n" + AFFECTED_TEST_BLOCK + "\n~~~"),
        (BROAD_TEST_BLOCK + "\n" + BROAD_TEST_BLOCK, AFFECTED_TEST_BLOCK),
        (BROAD_TEST_BLOCK + "\n" + AFFECTED_TEST_BLOCK, AFFECTED_TEST_BLOCK),
        (AFFECTED_TEST_BLOCK, BROAD_TEST_BLOCK),
    ],
)
def test_migration_requires_one_complete_original_block(monkeypatch, tmp_path, before, after):
    guide_change(monkeypatch, tmp_path, before, after)
    with pytest.raises(AssertionError):
        test_copy_edits_preserve_protocol_tokens()


@pytest.mark.parametrize("heading", ["## Stable heading", "## Renamed heading"])
def test_command_migration_preserves_anchor_guard(monkeypatch, tmp_path, heading):
    guide_change(
        monkeypatch,
        tmp_path,
        "## Stable heading\n" + BROAD_TEST_BLOCK,
        heading + "\n" + AFFECTED_TEST_BLOCK,
    )
    (tmp_path / "docs/inbound.md").write_text("[guide](reference.md#stable-heading)")
    test_copy_edits_preserve_protocol_tokens()
    if heading == "## Stable heading":
        test_changed_guides_preserve_existing_anchors_and_inbound_fragments()
    else:
        with pytest.raises(AssertionError):
            test_changed_guides_preserve_existing_anchors_and_inbound_fragments()


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


@pytest.mark.parametrize(
    "token",
    [
        "POST /api/workspaces",
        "GET /api/workspaces",
        "POST /api/workspaces/switch",
        "COMMONPLACE_WORKSPACE_LIMIT",
        "COMMONPLACE_WORKSPACE_CREATIONS_PER_HOUR",
        "COMMONPLACE_WORKSPACE_CREATIONS_PER_DAY",
        "MEMPALACE_MIN_FREE_BYTES",
        "users.is_person=0",
        "COMMONPLACE_SIGNUP=open",
        "COMMONPLACE_SIGNUP_ALLOW_EMAILS",
        "users.is_admin=1",
        "signup open",
        "instance admin",
        "allowlist",
        "This instance is closed to new workspaces.",
        "request_id",
        "csrf",
    ],
)
def test_workspace_creation_contract_names_are_deliberate(token):
    assert token in (ROOT / "docs/system/person-workspace-creation.md").read_text()


@pytest.mark.parametrize(
    "token",
    [
        "commonplace member remove",
        "commonplace user delete",
        "commonplace user add",
        "--disposable",
        "--force-owner",
        "--by",
        "--reason",
        "--yes",
        "users.is_disposable",
        "users.is_admin",
        "member.remove",
        "user.delete",
        "audit_log",
        "target_ids",
        "example.invalid",
        "example.test",
    ],
)
def test_admin_cleanup_contract_names_are_deliberate(token):
    assert token in (ROOT / "docs/system/admin-account-cleanup.md").read_text()
