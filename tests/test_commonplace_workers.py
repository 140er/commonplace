"""A real ``mempalace`` worker subprocess behind the supervisor."""

import http.client
import json
import sys
from pathlib import Path

from commonplace.workers import _PASSED_ENV, SubprocessLauncher, WorkerSupervisor
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
    # A shared remote store would mix workspaces; the worker must not see it.
    monkeypatch.setenv("MEMPALACE_BACKEND", "qdrant")
    monkeypatch.setenv("MEMPALACE_QDRANT_URL", "http://127.0.0.1:1")
    for secret in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "MEMPALACE_LLM_API_KEY",
        "MEMPALACE_ACCEPT_EXTERNAL_LLM",
        "COMMONPLACE_DATA",
    ):
        monkeypatch.setenv(secret, "leak-me")
    monkeypatch.setenv("MEMPALACE_EMBEDDING_THREADS", "2")
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
        if sys.platform.startswith("linux"):
            environ = Path(f"/proc/{worker.pid}/environ").read_bytes().split(b"\0")
            names = {e.split(b"=", 1)[0].decode() for e in environ if e}
            assert "leak-me".encode() not in b"\0".join(environ)
            assert "MEMPALACE_EMBEDDING_THREADS" in names
            assert names <= _PASSED_ENV | {
                "HOME",
                "USERPROFILE",
                "MEMPALACE_CONFIG_DIR",
                "MEMPALACE_PALACE_PATH",
                "MEMPALACE_MCP_HTTP_TOKEN",
                "MEMPALACE_MCP_TRUSTED_IDENTITY",
            }, names
        ws_dir = tmp_path / "data" / "workspaces" / "ws_1a2b"
        assert (ws_dir / "palace").is_dir() and (ws_dir / "home").is_dir()
        assert not elsewhere.exists()
    finally:
        sup.stop_all()
    assert not worker.alive()
