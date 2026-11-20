"""Chat refusals survive bookkeeping outages; only admitted machines claim queued chat."""

import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

import pytest

from commonplace.projects import model_modes
from mempalace.sqlite_crypto import connect, row_factory
from tests._relay_helpers import relay_hub_fixture  # noqa: F401

SETTING = "/api/settings/model-mode"
CONNECTIONS = "/api/me/model-connections"


def _mode(h, mode):
    assert h.request(h.owner, "PUT", SETTING, {"mode": mode})[0] == 200


def _connection(h, machine):
    status, body, _ = h.request(
        h.owner,
        "POST",
        CONNECTIONS,
        {"provider": "claude", "label": "my subscription", "machine": machine["machine_id"]},
    )
    assert status == 201
    return body


def _revoke(h, connection):
    assert h.request(h.owner, "DELETE", CONNECTIONS + "?id=" + connection["id"])[0] == 200


def _answers(h, message, who=None):
    return [
        reply
        for reply in h.chat(who or h.owner)[1]["replies"]
        if reply["message_id"] == message and reply["kind"] == "answer"
    ]


def _needs_rows(h):
    with h.db._lock:
        return [tuple(row) for row in h.db._conn.execute("SELECT * FROM model_connection_needs")]


@contextmanager
def _locked_needs(h):
    # A shared-cache table lock affects the real bookkeeping write, without blocking the post.
    uri = Path(h.db.path).as_uri() + "?cache=shared"
    with (
        closing(connect(uri, uri=True, check_same_thread=False, isolation_level=None)) as reader,
        closing(connect(uri, uri=True, isolation_level=None)) as writer,
    ):
        reader.row_factory = row_factory
        reader.execute("PRAGMA foreign_keys = ON")
        with h.db._lock:
            original, h.db._conn = h.db._conn, reader
        try:
            writer.execute("BEGIN IMMEDIATE")
            writer.execute("UPDATE model_connection_needs SET provider=provider")
            yield
        finally:
            with h.db._lock:
                writer.rollback()
                h.db._conn = original


@pytest.mark.parametrize("outage", ["missing_table", "locked_table"])
def test_missing_connection_still_replies_when_bookkeeping_is_unavailable(
    relay_hub, monkeypatch, outage
):
    h = relay_hub
    calls, attempts = h.fake_model(monkeypatch), h.no_outbound(monkeypatch)
    machine = h.machine()
    h.set_main_model("claude", "claude-haiku-5-5")
    assert h.poll(machine)[0] == 204
    _mode(h, "personal")
    assert h.propose_titled("Review this synthetic change")[0] == 201
    model_modes.note_missing(h.db, h.workspace, h.member_user, "work", "claude")
    original_rows = _needs_rows(h)
    status, before, _ = h.request(h.owner, "GET", "/api/needs-me")
    assert status == 200 and before["approvals"]
    errors = []

    if outage == "missing_table":
        with h.db._lock:
            h.db._conn.execute(
                "ALTER TABLE model_connection_needs RENAME TO preserved_connection_needs"
            )
    else:
        original_note = model_modes.note_missing

        def locked_note(*args, **kwargs):
            with _locked_needs(h):
                try:
                    return original_note(*args, **kwargs)
                except sqlite3.OperationalError as exc:
                    errors.append(exc)
                    raise

        monkeypatch.setattr(model_modes, "note_missing", locked_note)

    try:
        message = h.post(h.owner, "Which account can answer this question?")
        assert h.poll(machine)[0] == 204
        assert h.jobs() == []
        [reply] = _answers(h, message)
        assert reply["code"] == "connection_missing"
        assert "your own claude account" in reply["text"] and "Settings" in reply["text"]
        assert all(
            value not in reply["text"]
            for value in (h.admin, h.member_user, machine["machine_id"], machine["machine_token"])
        )
        assert _answers(h, message, h.member) == []
        status, after, _ = h.request(h.owner, "GET", "/api/needs-me")
        assert status == 200 and after == before
        assert calls == attempts == []
    finally:
        if outage == "missing_table":
            with h.db._lock:
                h.db._conn.execute(
                    "ALTER TABLE preserved_connection_needs RENAME TO model_connection_needs"
                )

    if outage == "locked_table":
        assert len(errors) == 1 and "locked" in str(errors[0])
    # Failed bookkeeping never pretends it created an item, or clears another person's item.
    assert _needs_rows(h) == original_rows
    assert "model_connections" not in h.request(h.owner, "GET", "/api/needs-me")[1]
    assert len(h.request(h.member, "GET", "/api/needs-me")[1]["model_connections"]) == 1


