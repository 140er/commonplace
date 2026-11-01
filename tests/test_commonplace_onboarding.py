"""Exact imports must retain a recoverable source until every write is verified."""

import hashlib
import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from commonplace import onboarding


class Client:
    def __init__(self, *, fail=None):
        self.memories = {}
        self.calls = []
        self.fail = fail

    def remember(self, text, **kwargs):
        self.calls.append(("memory_save", text, kwargs))
        if self.fail == "save":
            raise RuntimeError("SOURCE_CANARY must never appear in diagnostics")
        memory_id = f"memory-{len(self.memories) + 1}"
        self.memories[memory_id] = text
        return "uncertain" if self.fail == "unconfirmed" else f"saved {memory_id}"

    def get_memory_text(self, memory_id):
        self.calls.append(("memory_get", memory_id))
        if self.fail == "readback":
            raise RuntimeError("SOURCE_CANARY")
        return self.memories[memory_id] + ("changed" if self.fail == "different" else "")

    def write_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if self.fail == "rule":
            raise RuntimeError("SOURCE_CANARY")
        status = "accepted" if self.fail == "accepted" else "proposed"
        return f"{arguments['rules'][0]['key']} {status} 1\nseq 1"


@pytest.fixture
def home(tmp_path):
    directory = tmp_path / "home"
    directory.mkdir()
    return directory


def _source(home, data, name=".claude/CLAUDE.md"):
    path = home / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _record(result, path):
    return next(row for row in result["files"] if row["path"] == str(path))


def _remote(monkeypatch, project, url="https://example.test/team/project.git", *, top=None):
    def run(command, **kwargs):
        assert kwargs["timeout"] == 1 and kwargs["capture_output"]
        if "rev-parse" in command:
            return SimpleNamespace(stdout=str(top or project) + "\n")
        assert command[-5:] == ["config", "--local", "--no-includes", "--get", "remote.origin.url"]
        return SimpleNamespace(stdout=url + "\n")

    monkeypatch.setattr(onboarding.subprocess, "run", run)


def test_fresh_pointers_are_short_and_idempotent_without_remote_writes(home):
    client = Client()
    result = onboarding.onboard(client, home=home)
    assert [row["status"] for row in result["files"]] == ["created"] * 3
    assert client.calls == [] and not result["warnings"]
    assert len(onboarding.POINTER.splitlines()) <= 10
    for name in (".claude/CLAUDE.md", ".codex/AGENTS.md", ".agents/AGENTS.md"):
        assert (home / name).read_text() == onboarding.POINTER
    manifest = Path(result["manifest"])
    assert stat.S_IMODE(manifest.stat().st_mode) == 0o600
    assert stat.S_IMODE(manifest.parent.stat().st_mode) == 0o700
    before = manifest.read_bytes()
    rerun = onboarding.onboard(client, home=home)
    assert {row["status"] for row in rerun["files"]} == {"unchanged"}
    assert manifest.read_bytes() == before and client.calls == []
    assert onboarding.check_local_only(home=home) == []


def test_exact_rule_import_has_private_backup_readback_and_owner_proposal(home):
    data = "  Keep SOURCE_CANARY verbatim.\r\nUse α and 🚲.\r\n".encode()
    path = _source(home, data)
    client = Client()

    def save(text, **kwargs):
        assert path.read_bytes() == data
        backups = list((home / ".commonplace/backups").iterdir())
        assert len(backups) == 1 and backups[0].read_bytes() == data
        assert stat.S_IMODE(backups[0].stat().st_mode) == 0o600
        assert stat.S_IMODE(backups[0].parent.stat().st_mode) == 0o700
        return Client.remember(client, text, **kwargs)

    client.remember = save
    result = onboarding.onboard(client, home=home)
    row = _record(result, path)
    assert row["status"] == "migrated" and row["memory_id"] == "memory-1"
    assert "owner approval" in row["reason"]
    assert [call[0] for call in client.calls] == ["memory_save", "memory_get", "rules_publish"]
    assert client.memories["memory-1"].encode() == data
    published = client.calls[-1][1]
    assert published == {
        "agent": "*",
        "replace": False,
        "rules": [
            {
                "key": row["rule_key"],
                "text": data.decode(),
                "kind": "instruction",
                "scope": "global",
            }
        ],
    }
    source = client.calls[0][2]["source"]["thread"]
    assert source.startswith("onboarding:") and str(home) not in source
    manifest_bytes = Path(result["manifest"]).read_bytes()
    assert b"SOURCE_CANARY" not in manifest_bytes
    entry = json.loads(manifest_bytes)["files"][str(path)]
    assert Path(entry["backup"]).read_bytes() == data
    assert entry["original_sha256"] == hashlib.sha256(data).hexdigest()
    assert path.read_text() == onboarding.POINTER
    calls = len(client.calls)
    onboarding.onboard(client, home=home)
    assert len(client.calls) == calls


