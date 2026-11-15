"""A full data disk: writes fail loudly and retryably, leave nothing half-written, replay once.

Live failure (14-15 Nov 2026): the data disk filled and the memory worker logged
``WAL write failed: [Errno 28] No space left on device`` for hours while the same
calls were still answered as saved. The injected fault here is the same errno
raised on the write-ahead-log path (and on the store's own write), so the tests
need no real full disk.
"""

import errno
import json
import logging
import sqlite3

import pytest

from tests._commonplace_helpers import hub_fixture  # noqa: F401

FULL = "No space left on device"
STORAGE_FULL = "StorageFull"
STORAGE_FULL_CODE = -32006


class Disk:
    """A switch for a full disk on the write-ahead-log path."""

    def __init__(self, monkeypatch, wal, path):
        self.full = False
        self.refused = 0
        monkeypatch.setattr(wal, "_WAL_FILE", path / "wal" / "write_log.jsonl")
        monkeypatch.setattr(wal, "_WAL_INITIALIZED_DIR", None)
        real = wal.os.open

        def open_(target, flags, *args, **kwargs):
            if self.full and str(target).endswith("write_log.jsonl"):
                self.refused += 1
                raise OSError(errno.ENOSPC, FULL)
            return real(target, flags, *args, **kwargs)

        monkeypatch.setattr(wal.os, "open", open_)
        self.wal_file = wal._WAL_FILE


@pytest.fixture
def disk(monkeypatch, tmp_path):
    from mempalace import wal

    return Disk(monkeypatch, wal, tmp_path)


def _patch_server(monkeypatch, config, kg):
    from mempalace import mcp_server

    monkeypatch.setattr(mcp_server, "_config", config)
    monkeypatch.setattr(mcp_server, "_get_kg", lambda *a, **kw: kg)


def _call(name, **arguments):
    from mempalace import mcp_server

    return mcp_server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )


def _payload(response):
    return json.loads(response["result"]["content"][0]["text"])


def _ids(collection):
    return sorted(collection.get(include=[])["ids"])


ADD = {"wing": "notes", "room": "daily", "content": "the exact words that must not be lost"}


def test_wal_full_disk_refuses_the_write_and_stores_nothing(
    monkeypatch, config, kg, collection, disk
):
    _patch_server(monkeypatch, config, kg)
    disk.full = True

    response = _call("mempalace_add_drawer", **ADD)

    # Not saved, and said so: a retryable error, never an ok.
    assert "error" in response or response["result"].get("isError") is True, response
    error = response.get("error") or _payload(response)
    data = error.get("data", error)
    assert data["error_class"] == STORAGE_FULL
    assert data["retryable"] is True and data["retry_after_s"] > 0
    assert (response.get("error") or {}).get("code", STORAGE_FULL_CODE) == STORAGE_FULL_CODE
    # No half-written state: no drawer.
    assert _ids(collection) == []


def test_after_space_returns_the_same_write_lands_exactly_once(
    monkeypatch, config, kg, collection, disk
):
    _patch_server(monkeypatch, config, kg)
    disk.full = True
    refused = _call("mempalace_add_drawer", **ADD)
    assert "error" in refused or refused["result"].get("isError") is True
    assert _ids(collection) == []

    disk.full = False
    first = _payload(_call("mempalace_add_drawer", **ADD))
    again = _payload(_call("mempalace_add_drawer", **ADD))

    assert first["success"] is True and first["drawer_id"]
    # A second send of the same words is recognised, not stored again.
    assert again["success"] is True and again["drawer_id"] == first["drawer_id"]
    assert _ids(collection) == [first["drawer_id"]]
    # The journal now holds the intent and the outcome of the one write that landed.
    lines = [json.loads(line) for line in disk.wal_file.read_text().splitlines()]
    assert [line["operation"] for line in lines].count("add_drawer") >= 2


def test_a_store_that_runs_out_of_space_mid_write_answers_retryably(
    monkeypatch, config, kg, collection, disk
):
    from mempalace import mcp_server

    _patch_server(monkeypatch, config, kg)

    class FullStore:
        def __init__(self, inner):
            self.inner = inner
            self.full = True

        def upsert(self, *args, **kwargs):
            if self.full:
                raise sqlite3.OperationalError("database or disk is full")
            return self.inner.upsert(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self.inner, name)

    store = FullStore(collection)
    monkeypatch.setattr(mcp_server, "_get_collection", lambda *a, **kw: store)

    response = _call("mempalace_add_drawer", **ADD)

    # The flag MCP clients read, and the typed, retryable fields.
    assert response["result"].get("isError") is True, response
    body = _payload(response)
    assert body["success"] is False
    assert body["error_class"] == STORAGE_FULL
    assert body["retryable"] is True and body["retry_after_s"] > 0
    assert _ids(collection) == []

    store.full = False
    landed = _payload(_call("mempalace_add_drawer", **ADD))
    assert landed["success"] is True
    assert _ids(collection) == [landed["drawer_id"]]


