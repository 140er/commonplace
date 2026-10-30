"""A real ``mempalace`` worker subprocess behind the supervisor."""

import http.client
import json

from commonplace.workers import SubprocessLauncher, WorkerSupervisor
from mempalace import provenance

IDENTITY = {
    "user": "u",
    "connection": "c",
    "client": "x",
    "auth": "pat",
    "trust": "standard",
    "workspace": "ws_1",
}


def _post(worker, headers):
    conn = http.client.HTTPConnection(worker.host, worker.port, timeout=30)
    try:
        conn.request(
            "POST",
            "/mcp",
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
            {"Content-Type": "application/json", "Host": worker.host_header, **headers},
        )
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


def test_subprocess_worker_is_isolated_and_locked(tmp_path, monkeypatch):
    elsewhere = tmp_path / "operator-palace"
    monkeypatch.setenv("MEMPALACE_PALACE_PATH", str(elsewhere))
    monkeypatch.setenv("MEMPALACE_MCP_HTTP_TOKEN", "operator-token")
    sup = WorkerSupervisor(
        str(tmp_path / "data"), launcher=SubprocessLauncher(startup_timeout_s=120)
    )
    worker = sup.get("ws_1a2b")
    try:
        assert worker.token != "operator-token"
        assert _post(worker, {})[0] == 401
        assert _post(worker, {"Authorization": "Bearer operator-token"})[0] == 401
        assert _post(worker, {"Authorization": f"Bearer {worker.token}"})[0] == 403
        ok = {
            "Authorization": f"Bearer {worker.token}",
            provenance.IDENTITY_HEADER: provenance.encode_identity(IDENTITY),
        }
        status, body = _post(worker, ok)
        assert status == 200 and json.loads(body)["result"] == {}
        ws_dir = tmp_path / "data" / "workspaces" / "ws_1a2b"
        assert (ws_dir / "palace").is_dir() and (ws_dir / "home").is_dir()
        assert not elsewhere.exists()
    finally:
        sup.stop_all()
    assert not worker.alive()