@pytest.mark.parametrize("case", ["org_private", "personal_org", "not_online", "newly_org"])
def test_excluded_machine_polling_first_leaves_chat_for_its_admitted_machine(
    relay_hub, monkeypatch, case
):
    h = relay_hub
    calls, attempts = h.fake_model(monkeypatch), h.no_outbound(monkeypatch)
    eligible, excluded = h.machine(label="eligible"), h.machine(label="excluded")
    h.set_main_model("claude", "claude-haiku-5-5")
    if case in ("org_private", "newly_org"):
        connection = _connection(h, excluded)
        _mode(h, "org")
    else:
        _connection(h, eligible)
        if case == "not_online":
            _connection(h, excluded)
        _mode(h, "personal")
    assert h.poll(eligible)[0] == 204
    if case != "not_online":
        assert h.poll(excluded)[0] == 204
    message = h.post(h.owner, "Let the admitted account answer this question.")
    [queued] = h.jobs()
    assert queued["state"] == "queued"
    assert set(json.loads(queued["account_snapshot"])) == {eligible["machine_id"]}
    if case == "newly_org":
        _revoke(h, connection)  # Now a valid org machine, but not admitted for this message.

    assert h.poll(excluded)[0] == 204
    assert h.jobs() == [queued]
    assert _answers(h, message) == []
    status, lease = h.poll(eligible)
    assert status == 200
    assert "Let the admitted account answer this question." in lease["prompt"]
    assert (
        h.answer(eligible, lease["job"], lease["lease_token"], "The admitted account answered.")[0]
        == 200
    )
    [job] = h.jobs()
    assert (job["state"], job["code"], job["machine_id"]) == (
        "done",
        "ok",
        eligible["machine_id"],
    )
    [reply] = _answers(h, message)
    assert reply["text"] == "The admitted account answered."
    assert _answers(h, message, h.member) == []
    assert calls == attempts == []


@pytest.mark.parametrize("change", ["revoke", "replace"])
def test_admitted_machine_with_a_changed_account_is_refused_without_substitution(relay_hub, change):
    h = relay_hub
    first, second = h.machine(label="first"), h.machine(label="second")
    h.set_main_model("claude", "claude-haiku-5-5")
    connection = _connection(h, first)
    _connection(h, second)
    _mode(h, "personal")
    assert h.poll(first)[0] == h.poll(second)[0] == 204
    message = h.post(h.owner, "Keep this message on the admitted account.")
    _revoke(h, connection)
    if change == "replace":
        _connection(h, first)  # Same machine, provider, person and label; a new connection.
    assert h.poll(first)[0] == h.poll(second)[0] == 204
    [job] = h.jobs()
    expected = "account_changed" if change == "replace" else "machine_not_your_account"
    assert (job["state"], job["code"]) == ("skipped", expected)
    [reply] = _answers(h, message)
    assert reply["code"] == expected
    assert connection["id"] not in reply["text"] and first["machine_id"] not in reply["text"]


@pytest.mark.parametrize("snapshot", [None, "{}", "[]", "invalid-json"])
def test_legacy_or_unreadable_snapshot_is_preserved_and_refused(relay_hub, snapshot):
    h = relay_hub
    machine = h.machine()
    h.set_main_model("claude", "claude-haiku-5-5")
    assert h.poll(machine)[0] == 204
    message = h.post(h.owner, "A message whose original account cannot be proved.")
    [queued] = h.jobs()
    with h.db._lock:
        h.db._conn.execute(
            "UPDATE chat_runner_jobs SET account_snapshot=? WHERE id=?", (snapshot, queued["id"])
        )
    assert h.poll(machine)[0] == 204
    [job] = h.jobs()
    assert (job["id"], job["state"], job["code"], job["account_snapshot"]) == (
        queued["id"],
        "skipped",
        "account_changed",
        snapshot,
    )
    [reply] = _answers(h, message)
    assert reply["code"] == "account_changed" and "Send it again" in reply["text"]