def test_diary_and_bulk_writes_answer_a_full_disk_the_same_way(
    monkeypatch, config, kg, collection, disk
):
    _patch_server(monkeypatch, config, kg)
    disk.full = True

    diary = _call("mempalace_diary_write", agent_name="scribe", entry="today I kept notes")
    kg_add = _call("mempalace_kg_add", subject="Ada", predicate="likes", object="tea")

    for response in (diary, kg_add):
        error = response.get("error") or _payload(response)
        assert error.get("data", error)["error_class"] == STORAGE_FULL, response
    assert _ids(collection) == []


def test_the_free_space_floor_refuses_a_write_before_it_starts(
    monkeypatch, config, kg, collection, disk, caplog
):
    _patch_server(monkeypatch, config, kg)
    monkeypatch.setenv("MEMPALACE_MIN_FREE_BYTES", str(2**62))
    caplog.set_level(logging.WARNING)

    first = _call("mempalace_add_drawer", **ADD)
    second = _call("mempalace_add_drawer", **ADD)

    for response in (first, second):
        assert response["error"]["code"] == STORAGE_FULL_CODE, response
        assert response["error"]["data"]["retryable"] is True
        assert response["error"]["data"]["retry_after_s"] > 0
    # Refused at the door: no drawer and not even a journal line.
    assert _ids(collection) == []
    assert not disk.wal_file.exists()
    # One warning with the free space for the two refusals, not one per write.
    warnings = [r for r in caplog.records if "free" in r.getMessage() and "floor" in r.getMessage()]
    assert len(warnings) == 1, [r.getMessage() for r in caplog.records]

    monkeypatch.setenv("MEMPALACE_MIN_FREE_BYTES", "0")
    assert _payload(_call("mempalace_add_drawer", **ADD))["success"] is True
    assert len(_ids(collection)) == 1


def test_deleting_is_not_refused_by_the_floor(monkeypatch, config, kg, collection, disk):
    _patch_server(monkeypatch, config, kg)
    saved = _payload(_call("mempalace_add_drawer", **ADD))
    monkeypatch.setenv("MEMPALACE_MIN_FREE_BYTES", str(2**62))

    gone = _payload(_call("mempalace_delete_drawer", drawer_id=saved["drawer_id"]))

    assert gone["success"] is True
    assert _ids(collection) == []


def test_status_reports_free_space_and_low_disk(monkeypatch, config, kg, collection, disk):
    _patch_server(monkeypatch, config, kg)

    normal = _payload(_call("mempalace_status"))
    assert isinstance(normal["data_free_bytes"], int) and normal["data_free_bytes"] > 0
    assert normal["disk_low"] is False

    monkeypatch.setenv("MEMPALACE_MIN_FREE_BYTES", str(2**62))
    assert _payload(_call("mempalace_status"))["disk_low"] is True


def test_a_failed_journal_result_never_breaks_the_caller(monkeypatch, config, kg, collection, disk):
    """The write already landed; only its outcome line could not be journaled."""
    from mempalace import wal

    _patch_server(monkeypatch, config, kg)
    real = wal._wal_result

    def full_result(operation, result):
        disk.full = True
        try:
            real(operation, result)
        finally:
            disk.full = False

    monkeypatch.setattr(wal, "_wal_result", full_result)
    monkeypatch.setattr("mempalace.mcp_server._wal_result", full_result)

    saved = _payload(_call("mempalace_add_drawer", **ADD))

    assert saved["success"] is True
    assert _ids(collection) == [saved["drawer_id"]]


