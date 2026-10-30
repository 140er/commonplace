"""Real loopback UI fixture and a dependency-free browser protocol client.

The gateway, authentication, handoff wiring and stores are production code.
Only the Memory worker launcher is a stub: these pages must never start it.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import secrets
import shutil
import socket
import struct
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from commonplace.handoff.models import SettingRow, SuccessorRef, ThreadMessage, ThreadMeta
from commonplace.handoff.wiring import install
from tests._commonplace_helpers import Hub

SCRIPT_MESSAGE = '\n<script>window.handoffExecuted = "ran";</script>\r\n<&> "quoted" — café\n'
SUCCESSOR_MESSAGE = "Continuing #142 from the stored thread."
_LIMIT = 8 * 1024 * 1024


class NoWorkerLauncher:
    def start(self, workspace_id, workspace_dir):
        raise AssertionError("UI unexpectedly started a Memory worker")


class UIServer:
    def __init__(self, directory):
        self.hub = Hub(directory, NoWorkerLauncher())
        self.ctx = None
        try:
            self.ctx = install(self.hub.gateway, directory, start_outbox=False)
            member = self.hub.db.create_user("member@example.com")
            self.hub.db.add_member(self.hub.workspace, member, "member", actor=self.hub.admin)
            policy = dict(
                profile="full",
                scopes=["memory:read", "threads:read"],
                read_wings=["threads"],
                trust="standard",
            )
            self.owner_token = self.hub.token("ui-owner", **policy)
            self.member_token = self.hub.token("ui-member", user=member, **policy)
            self.origin = f"http://127.0.0.1:{self.hub.port}"
            self._seed()
            status, _ = self.api("/api/settings/handoff")
            assert status == 200, "Gateway did not become ready"
        except BaseException:
            self.close()
            raise

    def _seed(self):
        principal = self.hub.gateway.authenticate(f"Bearer {self.owner_token}")
        store = self.ctx.store_for(self.hub.workspace)
        old = store.store(
            self.hub.workspace,
            "ui-old-thread",
            [
                ThreadMessage(0, "user", "message", "Finish #142 with every word preserved.", "{}"),
                ThreadMessage(1, "assistant", "message", SCRIPT_MESSAGE, SCRIPT_MESSAGE),
            ],
            actor=principal.actor,
        )
        successor = store.store(
            self.hub.workspace,
            "ui-successor-thread",
            [ThreadMessage(0, "assistant", "message", SUCCESSOR_MESSAGE, SUCCESSOR_MESSAGE)],
            actor=principal.actor,
            parent_stored_id=old.id,
        )
        self.old_path = f"/app/threads/{old.id}"
        self.successor_path = f"/app/threads/{successor.id}"
        done, _ = self.ctx.handoff_db.begin(
            self.hub.workspace,
            "ui-done",
            ThreadMeta(
                "ui-old-thread", agent="Codex", task_ref="#142", branch="feat/context-handoff"
            ),
            connection_id=principal.connection_id,
        )
        self.ctx.handoff_db.mark_stored(done.id, old.id, "Continue #142.")
        self.ctx.handoff_db.set_successor(
            done.id,
            SuccessorRef("Codex successor", self.origin + self.successor_path, successor.id),
        )
        failed, _ = self.ctx.handoff_db.begin(
            self.hub.workspace,
            "ui-failed",
            ThreadMeta("ui-failed-thread", agent="Claude", task_ref="#143"),
            connection_id=principal.connection_id,
        )
        self.ctx.handoff_db.mark_failed(failed.id, "Seeded storage failure")

    def api(self, path, *, token=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.hub.port, timeout=5)
        try:
            connection.request(
                "GET", path, headers={"Authorization": f"Bearer {token or self.owner_token}"}
            )
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def settings(self, enabled, *, task=False):
        """Seed a deterministic screenshot state through the real settings service."""
        self.ctx.settings.set(
            SettingRow(self.hub.workspace, "workspace", "", enabled=enabled),
            actor=self.hub.admin,
            is_owner=True,
        )
        if task:
            self.ctx.settings.set(
                SettingRow(self.hub.workspace, "task", "#142", enabled=False),
                actor=self.hub.admin,
                is_owner=True,
            )

    def close(self):
        try:
            if self.ctx is not None:
                self.ctx.close()
        finally:
            self.hub.close()


@contextmanager
def ui_server(directory):
    """Start a seeded Hub on an OS-assigned loopback port; always close it."""
    server = UIServer(Path(directory))
    try:
        yield server
    finally:
        server.close()


class WebSocket:
    """One synchronous RFC 6455 connection, restricted to loopback."""

    def __init__(self, url, *, timeout=10):
        parsed = urlsplit(url)
        if parsed.scheme != "ws" or parsed.hostname != "127.0.0.1":
            raise ValueError("Browser socket must use loopback")
        self.timeout = timeout
        self.buffer = bytearray()
        self.sock = socket.create_connection((parsed.hostname, parsed.port), timeout=timeout)
        try:
            key = base64.b64encode(secrets.token_bytes(16)).decode()
            path = parsed.path or "/"
            if parsed.query:
                path += "?" + parsed.query
            self.sock.sendall(
                (
                    f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{parsed.port}\r\n"
                    "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                    f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
                ).encode("ascii")
            )
            deadline = time.monotonic() + timeout
            while b"\r\n\r\n" not in self.buffer:
                if len(self.buffer) > 65536:
                    raise ValueError("Oversized browser upgrade")
                self.buffer.extend(self._recv(deadline))
            header, tail = bytes(self.buffer).split(b"\r\n\r\n", 1)
            self.buffer = bytearray(tail)
            lines = header.decode("ascii").split("\r\n")
            headers = {
                name.lower(): value.strip()
                for name, value in (line.split(":", 1) for line in lines[1:])
            }
            expected = base64.b64encode(
                hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
            ).decode()
            if (
                lines[0].split()[1] != "101"
                or headers.get("upgrade", "").lower() != "websocket"
                or "upgrade"
                not in [item.strip().lower() for item in headers.get("connection", "").split(",")]
                or headers.get("sec-websocket-accept") != expected
            ):
                raise ValueError("Invalid browser upgrade")
        except BaseException:
            self.sock.close()
            raise

    def _recv(self, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Browser socket timed out")
        self.sock.settimeout(remaining)
        data = self.sock.recv(65536)
        if not data:
            raise ConnectionError("Browser socket closed")
        return data

    def _read(self, length, deadline):
        while len(self.buffer) < length:
            self.buffer.extend(self._recv(deadline))
        result = bytes(self.buffer[:length])
        del self.buffer[:length]
        return result

    def send(self, payload, *, opcode=1):
        mask = secrets.token_bytes(4)
        length = len(payload)
        prefix = bytes([0x80 | opcode])
        if length < 126:
            prefix += bytes([0x80 | length])
        elif length < 65536:
            prefix += b"\xfe" + struct.pack("!H", length)
        else:
            prefix += b"\xff" + struct.pack("!Q", length)
        masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        self.sock.settimeout(self.timeout)
        self.sock.sendall(prefix + mask + masked)

    def receive(self, deadline):
        fragments = bytearray()
        started = False
        while True:
            first, second = self._read(2, deadline)
            final, opcode = bool(first & 0x80), first & 15
            if first & 0x70 or second & 0x80:
                raise ValueError("Invalid browser frame flags")
            length = second & 127
            if length == 126:
                length = struct.unpack("!H", self._read(2, deadline))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read(8, deadline))[0]
            if length + len(fragments) > _LIMIT:
                raise ValueError("Oversized browser message")
            if opcode >= 8 and (not final or length > 125):
                raise ValueError("Invalid browser control frame")
            payload = self._read(length, deadline)
            if opcode == 8:
                self.send(payload, opcode=8)
                raise ConnectionError("Browser sent close")
            if opcode == 9:
                self.send(payload, opcode=10)
                continue
            if opcode == 10:
                continue
            if opcode == 1 and not started:
                started = True
            elif opcode != 0 or not started:
                raise ValueError("Invalid browser message sequence")
            fragments.extend(payload)
            if final:
                return json.loads(fragments.decode("utf-8"))

    def close(self):
        self.sock.close()


class Devtools:
    def __init__(self, url):
        self.wire = WebSocket(url)
        self.identifier = 0

    def call(self, method, params=None, *, session=None):
        self.identifier += 1
        message = {"id": self.identifier, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        self.wire.send(json.dumps(message).encode())
        deadline = time.monotonic() + self.wire.timeout
        while True:
            reply = self.wire.receive(deadline)
            if reply.get("id") != self.identifier:
                continue  # Events may arrive between a command and its reply.
            if "error" in reply:
                raise RuntimeError(f"{method}: {reply['error']}")
            return reply.get("result", {})

    def close(self):
        self.wire.close()


class Browser:
    """An isolated browser context with real cookies and HTML form navigation."""

    def __init__(self, tools, *, isolated=True):
        self.tools = tools
        self.context = None
        self.target = None
        try:
            params = {"url": "about:blank"}
            if isolated:
                self.context = tools.call("Target.createBrowserContext")["browserContextId"]
                params["browserContextId"] = self.context
            self.target = tools.call("Target.createTarget", params)["targetId"]
            self.session = tools.call(
                "Target.attachToTarget", {"targetId": self.target, "flatten": True}
            )["sessionId"]
            self.call("Page.enable")
            self.call("Runtime.enable")
        except BaseException:
            self.close()
            raise

    def call(self, method, params=None):
        return self.tools.call(method, params, session=self.session)

    def evaluate(self, expression, *, promise=False):
        result = self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": promise},
        )
        if "exceptionDetails" in result:
            raise RuntimeError(str(result["exceptionDetails"]))
        return result["result"].get("value")

    def wait(self, expression, *, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.evaluate(expression):
                return
            time.sleep(0.02)
        raise TimeoutError(f"Browser condition not reached: {expression}")

    def navigate(self, url):
        self.call("Page.navigate", {"url": url})
        self.wait(f"location.href === {json.dumps(url)} && document.readyState === 'complete'")

    def submit(self, selector, values, *, button="button", expected=None):
        """Fill the actual form, retaining its hidden CSRF input, and click."""
        self.evaluate(
            "(() => {const form = document.querySelector("
            + json.dumps(selector)
            + "); for (const [name, value] of Object.entries("
            + json.dumps(values)
            + ")) {const field = form.querySelector('[name=\"' + name + '\"]');"
            "if (field.type === 'checkbox') field.checked = value; else field.value = value;}"
            "window.handoffSubmitting = true; form.querySelector("
            + json.dumps(button)
            + ").click();})()"
        )
        self.wait(
            "!window.handoffSubmitting && document.readyState === 'complete'"
            + (f" && location.href === {json.dumps(expected)}" if expected else "")
        )

    def login(self, server, token):
        self.navigate(server.origin + "/app/login")
        self.submit("form.login", {"token": token}, expected=server.origin + "/app/settings/memory")

    def close(self):
        if self.context:
            self.tools.call("Target.disposeBrowserContext", {"browserContextId": self.context})
            self.context = None
        elif self.target:
            self.tools.call("Target.closeTarget", {"targetId": self.target})
        self.target = None


def lightpanda_binary():
    path = Path.home() / ".local/bin/lightpanda"
    return str(path) if path.is_file() else shutil.which("lightpanda")


def stop_process(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@contextmanager
def lightpanda(binary, directory):
    # Lightpanda cannot bind port zero and advertise the chosen port. Reserve a
    # dynamic port briefly; process-exit checks surface any bind collision.
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    log_path = Path(directory) / "lightpanda.log"
    with log_path.open("w+") as log:
        process = subprocess.Popen(
            [binary, "serve", "--host", "127.0.0.1", "--port", str(port), "--log-level", "error"],
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        tools = None
        try:
            deadline = time.monotonic() + 10
            while True:
                if process.poll() is not None:
                    raise RuntimeError(f"Lightpanda exited: {log_path.read_text()}")
                try:
                    tools = Devtools(f"ws://127.0.0.1:{port}/")
                    break
                except ConnectionRefusedError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Lightpanda startup timed out") from None
                    time.sleep(0.02)
            yield tools
        finally:
            if tools:
                tools.close()
            stop_process(process)


def form_body(fields):
    """Encode direct negative POST probes without relying on browser fetch CSP."""
    return urlencode(fields)
