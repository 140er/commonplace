"""Only browser owners can permanently remove newly marked standalone notes."""

from commonplace import sessions
from tests._chat_helpers import chat_environment, chat_hub_fixture  # noqa: F401


def note(h):
    status, body, _ = h.request(
        h.owner, "POST", "/api/memory", {"text": "deletioncanary exact words\nsecond line"}
    )
    assert status == 201, body
    return body["id"]


def auth(h, who=None):
    headers = dict(who or h.owner)
    headers["Origin"] = f"http://127.0.0.1:{h.port}"
    return headers


def delete(h, identifier, who=None, **overrides):
    headers = auth(h, who)
    session = sessions.cookie_value(headers.get("Cookie"))
    body = {"csrf": sessions.memory_delete_csrf(session or "", identifier)}
    body.update(overrides)
    return h.request(headers, "POST", f"/api/memory/{identifier}/delete", body)


def test_owner_delete_route_exists(chat_hub):
    h = chat_hub
    identifier = note(h)
    # Deliberately independent of the new CSRF helper: proves missing route on the base.
    status, body, _ = h.get(h.owner, f"/api/memory/{identifier}")
    assert status == 200
    response = h.request(
        auth(h),
        "POST",
        f"/api/memory/{identifier}/delete",
        {"csrf": (body.get("delete_note") or {}).get("csrf", "missing-on-base")},
    )
    assert response[0] == 200, response[:2]


def test_note_delete_authority_and_idempotence(chat_hub, monkeypatch, tmp_path):
    import json

    from mempalace import wal

    h = chat_hub
    monkeypatch.setattr(wal, "_WAL_FILE", tmp_path / "wal" / "write_log.jsonl")
    identifier = note(h)
    for who in [
        h.member,
        {"Authorization": "Bearer " + h.owner_token()},
        {"Authorization": "Bearer " + h.t1},
    ]:
        assert "delete_note" not in h.get(who, f"/api/memory/{identifier}")[1]
        assert delete(h, identifier, who)[0] == 403
    assert delete(h, identifier, csrf="bad")[0] == 403
    assert h.request(h.owner, "POST", f"/api/memory/{identifier}/delete", {"csrf": "bad"})[0] == 403
    foreign = {**auth(h), "Origin": "https://foreign.example.invalid"}
    assert h.request(foreign, "POST", f"/api/memory/{identifier}/delete", {"csrf": "bad"})[0] == 403
    assert delete(h, "missing")[0] == 404
    assert delete(h, identifier)[0] == 200
    assert h.get(h.owner, f"/api/memory/{identifier}")[0] == 404
    assert delete(h, identifier)[0] == 404
    assert h.get(h.owner, "/api/memory/search?q=deletioncanary")[1]["results"] == []
    result = h.gateway.call_memory_tool(
        h.person(h.admin), "mempalace_list_drawers", {"wing": "shared"}
    )
    assert identifier not in json.dumps(result)
    rows = [
        row for row in h.db.audit_entries(h.workspace) if row["action"] == "memory.note.deleted"
    ]
    assert len(rows) == 1
    row = dict(rows[0])
    assert row["workspace_id"] == h.workspace and row["user_id"] == h.admin
    assert row["at"] and row["wing"] == "shared" and "general" in row["reason"]
    assert identifier in row["target_ids"] and "deletioncanary" not in json.dumps(row)
    log = wal._WAL_FILE.read_text()
    assert '"delete_drawer"' in log and identifier in log and "deletioncanary" not in log
    from commonplace import events

    history = events.after(h.db, h.workspace, 0, events.latest_seq(h.db, h.workspace))
    tombstones = [e for e in history if e["kind"] == "memory.deleted"]
    assert len(tombstones) == 1 and identifier in json.dumps(tombstones)
    assert "deletioncanary" not in json.dumps(tombstones)


def test_non_notes_and_chunks_are_refused(chat_hub):
    from tests.test_commonplace_memory import _call, _save

    h = chat_hub
    token = h.owner_token()
    old = _save(h, token, "older unmarked note")
    imported = _save(
        h, token, "imported text", source={"thread": "thread-canary", "seq_start": 1, "seq_end": 2}
    )
    mined = _call(
        h,
        token,
        "mempalace_add_drawer",
        wing="shared",
        room="general",
        content="mined file text",
        source_file="/tmp/canary.txt",
    )
    import json

    mined_id = json.loads(mined["content"][0]["text"])["drawer_id"]
    for identifier in [old, imported, mined_id]:
        assert "delete_note" not in h.get(h.owner, f"/api/memory/{identifier}")[1]
        assert delete(h, identifier)[0] == 403
        assert h.get(h.owner, f"/api/memory/{identifier}")[0] == 200