def test_a_short_write_leaves_no_torn_line_for_the_next_entry(monkeypatch, tmp_path):
    from mempalace import wal

    monkeypatch.setattr(wal, "_WAL_FILE", tmp_path / "wal" / "write_log.jsonl")
    monkeypatch.setattr(wal, "_WAL_INITIALIZED_DIR", None)
    wal._wal_log("before", {"n": 1})
    real_write = wal.os.write
    state = {"cut": True}

    def short(fd, data):
        if state["cut"]:
            return real_write(fd, bytes(data)[: len(data) // 2])
        return real_write(fd, data)

    monkeypatch.setattr(wal.os, "write", short)
    with pytest.raises(OSError) as refused:
        wal._wal_log("torn", {"n": 2})
    assert refused.value.errno == errno.ENOSPC

    state["cut"] = False
    wal._wal_log("after", {"n": 3})

    lines = wal._WAL_FILE.read_text().splitlines()
    assert [json.loads(line)["operation"] for line in lines] == ["before", "after"]


def test_logging_survives_a_full_disk_and_keeps_the_first_lost_line(capsys):
    import io

    from mempalace.storage_guard import harden

    class Stream(io.StringIO):
        full = True

        def write(self, text):
            if self.full:
                raise OSError(errno.ENOSPC, FULL)
            return super().write(text)

    stream = Stream()
    handler = harden(logging.StreamHandler(stream))
    assert type(handler) is logging.StreamHandler  # same class, only its error path changes
    handler.setFormatter(logging.Formatter("%(message)s"))
    log = logging.getLogger("enospc-test")
    log.propagate = False
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    try:
        log.error("WAL write failed: the first error")  # must not raise
        log.error("a later error")
        assert "Logging error" not in capsys.readouterr().err
        stream.full = False
        log.error("space is back")
    finally:
        log.removeHandler(handler)

    text = stream.getvalue()
    assert "2 log line(s) were lost" in text
    assert "First lost: WAL write failed: the first error" in text
    assert text.rstrip().endswith("space is back")


def test_the_library_has_no_floor_until_one_is_set_and_a_typo_keeps_the_default(monkeypatch):
    from mempalace import storage_guard

    monkeypatch.delenv("MEMPALACE_MIN_FREE_BYTES", raising=False)
    # The bare library has no floor of its own; a deployment passes one.
    assert storage_guard.min_free_bytes() == 0
    assert storage_guard.min_free_bytes(storage_guard.DEPLOYED_MIN_FREE_BYTES) == 256 * 1024 * 1024
    monkeypatch.setenv("MEMPALACE_MIN_FREE_BYTES", "0")
    assert storage_guard.min_free_bytes(storage_guard.DEPLOYED_MIN_FREE_BYTES) == 0
    monkeypatch.setenv("MEMPALACE_MIN_FREE_BYTES", "lots")
    assert storage_guard.min_free_bytes(storage_guard.DEPLOYED_MIN_FREE_BYTES) == 256 * 1024 * 1024
    monkeypatch.setenv("MEMPALACE_MIN_FREE_BYTES", "1048576")
    assert storage_guard.min_free_bytes() == 1048576


@pytest.mark.parametrize(
    "exc",
    [
        OSError(errno.ENOSPC, FULL),
        OSError(errno.EDQUOT, "Disk quota exceeded"),
        sqlite3.OperationalError("database or disk is full"),
        RuntimeError("error returned from database: (code: 13) database or disk is full"),
    ],
)
def test_a_full_disk_is_recognised_however_it_is_wrapped(exc):
    from mempalace import storage_guard

    try:
        try:
            raise exc
        except Exception as inner:
            raise ValueError("write failed") from inner
    except ValueError as outer:
        assert storage_guard.is_storage_full(outer)
    assert not storage_guard.is_storage_full(PermissionError(errno.EACCES, "denied"))
    # What a person wrote is never mistaken for the disk saying so.
    assert not storage_guard.is_storage_full(ValueError("note: database or disk is full"))
    assert not storage_guard.is_storage_full_text("database or disk is full " + "x" * 500)


def test_a_queued_write_refused_for_a_full_disk_is_kept_and_runs_when_space_returns(
    tmp_path, monkeypatch
):
    """The daemon queue holds hook-submitted writes; a full disk must park them, not dead-letter them."""
    from mempalace import daemon
    from tests.test_daemon import _start_server, _stop_server

    calls = {"n": 0}
    refusals = daemon.MAX_ATTEMPTS + 2

    def fake_execute(kind, payload):
        calls["n"] += 1
        if calls["n"] <= refusals:
            return {
                "success": False,
                "error": "full",
                "error_class": STORAGE_FULL,
                "exit_code": 1,
            }
        return {"success": True, "exit_code": 0}

    monkeypatch.setattr(daemon, "LOCK_DEFER_BACKOFF_SECONDS", 0.02)
    monkeypatch.setattr(daemon, "STORAGE_DEFER_BACKOFF_SECONDS", 0.02, raising=False)
    client, thread, palace, holders = _start_server(tmp_path, monkeypatch, fake_execute)
    try:
        job = client.submit("diary_write", {"entry": "verbatim"})
        finished = client.wait(job["id"], timeout=20)
        assert finished["state"] == "succeeded"
        assert calls["n"] == refusals + 1
        assert finished["attempts"] == 1  # a refused claim is refunded
    finally:
        _stop_server(client, thread, holders)


# ── the gateway and the real worker ──────────────────────────────────────────


def _http(hub, token, path, body=None):
    status, text = hub.raw(
        token,
        body if body is not None else b"",
        method="POST" if body is not None else "GET",
        path=path,
    )
    return status, json.loads(text)


def _search(hub, token, text):
    status, body = _http(hub, token, "/api/memory/search?q=" + text.replace(" ", "%20"))
    assert status == 200, body
    return body["results"]


def test_rest_save_on_a_full_disk_is_a_507_with_a_retry_hint_then_lands_once(hub, disk):
    owner = hub.owner_token()
    words = "Kettle canary written during a full disk"
    disk.full = True

    status, body = _http(hub, owner, "/api/memory", {"text": words, "theme": "shared"})

    assert status == 507, body
    assert body["retry_after_s"] > 0 and "id" not in body
    assert body["error"].startswith("no: ") and "full" in body["error"]
    assert _search(hub, owner, words) == []

    disk.full = False
    status, saved = _http(hub, owner, "/api/memory", {"text": words, "theme": "shared"})
    assert status == 201, saved
    status, again = _http(hub, owner, "/api/memory", {"text": words, "theme": "shared"})
    assert status == 201 and again["id"] == saved["id"]
    assert len(_search(hub, owner, words)) == 1


def test_mcp_save_on_a_full_disk_is_an_error_not_a_saved(hub, disk):
    owner = hub.owner_token()
    disk.full = True

    response = hub.rpc(
        owner,
        "tools/call",
        {"name": "memory_save", "arguments": {"text": "Mcp canary during a full disk"}},
    )

    result = response["result"]
    assert result.get("isError") is True, response
    text = result["content"][0]["text"]
    assert text.startswith("no: ") and "not saved" in text and not text.startswith("saved")
    disk.full = False
    ok = hub.rpc(
        owner,
        "tools/call",
        {"name": "memory_save", "arguments": {"text": "Mcp canary during a full disk"}},
    )
    assert not ok["result"].get("isError"), ok


def test_gateway_status_still_counts_drawers_and_keeps_free_space_off_it(hub):
    """The worker's status now carries free-space fields; the gateway's fixed vocabulary drops them."""
    owner = hub.owner_token()
    saved = hub.rpc(
        owner, "tools/call", {"name": "memory_save", "arguments": {"text": "status canary words"}}
    )
    assert not saved["result"].get("isError"), saved

    response = hub.rpc(owner, "tools/call", {"name": "mempalace_status", "arguments": {}})

    status = _payload(response)
    assert status["total_drawers"] >= 1, status
    assert not {"data_free_bytes", "disk_low", "min_free_bytes"} & set(status)


def test_a_gateway_call_that_hits_a_full_disk_is_a_retryable_error_not_internal(hub, monkeypatch):
    from commonplace.gateway import Mediator

    def full(*args, **kwargs):
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(Mediator, "_tools_call", full)
    body = {
        "jsonrpc": "2.0",
        "id": 9,
        "method": "tools/call",
        "params": {"name": "memory_save", "arguments": {"text": "x"}},
    }

    status, text = hub.raw(hub.owner_token(), body)

    answer = json.loads(text)
    assert status == 200 and answer["error"]["code"] == STORAGE_FULL_CODE, text
    assert answer["error"]["data"]["retryable"] is True
    assert answer["error"]["data"]["retry_after_s"] > 0


def _worker_environ(tmp_path) -> dict:
    import sys
    from pathlib import Path

    from commonplace.workers import SubprocessLauncher, WorkerSupervisor

    if not sys.platform.startswith("linux"):
        pytest.skip("reads /proc/<pid>/environ")
    sup = WorkerSupervisor(
        str(tmp_path / "data"), launcher=SubprocessLauncher(startup_timeout_s=120)
    )
    worker = sup.get("ws_floor")
    try:
        raw = Path(f"/proc/{worker.pid}/environ").read_bytes().split(b"\0")
    finally:
        sup.stop_all()
    return dict(e.decode().split("=", 1) for e in raw if e)


def test_a_deployed_worker_starts_with_the_256_mib_floor(tmp_path, monkeypatch):
    monkeypatch.delenv("MEMPALACE_MIN_FREE_BYTES", raising=False)
    assert _worker_environ(tmp_path).get("MEMPALACE_MIN_FREE_BYTES") == str(256 * 1024 * 1024)


def test_the_operator_floor_reaches_the_worker(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMPALACE_MIN_FREE_BYTES", "1048576")
    assert _worker_environ(tmp_path).get("MEMPALACE_MIN_FREE_BYTES") == "1048576"
