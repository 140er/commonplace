#!/usr/bin/env python3
"""Check a fresh local client setup without accounts, credentials or downloads."""

from __future__ import annotations

import http.client
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[1]
BUDGET_SECONDS = 300

# Installed only in the disposable environment, including its real worker.
# Block Python network access beyond loopback even if a dependency tries it.
NETWORK_GUARD = """\
import ipaddress
import sys

def loopback(host):
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False

def audit(event, args):
    if event == "socket.getaddrinfo" and not loopback(args[0]):
        raise OSError("setup check permits loopback only")
    if event in {"socket.connect", "socket.sendto"}:
        address = args[1]
        if not isinstance(address, tuple) or not loopback(address[0]):
            raise OSError("setup check permits loopback only")

sys.addaudithook(audit)
"""


class CheckFailed(Exception):
    pass


def run(argv, *, env, cwd, timeout):
    """Capture output so the ephemeral token never reaches the console."""
    result = subprocess.run(
        argv, env=env, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
    )
    if result.returncode:
        # Don't dump CLI output: it may contain a newly issued credential.
        raise CheckFailed(f"{Path(argv[0]).name} command failed (exit {result.returncode})")
    return result.stdout.strip()


def post(config, message, *, timeout, headers=None):
    endpoint = urlsplit(config["url"])
    connection = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=timeout)
    try:
        connection.request(
            "POST",
            endpoint.path,
            json.dumps(message),
            {
                "Content-Type": "application/json",
                **(config["headers"] if headers is None else headers),
            },
        )
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def rpc(config, method, *, timeout, params=None, notification=False):
    message = {"jsonrpc": "2.0", "method": method}
    if not notification:
        message["id"] = 1
    if params is not None:
        message["params"] = params
    status, raw = post(config, message, timeout=timeout)
    if notification:
        if status != 202:
            raise CheckFailed(f"{method}: expected HTTP 202, got {status}")
        return None
    if status != 200:
        raise CheckFailed(f"{method}: expected HTTP 200, got {status}")
    response = json.loads(raw)
    if response.get("id") != 1 or "error" in response or "result" not in response:
        raise CheckFailed(f"{method}: invalid JSON-RPC response")
    return response["result"]


def gateway_port(process, timeout):
    """Drain stderr in the background; wait for the port chosen by --port 0."""
    ports = queue.Queue()

    def read():
        for line in process.stderr:
            match = re.search(r"commonplace gateway on 127\.0\.0\.1:(\d+)", line)
            if match:
                ports.put(int(match[1]))
        ports.put(None)

    threading.Thread(target=read, daemon=True).start()
    port = ports.get(timeout=timeout)
    if port is None:
        raise CheckFailed("gateway exited before listening")
    return port


def stop_gateway(process):
    """Stop the gateway gracefully, then remove any remaining worker processes."""
    try:
        process.terminate()
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        pass
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)
        process.stderr.close()


