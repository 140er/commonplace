"""Standalone notes use the existing Memory route and its real storage policy."""

import subprocess
from pathlib import Path

import pytest

from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_commonplace_memory import _call, _http, _save


def test_mcp_memory_save_under_limit_keeps_text_and_response_unchanged(hub):
    token = hub.owner_token()
    text = "\n  $& $' $` $$ < & \" ' café e\u0301 🧭\t\r\n" + "long exact words\n" * 1800
    result = _call(hub, token, "memory_save", text=text, theme="shared", topic="general")
    assert not result.get("isError"), result
    reply = result["content"][0]["text"]
    memory_id = reply.removeprefix("saved ")
    assert reply == f"saved {memory_id}" and memory_id != reply
    status, memory = _http(hub, token, f"/api/memory/{memory_id}")
    assert status == 200 and memory["text"].encode() == text.encode()
    assert memory["theme"] == "shared" and memory["topic"] == "general"


def test_note_composer_harness():
    result = subprocess.run(
        ["node", "--test", str(Path(__file__).with_name("memory_note.mjs"))],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_note_choices_default_and_verbatim_search(hub):
    token = hub.owner_token()
    _save(hub, token, "existing project note", theme="project_research", topic="decisions")
    status, page = _http(hub, token, "/api/memory/rooms?compose=1")
    assert status == 200
    composer = page["composer"]
    assert composer["default_wing"] == "shared"
    assert composer["max_length"] == 100_000
    assert {"shared", "project_research"} <= {row["wing"] for row in composer["wings"]}
    text = "  café e\u0301 漢字 🧭\t\r\nverbatimneedle " + "long words \n" * 2400 + "  "
    status, saved = _http(
        hub, token, "/api/memory", {"text": text, "theme": "project_research", "topic": "decisions"}
    )
    assert status == 201, saved
    assert saved["theme"] == "project_research" and saved["topic"] == "decisions"
    assert _http(hub, token, f"/api/memory/{saved['id']}")[1]["text"] == text
    found = _http(hub, token, "/api/memory/search?q=verbatimneedle&theme=project_research")[1]
    assert [(row["id"], row["text"]) for row in found["results"]] == [(saved["id"], text)]
    status, default = _http(hub, token, "/api/memory", {"text": "default workspace note"})
    assert status == 201 and default["theme"] == "shared" and default["topic"] == "general"


@pytest.mark.parametrize(
    ("text", "message"),
    [(" \n\t", "Write a note first."), ("x" * 100_001, "Use up to 100,000 characters.")],
    ids=["empty", "too-long"],
)
def test_note_validation_is_plain_and_saves_nothing(hub, text, message):
    status, body = _http(hub, hub.owner_token(), "/api/memory", {"text": text})
    assert status == 400 and body["error"] == message
    assert "id" not in body


def test_note_choices_and_writes_obey_scopes_and_wings(hub):
    owner = hub.owner_token()
    _save(hub, owner, "private evidence", theme="private")
    member = hub.db.create_user("note-member@example.invalid")
    hub.db.add_member(hub.workspace, member, "member", actor=hub.admin)
    token = hub.token(
        "note-member",
        user=member,
        profile="full",
        scopes=["memory:read", "memory:write"],
        read_wings=["shared"],
        write_wings=["shared"],
        trust="standard",
    )
    composer = _http(hub, token, "/api/memory/rooms?compose=1")[1]["composer"]
    assert [row["wing"] for row in composer["wings"]] == ["shared"]
    assert _http(hub, token, "/api/memory", {"text": "refused", "theme": "private"})[0] == 403
    read_only = hub.token(
        "note-reader",
        profile="full",
        scopes=["memory:read"],
        read_wings=["shared"],
        trust="standard",
    )
    assert _http(hub, read_only, "/api/memory/rooms?compose=1")[1]["composer"] is None
    assert _http(hub, read_only, "/api/memory", {"text": "refused"})[0] == 403
    assert _http(hub, None, "/api/memory", {"text": "refused"})[0] == 401


def test_note_storage_floor_and_busy_remain_retryable(hub, monkeypatch):
    from commonplace.extensions import RetryableToolError
    from mempalace import storage_guard

    token = hub.owner_token()
    monkeypatch.setattr(storage_guard, "_last_warning", None)
    monkeypatch.setenv("MEMPALACE_MIN_FREE_BYTES", str(2**62))
    status, body = _http(hub, token, "/api/memory", {"text": "floor canary"})
    assert status == 507 and body["code"] == "storage_full" and body["retry_after_s"] > 0
    assert "try again" in body["error"] and "id" not in body
    monkeypatch.setenv("MEMPALACE_MIN_FREE_BYTES", "0")

    def busy(*args, **kwargs):
        raise RetryableToolError("busy", retry_after_s=2, reason="busy")

    monkeypatch.setattr(hub.gateway, "call_memory_tool", busy)
    status, body = _http(hub, token, "/api/memory", {"text": "busy canary"})
    assert status == 503 and body["retry_after_s"] == 2 and "try again" in body["error"]
    assert "id" not in body