@pytest.mark.parametrize(
    "fail", ["save", "unconfirmed", "readback", "different", "rule", "accepted"]
)
def test_failed_or_ambiguous_persistence_keeps_original_and_backup(home, fail):
    data = b"Keep SOURCE_CANARY precisely.\r\n"
    path = _source(home, data)
    client = Client(fail=fail)
    result = onboarding.onboard(client, home=home)
    assert _record(result, path)["status"] == "refused"
    assert path.read_bytes() == data
    assert [p.read_bytes() for p in (home / ".commonplace/backups").iterdir()] == [data]
    assert "SOURCE_CANARY" not in json.dumps(result)
    assert (home / ".codex/AGENTS.md").read_text() == onboarding.POINTER


@pytest.mark.parametrize(
    "data",
    [b"x" * 4001, b"<" * 1100, "🚲".encode() * 1600, b"null\x00text", b"\xffinvalid", b"   "],
)
def test_rule_preflight_refuses_complete_overflow_or_invalid_source_before_remote(home, data):
    path = _source(home, data)
    client = Client()
    result = onboarding.onboard(client, home=home)
    assert _record(result, path)["status"] == "refused" and path.read_bytes() == data
    assert client.calls == []
    assert not (home / ".commonplace/backups").exists()
    assert all(warning.startswith("CLAUDE.md:") for warning in result["warnings"])


@pytest.mark.parametrize(
    "text",
    ["API endpoint https://user:example-password@example.test", "My medication is listed here."],
)
def test_sensitive_sources_are_never_sent_to_an_unverified_destination(home, text):
    path = _source(home, text.encode())
    client = Client()
    result = onboarding.onboard(client, home=home)
    assert _record(result, path)["status"] == "refused"
    assert "private" in _record(result, path)["reason"]
    assert path.read_text() == text and client.calls == []
    assert text not in json.dumps(result)


def test_existing_home_files_are_included_but_arbitrary_files_are_not(home):
    _source(home, b"Keep exact root instructions.\n", "CLAUDE.md")
    _source(home, b"Keep exact root guidance.\n", "AGENTS.md")
    unselected = _source(home, b"Untouched local data.", "nested/CLAUDE.md")
    memory = _source(home, b"Unselected home memory.", "MEMORY.md")
    credential = _source(home, b"Not an import source.", ".env")
    client = Client()
    result = onboarding.onboard(client, home=home)
    assert len(result["files"]) == 5
    assert len(client.memories) == 2
    assert unselected.read_bytes() == b"Untouched local data."
    assert memory.read_bytes() == b"Unselected home memory."
    assert credential.read_bytes() == b"Not an import source."


def test_project_rules_preserve_repo_scope_and_memory_is_one_complete_save(
    home, tmp_path, monkeypatch
):
    project = tmp_path / "project"
    data = b"Keep repository instructions.\r\n"
    rules = _source(project, data, "CLAUDE.md")
    memory = _source(project, ("Long exact memory line.\r\n" * 300).encode(), "MEMORY.md")
    original_memory = memory.read_bytes()
    _remote(monkeypatch, project)
    client = Client()
    result = onboarding.onboard(client, home=home, project=project)
    assert _record(result, rules)["status"] == "migrated"
    assert _record(result, memory)["status"] == "migrated"
    assert (project / "AGENTS.md").read_text() == onboarding.POINTER
    published = [call[1]["rules"][0] for call in client.calls if call[0] == "rules_publish"]
    assert len(published) == 1
    assert published[0]["scope"] == "repo" and published[0]["repo"] == "example.test/team/project"
    assert published[0]["text"].encode() == data
    assert list(client.memories.values()) == [data.decode(), original_memory.decode()]