def check_setup():
    if os.name != "posix":
        raise CheckFailed("run this check on Linux or macOS")
    uv = shutil.which("uv")
    if uv is None:
        raise CheckFailed("uv is required; prepare the cache with uv sync --extra dev")
    # Discover the existing dependency cache before isolating the home directory.
    cache = subprocess.check_output([uv, "cache", "dir"], text=True).strip()
    started = time.monotonic()

    def remaining():
        seconds = BUDGET_SECONDS - (time.monotonic() - started)
        if seconds <= 0:
            raise CheckFailed("setup exceeded the 300-second budget")
        return seconds

    with tempfile.TemporaryDirectory(prefix="commonplace-setup-") as directory:
        root = Path(directory)
        home = root / "home"
        home.mkdir()
        venv = root / "venv"
        # An allowlist keeps real tokens, provider keys and palace settings out.
        env = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL") if key in os.environ}
        env.update(
            HOME=str(home),
            USERPROFILE=str(home),
            XDG_CONFIG_HOME=str(home / ".config"),
            XDG_CACHE_HOME=str(home / ".cache"),
            UV_CACHE_DIR=cache,
            UV_PROJECT_ENVIRONMENT=str(venv),
            UV_PYTHON_DOWNLOADS="never",
            HF_HUB_OFFLINE="1",
            TRANSFORMERS_OFFLINE="1",
            ANONYMIZED_TELEMETRY="False",
        )
        run(
            [
                uv,
                "sync",
                "--offline",
                "--frozen",
                "--no-dev",
                "--no-editable",
                "--no-config",
                "--project",
                str(REPO),
                "--python",
                sys.executable,
            ],
            env=env,
            cwd=root,
            timeout=remaining(),
        )
        installed = time.monotonic() - started
        python = str(venv / "bin" / "python")
        site = Path(
            run(
                [python, "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
                env=env,
                cwd=root,
                timeout=remaining(),
            )
        )
        (site / "sitecustomize.py").write_text(NETWORK_GUARD, encoding="utf-8")
        cli = str(venv / "bin" / "commonplace")
        run(
            [cli, "init", "--admin-email", "setup@example.invalid"],
            env=env,
            cwd=root,
            timeout=remaining(),
        )
        token = run(
            [
                cli,
                "token",
                "add",
                "claude-code:setup",
                "--workspace",
                "personal",
                "--client",
                "claude-code",
            ],
            env=env,
            cwd=root,
            timeout=remaining(),
        )
        if not token.startswith("cmmn_") or "\n" in token:
            raise CheckFailed("token add did not return a single token")
        process = subprocess.Popen(
            [cli, "serve", "--host", "127.0.0.1", "--port", "0"],
            env=env,
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            port = gateway_port(process, remaining())
            config_path = root / ".mcp.json"
            config_path.touch(mode=0o600)
            config_path.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "commonplace": {
                                "type": "http",
                                "url": f"http://127.0.0.1:{port}/mcp",
                                "headers": {"Authorization": f"Bearer {token}"},
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            config = json.loads(config_path.read_text(encoding="utf-8"))["mcpServers"][
                "commonplace"
            ]
            for headers in ({}, {"Authorization": "Bearer invalid-setup-token"}):
                status, _ = post(
                    config,
                    {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                    headers=headers,
                    timeout=remaining(),
                )
                if status != 401:
                    raise CheckFailed(f"invalid/missing token: expected HTTP 401, got {status}")
            initialized = rpc(
                config,
                "initialize",
                timeout=remaining(),
                params={
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "commonplace-setup-check", "version": "1"},
                },
            )
            if initialized.get("serverInfo", {}).get("name") != "mempalace":
                raise CheckFailed("initialize did not reach the memory worker")
            rpc(config, "notifications/initialized", timeout=remaining(), notification=True)
            tools = rpc(config, "tools/list", timeout=remaining())["tools"]
            names = {tool["name"] for tool in tools}
            if not {"mempalace_search", "memory_search", "task_list"} <= names:
                raise CheckFailed("tools/list is missing memory or board tools")
            remaining()
            elapsed = time.monotonic() - started
        finally:
            stop_gateway(process)
    print(
        f"PASS: fresh offline install {installed:.2f}s; connected {elapsed:.2f}s / 300s; "
        f"{len(tools)} tools; missing/invalid tokens rejected; temporary state removed"
    )


def main():
    try:
        check_setup()
    except (
        CheckFailed,
        OSError,
        ValueError,
        KeyError,
        queue.Empty,
        subprocess.SubprocessError,
    ) as exc:
        # Timeout exceptions can contain argv; never include them in diagnostics.
        detail = str(exc) if isinstance(exc, CheckFailed) else type(exc).__name__
        print(
            f"FAIL: {detail}. Prepare dependencies with uv sync --extra dev; "
            "the check never downloads missing packages.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
