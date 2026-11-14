"""File imports through real HTTP, preserving session authority and exact bytes."""

import hashlib
import http.client
from dataclasses import replace

import pytest

from commonplace import sessions
from tests.test_commonplace_import import api, fixture, surface as surface, token

PREVIEW = "/api/memory/import/preview"
CONFIRM = "/api/memory/import/confirm"


def file_body(source="cursor", **overrides):
    transcript, fmt = fixture(source)
    return {
        "filename": "export.jsonl",
        "source": source,
        "format": fmt,
        "transcript": transcript,
        **overrides,
    }


def preview(surface, body):
    status, result = api(surface, "POST", PREVIEW, body)
    assert status == 200, result
    return result


def confirm(surface, body, checked):
    return api(surface, "POST", CONFIRM, {**body, "preview": checked["preview"], "consent": True})


@pytest.mark.parametrize(
    "source", ["cursor", "claude-code", "codex", "t3", "grok-bot", "chatgpt", "claude-ai"]
)
def test_choose_preview_confirm_report_and_verbatim(surface, source):
    body = file_body(source)
    checked = preview(surface, body)
    assert (
        checked.items() >= {"source": source, "scope": "threads", "total": 1, "messages": 1}.items()
    )
    assert checked["source_bytes"] == len(body["transcript"].encode("utf-8"))
    assert not surface.gateway.handoff.has_threads(surface.workspace)
    status, report = confirm(surface, body, checked)
    assert status == 200 and report["status"] == "completed", report
    assert report["processed"] == report["imported"] == report["total"] == 1
    store = surface.gateway.handoff.store_for(surface.workspace)
    messages = store.messages(report["results"][0]["stored_thread_id"])
    assert messages[0].raw.encode("utf-8") == body["transcript"].encode("utf-8")
    assert messages[1].text == "  café 🦉\r\nKeep  every word.\t"


def test_cancel_preview_writes_nothing(surface):
    before = sorted(surface.directory.rglob("*"))
    changes = surface.db._conn.total_changes
    preview(surface, file_body())
    assert surface.db._conn.total_changes == changes
    assert sorted(surface.directory.rglob("*")) == before
    assert not surface.gateway.handoff.has_threads(surface.workspace)


def test_exact_reimport_including_renamed_file_adds_nothing(surface):
    body = file_body()
    checked = preview(surface, body)
    _, first = confirm(surface, body, checked)
    store = surface.gateway.handoff.store_for(surface.workspace)
    identifier = first["results"][0]["stored_thread_id"]
    saved = store.messages(identifier)
    body["filename"] = "renamed.json"
    status, again = confirm(surface, body, preview(surface, body))
    assert status == 200 and again["duplicates"] == 1 and again["imported"] == 0
    assert again["results"][0]["stored_thread_id"] == identifier
    assert store.messages(identifier) == saved


def test_confirm_requires_consent_and_matching_content_and_scope(surface):
    body = file_body()
    checked = preview(surface, body)
    for delta in ({}, {"consent": False}, {"consent": 1}, {"consent": True, "preview": "wrong"}):
        status, _ = api(surface, "POST", CONFIRM, {**body, **delta})
        assert status == 409
    for delta in ({"transcript": body["transcript"] + "\n"}, {"private": True}):
        status, _ = confirm(surface, {**body, **delta}, checked)
        assert status == 409
    assert not surface.gateway.handoff.has_threads(surface.workspace)


def test_member_current_scope_and_private_refusal(surface, monkeypatch):
    user = surface.db.create_user("member@example.test")
    surface.db.add_member(surface.workspace, user, "member", actor=surface.user)
    surface.cookie = sessions.start_session(surface.db, user, surface.workspace)
    status, _ = api(surface, "POST", PREVIEW, file_body(private=True))
    assert status == 403
    # Ordinary members retain exactly the existing writable conversations scope.
    body = file_body()
    checked = preview(surface, body)
    policy = sessions.person_policy
    monkeypatch.setattr(
        sessions, "person_policy", lambda role: replace(policy(role), write_wings=("inbox",))
    )
    assert confirm(surface, body, checked)[0] == 403
    assert api(surface, "POST", PREVIEW, body)[0] == 403
    assert not surface.gateway.handoff.has_threads(surface.workspace)


def test_member_can_import_into_the_existing_writable_scope(surface):
    user = surface.db.create_user("member@example.test")
    surface.db.add_member(surface.workspace, user, "member", actor=surface.user)
    surface.cookie = sessions.start_session(surface.db, user, surface.workspace)
    body = file_body()
    status, report = confirm(surface, body, preview(surface, body))
    assert status == 200 and report["imported"] == 1


