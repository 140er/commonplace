"""Regression checks for source isolation, bounded idle work and CLI failures."""

import hashlib
import json
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path

import pytest

from commonplace import cli, sync_daemon, sync_service, sync_sources
from commonplace.sync_protocol import TOKEN_ENV
from commonplace.sync_state import open_state

NOW = 2_000_000_000


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def record(sid, text="verbatim café"):
    return (
        json.dumps({"type": "user", "sessionId": sid, "message": {"role": "user", "content": text}})
        + "\n"
    ).encode()


class Hub:
    def __init__(self, *, interval=0, reject=None, window=None):
        self.interval, self.reject, self.window = interval, reject, window
        self.posts, self.stored, self.feeds = [], {}, 0

    def __call__(self, target, token, method, route, body=None):
        if route.startswith("/api/events?"):
            self.feeds += 1
            return 200, {
                "feed": "sync",
                "seq": 0,
                "events": [],
                "more": False,
                "reset": False,
                "waited": False,
                "state": {
                    "capture_enabled": True,
                    "interval_seconds": self.interval,
                    "next_window_at": self.window,
                },
            }
        if method == "GET":
            return 200, {"offset": 0, "prefix_sha256": sha(b"")}
        self.posts.append(body)
        if body.get("thread_id") == "bad" and self.reject:
            return self.reject, {}
        if route == "/api/import":
            return 200, {"accepted": 1, "errors": 0}
        sid = body["thread_id"]
        self.stored[sid] = self.stored.get(sid, b"") + body["transcript"].encode()
        return 200, {"offset": len(self.stored[sid]), "prefix_sha256": sha(self.stored[sid])}


@pytest.fixture
def local(tmp_path):
    home = tmp_path / "home"
    root = home / ".claude/projects"
    root.mkdir(parents=True)
    config = {
        "home": str(home),
        "hub": "https://sync.example",
        "sources": [{"source": "claude-code", "root": str(root)}],
        "tokens": {TOKEN_ENV["claude-code"]: "synthetic", TOKEN_ENV["native-memory"]: "synthetic"},
    }
    with open_state(home / ".commonplace/sync/state.db") as state:
        yield config, state, root


def run(local, hub, *, continuous=False):
    config, state, _ = local
    sleeps = []
    return sync_daemon.run(
        config,
        state,
        clock=lambda: NOW,
        sleep=sleeps.append,
        transport=hub,
        once=not continuous,
        stop=lambda: bool(sleeps),
    )


