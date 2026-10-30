"""Real gateway, worker, webhook receiver, and isolated hook process state."""

import hashlib
import hmac
import json
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import ExitStack
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlencode

import pytest

from commonplace.handoff.wiring import install
from tests._commonplace_helpers import hub_fixture as hub_fixture
from tests.e2e_handoff.sample import FACT, HERE, TASK, transcript
from tests.e2e_handoff.wire import ThreadClient


class Markup(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.fields = {}
        self.visible = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        fields = dict(attrs)
        if tag == "input" and "name" in fields:
            self.fields[fields["name"]] = fields.get("value", "")

    def handle_data(self, data):
        self.visible.append(data)


class Receiver:
    def __init__(self):
        self._received = []
        self._lock = threading.Lock()
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                with receiver._lock:
                    receiver._received.append((raw, dict(self.headers)))
                self.send_response(204)
                self.end_headers()

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/events"

    def received(self):
        with self._lock:
            return list(self._received)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class Scenario:
    def __init__(self, hub, root, receiver, context):
        self.hub = hub
        self.root = root
        self.receiver = receiver
        self.context = context
        self.data = context.data_dir
        self.home = root / "hook-home"
        self.work = root / "work"
        self.results = root / "results"
        for directory in (self.home / ".commonplace", self.work, self.results):
            directory.mkdir(parents=True)
        self.token = hub.token(
            "handoff-e2e",
            client="claude-code",
            profile="full",
            scopes=["memory:read", "memory:write", "threads:read", "handoff:write"],
            read_wings=["*"],
            write_wings=["*"],
            trust="trusted",
        )
        self.url = f"http://127.0.0.1:{hub.port}"
        self.client = ThreadClient(self.url, self.token)
        self.client.initialize()
        self.secret = "local-e2e-signing-key"
        registered = self.client.json(
            "POST", "/api/webhooks", {"url": receiver.url, "secret": self.secret}, expected=201
        )
        assert registered["secret"] == self.secret
        self.environment = {
            key: value for key, value in os.environ.items() if not key.startswith("COMMONPLACE_")
        }
        self.environment.update(
            HOME=str(self.home),
            USERPROFILE=str(self.home),
            COMMONPLACE_URL=self.url,
            COMMONPLACE_TOKEN=self.token,
            COMMONPLACE_TASK=TASK,
            HANDOFF_E2E_RESULTS=str(self.results),
        )
        self.configure_successor(
            [sys.executable, str(HERE / "successor.py"), "{prompt_file}", "{handoff_id}"]
        )
        self.transcript = self.root / "original.jsonl"
        self.messages = transcript(self.transcript)
        self.payload = self.hook_payload("original-session", self.transcript)
        self.hooks = []

    @property
    def store_path(self):
        return self.data / "workspaces" / self.hub.workspace / "threads.sqlite3"

    def configure_successor(self, argv):
        (self.home / ".commonplace" / "handoff-client.json").write_text(
            json.dumps({"successor_argv": argv}), encoding="utf-8"
        )

    def setting(self, enabled):
        self.client.json(
            "PUT",
            "/api/settings/handoff",
            {"scope": "workspace", "enabled": enabled, "threshold": 0.8, "context_window": 200_000},
        )
        effective = self.client.json("GET", "/api/settings/handoff")["effective"]
        assert effective["enabled"] is enabled

    def hook_payload(self, session, path):
        return {
            "session_id": session,
            "transcript_path": str(path),
            "cwd": str(self.work),
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git diff --stat"},
            "tool_response": {"stdout": "validation finished", "exitCode": 0},
        }

    def hook(self, payload=None):
        result = subprocess.run(
            [sys.executable, "-m", "commonplace.handoff.hook", "--client", "claude-code"],
            input=json.dumps(payload or self.payload),
            text=True,
            capture_output=True,
            env=self.environment,
            cwd=self.work,
            timeout=45,
            check=True,
        )
        self.hooks.append({"stdout": result.stdout, "stderr": result.stderr})
        return result

    def records(self):
        return self.client.json("GET", "/api/handoffs?" + urlencode({"task": TASK}))

    def log(self, name):
        path = self.results / name
        if not path.exists():
            return []
        return [
            json.loads(line)
            for line in path.read_text().splitlines(keepends=True)
            if line.endswith("\n")
        ]

    def audit(self):
        return [
            dict(row)
            for row in self.hub.db.audit_entries(self.hub.workspace, limit=1000)
            if row["action"].startswith("handoff.")
        ]

    def diagnostics(self):
        return {
            "hooks": self.hooks,
            "errors": self.log("errors.jsonl"),
            "launches": self.log("launches.jsonl"),
            "records": [{"id": row["id"], "state": row["state"]} for row in self.records()],
            "events": [json.loads(raw)["event"] for raw, _ in self.receiver.received()],
        }

    def wait(self, observe, *, timeout=20):
        deadline = time.monotonic() + timeout
        while True:
            value = observe()
            if value:
                return value
            if self.log("errors.jsonl"):
                pytest.fail("Successor failed:\n" + json.dumps(self.diagnostics(), indent=2))
            if time.monotonic() >= deadline:
                pytest.fail("Handoff timed out:\n" + json.dumps(self.diagnostics(), indent=2))
            time.sleep(0.025)

    def completed(self, count=1, *, timeout=20):
        def observe():
            rows = self.records()
            if len(rows) != count or any(row["state"] != "done" for row in rows):
                return None
            paths = [self.results / f"{row['id']}.json" for row in rows]
            if not all(path.exists() for path in paths):
                return None
            try:
                return rows, [json.loads(path.read_text()) for path in paths]
            except json.JSONDecodeError:
                return None  # Another process is still flushing its result.

        return self.wait(observe, timeout=timeout)

    def thread_rows(self):
        if not self.store_path.exists():
            return []
        with sqlite3.connect(self.store_path) as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute("SELECT * FROM threads")]

    def drained(self):
        # A committed delivery acknowledgement is stronger than a fixed delay.
        with self.hub.db._lock:
            pending = self.hub.db._conn.execute(
                "SELECT count(*) FROM handoff_outbox WHERE delivered_at IS NULL AND gave_up_at IS NULL"
            ).fetchone()[0]
        return not pending

    def signed_events(self, names):
        self.wait(
            lambda: names <= {json.loads(raw)["event"] for raw, _ in self.receiver.received()}
        )
        self.wait(self.drained)
        payloads = []
        allowed = {
            "event",
            "workspace",
            "handoff_id",
            "stored_thread_id",
            "task_ref",
            "successor_agent_ref",
            "at",
            "decision",
            "reason",
        }
        for raw, headers in self.receiver.received():
            expected = "sha256=" + hmac.new(self.secret.encode(), raw, hashlib.sha256).hexdigest()
            assert hmac.compare_digest(headers["X-Commonplace-Signature"], expected)
            payload = json.loads(raw)
            assert payload.keys() <= allowed
            assert payload["workspace"] == self.hub.workspace
            assert headers["X-Commonplace-Event"] == payload["event"]
            assert headers["X-Commonplace-Delivery"].isdecimal()
            assert all(
                value not in raw.decode()
                for value in (FACT, "54329", "HELIOTROPE-7", "validation finished")
            )
            payloads.append(payload)
        return payloads

    def card(self):
        raw, headers = self.client.request("GET", "/app/login")
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        csrf = Markup(raw.decode()).fields["csrf"]
        _, headers = self.client.request(
            "POST",
            "/app/login",
            urlencode({"csrf": csrf, "token": self.token}).encode(),
            headers={"Cookie": cookie, "Content-Type": "application/x-www-form-urlencoded"},
            expected=303,
        )
        raw, _ = self.client.request(
            "GET", "/app/cards/1701", headers={"Cookie": headers["Set-Cookie"].split(";", 1)[0]}
        )
        return raw.decode(), " ".join(Markup(raw.decode()).visible)

    def stop_children(self):
        identifiers = {row["pid"] for row in self.log("launches.jsonl")}
        # Include children that died before writing their first diagnostic.
        for path in (self.home / ".commonplace" / "handoff-cache").glob("*.launch.json"):
            record = json.loads(path.read_text())
            if record.get("agent_ref", "").startswith("pid:"):
                identifiers.add(int(record["agent_ref"].removeprefix("pid:")))
        for identifier in identifiers:
            try:
                # Successors have their own process groups, created by the hook.
                os.killpg(identifier, signal.SIGTERM)
            except ProcessLookupError:
                pass


@pytest.fixture
def scenario(hub, tmp_path):
    with ExitStack() as cleanup:
        receiver = Receiver()
        cleanup.callback(receiver.close)
        context = install(hub.gateway, tmp_path / "data", start_outbox=True)
        cleanup.callback(context.close)
        run = Scenario(hub, tmp_path / "handoff", receiver, context)
        cleanup.callback(run.stop_children)
        yield run
