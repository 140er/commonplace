"""Bounded fact channel and completion ordering, without policy defaults."""

import importlib
import os
import threading

import pytest


def lifecycle():
    return importlib.import_module("mempalace.worker_lifecycle")


def test_completion_drains_only_after_final_response():
    module = lifecycle()
    events, facts = [], []
    coordinator = module.Coordinator(
        fact=facts.append,
        cleanup=lambda: events.append("cleanup"),
        exit=lambda: events.append("exit"),
    )
    coordinator.ready()
    for _ in range(16):
        assert coordinator.acquire() and coordinator.admit()
        coordinator.release(storage=True, completed=True)
    assert coordinator.acquire() and coordinator.admit()
    coordinator.drain("parent_lost")
    assert not coordinator.acquire()
    assert events == []
    events.append("flushed")
    coordinator.release(storage=True, completed=True)
    coordinator.drain("shutdown")
    assert events == ["flushed", "cleanup", "exit"]
    assert [r["kind"] for r in facts] == ["ready", "completed_16", "retiring"]
    assert facts[-1]["admitted"] == facts[-1]["completed"] == 17
    assert all(len(__import__("json").dumps(f).encode()) <= 512 for f in facts)


def test_actual_liveness_eof_waits_for_completion(monkeypatch):
    module = lifecycle()
    read_fd, write_fd = os.pipe()
    monkeypatch.setenv(module.LIVENESS_ENV, str(read_fd))
    monkeypatch.delenv(module.FACTS_ENV, raising=False)
    if os.name == "nt":
        import msvcrt

        monkeypatch.setenv(module.LIVENESS_ENV, str(msvcrt.get_osfhandle(read_fd)))
        pytest.skip("Windows inherited-handle ownership is exercised by subprocess smoke")
    closed = threading.Event()
    coordinator = module.child_coordinator(cleanup=closed.set, exit=lambda: None)
    assert coordinator.acquire() and coordinator.admit()
    original_drain = coordinator.drain
    eof = threading.Event()

    def drain(reason):
        original_drain(reason)
        eof.set()

    coordinator.drain = drain
    os.close(write_fd)
    assert eof.wait(5), "invalid harness: liveness EOF not delivered"
    assert not closed.is_set() and not coordinator.acquire()
    coordinator.release(storage=True, completed=True)
    assert closed.is_set()


def test_circuit_unknown_death_and_early_rss():
    module = lifecycle()
    now = [0.0]
    circuit = module.RestartCircuit(lambda: now[0])
    circuit.ended(None, None)
    assert circuit.failures == 1
    circuit.ended({"reason": "count", "admitted": 1}, 1)
    assert circuit.failures == 1
    for _ in range(2):
        circuit.ended({"reason": "rss", "admitted": 1}, 1)
    assert not circuit.available()
    now[0] = 60
    assert circuit.available()
    circuit.healthy(16)
    assert circuit.failures == 0


def test_fact_schema_rejects_unknown_fields_and_invented_counts():
    module = lifecycle()
    control = module.ControlPipes()
    try:
        base = {"v": 1, "kind": "ready", "generation": control.generation}
        assert control.accept(base)
        assert not control.accept({**base, "request": "forbidden"})
        assert control.completed is None and control.terminal is None
        assert not control.accept({**base, "generation": "wrong"})
    finally:
        control.close()


