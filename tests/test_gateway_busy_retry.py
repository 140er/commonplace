"""#420: SQLite lock contention gives a retryable MCP error, never a bare Internal error.

Live failure (11 Nov 2026): every authenticated MCP call returned -32603 while the
workspace databases were locked, and the client could not tell whether a write applied.
"""

import logging
import sqlite3
import threading
from pathlib import Path

import pytest

from commonplace.orch import store as orch_store
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_orch_surfaces import BOARD, ROLE, Env


@pytest.fixture
def env(hub):
    return Env(hub)


def hold_write_lock(path):
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.execute("BEGIN IMMEDIATE")
    return conn


def short_busy_timeout(monkeypatch, seconds):
    real = orch_store.connect
    monkeypatch.setattr(
        orch_store, "connect", lambda target, **kw: real(target, **{**kw, "timeout": seconds})
    )


def claim(env):
    ok, lease = env.mcp(env.a_token, "orch_resume", board=BOARD, role=ROLE)
    assert ok, lease
    return {"board": BOARD, "role": ROLE, "epoch": lease["epoch"], "token": lease["token"]}


def runner_db(env):
    return str(Path(env.hub.gateway.workspace_dir(env.ws)) / orch_store.DB_NAME)


def heartbeat(env, lease):
    return env.hub.rpc(env.a_token, "tools/call", {"name": "orch_heartbeat", "arguments": lease})


def test_heartbeat_waits_out_a_lock_released_inside_the_timeout(env):
    lease = claim(env)
    holder = hold_write_lock(runner_db(env))
    release = threading.Timer(1.0, holder.rollback)
    release.start()
    try:
        response = heartbeat(env, lease)
    finally:
        release.join()
        holder.close()
    assert "error" not in response, response
    assert response["result"]["content"][0]["text"]


def test_heartbeat_under_a_longer_lock_is_retryable_not_internal_error(env, monkeypatch, caplog):
    lease = claim(env)
    short_busy_timeout(monkeypatch, 0.2)
    holder = hold_write_lock(runner_db(env))
    try:
        with caplog.at_level(logging.ERROR):
            response = heartbeat(env, lease)
    finally:
        holder.rollback()
        holder.close()
    error = response["error"]
    assert error["code"] != -32603 and error["message"] != "Internal error"
    assert error["data"]["retryable"] is True
    assert error["data"]["retry_after_s"] > 0
    message = caplog.records[-1].getMessage()
    assert "call=orch_heartbeat" in message and "OperationalError" in message
    assert lease["token"] not in message
    # The lock is gone: the same call goes through.
    assert "error" not in heartbeat(env, lease)


def test_non_busy_failures_stay_internal_errors(env, monkeypatch):
    lease = claim(env)

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("no such table: secret_card_title")

    monkeypatch.setattr(orch_store.Store, "transaction", boom)
    body = {
        "jsonrpc": "2.0",
        "id": 9,
        "method": "tools/call",
        "params": {"name": "orch_heartbeat", "arguments": lease},
    }
    status, text = env.hub.raw(env.a_token, body)
    assert status == 500 and '"code": -32603' in text


def test_control_db_has_an_explicit_busy_timeout(hub):
    wait_ms = hub.db._conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert wait_ms >= 10_000


def busy():
    from commonplace import db_busy

    return db_busy


class FlakyConn:
    def __init__(self, failures, error):
        self.failures, self.error, self.calls = failures, error, 0

    def execute(self, sql):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error


def test_begin_immediate_retries_an_instant_busy_refusal():
    conn = FlakyConn(2, sqlite3.OperationalError("database is locked"))
    busy().begin_immediate(conn)
    assert conn.calls == 3


def test_begin_immediate_gives_up_after_the_bound():
    conn = FlakyConn(99, sqlite3.OperationalError("database is locked"))
    with pytest.raises(sqlite3.OperationalError):
        busy().begin_immediate(conn)
    assert conn.calls == busy().BEGIN_ATTEMPTS


def test_begin_immediate_does_not_retry_other_errors():
    conn = FlakyConn(1, sqlite3.OperationalError("disk I/O error"))
    with pytest.raises(sqlite3.OperationalError):
        busy().begin_immediate(conn)
    assert conn.calls == 1


def test_is_busy_matches_lock_errors_only():
    assert busy().is_busy(sqlite3.OperationalError("database is locked"))
    assert not busy().is_busy(sqlite3.OperationalError("no such table: x"))
    assert not busy().is_busy(ValueError("database is locked"))
