"""The successor must recover a fact that occurs only in old tool output."""

import json
import os
import shutil
import sys
from collections import Counter

import pytest

from tests.e2e_handoff.sample import FACT, HERE, transcript

SUCCESS = {"handoff.started", "handoff.stored", "handoff.completed"}


def assert_answer(result):
    assert result["port"] == "54329"
    assert result["codename"] == "HELIOTROPE-7"
    calls = result["calls"]
    assert calls[0]["name"] == "thread_recall"
    assert calls[0]["arguments"]["query"] == "staging database port"
    assert calls[0]["arguments"]["follow_chain"] is True
    assert any(call["name"] == "thread_get" for call in calls[1:])


def assert_events(scenario, count=1):
    payloads = scenario.signed_events(SUCCESS)
    audit = [row for row in scenario.audit() if row["action"] != "handoff.settings_changed"]
    assert Counter(row["action"] for row in audit) == Counter(dict.fromkeys(SUCCESS, count))
    events = [row for row in payloads if row["kind"] != "handoff.settings_changed"]
    assert Counter(row["kind"] for row in events) == Counter(dict.fromkeys(SUCCESS, count))
    assert FACT not in json.dumps(audit)
    return events


def test_successor_recalls_middle_tool_result(scenario):
    scenario.setting(True)
    hook = scenario.hook()
    output = json.loads(hook.stdout)
    assert output["continue"] is False, scenario.diagnostics()
    assert hook.stderr == ""
    rows, results = scenario.completed()
    row, result = rows[0], results[0]
    assert_answer(result)
    assert row["successor"]["agent_ref"] == f"pid:{result['pid']}"
    assert row["stored_thread_id"] == result["thread_id"]
    assert row["successor"]["agent_ref"] in output["stopReason"]
    assert FACT not in row["doc_markdown"]
    stored = scenario.thread_rows()
    assert len(stored) == 1 and stored[0]["message_count"] == 200
    assert scenario.hub.supervisor.launcher.started == [scenario.hub.workspace]

    # Check the complete stored source and text through the actual tool, including
    # CR/LF and JSON escapes. No handoff summary stands in for the full transcript.
    fetched = scenario.client.tool(
        "thread_get", thread_id=row["stored_thread_id"], start=0, end=199, include_raw=True
    )["messages"]
    assert fetched == [message.to_dict() for message in scenario.messages]
    assert "".join(message["raw"] for message in fetched) == scenario.transcript.read_text()
    events = assert_events(scenario)
    assert {event["data"]["handoff_id"] for event in events} == {row["id"]}
    completed = next(event for event in events if event["kind"] == "handoff.completed")
    assert completed["data"]["successor_agent_ref"] == row["successor"]["agent_ref"]
    html, visible = scenario.card()
    assert "handed off" in visible
    assert f"/app/threads/{row['stored_thread_id']}" in html
    assert row["successor"]["agent_ref"] in visible


def test_identical_hook_retry_stores_and_launches_once(scenario):
    scenario.setting(True)
    original = scenario.transcript.read_bytes()
    first = scenario.hook()
    retry = scenario.hook()  # Identical session, payload, transcript and idempotency key.
    assert scenario.transcript.read_bytes() == original
    assert json.loads(first.stdout) == json.loads(retry.stdout)
    assert json.loads(retry.stdout)["continue"] is False
    rows, results = scenario.completed()
    assert_answer(results[0])
    assert len(scenario.thread_rows()) == 1
    assert len(scenario.log("launches.jsonl")) == 1
    assert rows[0]["idempotency_key"] == "thread:original-session"
    assert_events(scenario)


def test_successor_hands_off_again_and_generation_two_recalls_ancestor(scenario):
    scenario.setting(True)
    next_transcript = scenario.root / "successor.jsonl"
    transcript(next_transcript, planted=False)
    payload = scenario.root / "successor-payload.json"
    payload.write_text(json.dumps(scenario.hook_payload("replaced-by-successor", next_transcript)))
    scenario.environment["HANDOFF_E2E_CHAIN_PAYLOAD"] = str(payload)
    assert json.loads(scenario.hook().stdout)["continue"] is False
    rows, results = scenario.completed(count=2)
    first = next(row for row in rows if not row["parent_handoff_id"])
    second = next(row for row in rows if row["parent_handoff_id"])
    assert second["parent_handoff_id"] == first["id"]
    assert second["source_thread_id"] == first["successor"]["agent_ref"]
    assert len(scenario.thread_rows()) == 2
    assert len(scenario.log("launches.jsonl")) == 2
    for result in results:
        assert_answer(result)
    generation_two = next(result for result in results if result["handoff_id"] == second["id"])
    assert [row["thread_id"] for row in generation_two["chain"]] == [
        second["stored_thread_id"],
        first["stored_thread_id"],
    ]
    assert any(
        call["name"] == "thread_get" and call["arguments"]["thread_id"] == first["stored_thread_id"]
        for call in generation_two["calls"]
    )
    own_messages = scenario.client.tool("thread_get", thread_id=second["stored_thread_id"])[
        "messages"
    ]
    assert len(own_messages) == 200 and FACT not in json.dumps(own_messages)
    local_hits = scenario.client.tool(
        "thread_recall",
        thread_id=second["stored_thread_id"],
        query="staging database port",
        follow_chain=False,
    )["hits"]
    assert all(
        hit["thread_id"] == second["stored_thread_id"] and FACT not in hit["text"]
        for hit in local_hits
    )
    assert_events(scenario, count=2)


