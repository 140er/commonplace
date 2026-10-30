"""A manual Claude → Codex handoff recovers a fact absent from its prompt."""

import json
from pathlib import Path

import pytest

from commonplace import cli
from commonplace.handoff import manual
from commonplace.handoff.db import HandoffDB
from tests.e2e_handoff.fake_t3 import source_fixture
from tests.e2e_handoff.sample import FACT, HERE


@pytest.mark.parametrize("entrypoint", ["cli", "mcp"])
def test_manual_cross_provider_successor_recalls_buried_fact(
    scenario, monkeypatch, entrypoint, capsys
):
    scenario.setting(True)
    fake = source_fixture(
        scenario.root / "manual-t3", monkeypatch, worktree=True, transcript=scenario.transcript
    )
    configuration = json.loads(fake.config.read_text())
    configuration["successor"] = str(HERE / "manual_successor.py")
    fake.config.write_text(json.dumps(configuration))
    monkeypatch.setattr(manual, "T3", lambda: fake.adapter)
    result_path = scenario.root / "manual-result.json"
    for key in ("COMMONPLACE_URL", "COMMONPLACE_TOKEN", "COMMONPLACE_TASK"):
        monkeypatch.setenv(key, scenario.environment[key])
    monkeypatch.setenv("HANDOFF_MANUAL_RESULT", str(result_path))
    monkeypatch.setenv("HOME", str(scenario.home))
    monkeypatch.delenv("COMMONPLACE_HANDOFF", raising=False)
    config_path = scenario.home / ".commonplace/handoff-client.json"
    config_path.write_text(json.dumps({"workspaces": {str(fake.project): "home"}}))
    if entrypoint == "cli":
        assert (
            cli.main(
                [
                    "--data",
                    str(scenario.data),
                    "handoff",
                    "start",
                    "--thread",
                    "mcp:source",
                    "--to",
                    "codex/gpt-6.1-sol",
                    "--effort",
                    "max",
                    "--note",
                    "Check deployment next.",
                ]
            )
            == 0
        )
        record = json.loads(capsys.readouterr().out)
    else:
        record = scenario.client.tool(
            "handoff_start",
            thread_id="mcp:source",
            to="codex/gpt-6.1-sol",
            effort="max",
        )
    result = json.loads(result_path.read_text())
    assert result["provider"] == "codex"
    assert result["port"] == "54329" and result["codename"] == "HELIOTROPE-7"
    assert result["calls"][0]["name"] == "thread_recall"
    assert result["thread_id"] == record["stored_thread_id"]
    assert record["successor"]["agent_ref"] == "t3:mcp:successor"
    assert record["state"] == "done" and record["source_settled"]
    prompt = fake.log.with_suffix(".prompt").read_text()
    assert FACT not in prompt and "54329" not in prompt and "HELIOTROPE-7" not in prompt
    fetched = scenario.client.tool(
        "thread_get",
        thread_id=record["stored_thread_id"],
        start=0,
        end=199,
        include_raw=True,
    )["messages"]
    assert fetched == [message.to_dict() for message in scenario.messages]
    assert "".join(message["raw"] for message in fetched) == scenario.transcript.read_text()
    saved = scenario.client.json("GET", f"/api/handoffs/{record['handoff_id']}")
    assert saved["idempotency_key"] == "thread:mcp:source"
    # Repeat through the agent tool even when the first caller was the CLI.
    retry = scenario.client.tool(
        "handoff_start", thread_id="mcp:source", to="claude-code/claude-opus-5-5"
    )
    assert retry["successor"] == record["successor"]
    log = [json.loads(line)["argv"] for line in fake.log.read_text().splitlines()]
    assert sum(args[:2] == ["acp-mcp-call", "t3_thread_launch"] for args in log) == 1
    assert log.index(next(args for args in log if args[:2] == ["thread", "settle"])) > log.index(
        next(args for args in log if args[:2] == ["acp-mcp-call", "t3_thread_launch"])
    )
    assert Path(fake.cwd).is_dir()


def test_missing_worktree_grant_refuses_before_storage_or_launch(scenario, monkeypatch, capsys):
    scenario.setting(True)
    fake = source_fixture(
        scenario.root / "manual-t3", monkeypatch, worktree=True, transcript=scenario.transcript
    )
    monkeypatch.setattr(manual, "T3", lambda: fake.adapter)
    monkeypatch.delenv("COMMONPLACE_T3_MCP_TOKEN")
    monkeypatch.setenv("T3_ACP_MCP_AUTHORIZATION", "Bearer another-agents-credential")
    assert (
        cli.main(
            [
                "--data",
                str(scenario.data),
                "handoff",
                "start",
                "--thread",
                "mcp:source",
                "--to",
                "codex/gpt-6.1-sol",
                "--workspace",
                "home",
            ]
        )
        == 2
    )
    assert "unsupported existing-worktree launch" in capsys.readouterr().err
    assert scenario.context.handoff_db.list(scenario.hub.workspace) == []
    assert len(fake.log.read_text().splitlines()) == 1


def test_insufficient_worktree_grant_records_failure_without_settling(
    scenario, monkeypatch, capsys
):
    scenario.setting(True)
    fake = source_fixture(
        scenario.root / "manual-t3", monkeypatch, worktree=True, transcript=scenario.transcript
    )
    configuration = json.loads(fake.config.read_text())
    configuration["insufficient_grant"] = True
    fake.config.write_text(json.dumps(configuration))
    monkeypatch.setattr(manual, "T3", lambda: fake.adapter)
    assert (
        cli.main(
            [
                "--data",
                str(scenario.data),
                "handoff",
                "start",
                "--thread",
                "mcp:source",
                "--to",
                "codex/gpt-6.1-sol",
                "--workspace",
                "home",
            ]
        )
        == 2
    )
    assert "source permission mode" in capsys.readouterr().err
    record = HandoffDB(scenario.hub.db).list(scenario.hub.workspace)[0]
    assert record.state == "launch_failed" and record.successor is None and record.stored_thread_id
    assert not any(
        json.loads(line)["argv"][:2] == ["thread", "settle"]
        for line in fake.log.read_text().splitlines()
    )