@pytest.mark.parametrize("data", [b"m" * 100_001, b"m" * 400_001])
def test_oversized_project_memory_is_preserved_without_chunking(home, tmp_path, monkeypatch, data):
    project = tmp_path / "project"
    path = _source(project, data, "MEMORY.md")
    _remote(monkeypatch, project)
    client = Client()
    result = onboarding.onboard(client, home=home, project=project)
    assert _record(result, path)["status"] == "refused"
    assert path.read_bytes() == data and client.calls == []


@pytest.mark.parametrize(
    "remote",
    [
        "",
        "/some/local/repo",
        "https://user:password@example.test/team/project",
        "https://example.test/team/project?token=example",
        "https://example.test/team/../project",
        "https://example.test/team/project\nextra",
    ],
)
def test_unreliable_project_scope_refuses_rules_but_allows_fresh_pointers(
    home, tmp_path, monkeypatch, remote
):
    project = tmp_path / "project"
    path = _source(project, b"Must stay scoped to this project.", "AGENTS.md")
    _remote(monkeypatch, project, remote)
    client = Client()
    result = onboarding.onboard(client, home=home, project=project)
    assert _record(result, path)["status"] == "refused"
    assert path.read_bytes() == b"Must stay scoped to this project."
    assert client.calls == []
    assert (project / "CLAUDE.md").read_text() == onboarding.POINTER


@pytest.mark.parametrize(
    "remote", ["git@example.test:team/project.git", "ssh://git@example.test/team/project.git"]
)
def test_ssh_repo_identity_is_supported_without_exposing_credentials(
    home, tmp_path, monkeypatch, remote
):
    _remote(monkeypatch, tmp_path, remote)
    assert onboarding._repo_scope(tmp_path) == "example.test/team/project"


def test_subdirectory_is_not_silently_promoted_to_repository_scope(home, tmp_path, monkeypatch):
    project = tmp_path / "project"
    path = _source(project, b"Nested scope.", "CLAUDE.md")
    _remote(monkeypatch, project, top=tmp_path)
    client = Client()
    result = onboarding.onboard(client, home=home, project=project)
    assert _record(result, path)["status"] == "refused" and client.calls == []


def test_no_project_skips_every_project_file(home, tmp_path, monkeypatch):
    project = tmp_path / "project"
    path = _source(project, b"Keep local project content.", "CLAUDE.md")
    monkeypatch.chdir(project)
    client = Client()
    result = onboarding.onboard(client, home=home, project=None)
    assert all(str(project) not in row["path"] for row in result["files"])
    assert path.read_bytes() == b"Keep local project content."
    assert not (project / "AGENTS.md").exists() and client.calls == []


def test_dry_run_is_local_only_and_nonmutating(home, tmp_path, monkeypatch):
    path = _source(home, b"Keep complete original.")
    project = tmp_path / "project"
    project.mkdir()
    _remote(monkeypatch, project)
    before = sorted(p.relative_to(home) for p in home.rglob("*"))
    client = Client()
    result = onboarding.onboard(client, home=home, project=project, dry_run=True)
    assert _record(result, path)["status"] == "migrated" and result["dry_run"]
    assert sorted(p.relative_to(home) for p in home.rglob("*")) == before
    assert path.read_bytes() == b"Keep complete original."
    assert list(project.iterdir()) == [] and client.calls == []


@pytest.mark.parametrize("location", ["target", "ancestor", "backup", "manifest"])
def test_symlinks_are_refused_without_reading_or_writing_the_target(home, tmp_path, location):
    outside = tmp_path / "outside"
    outside.mkdir()
    target = _source(outside, b"Outside scope.", "CLAUDE.md")
    if location == "target":
        (home / ".claude").mkdir()
        (home / ".claude/CLAUDE.md").symlink_to(target)
    elif location == "ancestor":
        (home / ".claude").symlink_to(outside, target_is_directory=True)
    else:
        _source(home, b"Keep selected source.")
        (home / ".commonplace").mkdir()
        if location == "backup":
            (home / ".commonplace/backups").symlink_to(outside, target_is_directory=True)
        else:
            (home / ".commonplace/onboarding.json").symlink_to(target)
    client = Client()
    result = onboarding.onboard(client, home=home)
    assert _record(result, home / ".claude/CLAUDE.md")["status"] == "refused"
    assert client.calls == []
    assert target.read_bytes() == b"Outside scope." and list(outside.iterdir()) == [target]