@pytest.mark.parametrize(
    "filename",
    [
        "../export.json",
        "folder/export.json",
        "..\\export.json",
        "C:export.json",
        "bad\x00.json",
        "export.zip",
        "export.txt",
    ],
)
def test_wrong_type_and_traversal_refused(surface, filename):
    assert api(surface, "POST", PREVIEW, file_body(filename=filename))[0] in (400, 415)
    assert not surface.gateway.handoff.has_threads(surface.workspace)


def test_oversize_and_invalid_type_content_refused(surface):
    # Both the outer envelope and the decoded file are bounded. A peer can
    # close early on an excessive body; read its HTTP refusal even in that case.
    import json

    connection = http.client.HTTPConnection("127.0.0.1", surface.port, timeout=5)
    try:
        try:
            connection.request(
                "POST",
                PREVIEW,
                json.dumps(file_body(transcript=" " * (4 * 1024 * 1024 + 1))),
                headers={
                    "Content-Type": "application/json",
                    "Cookie": f"{sessions.COOKIE}={surface.cookie}",
                },
            )
        except BrokenPipeError:
            pass
        response = connection.getresponse()
        assert response.status == 413
        response.read()
    finally:
        connection.close()
    for delta in (
        {"transcript": "not JSON"},
        {"transcript": "[]"},
        {"transcript": "\ud800"},
        {"wing": "private"},
        {"source": "unknown"},
    ):
        assert api(surface, "POST", PREVIEW, file_body(**delta))[0] == 400
    connection = http.client.HTTPConnection("127.0.0.1", surface.port, timeout=5)
    try:
        connection.request("POST", PREVIEW, headers={"Content-Length": str(64 * 1024 * 1024 + 1)})
        response = connection.getresponse()
        assert response.status == 413
        response.read()
    finally:
        connection.close()
    assert not surface.gateway.handoff.has_threads(surface.workspace)


def test_session_only_and_cross_origin_refusals(surface):
    for path in (PREVIEW, CONFIRM):
        assert api(surface, "POST", path, file_body(), cookie=False)[0] == 401
        assert api(surface, "POST", path, file_body(), cookie=False, token=token(surface))[0] == 403
        assert (
            api(
                surface, "POST", path, file_body(), headers={"Origin": "https://elsewhere.invalid"}
            )[0]
            == 403
        )
        assert (
            api(surface, "POST", path, file_body(), headers={"Content-Type": "text/plain"})[0]
            == 403
        )


def test_private_confirmation_reuses_grant_and_safe_retry(surface):
    body = file_body(private=True)
    checked = preview(surface, body)
    assert checked["scope"] == "private"
    for _ in range(2):
        status, report = confirm(surface, body, checked)
        assert status == 200 and report["accepted"] == 1
        assert "duplicates" not in report
    identity = hashlib.sha256(body["transcript"].encode()).hexdigest()
    store = surface.gateway.handoff.store_for(surface.workspace)
    stored = store.by_source(surface.workspace, f"import-private:{surface.user}:cursor:{identity}")
    assert store.messages(stored.id)[0].raw.encode() == body["transcript"].encode()


def test_no_private_content_in_errors_or_logs(surface, monkeypatch, caplog):
    sentinel = "private-upload-sentinel"
    body = file_body(transcript=sentinel)
    status, error = api(surface, "POST", PREVIEW, body)
    assert status == 400 and sentinel not in str(error)
    body = file_body()
    checked = preview(surface, body)

    def fail(*args, **kwargs):
        raise RuntimeError(sentinel)

    monkeypatch.setattr(surface.gateway.handoff, "import_batch", fail)
    status, error = confirm(surface, body, checked)
    assert status == 500 and sentinel not in str(error)
    assert sentinel not in caplog.text and body["transcript"] not in caplog.text


def test_memory_text_uses_existing_private_parser_and_keeps_exact_bytes(surface):
    from commonplace import sync_protocol

    text = "  # Memory\r\ncafé 🦉\t\n"
    body = file_body(source="cursor", filename="memory.md", transcript=text)
    body.update(source=sync_protocol.MEMORY_IMPORT_SOURCE, format=sync_protocol.MEMORY_FORMAT)
    assert api(surface, "POST", PREVIEW, body)[0] == 403
    body["private"] = True
    checked = preview(surface, body)
    for _ in range(2):
        status, report = confirm(surface, body, checked)
        assert status == 200 and report["accepted"] == 1
    digest = hashlib.sha256(text.encode()).hexdigest()
    identity = sync_protocol.memory_thread_id("file", "content", digest)
    store = surface.gateway.handoff.store_for(surface.workspace)
    stored = store.by_source(
        surface.workspace, f"import-private:{surface.user}:native-memory:{identity}"
    )
    messages = store.messages(stored.id)
    assert len(messages) == 2
    assert messages[0].raw.encode() == messages[1].text.encode() == text.encode()