def test_delete_storage_floor_and_backup_pause(chat_hub, monkeypatch):
    from commonplace.gateway import WorkerPaused
    from mempalace import storage_guard

    h = chat_hub
    identifier = note(h)
    monkeypatch.setattr(storage_guard, "_last_warning", None)
    monkeypatch.setenv("MEMPALACE_MIN_FREE_BYTES", str(2**62))
    status, body, _ = delete(h, identifier)
    assert status == 507 and body["code"] == "storage_full" and body["retry_after_s"] > 0
    monkeypatch.setenv("MEMPALACE_MIN_FREE_BYTES", "0")
    original = h.gateway.mediator.workers.post

    def paused(principal, request):
        if request.get("params", {}).get("name") == "mempalace_delete_drawer":
            raise WorkerPaused(3)
        return original(principal, request)

    monkeypatch.setattr(h.gateway.mediator.workers, "post", paused)
    status, body, _ = delete(h, identifier)
    assert status == 503 and body["retry_after_s"] == 3 and "backup" in body["error"]
    assert h.get(h.owner, f"/api/memory/{identifier}")[0] == 200


def test_chunks_and_backend_origin_guard(chat_hub):
    import pytest

    from commonplace.extensions import ToolError
    from mempalace import provenance
    from tests.test_commonplace_memory import _save

    h = chat_hub
    status, body, _ = h.request(
        h.owner, "POST", "/api/memory", {"text": "chunked note words\n" * 1000}
    )
    assert status == 201
    identifier = body["id"]
    record = h.gateway.call_memory_tool(
        h.person(h.admin), "mempalace_get_drawer", {"drawer_id": identifier}
    )
    chunks = record["metadata"]["chunk_ids"]
    assert len(chunks) > 1
    assert delete(h, chunks[0])[0] == 403
    assert h.get(h.owner, f"/api/memory/{identifier}")[0] == 200
    assert delete(h, identifier)[0] == 200
    assert all(h.get(h.owner, f"/api/memory/{chunk}")[0] == 404 for chunk in chunks)
    old = _save(h, h.owner_token(), "unmarked guarded by backend")
    with provenance.standalone_note_operation(), pytest.raises(ToolError, match="standalone"):
        h.gateway.call_memory_tool(h.person(h.admin), "mempalace_delete_drawer", {"drawer_id": old})
    assert h.get(h.owner, f"/api/memory/{old}")[0] == 200


def test_oauth_pipeline_and_unwritable_wing_fail_closed(chat_hub, monkeypatch):
    from dataclasses import replace

    from commonplace.policy import Policy

    h = chat_hub
    identifier = note(h)
    owner = h.person(h.admin)

    # A person-shaped OAuth app grant still cannot act as the owner's browser.
    class Grants:
        def authenticate(self, token):
            if token == "synthetic-oauth":
                return replace(owner, auth="oauth", person=True)
            if token == "synthetic-pipeline":
                return replace(
                    owner, auth="token", person=False, runner="worker", connection_id="pipeline"
                )
            return None

    h.gateway.authenticators.insert(0, Grants())
    for token in ["synthetic-oauth", "synthetic-pipeline"]:
        assert delete(h, identifier, {"Authorization": "Bearer " + token})[0] == 403
    original = sessions.session_principal
    restricted = Policy.build(
        profile="full",
        scopes=["memory:read", "memory:delete"],
        read_wings=["shared"],
        write_wings=["other"],
        trust="trusted",
    )
    monkeypatch.setattr(
        sessions,
        "session_principal",
        lambda db, session: replace(original(db, session), policy=restricted),
    )
    assert "delete_note" not in h.get(h.owner, f"/api/memory/{identifier}")[1]
    assert delete(h, identifier)[0] == 403


def test_duplicate_and_token_saves_never_promote_a_source(chat_hub):
    from tests.test_commonplace_memory import _save

    h = chat_hub
    text = "same words keep the original source"
    original = _save(h, h.owner_token(), text)
    status, saved, _ = h.request(h.owner, "POST", "/api/memory", {"text": text})
    assert status == 201 and saved["id"] == original
    assert "delete_note" not in h.get(h.owner, f"/api/memory/{original}")[1]
    assert delete(h, original)[0] == 403
    status, saved, _ = h.request(
        h.owner_token("rest-author"),
        "POST",
        "/api/memory",
        {"text": "token REST note", "origin": "standalone-note-v1"},
    )
    assert status == 201
    assert "delete_note" not in h.get(h.owner, f"/api/memory/{saved['id']}")[1]
    assert delete(h, saved["id"])[0] == 403


def test_csrf_is_bound_to_both_drawer_and_session(chat_hub):
    h = chat_hub
    identifier = note(h)
    proof = h.get(h.owner, f"/api/memory/{identifier}")[1]["delete_note"]["csrf"]
    status, other, _ = h.request(h.owner, "POST", "/api/memory", {"text": "other note words"})
    assert status == 201
    assert delete(h, other["id"], csrf=proof)[0] == 403
    second_session = h.cookie("owner@example.com", h.workspace)
    assert delete(h, identifier, second_session, csrf=proof)[0] == 403
    assert h.get(h.owner, f"/api/memory/{identifier}")[0] == 200


def test_storage_quota_does_not_block_removing_a_note(chat_hub, monkeypatch):
    h = chat_hub
    identifier = note(h)
    monkeypatch.setattr(h.gateway.storage_meter, "used", lambda *args: 2**62)
    assert delete(h, identifier)[0] == 200