def test_corrupt_manifest_is_preserved_and_prevents_remote_import(home):
    source = _source(home, b"Keep original content.")
    manifest = _source(home, b"not a manifest", ".commonplace/onboarding.json")
    client = Client()
    result = onboarding.onboard(client, home=home)
    assert {row["status"] for row in result["files"]} == {"refused"}
    assert source.read_bytes() == b"Keep original content."
    assert manifest.read_bytes() == b"not a manifest" and client.calls == []


def test_manifest_never_copies_unrecognized_raw_content(home):
    path = _source(home, b"Keep original content.")
    raw = json.dumps({"version": 1, "files": {}, "text": "PRIVATE_CANARY"}).encode()
    manifest = _source(home, raw, ".commonplace/onboarding.json")
    client = Client()
    result = onboarding.onboard(client, home=home)
    assert _record(result, path)["status"] == "refused" and client.calls == []
    assert manifest.read_bytes() == raw and "PRIVATE_CANARY" not in json.dumps(result)


def test_symlinked_git_metadata_is_not_used_for_project_scope(home, tmp_path, monkeypatch):
    project = tmp_path / "project"
    path = _source(project, b"Must stay local without a reliable scope.", "CLAUDE.md")
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / ".git").symlink_to(outside, target_is_directory=True)

    def no_git(*args, **kwargs):
        pytest.fail("Git must not follow symlinked metadata")

    monkeypatch.setattr(onboarding.subprocess, "run", no_git)
    client = Client()
    result = onboarding.onboard(client, home=home, project=project)
    assert _record(result, path)["status"] == "refused" and client.calls == []


def test_source_changed_during_remote_save_is_never_overwritten(home):
    path = _source(home, b"First wording.")
    client = Client()

    def readback(memory_id):
        path.write_bytes(b"New local wording.")
        return Client.get_memory_text(client, memory_id)

    client.get_memory_text = readback
    result = onboarding.onboard(client, home=home)
    assert _record(result, path)["status"] == "refused"
    assert path.read_bytes() == b"New local wording."
    manifest = json.loads(Path(result["manifest"]).read_bytes())
    assert manifest["files"][str(path)]["memory_id"] == "memory-1"
    assert Path(manifest["files"][str(path)]["backup"]).read_bytes() == b"First wording."


def test_local_checker_only_reads_selected_managed_files_and_never_uploads(home, tmp_path):
    onboarding.onboard(Client(), home=home)
    path = home / ".codex/AGENTS.md"
    path.write_text(onboarding.POINTER + "\nKeep LOCAL_CANARY only locally.\n")
    arbitrary = _source(home, b"Do not scan me.", "notes/MEMORY.md")
    backup = _source(home, b"Do not scan me.", ".commonplace/backups/AGENTS.md")
    external = tmp_path / "unselected"
    external.mkdir()
    (external / "CLAUDE.md").symlink_to(external / "CLAUDE.md")
    warnings = onboarding.check_local_only(home=home)
    assert [row["path"] for row in warnings] == [str(path)]
    assert warnings[0]["status"] == "local_only" and "rules_publish" in warnings[0]["reason"]
    assert "LOCAL_CANARY" not in json.dumps(warnings)
    assert onboarding.check_local_only(home=home, paths=[arbitrary, backup, external]) == []
    assert onboarding.check_local_only(home=home, paths=[".codex/AGENTS.md"]) == warnings


def test_checker_refuses_symlink_content_without_following_it(home, tmp_path):
    onboarding.onboard(Client(), home=home)
    path = home / ".claude/CLAUDE.md"
    path.unlink()
    secret = tmp_path / "secret"
    secret.write_text("PRIVATE_CANARY")
    path.symlink_to(secret)
    warnings = onboarding.check_local_only(home=home)
    assert len(warnings) == 1 and warnings[0]["path"] == str(path)
    assert "safely" in warnings[0]["reason"] and "PRIVATE_CANARY" not in json.dumps(warnings)
