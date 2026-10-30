"""The manual agent tool is scoped, trusted and bound to a local workspace."""

import json

import pytest

from commonplace import extensions
from commonplace.handoff import manual
from tests.e2e_handoff.fake_t3 import source_fixture
from tests.test_handoff_mcp_tools import ToolHub, _error, _value


@pytest.fixture
def writer(tmp_path, monkeypatch, request):
    hub = ToolHub(tmp_path)
    request.addfinalizer(hub.close)
    hub.setting()
    fake = source_fixture(tmp_path / "fake-t3", monkeypatch)
    monkeypatch.setattr(manual, "T3", lambda: fake.adapter)
    home = tmp_path / "home"
    (home / ".commonplace").mkdir(parents=True)
    (home / ".commonplace/handoff-client.json").write_text(
        json.dumps(
            {
                "workspaces": {str(fake.project): "home"},
            }
        )
    )
    monkeypatch.setenv("HOME", str(home))
    for name in ("COMMONPLACE_HANDOFF", "COMMONPLACE_AGENT", "COMMONPLACE_TASK"):
        monkeypatch.delenv(name, raising=False)
    token = hub.reader_token("manual-writer", scopes=["handoff:write"], trust="trusted")
    hub.fake = fake
    hub.invoke_start = lambda arguments, access=token: hub.rpc(
        access, "tools/call", {"name": "handoff_start", "arguments": arguments}
    )
    return hub


ARGS = {"thread_id": "mcp:source", "to": "codex/gpt-6.1-sol"}


def test_tool_registration_and_successful_local_start(writer):
    tool = extensions.REGISTRY["handoff_start"]
    assert tool.kind == "write" and tool.scope == "handoff:write" and tool.trusted_only
    assert tool.input_schema["additionalProperties"] is False
    result = _value(writer.invoke_start(ARGS))
    assert result["state"] == "done" and result["source_settled"]
    assert result["successor"]["agent_ref"] == "t3:mcp:successor"


@pytest.mark.parametrize(
    "options",
    [
        {"trust": "untrusted", "scopes": ["handoff:write"]},
        {"trust": "trusted", "scopes": ["threads:read"]},
        {"trust": "trusted", "scopes": ["handoff:write"], "profile": "slim"},
        {"trust": "trusted", "scopes": ["handoff:write"], "tools": ["thread_get"]},
    ],
)
def test_tool_hidden_without_permission_and_never_launches(writer, options):
    token = writer.reader_token("denied", **options)
    assert "handoff_start" not in writer.tool_names(token)
    response = writer.invoke_start(ARGS, access=token)
    assert response["error"] == {"code": -32601, "message": "Unknown tool: handoff_start"}
    assert not writer.fake.log.exists()


@pytest.mark.parametrize(
    "arguments,message",
    [
        ({"to": "codex/sol"}, "thread_id is required"),
        ({"thread_id": "old"}, "to is required"),
        ({**ARGS, "effort": 2}, "effort must be string"),
        ({**ARGS, "dry_run": "true"}, "dry_run must be boolean"),
        ({**ARGS, "workspace": "other"}, "unknown argument: workspace"),
    ],
)
def test_arguments_checked_before_any_t3_call(writer, arguments, message):
    assert _error(writer.invoke_start(arguments)) == f"no: invalid arguments: {message}"
    assert not writer.fake.log.exists()


def test_source_checkout_must_be_bound_to_callers_workspace(writer, monkeypatch):
    monkeypatch.setattr(manual, "workspace_for", lambda _: "another-workspace")
    assert (
        _error(writer.invoke_start(ARGS))
        == "no: source checkout is not bound to this Commonplace workspace"
    )
    assert writer.ctx.handoff_db.list(writer.workspace) == []
    calls = [json.loads(line)["argv"] for line in writer.fake.log.read_text().splitlines()]
    assert len(calls) == 1 and calls[0][:2] == ["thread", "show"]


def test_disabled_agent_scope_refuses_without_store_or_launch(writer):
    writer.setting(scope="agent", ref="manual-writer", enabled=False)
    assert "handoff is off (agent)" in _error(writer.invoke_start(ARGS))
    assert writer.ctx.handoff_db.list(writer.workspace) == []


def test_dry_run_does_not_store_or_launch(writer):
    result = _value(writer.invoke_start({**ARGS, "dry_run": True}))
    assert result["dry_run"] and result["target"]["model"] == "gpt-6.1-sol"
    assert writer.ctx.handoff_db.list(writer.workspace) == []
