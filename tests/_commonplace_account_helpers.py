"""A real gateway over temporary fixture data for the account export and deletion tests."""

import hashlib
import http.client
import io
import json
import sqlite3
import threading
import time
import zipfile
from pathlib import Path

import pytest

from commonplace import sessions
from commonplace.control_db import ControlDB
from commonplace.gateway import Gateway
from commonplace.policy import Policy
from commonplace.workers import WorkerSupervisor

ALICE_NOTE = "Alice's note: café ☕, line one\nline two\ttabbed — kept exactly"
BOB_NOTE = "Bob's private plan nobody else may receive"
TEAM_NOTE = "Team card written in the shared workspace"


class NoWorkers:
    """These tests never call a memory tool, so no worker may start."""

    def start(self, workspace_id, workspace_dir):
        raise AssertionError("no worker expected")


class Site:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.db = ControlDB(str(self.data_dir / "control.sqlite3"))
        self.supervisor = WorkerSupervisor(str(self.data_dir), launcher=NoWorkers())
        self.gateway = Gateway(self.db, self.supervisor)
        self.server = self.gateway.build_server("127.0.0.1", 0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self.thread.start()
        db = self.db
        # An instance administrator who is nobody's fixture: accounts under test can leave.
        self.root = db.create_user("root@example.test", is_admin=True)
        self.alice = db.create_user("alice@example.test", "Alice Example")
        self.bob = db.create_user("bob@example.test", "Bob Example")
        self.alice_home = db.create_workspace("alice home", self.alice, personal=True)
        self.bob_home = db.create_workspace("bob home", self.bob, personal=True)
        # Bob owns the team; Alice is a member with her own agent connection.
        self.team = db.create_workspace("team", self.bob)
        db.add_member(self.team, self.alice, "member", actor=self.bob)
        self.alice_home_token = self.token("alice-laptop", self.alice, self.alice_home, self.alice)
        self.alice_team_token = self.token("alice-agent", self.alice, self.team, self.bob)
        self.bob_team_token = self.token("bob-agent", self.bob, self.team, self.bob)
        self.seed_store(self.alice_home, ALICE_NOTE)
        self.seed_store(self.bob_home, BOB_NOTE)
        self.seed_store(self.team, TEAM_NOTE)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.supervisor.stop_all()
        self.db.close()

    def token(self, name, user, workspace, actor):
        policy = Policy.build(profile="standard", scopes=["memory:read"], read_wings=["*"])
        return self.db.create_token(
            workspace_ref=workspace,
            user_ref=user,
            name=name,
            client="test",
            policy=policy,
            actor=actor,
        )[1]

    def directory(self, workspace) -> Path:
        return Path(self.gateway.workspace_dir(workspace))

    def seed_store(self, workspace, note):
        """One workspace database with text and bytes, one plain file, one WAL side file."""
        directory = self.directory(workspace)
        conn = sqlite3.connect(directory / "boards.sqlite3")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, title TEXT, attachment BLOB)")
        conn.execute("INSERT INTO tasks(title, attachment) VALUES (?, ?)", (note, b"\x00\xff\x10"))
        conn.execute("INSERT INTO tasks(title, attachment) VALUES (?, NULL)", ("second " + note,))
        conn.commit()
        conn.close()
        (directory / "palace").mkdir(exist_ok=True)
        (directory / "palace" / "notes.txt").write_text(note, encoding="utf-8")

    def session(self, user, workspace) -> str:
        return sessions.start_session(self.db, user, workspace)

    def session_now(self, user, workspace) -> str:
        """A session started at the gateway's (advanced) time, not the real one."""
        session = self.session(user, workspace)
        started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.gateway.wall_clock()))
        with self.db._lock:
            self.db._conn.execute(
                "UPDATE sessions SET created_at = ? WHERE id_hash = ?",
                (started, hashlib.sha256(session.encode("utf-8")).hexdigest()),
            )
        return session

    def request(self, method, path, *, session=None, token=None, body=None):
        """``(status, headers, raw body)``; a JSON body is sent the way the app sends it."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            headers = {}
            if session:
                headers["Cookie"] = f"{sessions.COOKIE}={session}"
            if token:
                headers["Authorization"] = f"Bearer {token}"
            raw = None
            if method != "GET":
                headers["Content-Type"] = "application/json"
                raw = json.dumps(body).encode("utf-8") if body is not None else b""
            conn.request(method, path, raw, headers=headers)
            response = conn.getresponse()
            try:
                data = response.read()
            except http.client.IncompleteRead as exc:
                data = exc.partial
            return response.status, dict(response.getheaders()), data
        finally:
            conn.close()

    def json(self, method, path, **kwargs):
        status, _headers, data = self.request(method, path, **kwargs)
        return status, json.loads(data)

    def export(self, user, workspace):
        status, headers, data = self.request(
            "GET", "/api/account/export?download=1", session=self.session(user, workspace)
        )
        assert status == 200, (status, data[:300])
        assert headers["Content-Type"] == "application/zip"
        return Archive(data)

    def advance(self, seconds):
        """Move the gateway's clock forward; sessions keep their real start time."""
        base = self.gateway.wall_clock
        self.gateway.wall_clock = lambda: base() + seconds

    def rows(self, sql, *params):
        with self.db._lock:
            return [dict(row) for row in self.db._conn.execute(sql, params)]

    def control_dump(self, skip=()) -> dict:
        """Every control row as text, by table."""
        dump = {}
        with self.db._lock:
            names = [
                row[0]
                for row in self.db._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            ]
            for name in names:
                if name not in skip:
                    dump[name] = [
                        tuple(repr(value) for value in row)
                        for row in self.db._conn.execute(f'SELECT * FROM "{name}"')
                    ]
        return dump

    def tree(self, workspace) -> dict:
        """Content digests of the stable files of a workspace directory."""
        root = self.data_dir / "workspaces" / workspace
        return {
            path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*"))
            if path.is_file() and not path.name.endswith(("-wal", "-shm"))
        }


class Archive:
    def __init__(self, data):
        self.data = data
        self.zip = zipfile.ZipFile(io.BytesIO(data))
        assert self.zip.testzip() is None
        self.names = self.zip.namelist()
        self.manifest = json.loads(self.zip.read("manifest.json"))

    def rows(self, name):
        return [json.loads(line) for line in self.zip.read(name).splitlines()]

    def text(self) -> str:
        """Everything in the archive, decompressed, for "is this string anywhere" checks."""
        return "\n".join(self.zip.read(name).decode("utf-8", "replace") for name in self.names)


@pytest.fixture(name="site")
def site_fixture(tmp_path, monkeypatch):
    monkeypatch.delenv("COMMONPLACE_ACCOUNT_DELETION", raising=False)
    monkeypatch.delenv("COMMONPLACE_ACCOUNT_DELETION_GRACE_DAYS", raising=False)
    monkeypatch.setenv("COMMONPLACE_RATE_LIMITS", "off")
    made = Site(tmp_path / "data")
    try:
        yield made
    finally:
        made.close()


def wait_for(check, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.02)
    return False