def test_http_drain_refuses_before_body_and_flushes_final_reply(monkeypatch):
    import http.client
    import json

    from mempalace import mcp_server

    entered = threading.Event()
    release = threading.Event()
    cleaned = threading.Event()
    replies = []
    monkeypatch.setenv("MEMPALACE_MCP_HTTP_TOKEN", "fixture-token")
    monkeypatch.delenv("MEMPALACE_MCP_TRUSTED_IDENTITY", raising=False)
    monkeypatch.setattr(mcp_server, "_http_coordinator", None)

    def dispatch(request):
        entered.set()
        assert release.wait(5), "invalid harness: reply not released"
        return {"jsonrpc": "2.0", "id": request["id"], "result": "exact fixture words"}

    monkeypatch.setattr(mcp_server, "_http_dispatch", dispatch)
    server = mcp_server._build_http_server("127.0.0.1", 0)
    server.coordinator.cleanup = cleaned.set
    port = server.server_address[1]
    serving = threading.Thread(target=server.serve_forever)
    serving.start()

    def first():
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            conn.request(
                "POST",
                "/mcp",
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {"name": "fixture"},
                    }
                ),
                {"Authorization": "Bearer fixture-token", "Content-Type": "application/json"},
            )
            response = conn.getresponse()
            replies.append(
                (
                    response.status,
                    response.getheader("X-Mempalace-State"),
                    response.getheader("X-Mempalace-Admitted"),
                    response.read(),
                )
            )
        finally:
            conn.close()

    thread = threading.Thread(target=first)
    refusal = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    thread.start()
    try:
        assert entered.wait(5), "invalid harness: request not accepted"
        server.coordinator.drain("parent_lost")
        assert not cleaned.is_set()
        refusal.putrequest("POST", "/mcp")
        refusal.putheader("Authorization", "Bearer fixture-token")
        refusal.putheader("Content-Type", "application/json")
        refusal.putheader("Content-Length", "999")
        refusal.endheaders()
        # No body is ever sent. Refusal must precede any read of those 999 bytes.
        response = refusal.getresponse()
        body = json.loads(response.read())
        assert response.status == 503 and body["id"] is None
        assert response.getheader("X-Mempalace-Dispatch") == "not-started"
        assert body["error"]["data"] == {
            "dispatch_started": False,
            "generation": server.coordinator.generation,
            "reason": "recycling",
        }
        assert not cleaned.is_set()
        release.set()
        thread.join(5)
        assert not thread.is_alive()
        assert replies[0][:3] == (200, "DRAINING", "1")
        assert json.loads(replies[0][3])["result"] == "exact fixture words"
        assert cleaned.wait(5)
    finally:
        release.set()
        thread.join(5)
        refusal.close()
        server.shutdown()
        server.server_close()
        serving.join(5)


def test_expired_parent_does_not_hide_live_child(tmp_path, monkeypatch):
    import json

    from mempalace import server_registry

    module = lifecycle()
    monkeypatch.setattr(module, "process_identity", lambda pid: {11: 100, 12: 200}.get(pid))
    monkeypatch.setattr(server_registry, "server_state_dir", lambda _: tmp_path)
    marker = {
        "pid": 11,
        "start_identity": 99,
        "child_pid": 12,
        "child_start_identity": 200,
        "state": "running",
    }
    (tmp_path / "monitor.json").write_text(json.dumps(marker))
    assert server_registry.read_monitor_marker("fixture") is None
    assert server_registry.live_child_record("fixture") == marker
    marker["child_start_identity"] = 199
    (tmp_path / "monitor.json").write_text(json.dumps(marker))
    assert server_registry.live_child_record("fixture") is None