def test_fix1_default_install_syncs_available_source(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = home / ".claude/projects"
    root.mkdir(parents=True)
    raw = record("good")
    (root / "good.jsonl").write_bytes(raw)
    env = tmp_path / "client.env"
    env.write_text(
        "COMMONPLACE_URL=https://sync.example\nCOMMONPLACE_CAPTURE_TOKEN_CLAUDE_CODE=synthetic\n"
    )
    env.chmod(0o600)
    hub = Hub()
    monkeypatch.setattr(sync_daemon, "request", hub)
    assert (
        cli.main(
            [
                "sync",
                "install",
                "--enable",
                "--no-service",
                "--env-file",
                str(env),
                "--home",
                str(home),
            ]
        )
        == 0
    )
    assert cli.main(["sync", "run", "--once", "--home", str(home)]) == 0
    assert hub.stored == {"good": raw}


def test_fix1_explicit_missing_root_fails_before_config(tmp_path):
    home = tmp_path / "home"
    assert (
        cli.main(
            [
                "sync",
                "install",
                "--source",
                "codex:" + str(tmp_path / "absent"),
                "--enable",
                "--no-service",
                "--home",
                str(home),
            ]
        )
        == 2
    )
    assert not (home / ".commonplace/sync/config.json").exists()


def test_fix1_failed_root_does_not_hide_other_roots(local):
    config, _, root = local
    (root / "good.jsonl").write_bytes(record("good"))
    config["sources"].insert(0, {"source": "codex", "root": str(root / "missing")})
    hub = Hub()
    run(local, hub)
    assert "good" in hub.stored


@pytest.mark.parametrize("continuous", [False, True])
@pytest.mark.parametrize("fault", ["oversize", "encoding", "conflict", "unreadable"])
def test_fix2_bad_source_does_not_block_good_source(local, continuous, fault, monkeypatch):
    config, state, root = local
    (root / "z-good.jsonl").write_bytes(record("good"))
    bad = root / "a-bad.jsonl"
    if fault == "encoding":
        bad = root / "bad.md"
        bad.write_bytes(b"\xff")
        os.utime(bad, (NOW - 120, NOW - 120))
        config["memories"] = [{"label": "notes", "root": str(root)}]
    else:
        bad.write_bytes(
            record("bad") + (record("bad", "x" * 1_700_000) if fault == "oversize" else b"")
        )
        if fault == "conflict":
            state.store_cursor("claude-code", str(bad), "bad", 1, sha(b"wrong"))
        if fault == "unreadable":
            original = sync_sources._open

            def unavailable(path):
                if Path(path) == bad:
                    raise PermissionError()
                return original(path)

            monkeypatch.setattr(sync_sources, "_open", unavailable)
    hub = Hub()
    run(local, hub, continuous=continuous)
    assert hub.stored.get("good") == record("good")
    assert any(row["status"] in {"conflict", "too_large", "unreadable"} for row in state.statuses())


@pytest.mark.parametrize("status", [400, 401, 403, 404, 413])
def test_fix3_rejected_op_yields_to_next_and_retries_on_restart(local, status):
    _, state, root = local
    (root / "a-bad.jsonl").write_bytes(record("bad"))
    (root / "z-good.jsonl").write_bytes(record("good"))
    hub = Hub(reject=status)
    assert run(local, hub) == 1
    assert hub.stored.get("good") == record("good")
    assert state.cursor("claude-code", root / "a-bad.jsonl", "bad")["offset"] == 0
    assert not state.outbox_pending()
    hub.reject = None
    assert run(local, hub) == 0
    assert hub.stored["bad"] == record("bad")


def test_fix4_idle_cycles_read_no_source_or_memory_content(local, monkeypatch):
    config, _, root = local
    (root / "session.jsonl").write_bytes(record("good"))
    note = root / "note.md"
    note.write_bytes(b"verbatim memory")
    os.utime(note, (NOW - 120, NOW - 120))
    config["memories"] = [{"label": "notes", "root": str(root)}]
    from commonplace.onboarding import POINTER

    (root / "pointer.md").write_bytes(POINTER.encode())
    (root / "empty.md").write_bytes(b"")
    original_note = root / "original.md"
    original_note.write_bytes(b"previously onboarded memory")
    for path in (root / "pointer.md", root / "empty.md", original_note):
        os.utime(path, (NOW - 120, NOW - 120))
    manifest = Path(config["home"]) / ".commonplace/onboarding.json"
    manifest.write_text(
        json.dumps({"files": {"original": {"original_sha256": sha(original_note.read_bytes())}}})
    )
    hub = Hub()
    assert run(local, hub) == 0
    read_bytes = []
    discoveries = []
    discover = sync_sources.discover

    def discover_once(config):
        discoveries.append(1)
        return discover(config)

    monkeypatch.setattr(sync_sources, "discover", discover_once)
    original = sync_sources._open

    @contextmanager
    def counted(path):
        with original(path) as stream:

            class Reader:
                def read(self, *args):
                    raw = stream.read(*args)
                    read_bytes.append(len(raw))
                    return raw

                def fileno(self):
                    return stream.fileno()

            yield Reader()

    monkeypatch.setattr(sync_sources, "_open", counted)
    for _ in range(10):
        assert run(local, hub) == 0
    assert sum(read_bytes) == 0
    assert len(discoveries) == 10
    # A new version invalidates the metadata cache and remains verbatim.
    note.write_bytes(b"new verbatim memory")
    os.utime(note, (NOW - 120, NOW - 120))
    assert run(local, hub) == 0
    assert hub.posts[-1]["threads"][0]["transcript"] == "new verbatim memory"


def test_fix7_free_feed_waits_until_next_window(local):
    now = [NOW]
    hub = Hub(interval=900, window="2033-05-18T03:46:40Z")  # NOW + 800
    config, state, _ = local

    def advance(seconds):
        now[0] += seconds

    assert (
        sync_daemon.run(
            config,
            state,
            clock=lambda: now[0],
            sleep=advance,
            transport=hub,
            stop=lambda: now[0] >= NOW + 300,
        )
        == 0
    )
    assert hub.feeds <= 1


def test_fix8_service_failure_is_generic_exit_two(tmp_path, monkeypatch, capsys):
    def failed(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["synthetic-manager"])

    monkeypatch.setattr(sync_service, "install", failed)
    env = tmp_path / "client.env"
    env.write_text("COMMONPLACE_CAPTURE_TOKEN_CLAUDE_CODE=synthetic\n")
    env.chmod(0o600)
    try:
        result = cli.main(
            [
                "sync",
                "install",
                "--source",
                "claude-code",
                "--enable",
                "--no-activate",
                "--env-file",
                str(env),
                "--home",
                str(tmp_path / "home"),
            ]
        )
    except subprocess.CalledProcessError:
        result = None
    assert result == 2
    assert (
        capsys.readouterr().err
        == "Sync failed; check configuration, credentials and private file permissions.\n"
    )
