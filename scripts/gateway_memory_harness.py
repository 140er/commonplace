#!/usr/bin/env python
"""Synthetic load for the gateway process: where does its own memory go?

Runs the real gateway (``Gateway.serve``, the same reaper thread, backup socket and request
threads as ``commonplace serve``) in a child process against a stub memory worker in a third
process, so the figures are the gateway's alone. A control port in the gateway child answers
``sample`` (after a full ``gc.collect``) with the anonymous memory of the process, the Python
heap tracemalloc sees, live object counts, threads and open descriptors; ``diff`` lists the
allocation sites that grew since ``baseline``.

    python scripts/gateway_memory_harness.py routes [--requests N]   # per-route growth table
    python scripts/gateway_memory_harness.py soak --minutes M        # constant mixed load

Nothing here touches the network beyond loopback, and the data directory is a temporary one that
is removed afterwards. Not part of the shipped product; the growth tests import its helpers.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import http.client
import json
import os
import random
import re
import secrets
import selectors
import shutil
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
USERS = 24
# Routes the load never drives: they end the session being measured, reach outside loopback,
# or load a model the memory workers own.
SKIP = (
    ("POST", "/app/logout"),
    ("POST", "/api/account/deletion"),
    ("POST", "/api/phone-push/test"),
    ("POST", "/api/voice/transcribe"),
    ("GET", "/api/voice"),
    ("PATCH", "/api/voice"),
)
SKIP_PREFIXES = ("/api/connections/", "/app/auth/", "/oauth/", "/s/")


# ── the stub memory worker (its own process) ──────────────────────────────────────────────


def run_stub(port: int, token: str) -> int:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from mempalace.mcp_server import TOOLS

    tools = [
        {
            "name": name,
            "description": spec.get("description", ""),
            "inputSchema": spec["input_schema"],
        }
        for name, spec in TOOLS.items()
    ]

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if self.headers.get("Authorization") != f"Bearer {token}":
                return self._send(401, {"error": "unauthorized"})
            request = json.loads(body.decode("utf-8"))
            method, req_id = request.get("method"), request.get("id")
            if isinstance(method, str) and method.startswith("notifications/"):
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return None
            if method == "tools/list":
                result = {"tools": tools}
            elif method == "tools/call":
                arguments = (request.get("params") or {}).get("arguments") or {}
                if str(arguments.get("query", "")).startswith("busy"):
                    return self._send(
                        200,
                        {
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "error": {
                                "code": -32001,
                                "message": "busy",
                                "data": {"retryable": True, "retry_after_s": 2},
                            },
                        },
                    )
                name = (request.get("params") or {}).get("name")
                ids = arguments.get("drawer_ids") or [f"drawer_{i}" for i in range(5)]
                if name == "mempalace_get_drawers":
                    hits = [
                        {
                            "drawer_id": i,
                            "content": "stub drawer " * 40,
                            "wing": "w",
                            "room": "r",
                            "metadata": {"wing": "w", "room": "r", "hub_trust": "standard"},
                        }
                        for i in ids
                    ]
                else:
                    hits = [
                        {"drawer_id": i, "text": "stub drawer " * 40, "wing": "w", "room": "r"}
                        for i in ids
                    ]
                text = json.dumps({"results": hits, "count": len(hits), "total_drawers": 1234})
                result = {"content": [{"type": "text", "text": text}]}
            else:
                result = {}
            self._send(200, {"jsonrpc": "2.0", "id": req_id, "result": result})

        def _send(self, status, value):
            raw = json.dumps(value).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.serve_forever(poll_interval=0.1)
    return 0


# ── the gateway child ─────────────────────────────────────────────────────────────────────


def _status_kib(name: str) -> int:
    with open("/proc/self/status", encoding="ascii") as stream:
        for line in stream:
            if line.startswith(name + ":"):
                return int(line.split()[1])
    return 0


def process_sample(trace: bool) -> dict:
    import tracemalloc

    gc.collect()
    gc.collect()  # untrack snapshot tuples before counting live gateway objects
    objects = gc.get_objects()
    counts = Counter(type(obj).__name__ for obj in objects)
    resources = Counter(f"{type(obj).__module__}.{type(obj).__name__}" for obj in objects)
    del objects
    sample = {
        "anon_mib": round(_status_kib("RssAnon") / 1024, 2),
        "rss_mib": round(_status_kib("VmRSS") / 1024, 2),
        "threads": threading.active_count(),
        "os_threads": _status_kib("Threads"),
        "fds": len(os.listdir("/proc/self/fd")),
        "objects": sum(counts.values()),
        "top_types": counts.most_common(20),
        "sqlite_connections": sum(
            n
            for k, n in resources.items()
            if k.endswith(".Connection") and ("sqlite" in k or "sqlcipher" in k)
        ),
        "asyncio_tasks": resources["_asyncio.Task"],
        "sockets": resources["socket.socket"],
    }
    if trace and tracemalloc.is_tracing():
        sample["traced_kib"] = round(tracemalloc.get_traced_memory()[0] / 1024, 1)
    return sample


def allocator_arenas():
    """glibc's actual heap count, for the shipped-container growth regression."""
    import ctypes

    try:
        libc = ctypes.CDLL(None)
        libc.fdopen.argtypes = [ctypes.c_int, ctypes.c_char_p]
        libc.fdopen.restype = ctypes.c_void_p
        libc.malloc_info.argtypes = [ctypes.c_int, ctypes.c_void_p]
        libc.malloc_info.restype = ctypes.c_int
        libc.fflush.argtypes = [ctypes.c_void_p]
        libc.fclose.argtypes = [ctypes.c_void_p]
        with tempfile.TemporaryFile() as output:
            descriptor = os.dup(output.fileno())
            stream = libc.fdopen(descriptor, b"w+")
            if not stream:
                os.close(descriptor)
                return None
            try:
                if libc.malloc_info(0, stream) != 0:
                    return None
                libc.fflush(stream)
                output.seek(0)
                return output.read().count(b"<heap nr=")
            finally:
                libc.fclose(stream)
    except (OSError, AttributeError):
        return None