@pytest.mark.parametrize("matched", [True, False])
def test_hub_client_recognizes_proved_refusal_without_replay(monkeypatch, matched):
    import io
    import json
    import urllib.error

    from mempalace.hub_client import HubClient, ManagedHubUnavailable

    generation = "11111111-1111-4111-8111-111111111111"
    info = {"host": "127.0.0.1", "port": 1, "generation": generation}
    client = HubClient("fixture", info)
    calls = []
    headers = {
        "X-Mempalace-Generation": generation if matched else "wrong",
        "X-Mempalace-Dispatch": "not-started",
        "X-Mempalace-Reason": "recycling",
    }
    payload = {
        "jsonrpc": "2.0",
        "id": None,
        "error": {
            "code": -32071,
            "data": {"dispatch_started": False, "generation": generation, "reason": "recycling"},
        },
    }

    def fail(*args):
        calls.append(True)
        raise urllib.error.HTTPError(
            "http://127.0.0.1:1/mcp",
            503,
            "fixture",
            headers,
            io.BytesIO(json.dumps(payload).encode()),
        )

    monkeypatch.setattr(client, "_open", fail)
    with pytest.raises(ManagedHubUnavailable if matched else urllib.error.HTTPError):
        client.post({"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert calls == [True]


def test_orphan_publication_preserves_identity_without_child_serverinfo(tmp_path, monkeypatch):
    import json

    from mempalace import server_registry

    module = lifecycle()
    identities = {11: 100, 12: 200, os.getpid(): 300}
    monkeypatch.setattr(module, "process_identity", identities.get)
    monkeypatch.setattr(server_registry, "server_state_dir", lambda _: tmp_path)
    marker = {
        "pid": 11,
        "start_identity": 99,
        "child_pid": 12,
        "child_start_identity": 200,
        "state": "starting",
    }
    (tmp_path / "monitor.json").write_text(json.dumps(marker))
    for _ in range(2):
        server_registry.write_monitor_marker("fixture", "orphan_draining")
        published = server_registry.read_monitor_marker("fixture")
        assert published["pid"] == os.getpid()
        assert published["child_pid"] == 12
        assert published["child_start_identity"] == 200
        assert server_registry.live_child_record("fixture") == published
        assert not (tmp_path / "serverinfo.json").exists()


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX pipe descriptor fixture; process smoke covers EOF"
)
def test_runtime_server_uses_exact_eof_wired_coordinator(monkeypatch):
    from types import SimpleNamespace

    from mempalace import mcp_server as server

    assert server._build_http_server.__globals__ is vars(server)
    assert server._run_http_loop.__globals__ is vars(server)
    module = lifecycle()
    read_fd, write_fd = os.pipe()
    monkeypatch.setenv(module.CHILD_ENV, "1")
    monkeypatch.setenv(module.LIVENESS_ENV, str(read_fd))
    monkeypatch.delenv(module.FACTS_ENV, raising=False)
    monkeypatch.setattr(server, "_http_coordinator", None)
    monkeypatch.setattr(server, "_READ_ONLY", True)
    monkeypatch.setattr(server, "_args", SimpleNamespace(host="127.0.0.1", port=0))
    for name in (
        "_refresh_vector_disabled_flag",
        "_start_idle_exit_watchdog",
        "_start_write_stall_watchdog",
    ):
        monkeypatch.setattr(server, name, lambda: None)
    cleaned = threading.Event()
    monkeypatch.setattr(server, "_exit_running_registered_cleanup", cleaned.set)
    built = []

    def serve(host, port):
        built.append(server._build_http_server(host, port))
        assert built[0].coordinator is server._http_coordinator

    monkeypatch.setattr(server, "_serve_http", serve)
    try:
        server._run_http_loop()
        assert built[0].coordinator is server._http_coordinator
        os.close(write_fd)
        write_fd = None
        assert cleaned.wait(5), "invalid harness: EOF cleanup not delivered"
        assert built[0].coordinator.reason == "parent_lost"
        assert not built[0].coordinator.acquire()
    finally:
        if write_fd is not None:
            os.close(write_fd)
        for httpd in built:
            httpd.server_close()


def test_running_marker_invalid_serverinfo_stays_unavailable(tmp_path, monkeypatch):
    import json

    from mempalace import server_registry

    monkeypatch.setattr(server_registry, "server_state_dir", lambda _: tmp_path)
    server_registry.write_monitor_marker("fixture", "running")
    (tmp_path / "serverinfo.json").write_text(
        json.dumps({"pid": os.getpid(), "port": "bad", "host": "127.0.0.1"})
    )
    info = server_registry.read_live_serverinfo("fixture")
    assert info["hub_unavailable"] is True
    assert info["managed"] is True


def test_partial_hub_refusal_body_stays_unproven(monkeypatch):
    import http.client
    import io
    import urllib.error

    from mempalace.hub_client import HubClient

    class BrokenBody(io.BytesIO):
        def read(self, *args):
            raise http.client.IncompleteRead(b"fixture")

    client = HubClient("fixture", {"host": "127.0.0.1", "port": 1})
    error = urllib.error.HTTPError("http://127.0.0.1:1/mcp", 503, "fixture", {}, BrokenBody())
    calls = []

    def fail(*args):
        calls.append(True)
        raise error

    monkeypatch.setattr(client, "_open", fail)
    with pytest.raises(urllib.error.HTTPError) as caught:
        client.post({"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert caught.value is error
    assert calls == [True]
    error.close()
