"""Native thread resolution and real subprocess argv against an executable fake."""

import json
import sqlite3

import pytest

from commonplace.handoff.t3 import T3Error, Target, UnsupportedLaunch
from tests.e2e_handoff.fake_t3 import source_fixture


def configure(source, **values):
    config = json.loads(source.config.read_text())
    config.update(values)
    source.config.write_text(json.dumps(config))


def calls(source):
    return [json.loads(line) for line in source.log.read_text().splitlines()]


@pytest.mark.parametrize("provider,fmt", [("claudeAgent", "claude-code"), ("codex", "codex")])
def test_resolve_native_transcript_and_cwd(tmp_path, monkeypatch, provider, fmt):
    source = source_fixture(tmp_path, monkeypatch, provider=provider)
    resolved = source.adapter.resolve("mcp:source")
    assert resolved.cwd == str(source.cwd)
    assert resolved.provider == provider and resolved.transcript_format == fmt
    assert resolved.transcript_paths == (source.path,)
    assert resolved.transcript().encode() == source.path.read_bytes()
    assert calls(source)[0]["argv"] == ["thread", "show", "mcp:source", "--json", "--messages", "0"]


def test_root_launch_passes_explicit_target_and_stdin_in_source_cwd(tmp_path, monkeypatch):
    source = source_fixture(tmp_path, monkeypatch)
    resolved = source.adapter.resolve("mcp:source")
    ref = source.adapter.start(
        resolved, "prompt with 'quotes' and $shell\n", Target("codex", "gpt-6.1-sol", "max")
    )
    assert ref.agent_ref == "t3:mcp:successor" and ref.thread_id == "mcp:successor"
    launch = calls(source)[-1]
    assert launch["cwd"] == str(source.cwd)
    assert launch["argv"] == [
        "thread",
        "start",
        str(source.cwd),
        "-",
        "--provider",
        "codex",
        "--model",
        "gpt-6.1-sol",
        "--json",
        "--effort",
        "max",
    ]
    assert source.log.with_suffix(".prompt").read_text() == "prompt with 'quotes' and $shell\n"


def test_existing_worktree_uses_own_grant_and_preserves_permission_mode(tmp_path, monkeypatch):
    source = source_fixture(tmp_path, monkeypatch, worktree=True)
    resolved = source.adapter.resolve("mcp:source")
    ref = source.adapter.start(
        resolved, "short handoff", Target("claudeAgent", "claude-opus-5-5", "high")
    )
    assert ref.agent_ref == "t3:mcp:successor"
    log = calls(source)
    launch = log[-1]
    assert launch["argv"][:2] == ["acp-mcp-call", "t3_thread_launch"]
    args = json.loads(launch["argv"][2])
    assert args["workspaceStrategy"] == {
        "type": "existing_worktree",
        "worktreePath": str(source.cwd),
    }
    assert args["modelSelection"] == {"instanceId": "claudeAgent", "model": "claude-opus-5-5"}
    assert args["effort"] == "high" and args["message"] == "short handoff"
    assert args["runtimeMode"] == "full-access"
    assert not any(row["argv"][0] == "auth" for row in log)
    assert "fake.commonplace-grant" not in source.log.read_text()


@pytest.mark.parametrize("setting", ["start_fails", "invalid_reply"])
def test_unconfirmed_worktree_launch_never_settles(tmp_path, monkeypatch, setting):
    source = source_fixture(tmp_path, monkeypatch, worktree=True)
    configure(source, **{setting: True})
    with pytest.raises(T3Error):
        source.adapter.start(source.adapter.resolve("mcp:source"), "prompt", Target("codex", "sol"))
    log = calls(source)
    assert not any(row["argv"][0] == "auth" for row in log)
    assert not any(row["argv"][:2] == ["thread", "settle"] for row in log)


def test_missing_own_grant_does_not_reuse_a_provider_credential(tmp_path, monkeypatch):
    source = source_fixture(tmp_path, monkeypatch, worktree=True)
    monkeypatch.delenv("COMMONPLACE_T3_MCP_TOKEN")
    monkeypatch.setenv("T3_ACP_MCP_AUTHORIZATION", "Bearer another-agents-credential")
    with pytest.raises(UnsupportedLaunch, match="own approved"):
        source.adapter.start(source.adapter.resolve("mcp:source"), "prompt", Target("codex", "sol"))
    assert len(calls(source)) == 1  # Resolving the source only.


def test_insufficient_own_grant_is_unsupported_without_lowering_permissions(tmp_path, monkeypatch):
    source = source_fixture(tmp_path, monkeypatch, worktree=True)
    configure(source, insufficient_grant=True)
    with pytest.raises(UnsupportedLaunch, match="source permission mode"):
        source.adapter.start(source.adapter.resolve("mcp:source"), "prompt", Target("codex", "sol"))
    launch = calls(source)[-1]
    assert json.loads(launch["argv"][2])["runtimeMode"] == "full-access"
    assert not source.log.with_suffix(".prompt").exists()


@pytest.mark.parametrize(
    "reply",
    [
        {"successor_id": "mcp:source"},
        {"run_id": None},
        {"run_status": "failed"},
    ],
)
def test_a_thread_without_a_confirmed_new_run_is_not_a_successor(tmp_path, monkeypatch, reply):
    source = source_fixture(tmp_path, monkeypatch)
    configure(source, **reply)
    with pytest.raises(T3Error, match="did not confirm"):
        source.adapter.start(source.adapter.resolve("mcp:source"), "prompt", Target("codex", "sol"))


def test_missing_transcript_refuses_instead_of_using_latest_messages(tmp_path, monkeypatch):
    source = source_fixture(tmp_path, monkeypatch)
    source.path.unlink()
    with pytest.raises(T3Error, match="full native transcript"):
        source.adapter.resolve("mcp:source")
    assert len(calls(source)) == 1


def test_subagent_native_threads_do_not_replace_main_transcript(tmp_path, monkeypatch):
    source = source_fixture(tmp_path, monkeypatch)
    row = {
        **source.native,
        "ownerNodeId": "subagent-node",
        "nativeThreadRef": {"nativeId": "subagent"},
    }
    with sqlite3.connect(source.state / "statev2.sqlite") as db:
        db.execute(
            "INSERT INTO orchestration_v2_projection_provider_threads VALUES (?, ?, 1, 'later')",
            ("mcp:source", json.dumps(row)),
        )
    assert source.adapter.resolve("mcp:source").transcript_paths == (source.path,)