def test_off_is_silent_without_storage_launches_or_handoff_events(scenario):
    assert scenario.client.json("GET", "/api/settings/handoff")["effective"]["enabled"] is False
    tools = scenario.client.rpc("tools/list")["tools"]
    assert not {"thread_recall", "thread_get", "thread_chain"} & {tool["name"] for tool in tools}
    scenario.setting(False)  # Explicit REST disable, rather than a local environment override.
    scenario.signed_events({"handoff.settings_changed"})
    before = scenario.receiver.received()
    hook = scenario.hook()
    assert hook.stdout == hook.stderr == ""
    assert scenario.records() == []
    assert not scenario.store_path.exists()
    assert scenario.context._stores == {}
    assert scenario.log("launches.jsonl") == []
    assert scenario.log("errors.jsonl") == []
    scenario.wait(scenario.deliveries_complete)
    assert scenario.receiver.received() == before
    assert {row["kind"] for row in scenario.event_rows()} == {"handoff.settings_changed"}
    assert {row["action"] for row in scenario.audit()} == {"handoff.settings_changed"}


def test_unwritable_store_falls_back_without_starting_successor(scenario):
    scenario.setting(True)
    assert scenario.context._stores == {} and not scenario.store_path.exists()
    # An unreadable directory at the *database filename* cannot be opened with
    # O_RDWR even as root, and has no preexisting SQLite handle/WAL to bypass it.
    scenario.store_path.mkdir(parents=True)
    scenario.store_path.chmod(0)
    try:
        with pytest.raises(OSError):
            descriptor = os.open(scenario.store_path, os.O_RDWR)
            os.close(descriptor)
        hook = scenario.hook()
        assert hook.stdout == hook.stderr == "", scenario.diagnostics()
        rows = scenario.records()
        assert len(rows) == 1
        failed = rows[0]
        assert failed["state"] == "failed" and failed["error"] == "thread storage failed"
        assert failed["stored_thread_id"] == "" and failed["successor"] is None
        assert scenario.context._stores == {}
        assert scenario.log("launches.jsonl") == []
        events = scenario.signed_events({"handoff.started", "handoff.failed"})
        assert {event["kind"] for event in events} == {
            "handoff.settings_changed",
            "handoff.started",
            "handoff.failed",
        }
        error = next(event for event in events if event["kind"] == "handoff.failed")
        assert error["data"]["decision"] == "error"
        assert "reason" not in error["data"]  # The hub webhook filters diagnostics out.
        failed_event = next(
            event for event in scenario.event_rows() if event["kind"] == "handoff.failed"
        )
        assert failed_event["data"]["reason"] == "thread storage failed"
        assert failed_event["data"]["decision"] == "error"
        assert {row["action"] for row in scenario.audit()} == {
            "handoff.settings_changed",
            "handoff.started",
            "handoff.failed",
        }
        assert not (scenario.home / ".commonplace" / "handoff-prompts").exists()
    finally:
        scenario.store_path.chmod(0o700)
        scenario.store_path.rmdir()


@pytest.mark.skipif(
    os.environ.get("COMMONPLACE_E2E_REAL_AGENT") != "1",
    reason="opt in with COMMONPLACE_E2E_REAL_AGENT=1 (requires Claude credentials and model access)",
)
def test_real_claude_successor_recalls_planted_fact(scenario):
    executable = shutil.which("claude")
    if not executable:
        pytest.skip("claude is not on PATH")
    scenario.setting(True)
    configuration = scenario.root / "agent-tools.json"
    configuration.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "memory": {
                        "type": "http",
                        "url": scenario.url + "/mcp",
                        "headers": {"Authorization": "Bearer " + scenario.token},
                    }
                }
            }
        )
    )
    configuration.chmod(0o600)
    scenario.environment["HANDOFF_E2E_AGENT_TOOLS"] = str(configuration)
    scenario.configure_successor(
        [
            sys.executable,
            str(HERE / "real_successor.py"),
            "{prompt_file}",
            "{handoff_id}",
            executable,
        ]
    )
    assert json.loads(scenario.hook().stdout)["continue"] is False
    rows, results = scenario.completed(timeout=110)
    assert len(rows) == 1
    assert "54329" in results[0]["answer"] and "HELIOTROPE-7" in results[0]["answer"]
    assert_events(scenario)