def serve_gateway(args) -> int:
    import logging
    import tracemalloc

    logging.basicConfig(level=logging.WARNING)

    from commonplace import sessions
    from commonplace.control_db import ControlDB
    from commonplace.gateway import Gateway
    from commonplace.policy import Policy
    from commonplace.workers import Worker, WorkerSupervisor

    data = Path(args.data)
    stub_port, stub_token = args.stub_port, args.stub_token

    class Launcher:
        def start(self, workspace_id, workspace_dir):
            return Worker(workspace_id, "127.0.0.1", stub_port, stub_token, lambda: None)

    db = ControlDB(str(data / "control.sqlite3"))
    gateway = Gateway(db, WorkerSupervisor(str(data), launcher=Launcher(), idle_stop_s=900))
    admin = db.create_user("owner@example.com", is_admin=True)
    home = db.create_workspace("home", admin, personal=True)
    full = Policy.build(
        profile="full",
        scopes=[
            "memory:read",
            "memory:write",
            "memory:delete",
            "kg:read",
            "kg:write",
            "events:read",
            "events:write",
            "private:read",
            "private:write",
        ],
        read_wings=["*"],
        write_wings=["*"],
        trust="trusted",
    )
    slim = Policy.build(
        profile="slim",
        scopes=["memory:read", "memory:write", "kg:read"],
        read_wings=["shared", "inbox"],
        write_wing="inbox",
        trust="untrusted",
    )
    owner_cid, owner_token = db.create_token(
        workspace_ref=home, user_ref=admin, name="owner", client="harness", policy=full, actor=admin
    )
    _cid, bot_token = db.create_token(
        workspace_ref=home, user_ref=admin, name="bot", client="harness", policy=slim, actor=admin
    )
    people = [
        {
            "workspace": home,
            "connection": owner_cid,
            "cookie": sessions.start_session(db, admin, home, via="harness"),
            "token": owner_token,
        }
    ]
    for index in range(USERS):
        user = db.create_user(f"person{index}@example.com")
        workspace = db.create_workspace(f"space{index}", user, personal=True)
        cid, token = db.create_token(
            workspace_ref=workspace,
            user_ref=user,
            name="t",
            client="harness",
            policy=full,
            actor=user,
        )
        people.append(
            {
                "workspace": workspace,
                "connection": cid,
                "cookie": sessions.start_session(db, user, workspace, via="harness"),
                "token": token,
            }
        )
    routes = sorted({(m, p) for (m, p) in gateway.routes})
    prefixes = sorted({(m, p) for (m, p, _h) in gateway.prefix_routes})
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    control_port = probe.getsockname()[1]
    probe.close()
    fixture = {
        "port": args.port,
        "control": control_port,
        "people": people,
        "bot_token": bot_token,
        "routes": routes,
        "prefixes": prefixes,
    }

    baseline = {}

    class Control(socketserver.StreamRequestHandler):
        def handle(self):
            request = json.loads(self.rfile.readline().decode("utf-8"))
            cmd = request["cmd"]
            if cmd == "trace":
                tracemalloc.start(args.frames)
                reply = {"ok": True}
            elif cmd == "arenas":
                reply = {"arenas": allocator_arenas()}
            elif cmd == "sample":
                reply = process_sample(args.trace)
            elif cmd == "trim":
                from mempalace.worker_memory import trim_heap

                trim_heap()
                reply = process_sample(False)
            elif cmd == "shrink":
                # What the idle sweep does to every open thread store, without waiting for idle.
                stores = list(getattr(gateway.handoff, "_stores", {}).values())
                for store in stores:
                    store.release_memory()
                reply = {**process_sample(False), "stores": len(stores)}
            elif cmd == "baseline":
                gc.collect()
                baseline["snap"] = tracemalloc.take_snapshot()
                reply = {"ok": True}
            elif cmd == "diff":
                gc.collect()
                snap = tracemalloc.take_snapshot()
                before = baseline["snap"]
                stats = snap.compare_to(before, request.get("key", "lineno"))[
                    : request.get("n", 15)
                ]
                reply = {
                    "top": [
                        {
                            "site": " <- ".join(
                                f"{Path(f.filename).as_posix().split('/site-packages/')[-1] if 'site-packages' in f.filename else Path(f.filename).name}:{f.lineno}"
                                for f in list(reversed(s.traceback))[:6]
                            ),
                            "size_kib": round(s.size_diff / 1024, 1),
                            "count": s.count_diff,
                        }
                        for s in stats
                    ]
                }
            else:
                reply = {"error": "unknown"}
            self.wfile.write(json.dumps(reply).encode("utf-8") + b"\n")

    class ControlServer(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    control = ControlServer(("127.0.0.1", control_port), Control)
    threading.Thread(target=control.serve_forever, daemon=True).start()
    (data / "fixture.json").write_text(json.dumps(fixture))
    build_server = gateway.build_server

    def ready_server(host, port):
        server = build_server(host, port)
        print("gateway-ready", flush=True)
        return server

    gateway.build_server = ready_server
    gateway.serve("127.0.0.1", args.port, idle_check_s=5.0)
    return 0


# ── the load ──────────────────────────────────────────────────────────────────────────────


class Rig:
    """A gateway child, a stub worker and a temporary data directory, torn down on exit."""

    def __init__(self, trace: bool, env: dict | None = None, frames: int = 25):
        self.trace = trace
        self.frames = frames
        self.dir = Path(tempfile.mkdtemp(prefix="cp-gw-harness-"))
        self.socket_dir = Path(tempfile.mkdtemp(prefix="cp-gw-sock-"))
        self.env = {
            **{
                k: v
                for k, v in os.environ.items()
                if not k.startswith(("COMMONPLACE_", "MEMPALACE_")) and k != "MALLOC_ARENA_MAX"
            },
            "PYTHONPATH": str(ROOT),
            "COMMONPLACE_RATE_LIMITS": "off",
            "COMMONPLACE_ADMIN_SOCKET": str(self.socket_dir / "gateway.sock"),
        }
        # Match the gateway image's startup allocator setting; libc reads this before Python.
        image = (ROOT / "deploy" / "commonplace" / "Dockerfile").read_text()
        setting = re.search(r"(?:^|\s)MALLOC_ARENA_MAX=(\S+)", image)
        if setting:
            self.env.setdefault("MALLOC_ARENA_MAX", setting[1])
        self.env.update(env or {})
        if self.env.get("MALLOC_ARENA_MAX") == "default":
            self.env.pop("MALLOC_ARENA_MAX")
        self.procs = []

    def __enter__(self):
        try:
            return self._start()
        except BaseException:
            self.__exit__()
            raise

    def _start(self):
        me = str(Path(__file__).resolve())
        free = []
        for _ in range(2):
            probe = socket.socket()
            probe.bind(("127.0.0.1", 0))
            free.append(probe.getsockname()[1])
            probe.close()
        token = secrets.token_urlsafe(16)
        stub = subprocess.Popen(
            [sys.executable, me, "stub", "--port", str(free[0]), "--token", token],
            env=self.env,
            stdout=subprocess.DEVNULL,
        )
        gateway = subprocess.Popen(
            [
                sys.executable,
                me,
                "gateway",
                "--data",
                str(self.dir),
                "--port",
                str(free[1]),
                "--stub-port",
                str(free[0]),
                "--stub-token",
                token,
            ]
            + (["--trace", "--frames", str(self.frames)] if self.trace else []),
            env={**self.env, "COMMONPLACE_DATA": str(self.dir)},
            stdout=subprocess.PIPE,
            text=True,
        )
        self.procs = [gateway, stub]
        self.pid = gateway.pid
        with selectors.DefaultSelector() as ready:
            ready.register(gateway.stdout, selectors.EVENT_READ)
            if not ready.select(timeout=120):
                raise RuntimeError("gateway child did not start within 120 seconds")
            if gateway.stdout.readline().strip() != "gateway-ready":
                raise RuntimeError("gateway child exited during start")
        self.fixture = json.loads((self.dir / "fixture.json").read_text())
        seed(self)
        if self.trace:
            self.control("trace")
        return self

    def __exit__(self, *_exc):
        for proc in self.procs:
            if proc.poll() is None:
                proc.terminate()
        for proc in self.procs:
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            if proc.stdout is not None:
                proc.stdout.close()
        shutil.rmtree(self.dir, ignore_errors=True)
        shutil.rmtree(self.socket_dir, ignore_errors=True)

    def control(self, cmd: str, **kw) -> dict:
        with socket.create_connection(("127.0.0.1", self.fixture["control"]), timeout=120) as s:
            s.sendall(json.dumps({"cmd": cmd, **kw}).encode("utf-8") + b"\n")
            return json.loads(s.makefile().readline())

    def mcp(self, headers, name, arguments) -> str:
        conn = http.client.HTTPConnection("127.0.0.1", self.fixture["port"], timeout=30)
        try:
            body = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
            conn.request(
                "POST",
                "/mcp",
                json.dumps(body).encode("utf-8"),
                {"Content-Type": "application/json", **headers},
            )
            reply = json.loads(conn.getresponse().read().decode("utf-8"))
            return json.dumps(reply)
        finally:
            conn.close()

    def http(self, method, path, body=None, headers=None, keepalive=None):
        conn = keepalive or http.client.HTTPConnection(
            "127.0.0.1", self.fixture["port"], timeout=10
        )
        try:
            raw = None if body is None else json.dumps(body).encode("utf-8")
            hdrs = {"Content-Type": "application/json", **(headers or {})}
            conn.request(method, path, raw, headers=hdrs)
            response = conn.getresponse()
            response.read()
            return response.status
        finally:
            if keepalive is None:
                conn.close()


def _id() -> str:
    return uuid.uuid4().hex[:12]


def route_requests(rig: Rig):
    """(group, method, path, body, auth) per route: every registered route, fresh keys each call."""
    fixture = rig.fixture
    for method, path in fixture["routes"]:
        if (method, path) in SKIP or method == "DELETE":
            continue
        if any(path.startswith(prefix) for prefix in SKIP_PREFIXES):
            continue
        yield (f"{method} {path}", method, path, "exact")
    for method, prefix in fixture["prefixes"]:
        if method == "DELETE" or any(prefix.startswith(p) for p in SKIP_PREFIXES):
            continue
        yield (f"{method} {prefix}*", method, prefix, "prefix")


def make_request(rng, rig: Rig, method, path, kind):
    """One concrete request: a person (cookie) or a token holder, a unique key every time."""
    person = rng.choice(rig.fixture["people"])
    headers = {}
    mode = rng.random()
    if mode < 0.55:
        headers["Cookie"] = "cmmn_session=" + person["cookie"]
    elif mode < 0.9:
        headers["Authorization"] = "Bearer " + person["token"]
    elif mode < 0.95:
        headers["Authorization"] = "Bearer " + rig.fixture["bot_token"]
    if kind == "prefix":
        tail = rng.choice(
            [_id(), f"{_id()}/{_id()}", f"ws_{_id()}", "x/" + _id() + "/y", f"{_id()}?q={_id()}"]
        )
        target = path + tail
    else:
        target = path
    if "?" not in target and method == "GET":
        target += f"?q={_id()}&limit={rng.randint(1, 50)}&workspace={_id()}"
    body = None
    if method in ("POST", "PUT", "PATCH"):
        body = rng.choice(
            [{}, {"q": _id()}, {"title": _id(), "workspace": person["workspace"]}, {"id": _id()}]
        )
    return method, target, body, headers


def seed(rig: Rig) -> None:
    """A board and a few cards per person, made through the gateway's own tools."""
    import re

    for index, person in enumerate(rig.fixture["people"]):
        auth = {"Authorization": "Bearer " + person["token"]}
        slug = f"deck{index}"
        rig.mcp(auth, "board_add", {"slug": slug, "name": f"Deck {index}"})
        cards = []
        for n in range(4):
            text = rig.mcp(
                auth, "task_add", {"title": f"card {n}", "board": slug, "body": "x" * 200}
            )
            match = re.search(r"#(\d+)", text)
            if match:
                cards.append(int(match.group(1)))
        person["board"], person["cards"] = slug, cards
        rig.http(
            "POST",
            "/api/settings/capture",
            {"enabled": True},
            {"Cookie": "cmmn_session=" + person["cookie"]},
        )


def _who(rng, rig: Rig):
    person = rng.choice(rig.fixture["people"])
    return (
        person,
        {"Cookie": "cmmn_session=" + person["cookie"]},
        {"Authorization": "Bearer " + person["token"]},
    )


def _realistic():
    """(name, weight, build): what open Deck tabs, agents and monitors send. Fresh keys each call."""

    def board(person):
        return person.get("board", "deck0")

    def card(rng, person):
        return rng.choice(person.get("cards") or [1])

    def events(rng, rig):
        _p, cookie, _b = _who(rng, rig)
        return "GET", f"/api/events?after={rng.randint(0, 5000)}&epoch={_id()}", None, cookie

    def needs_me(rng, rig):
        _p, cookie, bearer = _who(rng, rig)
        return "GET", "/api/needs-me", None, rng.choice([cookie, bearer])

    def cards(rng, rig):
        _p, cookie, _b = _who(rng, rig)
        return "GET", f"/api/cards?limit={rng.randint(1, 40)}", None, cookie

    def board_read(rng, rig):
        p, cookie, _b = _who(rng, rig)
        return "GET", rng.choice(["/api/boards", f"/api/boards/{board(p)}/cards"]), None, cookie

    def card_read(rng, rig):
        p, cookie, _b = _who(rng, rig)
        return "GET", f"/api/cards/{card(rng, p)}", None, cookie

    def chat(rng, rig):
        p, cookie, _b = _who(rng, rig)
        return (
            "GET",
            f"/api/chat/{board(p)}?last={rng.randint(1, 30)}",
            None,
            cookie,
        )

    def chat_status(rng, rig):
        p, cookie, _b = _who(rng, rig)
        return "GET", f"/api/chat/{board(p)}/status", None, cookie

    def memory_search(rng, rig):
        _p, cookie, _b = _who(rng, rig)
        return "GET", f"/api/memory/search?q={_id()}&wing={_id()}", None, cookie

    def console_log(rng, rig):
        person, cookie, _bearer = _who(rng, rig)
        return "GET", f"/api/console/stream?agent={person['connection']}&limit=30", None, cookie

    def busy_search(rng, rig):
        _p, cookie, _b = _who(rng, rig)
        return "GET", f"/api/memory/search?q=busy_{_id()}", None, cookie

    def simple(path):
        def build(rng, rig):
            _p, cookie, _b = _who(rng, rig)
            return "GET", path, None, cookie

        return build

    def deploy(rng, rig):
        return (
            "GET",
            "/api/admin/instance/deploy",
            None,
            {"Cookie": "cmmn_session=" + rig.fixture["people"][0]["cookie"]},
        )

    def shell(rng, rig):
        return "GET", rng.choice(["/app/", "/app/app.js", "/app/app.css"]), None, {}

    def card_add(rng, rig):
        p, cookie, _b = _who(rng, rig)
        return (
            "POST",
            f"/api/boards/{board(p)}/cards",
            {"title": f"c {_id()}", "body": _id()},
            cookie,
        )

    def comment(rng, rig):
        p, cookie, _b = _who(rng, rig)
        return "POST", f"/api/cards/{card(rng, p)}/comments", {"text": _id()}, cookie

    def missing(rng, rig):
        _p, cookie, _b = _who(rng, rig)
        return "GET", f"/api/cards/{rng.randint(10**6, 10**9)}", None, cookie

    def capture(rng, rig):
        """A client's transcript upload: a new thread each time; now and then a large one."""
        _p, cookie, _b = _who(rng, rig)
        sid = str(uuid.uuid4())
        roll = rng.random()
        lines = 20 if roll < 0.7 else 400 if roll < 0.95 else 4000
        rows = []
        for i in range(lines):
            role = "user" if i % 2 == 0 else "assistant"
            text = f"message {i} {_id()} " * 12
            content = text if role == "user" else [{"type": "text", "text": text}]
            rows.append(
                json.dumps(
                    {
                        "type": role,
                        "uuid": str(uuid.uuid4()),
                        "sessionId": sid,
                        "timestamp": f"2026-11-15T10:{(i // 60) % 60:02d}:{i % 60:02d}Z",
                        "message": {"role": role, "content": content},
                    }
                )
            )
        body = {
            "source": "claude-code",
            "thread_id": sid,
            "offset": 0,
            "prefix_sha256": hashlib.sha256(b"").hexdigest(),
            "transcript": "\n".join(rows) + "\n",
        }
        return "POST", "/api/capture", body, cookie

    def unauthorized(rng, rig):
        return (
            "GET",
            f"/api/events?after={rng.randint(0, 99)}",
            None,
            {"Authorization": "Bearer cmmn_" + _id()},
        )

    return [
        ("GET /api/events (poll)", 30, events),
        ("GET /api/needs-me", 10, needs_me),
        ("GET /api/cards", 8, cards),
        ("GET /api/boards[/<slug>/cards]", 8, board_read),
        ("GET /api/cards/<n>", 6, card_read),
        ("GET /api/chat/<slug>", 5, chat),
        ("GET /api/chat/<slug>/status", 3, chat_status),
        ("GET /api/memory/search", 5, memory_search),
        ("GET /api/memory/search (503 retry)", 1, busy_search),
        ("GET /api/me", 3, simple("/api/me")),
        ("GET /api/bots", 3, simple("/api/bots")),
        ("GET /api/admin/instance/deploy", 3, deploy),
        ("GET /api/connections", 1, simple("/api/connections")),
        ("GET /api/workspaces", 1, simple("/api/workspaces")),
        ("GET /api/plan", 1, simple("/api/plan")),
        ("GET /api/memory/rooms", 2, simple("/api/memory/rooms")),
        ("GET /api/console", 1, simple("/api/console")),
        ("GET /api/console/stream (log page)", 1, console_log),
        ("GET /api/pipeline/prs (demo)", 1, simple("/api/pipeline/prs?demo=1")),
        ("GET /app/login (invalid token)", 1, simple("/app/login?t=invalid")),
        ("GET /app/ shell + static", 3, shell),
        ("POST /api/boards/<slug>/cards", 4, card_add),
        ("POST /api/cards/<n>/comments", 2, comment),
        ("POST /api/capture (transcript upload)", 3, capture),
        ("GET /api/cards/<missing> (404)", 2, missing),
        ("GET /api/events bad token (401)", 2, unauthorized),
    ]


def realistic_request(rng, rig: Rig):
    table = _realistic()
    _name, _weight, build = rng.choices(table, weights=[w for _n, w, _b in table])[0]
    return build(rng, rig)


def mcp_request(rng, rig: Rig):
    person = rng.choice(rig.fixture["people"])
    token = person["token"] if rng.random() < 0.85 else rig.fixture["bot_token"]
    name = rng.choice(
        [
            "mempalace_search",
            "mempalace_status",
            "memory_search",
            "needs_me",
            "rules_get",
            "board_list",
            "task_list",
            "session_start",
        ]
    )
    body = {
        "jsonrpc": "2.0",
        "id": rng.randint(1, 10**6),
        "method": rng.choice(["tools/call", "tools/call", "tools/list", "ping", "initialize"]),
        "params": {
            "name": name,
            "arguments": {"query": ("busy " if rng.random() < 0.05 else "") + _id()},
        },
    }
    return "POST", "/mcp", body, {"Authorization": "Bearer " + token}


def drive(rig: Rig, requests: int, threads: int, picker, seed=1) -> Counter:
    """Send ``requests`` requests from ``threads`` threads; each call is built by ``picker(rng)``."""
    statuses: Counter = Counter()
    lock = threading.Lock()
    left = [requests]

    def work(index):
        rng = random.Random(seed * 1000 + index)
        while True:
            with lock:
                if left[0] <= 0:
                    return
                left[0] -= 1
            method, target, body, headers = picker(rng)
            try:
                code = rig.http(method, target, body, headers)
            except (OSError, http.client.HTTPException):
                code = "err"
            with lock:
                statuses[code] += 1

    pool = [threading.Thread(target=work, args=(i,)) for i in range(threads)]
    for thread in pool:
        thread.start()
    for thread in pool:
        thread.join()
    return statuses


def _group_picker(rig, name, method, path, kind):
    if kind == "mcp":
        return lambda rng: mcp_request(rng, rig)
    if kind == "real":
        return lambda rng, build=method: build(rng, rig)
    return lambda rng, m=method, p=path, k=kind: make_request(rng, rig, m, p, k)


def cmd_routes(args) -> int:
    """Each route twice: the first pass pays imports and per-workspace opens, the second is steady."""
    with Rig(trace=True, frames=args.frames) as rig:
        groups = [g for g in route_requests(rig) if args.only in g[0]]
        if args.realistic_only:
            groups = []
        groups.insert(0, ("POST /mcp (mixed tools)", None, None, "mcp"))
        for name, build, _w in [(n, b, w) for n, w, b in _realistic()]:
            groups.append((name, build, None, "real"))
        rows = []
        groups = [group for group in groups if args.only in group[0]]
        head = f"{'route':44} {'reqs':>5} | first pass: anon MiB  traced KiB  objects | steady: anon MiB  traced KiB  objects  thr fds  status"
        print(head, flush=True)
        for name, method, path, kind in groups:
            picker = _group_picker(rig, name, method, path, kind)
            n = args.requests * (3 if kind in ("real", "mcp") else 1)
            marks = [rig.control("sample")]
            statuses = Counter()
            for _ in range(2):
                statuses += drive(rig, n, args.threads, picker)
                marks.append(rig.control("sample"))
            rows.append(
                {
                    "route": name,
                    "requests_per_pass": n,
                    "samples": marks,
                    "statuses": dict(statuses),
                }
            )
            if args.output:
                Path(args.output).write_text(json.dumps(rows, indent=2))
            f, s_ = [(marks[i + 1], marks[i]) for i in range(2)]
            print(
                f"{name[:44]:44} {n:5d} | {f[0]['anon_mib'] - f[1]['anon_mib']:20.2f} "
                f"{f[0].get('traced_kib', 0) - f[1].get('traced_kib', 0):11.1f} "
                f"{f[0]['objects'] - f[1]['objects']:8d} | "
                f"{s_[0]['anon_mib'] - s_[1]['anon_mib']:16.2f} "
                f"{s_[0].get('traced_kib', 0) - s_[1].get('traced_kib', 0):11.1f} "
                f"{s_[0]['objects'] - s_[1]['objects']:8d} "
                f"{s_[0]['threads'] - s_[1]['threads']:4d} {s_[0]['fds'] - s_[1]['fds']:3d}  "
                f"{dict(statuses)}",
                flush=True,
            )
    return 0


def cmd_soak(args) -> int:
    trace = not args.no_trace
    env = {"MALLOC_ARENA_MAX": args.arena_max} if args.arena_max else {}
    with Rig(trace=trace, env=env, frames=args.frames) as rig:
        groups = list(route_requests(rig))

        def picker(rng):
            roll = rng.random()
            if roll < 0.15:
                return mcp_request(rng, rig)
            if roll < 0.75:
                return realistic_request(rng, rig)
            _name, method, path, kind = rng.choice(groups)
            return make_request(rng, rig, method, path, kind)

        drive(rig, 400, args.threads, picker)  # warm
        start = time.time()
        report = {"samples": [], "statuses": {}}
        based = False
        sent = 0
        print("minute  reqs  anon_MiB  rss_MiB  traced_KiB  objects  threads  fds")
        while time.time() - start < args.minutes * 60:
            statuses = drive(rig, args.batch, args.threads, picker, seed=sent + 7)
            sent += sum(statuses.values())
            if trace and not based and time.time() - start >= args.baseline_minutes * 60:
                rig.control("baseline")  # caches that fill to a bound are full by now
                based = True
                print("-- baseline taken --", flush=True)
            sample = rig.control("sample")
            report["samples"].append(
                {"minute": (time.time() - start) / 60, "requests": sent, **sample}
            )
            for code, count in statuses.items():
                report["statuses"][str(code)] = report["statuses"].get(str(code), 0) + count
            if args.output:
                Path(args.output).write_text(json.dumps(report, indent=2))
            print(
                f"{(time.time() - start) / 60:6.1f} {sent:6d} {sample['anon_mib']:9.1f} "
                f"{sample['rss_mib']:8.1f} {sample.get('traced_kib', 0):11.0f} "
                f"{sample['objects']:8d} {sample['threads']:8d} {sample['fds']:4d}",
                flush=True,
            )
        if trace:
            for key in ("lineno", "traceback"):
                print(f"top growth sites since the baseline ({key}):")
                report[key] = rig.control("diff", n=12, key=key)["top"]
                for row in report[key]:
                    print(f"  {row['size_kib']:10.1f} KiB {row['count']:8d}  {row['site']}")
        report["final"] = rig.control("sample")
        if args.output:
            Path(args.output).write_text(json.dumps(report, indent=2))
        print("final:", report["final"])
        print("statuses:", report["statuses"])
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="mode", required=True)
    stub = sub.add_parser("stub")
    stub.add_argument("--port", type=int, required=True)
    stub.add_argument("--token", required=True)
    gateway = sub.add_parser("gateway")
    gateway.add_argument("--data", required=True)
    gateway.add_argument("--port", type=int, required=True)
    gateway.add_argument("--stub-port", type=int, required=True)
    gateway.add_argument("--stub-token", required=True)
    gateway.add_argument("--trace", action="store_true")
    gateway.add_argument("--frames", type=int, default=25)
    routes = sub.add_parser("routes")
    routes.add_argument("--requests", type=int, default=60)
    routes.add_argument("--threads", type=int, default=8)
    routes.add_argument("--only", default="")
    routes.add_argument("--frames", type=int, default=25)
    routes.add_argument("--realistic-only", action="store_true")
    routes.add_argument("--output")
    soak = sub.add_parser("soak")
    soak.add_argument("--minutes", type=float, default=15)
    soak.add_argument("--threads", type=int, default=12)
    soak.add_argument("--batch", type=int, default=3000)
    soak.add_argument("--no-trace", action="store_true")
    soak.add_argument("--frames", type=int, default=25)
    soak.add_argument("--baseline-minutes", type=float, default=0)
    soak.add_argument("--arena-max", default="")
    soak.add_argument("--output")
    args = parser.parse_args(argv)
    if args.mode == "stub":
        return run_stub(args.port, args.token)
    if args.mode == "gateway":
        return serve_gateway(args)
    return {"routes": cmd_routes, "soak": cmd_soak}[args.mode](args)


if __name__ == "__main__":
    sys.exit(main())
