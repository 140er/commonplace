"""Failure paths for the disposable setup check (the full check runs separately)."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.check_commonplace_setup import NETWORK_GUARD, CheckFailed, rpc


@pytest.mark.parametrize(
    "operation",
    [
        "socket.getaddrinfo('example.invalid', 443)",
        "socket.socket().connect(('203.0.113.1', 443))",
        "socket.socket(type=socket.SOCK_DGRAM).sendto(b'x', ('203.0.113.1', 53))",
    ],
)
def test_network_guard_blocks_external_access_but_allows_loopback(operation):
    program = (
        NETWORK_GUARD
        + f"""
import socket
try:
    {operation}
except OSError as exc:
    assert str(exc) == "setup check permits loopback only"
else:
    raise AssertionError("external access allowed")
with socket.socket() as listener, socket.socket() as client:
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    client.connect(listener.getsockname())
print("blocked external access; connected on loopback")
"""
    )
    result = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr
    assert "connected on loopback" in result.stdout


@pytest.mark.skipif(os.name != "posix", reason="the fresh-install check requires POSIX")
def test_empty_cache_fails_offline_and_removes_temporary_state(tmp_path):
    if shutil.which("uv") is None:
        pytest.skip("uv is required for the standalone install check")
    script = Path(__file__).resolve().parents[1] / "scripts" / "check_commonplace_setup.py"
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    home = tmp_path / "untouched-home"
    home.mkdir()
    sentinel = home / "keep.txt"
    sentinel.write_text("existing user data", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path,
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(home),
            "UV_CACHE_DIR": str(tmp_path / "empty-cache"),
            "TMPDIR": str(scratch),
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 1
    assert "FAIL: uv command failed" in result.stderr
    assert "never downloads missing packages" in result.stderr
    assert not list(scratch.iterdir())
    assert list(home.iterdir()) == [sentinel]
    assert sentinel.read_text(encoding="utf-8") == "existing user data"


def test_rpc_rejects_jsonrpc_error_even_with_http_success(monkeypatch):
    monkeypatch.setattr(
        "scripts.check_commonplace_setup.post",
        lambda *args, **kwargs: (200, b'{"jsonrpc":"2.0","id":1,"error":{"code":-32603}}'),
    )
    with pytest.raises(CheckFailed, match="invalid JSON-RPC response"):
        rpc({}, "initialize", timeout=1)
