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


@pytest.mark.parametrize("once", [False, True], ids=["continuous", "once"])
def test_exhausted_token_does_not_block_import_or_codex(local, once):
    config, state, root = local
    for sid in ("a-blocked", "b-blocked"):
        (root / f"{sid}.jsonl").write_bytes(record(sid))
    codex = root.parent / "codex"
    codex.mkdir()
    (codex / "session.jsonl").write_text(
        json.dumps({"type": "session_meta", "payload": {"id": "codex-session"}}) + "\n"
    )
    config["sources"].append({"source": "codex", "root": str(codex)})
    notes = root.parent / "notes"
    notes.mkdir()
    note = notes / "memory.md"
    note.write_text("Synthetic verbatim memory.\n")
    os.utime(note, (NOW - 120, NOW - 120))
    config["memories"] = [{"label": "notes", "root": str(notes)}]
    config["tokens"] = {env: "synthetic-" + source for source, env in TOKEN_ENV.items()}
    hub = Hub()

    def transport(target, token, method, route, body=None):
        if method == "POST":
            assert token == config["tokens"][TOKEN_ENV[body["source"]]]
        return hub(target, token, method, route, body)

    active = sync_daemon._Session(config, state, lambda: NOW, transport)
    # Exercise steady-state exhaustion, without the first-sync backfill allowance.
    state.defer(active.bkey + "-done", NOW)
    for _ in range(sync_daemon.WRITE_BUDGET[0]):
        assert active._budget(TOKEN_ENV["claude-code"])
    assert active.cycle(once=once) == 0
    assert [body["source"] for body in hub.posts] == ["codex", "native-memory"]  # RED
    pending = state.outbox_pending()
    assert [op.native_id for op in pending] == ["a-blocked", "b-blocked"]
    assert all(op.attempts == 0 for op in pending)
    assert state.next_due(active.key) == 0

    # Reopening the session preserves the budget and the order within its token.
    state.defer(active.key + ":writes:" + TOKEN_ENV["claude-code"] + ":0", NOW)
    active = sync_daemon._Session(config, state, lambda: NOW, transport)
    assert active.cycle(once=once) == 0
    assert hub.posts[-1]["thread_id"] == "a-blocked"
    assert [op.native_id for op in state.outbox_pending()] == ["b-blocked"]


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
def test_fix3_rejected_op_yields_to_next_and_retries_after_restart_or_backoff(local, status):
    _, state, root = local
    (root / "a-bad.jsonl").write_bytes(record("bad"))
    (root / "z-good.jsonl").write_bytes(record("good"))
    hub = Hub(reject=status)
    assert run(local, hub) == 1
    assert hub.stored.get("good") == record("good")
    assert state.cursor("claude-code", root / "a-bad.jsonl", "bad")["offset"] == 0
    assert bool(state.outbox_pending()) == (status not in (401, 403))
    hub.reject = None
    config, _, _ = local
    assert (
        sync_daemon.run(
            config, state, clock=lambda: NOW + 60, sleep=lambda _: None, transport=hub, once=True
        )
        == 0
    )
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


@pytest.mark.parametrize(
    "selection", ["default-parent", "default-root", "memory-parent", "memory-root", "empty"]
)
def test_fix2_install_refuses_symlinked_or_empty_selections(tmp_path, selection):
    home = tmp_path / "home"
    home.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    args = ["sync", "install", "--enable", "--no-service", "--home", str(home)]
    if selection.startswith("default"):
        if selection == "default-parent":
            (target / "projects").mkdir()
            (home / ".claude").symlink_to(target, target_is_directory=True)
        else:
            (home / ".claude").mkdir()
            (home / ".claude/projects").symlink_to(target, target_is_directory=True)
    elif selection.startswith("memory"):
        link = home / "notes"
        link.symlink_to(target, target_is_directory=True)
        root = link
        if selection == "memory-parent":
            (target / "nested").mkdir()
            root = link / "nested"
        args += ["--memory", "notes=" + str(root)]
    assert cli.main(args) == 2
    assert not (home / ".commonplace/sync/config.json").exists()


def test_fix2_free_sleep_handles_expired_deadlines(local, monkeypatch):
    now, slept = [NOW], []
    config, state, _ = local
    hub = Hub(interval=900, window="2033-05-18T03:33:21Z")  # NOW + 1

    def cycle(session, **kwargs):
        state.defer(session.key, NOW + 1)
        now[0] = NOW + 2  # Work finishes after both previously future deadlines.
        return 0

    monkeypatch.setattr(sync_daemon._Session, "cycle", cycle)
    try:
        result = sync_daemon.run(
            config,
            state,
            clock=lambda: now[0],
            sleep=slept.append,
            transport=hub,
            stop=lambda: bool(slept),
        )
    except ValueError as exc:
        raise AssertionError("expired deadlines must still produce a bounded sleep") from exc
    assert result == 0 and slept == [2]


@pytest.mark.parametrize("earlier", ["rejected", "conflict"])
def test_fix2_pause_exits_successfully_after_prior_failure(local, earlier):
    _, _, root = local
    (root / "a.jsonl").write_bytes(record("bad"))
    (root / "b.jsonl").write_bytes(record("good"))
    hub = Hub()

    def transport(target, token, method, route, body=None):
        if method == "POST":
            if body["thread_id"] == "bad":
                return (403, {}) if earlier == "rejected" else (409, {"code": "prefix_conflict"})
            return 409, {"error": "Workspace capture is off"}
        return hub(target, token, method, route, body)

    assert run(local, transport) == 0


@pytest.mark.parametrize("status", [408, 425])
def test_fix2_temporary_rejection_remains_pending(local, status):
    config, state, root = local
    (root / "bad.jsonl").write_bytes(record("bad"))
    assert run(local, Hub(reject=status)) == 1
    pending = state.outbox_pending()
    assert len(pending) == 1 and pending[0].native_id == "bad"
    # Retry after the durable backoff does not require restart/requeue.
    session = sync_daemon._Session(config, state, lambda: NOW + 60, Hub())
    assert session.cycle(once=True) == 0
    assert not state.outbox_pending()


@pytest.mark.parametrize("failure", ["symlink", "regular-file"])
def test_fix2_source_error_status_matches_actual_kind(local, monkeypatch, failure):
    config, state, root = local
    path = root / "bad.jsonl"
    path.write_bytes(record("bad"))
    source = sync_sources.Source("claude-code", str(path), "bad", path.stat().st_size)
    state.store_cursor("claude-code", path, "bad", 0, sha(b""))
    config["_discovered"] = [source]

    def refused(path):
        raise sync_sources.SourceError(
            "Symlinked source refused" if failure == "symlink" else "Source must be a regular file"
        )

    monkeypatch.setattr(sync_sources, "_open", refused)
    assert sync_sources.scan(config, state, now=NOW) == []
    assert state.statuses()[0]["status"] == "unreadable"


def parking_sources(local):
    config, _, root = local
    (root / "a-bad.jsonl").write_bytes(record("bad"))
    (root / "b-good.jsonl").write_bytes(record("good"))
    codex = root.parent / "codex"
    codex.mkdir()
    (codex / "session.jsonl").write_text(
        json.dumps({"type": "session_meta", "payload": {"id": "codex-session"}}) + "\n"
    )
    config["sources"].append({"source": "codex", "root": str(codex)})
    notes = root.parent / "notes"
    notes.mkdir()
    note = notes / "memory.md"
    note.write_text("Synthetic verbatim memory.\n")
    os.utime(note, (NOW - 120, NOW - 120))
    config["memories"] = [{"label": "notes", "root": str(notes)}]
    config["tokens"] = {env: "synthetic-" + source for source, env in TOKEN_ENV.items()}


@pytest.mark.parametrize(
    "stage,once",
    [("get", False), ("get", True), ("post", False), ("post", True), ("legacy", False)],
)
def test_card223_f1_capture_cadence_is_stream_local(local, stage, once):
    parking_sources(local)
    config, state, root = local
    hub, now, attempts = Hub(), [NOW], []

    def transport(target, token, method, route, body=None):
        limited = (stage == "get" and method == "GET" and "thread_id=bad" in route) or (
            stage == "post" and method == "POST" and body.get("thread_id") == "bad"
        )
        if limited:
            attempts.append(now[0])
            if now[0] < NOW + 7:
                return 429, {"error": "Capture not due; retry in 7 seconds"}
        return hub(target, token, method, route, body)

    active = sync_daemon._Session(config, state, lambda: now[0], transport)
    if stage == "legacy":
        state.defer(active.key, NOW + 900)
    assert active.cycle(once=once) == 0
    assert hub.stored.get("good") == record("good")  # RED
    assert {body["source"] for body in hub.posts} == {"claude-code", "codex", "native-memory"}
    if stage != "legacy":
        assert state.next_due(active.key) == 0  # RED
        assert active.retry_due == NOW + 7
        assert "bad" not in hub.stored
        if stage == "get":
            assert state.cursor("claude-code", root / "a-bad.jsonl", "bad") is None
        else:
            assert len(state.outbox_pending()) == 1
        # A fresh session honors the durable deadline even in --once mode.
        active = sync_daemon._Session(config, state, lambda: now[0], transport)
        assert active.cycle(once=once) == 0
        assert attempts == [NOW]
        now[0] += 7
        assert active.cycle(once=once) == 0
        assert hub.stored["bad"] == record("bad")
        assert attempts == [NOW, NOW + 7]
    assert not state.outbox_pending()


@pytest.mark.parametrize("fault", ["open", "read"])
def test_card223_f2_transient_slice_read_remains_retryable(local, monkeypatch, fault):
    parking_sources(local)
    config, state, root = local
    hub, now = Hub(), [NOW]
    active = sync_daemon._Session(config, state, lambda: now[0], hub)
    assert active.initialize() == 0
    sync_sources.scan(config, state, now=NOW)
    original_op = next(op for op in state.outbox_pending() if op.native_id == "bad")
    original_open = sync_sources._open

    @contextmanager
    def unavailable(path):
        if Path(path) == root / "a-bad.jsonl":
            if fault == "open":
                raise OSError("Synthetic transient failure")

            class Unreadable:
                def read(self, length):
                    raise OSError("Synthetic transient failure")

            yield Unreadable()
        else:
            with original_open(path) as stream:
                yield stream

    monkeypatch.setattr(sync_sources, "_open", unavailable)
    assert active.cycle() == 1
    pending = state.outbox_pending()
    assert [op.op_id for op in pending] == [original_op.op_id]  # RED
    assert pending[0].attempts == 0
    assert hub.stored.get("good") == record("good")
    assert {body["source"] for body in hub.posts} == {"claude-code", "codex", "native-memory"}
    assert active.retry_due == NOW + 60
    monkeypatch.setattr(sync_sources, "_open", original_open)
    active = sync_daemon._Session(config, state, lambda: now[0], hub)
    assert active.cycle(once=True) == 0
    assert "bad" not in hub.stored
    now[0] += 60
    assert active.cycle(once=True) == 0
    assert hub.stored["bad"] == record("bad")
    assert hub.posts[-1]["client_op_id"] == original_op.op_id
    assert not state.outbox_pending()


@pytest.mark.parametrize("status", [302, 408, 425, 500, 503, 200])
def test_card223_f3_failed_head_yields_then_retries_same_op(local, status):
    parking_sources(local)
    config, state, _ = local
    hub, now, attempts = Hub(), [NOW], []

    def transport(target, token, method, route, body=None):
        if method == "POST" and body.get("thread_id") == "bad":
            attempts.append(body.copy())
            if now[0] < NOW + 120:
                return status, {}  # A 200 without an offset is not an acknowledgement.
        return hub(target, token, method, route, body)

    active = sync_daemon._Session(config, state, lambda: now[0], transport)
    assert active.cycle() == 1
    assert hub.stored.get("codex-session") is not None  # RED
    # A valid cursor GET attributes POST-only errors to the suspect operation.
    assert "good" in hub.stored
    assert any(body["source"] == "native-memory" for body in hub.posts)
    pending = state.outbox_pending()
    assert len(pending) == 1
    assert pending[0].native_id == "bad" and pending[0].attempts == 1
    assert active.retry_due == NOW + 60
    active = sync_daemon._Session(config, state, lambda: now[0], transport)
    assert active.cycle(once=True) == 0
    assert len(attempts) == 1
    now[0] += 60
    assert active.cycle() == 1
    assert len(attempts) == 2 and attempts[0] == attempts[1]
    assert active.cycle() == 0
    assert len(attempts) == 2
    now[0] += 120
    assert active.cycle() == 0
    assert attempts == [attempts[0]] * 3
    assert hub.stored["bad"] == record("bad")
    assert not state.outbox_pending()


def test_card223_f4_legacy_without_retry_slot_yields_once(local, monkeypatch):
    parking_sources(local)
    config, state, _ = local
    hub, now, attempts = Hub(), [NOW], []
    budget = sync_daemon._Session._budget

    def no_legacy_slot(session, env_name, retry=False, **kwargs):
        return False if retry else budget(session, env_name, **kwargs)

    monkeypatch.setattr(sync_daemon._Session, "_budget", no_legacy_slot)

    def transport(target, token, method, route, body=None):
        if method == "POST" and body.get("thread_id") == "bad":
            attempts.append(body.copy())
            return 400, {"error": "Invalid or unsupported native transcript"}
        return hub(target, token, method, route, body)

    active = sync_daemon._Session(config, state, lambda: now[0], transport)
    state.defer(active.bkey + "-done", NOW)
    result = active.cycle()
    assert len(attempts) == 1  # RED
    assert result == 1
    assert hub.stored.get("codex-session") is not None
    assert any(body["source"] == "native-memory" for body in hub.posts)
    assert active.cycle() == 0
    assert len(attempts) == 1
    now[0] += 60
    assert active.cycle() == 1
    assert attempts == [attempts[0]] * 2


def test_card223_s1_two_poison_captures_do_not_starve_healthy(local):
    config, state, root = local
    for sid in ("p0", "p1"):
        (root / f"{sid}.jsonl").write_bytes(record(sid))
    now, attempts, hub = [NOW], [], Hub()

    def transport(target, token, method, route, body=None):
        if method == "POST" and body.get("thread_id", "").startswith("p"):
            attempts.append((now[0], body["thread_id"]))
            return 500, {}
        return hub(target, token, method, route, body)

    active = sync_daemon._Session(config, state, lambda: now[0], transport)
    state.defer(active.bkey + "-done", NOW)
    for step in range(0, 3 * 3600 + 1, 60):
        now[0] = NOW + step
        if step == 3660:
            (root / "healthy.jsonl").write_bytes(record("healthy"))
        active.cycle()
    assert hub.stored.get("healthy") == record("healthy")  # RED
    assert len(attempts) <= 24
    assert all(op.state == "pending" for op in state.outbox_all())
    assert all(state.next_due(active.key + ":op:" + op.op_id) > now[0] for op in state.outbox_all())


def test_card223_old_verified_conflict_is_requeued_but_changed_slice_stays_parked(local):
    parking_sources(local)
    config, state, root = local
    hub = Hub()
    active = sync_daemon._Session(config, state, lambda: NOW, hub)
    assert active.initialize() == 0
    sync_sources.scan(config, state, now=NOW)
    for op in state.outbox_pending():
        if op.native_id in ("bad", "good"):
            state.outbox_mark(op.op_id, "conflict")
    (root / "b-good.jsonl").write_bytes(record("good", "changed words"))
    assert run(local, hub) == 0
    assert hub.stored.get("bad") == record("bad")  # RED
    assert "good" not in hub.stored
    assert [(op.native_id, op.state) for op in state.outbox_all()] == [("good", "conflict")]


@pytest.mark.parametrize("fault", ["state-error", "invalid-prefix"])
def test_card223_ack_failure_yields_to_other_tokens(local, monkeypatch, fault):
    from commonplace.sync_state import StateError

    parking_sources(local)
    _, state, _ = local
    hub = Hub()
    ack = state.outbox_ack
    bad_acks = []

    def acknowledge(op_id, payload):
        op = next(op for op in state.outbox_pending() if op.op_id == op_id)
        if op.native_id == "bad":
            bad_acks.append(op_id)
            raise StateError("Synthetic acknowledgement failure")
        return ack(op_id, payload)

    monkeypatch.setattr(state, "outbox_ack", acknowledge)

    def transport(target, token, method, route, body=None):
        if method == "POST" and body.get("thread_id") == "bad":
            return 200, {
                "offset": body["offset"] + len(body["transcript"].encode()),
                "prefix_sha256": "invalid" if fault == "invalid-prefix" else sha(record("bad")),
            }
        return hub(target, token, method, route, body)

    assert run(local, transport) == 1
    assert hub.stored.get("codex-session") is not None  # RED
    assert hub.stored.get("good") == record("good")
    assert len(state.outbox_pending()) == 1
    assert len(bad_acks) == (fault == "state-error")


@pytest.mark.parametrize("fault", ["read", "response"])
def test_card223_retry_cap_is_visible_and_restart_recovers_same_op(local, monkeypatch, fault):
    config, state, root = local
    path = root / "bad.jsonl"
    path.write_bytes(record("bad"))
    now, hub = [NOW], Hub()
    active = sync_daemon._Session(config, state, lambda: now[0], hub)
    assert active.initialize() == 0
    sync_sources.scan(config, state, now=NOW)
    (original_op,) = state.outbox_pending()
    original_open = sync_sources._open
    broken = [True]

    def unavailable(path):
        if broken[0]:
            raise OSError("Synthetic unavailable source")
        return original_open(path)

    def transport(target, token, method, route, body=None):
        if method == "POST" and broken[0]:
            return 302, {}
        return hub(target, token, method, route, body)

    if fault == "read":
        monkeypatch.setattr(sync_sources, "_open", unavailable)
    active.transport = transport
    for step in (0, 60, 180, 420, 900, 1860):
        now[0] = NOW + step
        active.cycle(once=True)
    (parked,) = state.outbox_all()
    expected = "unreadable" if fault == "read" else "failed"
    assert parked.state == expected  # RED
    assert any(row["native_id"] == "bad" and row["status"] == expected for row in state.statuses())
    assert not state.conn.execute("SELECT 1 FROM cadence WHERE key LIKE '%:op:%'").fetchall()
    broken[0] = False
    assert (
        sync_daemon.run(
            config,
            state,
            clock=lambda: now[0],
            sleep=lambda _: None,
            transport=transport,
            once=True,
        )
        == 0
    )
    assert hub.posts[-1]["client_op_id"] == original_op.op_id
    assert hub.stored["bad"] == record("bad")
    assert not state.outbox_all()


def test_card223_guard_s2_short_hub_outage_preserves_budget(local):
    config, state, root = local
    for i in range(130):
        path = root / f"s{i:03d}.jsonl"
        path.write_bytes(record(f"s{i:03d}"))
        state.store_cursor("claude-code", path, f"s{i:03d}", 0, sha(b""))
    now, attempts, hub = [NOW], [], Hub()

    def transport(target, token, method, route, body=None):
        if method == "POST":
            attempts.append(now[0])
        if now[0] < NOW + 60:
            return 500, {}  # The hub outage affects cursor GETs as well as POSTs.
        return hub(target, token, method, route, body)

    active = sync_daemon._Session(config, state, lambda: now[0], transport)
    state.defer(active.bkey + "-done", NOW)
    assert active.cycle() == 1
    assert len(attempts) <= 2
    now[0] += 60
    assert active.cycle() == 0
    assert len(hub.stored) >= 118
    assert any(stamp == NOW + 60 for stamp in attempts)


def test_card223_guard_s3_import_account_cadence_limits_fanout(local):
    config, state, root = local
    notes = root.parent / "notes"
    notes.mkdir()
    for i in range(36):
        note = notes / f"m{i:02d}.md"
        note.write_text(f"Synthetic verbatim memory {i}.\n")
        os.utime(note, (NOW - 120, NOW - 120))
    config["memories"] = [{"label": "notes", "root": str(notes)}]
    now, posts, admitted, hub = [NOW], [], [], Hub()

    def transport(target, token, method, route, body=None):
        if method == "POST" and route == "/api/import":
            posts.append(now[0])
            if admitted and now[0] - admitted[-1] < 900:
                return 429, {"error": "Import not due; retry in 900 seconds"}
            admitted.append(now[0])
        return hub(target, token, method, route, body)

    active = sync_daemon._Session(config, state, lambda: now[0], transport)
    state.defer(active.bkey + "-done", NOW)
    for step in range(0, 3600 + 1, 60):
        now[0] = NOW + step
        assert active.cycle() == 0
    assert admitted == [NOW + step for step in (0, 900, 1800, 2700, 3600)]
    assert all(sum(start <= stamp < start + 900 for stamp in posts) <= 2 for start in admitted)


def test_card223_guard_slow_down_stops_get_pass(local):
    config, state, root = local
    for sid in ("a", "b", "c"):
        (root / f"{sid}.jsonl").write_bytes(record(sid))
    gets = []

    def transport(target, token, method, route, body=None):
        gets.append(route)
        return 429, {"error": "slow down"}

    active = sync_daemon._Session(config, state, lambda: NOW, transport)
    assert active.cycle() == 0
    assert len(gets) == 1


def test_card223_guard_s5b_run_loop_wakes_at_900_seconds(local):
    config, state, root = local
    (root / "bad.jsonl").write_bytes(record("bad"))
    now, attempts, sleeps, hub = [NOW], [], [], Hub(interval=900)

    def transport(target, token, method, route, body=None):
        if method == "POST":
            attempts.append(now[0] - NOW)
            if now[0] < NOW + 900:
                return 429, {"error": "Capture not due; retry in 900 seconds"}
        return hub(target, token, method, route, body)

    def advance(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    assert (
        sync_daemon.run(
            config,
            state,
            clock=lambda: now[0],
            sleep=advance,
            transport=transport,
            stop=lambda: now[0] >= NOW + 1800,
        )
        == 0
    )
    assert attempts == [0, 900]
    assert sleeps == [900, 900]


def test_card223_account_capture_wait_defers_whole_token(local):
    parking_sources(local)
    config, state, _ = local
    hub, posts = Hub(), []

    def transport(target, token, method, route, body=None):
        if method == "POST" and body["source"] == "claude-code":
            posts.append(body)
            return 429, {"error": "Account capture not due; retry in 900 seconds"}
        return hub(target, token, method, route, body)

    active = sync_daemon._Session(config, state, lambda: NOW, transport)
    assert active.cycle() == 0
    assert any(body["source"] == "native-memory" for body in hub.posts)  # RED
    assert len(posts) == 1
    assert state.next_due(active._token_key(TOKEN_ENV["claude-code"])) == NOW + 900


def test_card223_exhausted_budget_does_not_reread_body(local, monkeypatch):
    config, state, root = local
    (root / "bad.jsonl").write_bytes(record("bad"))
    active = sync_daemon._Session(config, state, lambda: NOW, Hub())
    state.defer(active.bkey + "-done", NOW)
    assert active.initialize() == 0
    sync_sources.scan(config, state, now=NOW)
    for _ in range(sync_daemon.WRITE_BUDGET[0]):
        assert active._budget(TOKEN_ENV["claude-code"])
    reads = []
    body = sync_sources.body

    def counted(op):
        reads.append(op.op_id)
        return body(op)

    monkeypatch.setattr(sync_sources, "body", counted)
    assert active.cycle() == 0
    assert reads == []  # RED


@pytest.mark.parametrize("status", [429, 408, 425, 500, 503])
def test_card223_token_retry_after_exponential_backoff_and_success_reset(local, status):
    parking_sources(local)
    config, state, root = local
    for name, sid in (("a-bad", "bad"), ("b-good", "good")):
        state.store_cursor("claude-code", root / f"{name}.jsonl", sid, 0, sha(b""))
    now, posts, hub, broken = [NOW], [], Hub(), [True]

    def transport(target, token, method, route, body=None):
        if method == "GET" and broken[0] and token == config["tokens"][TOKEN_ENV["claude-code"]]:
            return status, {"error": "slow down"}
        if method == "POST" and body["source"] == "claude-code":
            posts.append((now[0], body["thread_id"]))
            if broken[0]:
                reply = {"error": "slow down"}
                if len(posts) == 1:
                    reply["_retry_after"] = "37"
                return status, reply
        return hub(target, token, method, route, body)

    active = sync_daemon._Session(config, state, lambda: now[0], transport)
    state.defer(active.bkey + "-done", NOW)
    result = active.cycle()
    assert "codex-session" in hub.stored  # RED
    assert result == (0 if status == 429 else 1)
    assert len(posts) == 1
    key = active._token_key(TOKEN_ENV["claude-code"])
    assert state.next_due(key) == NOW + 37
    now[0] += 36
    assert active.cycle() == 0
    assert len(posts) == 1
    now[0] += 1
    assert active.cycle() == (0 if status == 429 else 1)
    assert len(posts) == 2
    assert state.next_due(key) == NOW + 157
    now[0] = NOW + 157
    broken[0] = False
    assert active.cycle() == 0
    assert hub.stored["good"] == record("good")
    assert state.next_due(key + ":failures") == 0
    assert not state.conn.execute("SELECT 1 FROM cadence WHERE key LIKE '%:op:%'").fetchall()
    broken[0] = True
    (root / "new.jsonl").write_bytes(record("new"))
    assert active.cycle() == (0 if status == 429 else 1)
    assert state.next_due(key) == now[0] + 60


def test_card223_initialize_backoff_doubles_and_caps_at_1800(local):
    config, state, root = local
    (root / "bad.jsonl").write_bytes(record("bad"))
    now, gets = [NOW], []

    def transport(target, token, method, route, body=None):
        gets.append(now[0])
        return 429, {"error": "slow down"}

    active = sync_daemon._Session(config, state, lambda: now[0], transport)
    assert active.cycle() == 0
    assert state.next_due(active.key) == 0  # RED
    key = active._token_key(TOKEN_ENV["claude-code"])
    for delay in (60, 120, 240, 480, 960, 1800, 1800):
        assert state.next_due(key) == now[0] + delay
        assert active.cycle() == 0
        before = len(gets)
        now[0] += delay
        assert active.cycle() == 0
        assert len(gets) == before + 1


@pytest.mark.parametrize("status", [408, 425, 500, 503, 429])
def test_card223_initialize_token_failure_skips_same_token_but_sends_others(local, status):
    parking_sources(local)
    config, state, _ = local
    hub, gets = Hub(), []

    def transport(target, token, method, route, body=None):
        if (
            method == "GET"
            and route.startswith("/api/capture?")
            and token == config["tokens"][TOKEN_ENV["claude-code"]]
        ):
            gets.append(route)
            return status, {"error": "slow down", "_retry_after": "37"}
        return hub(target, token, method, route, body)

    active = sync_daemon._Session(config, state, lambda: NOW, transport)
    result = active.cycle()
    assert len(gets) == 1  # RED
    assert "codex-session" in hub.stored  # RED
    assert any(body["source"] == "native-memory" for body in hub.posts)
    assert "good" not in hub.stored
    assert result == (0 if status == 429 else 1)
    assert state.next_due(active._token_key(TOKEN_ENV["claude-code"])) == NOW + 37


@pytest.mark.parametrize("status", [500, 408, 425])
@pytest.mark.parametrize("post_only", [False, True], ids=["hub-wide", "post-only"])
def test_card223_e1_outage_recovers_without_restart_or_park(local, status, post_only):
    config, state, root = local
    path = root / "bad.jsonl"
    path.write_bytes(record("bad"))
    state.store_cursor("claude-code", path, "bad", 0, sha(b""))
    now, posts, health_gets, hub = [NOW], [], [], Hub()

    def transport(target, token, method, route, body=None):
        if method == "POST":
            posts.append(now[0])
        else:
            health_gets.append((now[0], route))
        if now[0] < NOW + 2400 and (method == "POST" or not post_only):
            return status, {}
        return hub(target, token, method, route, body)

    active = sync_daemon._Session(config, state, lambda: now[0], transport)
    state.defer(active.bkey + "-done", NOW)
    for step in range(0, 2400, 60):
        now[0] = NOW + step
        active.cycle()
    assert len(posts) <= 8  # RED
    assert [op.state for op in state.outbox_all()] == ["pending"]  # RED
    op = state.outbox_all()[0]
    assert state.next_due(active.key + ":op:" + op.op_id + ":failures") == 0
    assert len(health_gets) == len(posts)
    # Cursor health probes spend no client write slots.
    key = active.key + ":writes:" + TOKEN_ENV["claude-code"]
    assert sum(state.next_due(f"{key}:{i}") > 0 for i in range(120)) == len(posts)
    for step in range(2400, 3 * 3600 + 1, 60):
        now[0] = NOW + step
        active.cycle()
    assert hub.stored.get("bad") == record("bad")  # RED
    assert not state.outbox_all()
    assert not state.conn.execute("SELECT 1 FROM cadence WHERE key LIKE '%:op:%'").fetchall()


def test_card223_s1b_known_healthy_capture_follows_two_poisons_in_first_pass(local):
    config, state, root = local
    for sid in ("a0", "a1", "z"):
        path = root / f"{sid}.jsonl"
        path.write_bytes(record(sid))
        state.store_cursor("claude-code", path, sid, 0, sha(b""))
    hub, posts, gets = Hub(), [], []

    def transport(target, token, method, route, body=None):
        if method == "POST":
            posts.append(body.copy())
            if body["thread_id"].startswith("a"):
                return 500, {}
        else:
            gets.append(route)
            return 200, {"offset": 12345, "prefix_sha256": sha(b"synthetic remote cursor")}
        return hub(target, token, method, route, body)

    active = sync_daemon._Session(config, state, lambda: NOW, transport)
    state.defer(active.bkey + "-done", NOW)
    result = active.cycle()
    assert hub.stored.get("z") == record("z")  # RED
    assert result == 1
    assert [body["thread_id"] for body in posts] == ["a0", "a1", "z"]
    assert len(gets) == 2
    for sid, route in zip(("a0", "a1"), gets):
        assert "source=claude-code" in route and f"thread_id={sid}" in route
        cursor = state.cursor("claude-code", root / f"{sid}.jsonl", sid)
        assert cursor["offset"] == 0 and cursor["prefix_sha256"] == sha(b"")
    assert all(op.state == "pending" for op in state.outbox_all())
    assert state.next_due(active.key + ":token:" + TOKEN_ENV["claude-code"] + ":failures") == 0


@pytest.mark.parametrize(
    "first,second,scope",
    [
        (
            {"error": "slow down", "_retry_after": "10000000"},
            {"error": "slow down", "_retry_after": "0"},
            "token",
        ),
        (
            {"error": "slow down", "_retry_after": "Fri, 01 Jan 2100 00:00:00 GMT"},
            {"error": "slow down", "_retry_after": "Wed, 01 Jan 2020 00:00:00 GMT"},
            "token",
        ),
        (
            {"error": "Account capture not due; retry in 10000000 seconds"},
            {"error": "Account capture not due; retry in 0 seconds"},
            "token",
        ),
        (
            {"error": "Capture not due; retry in 10000000 seconds"},
            {"error": "Capture not due; retry in 0 seconds"},
            "stream",
        ),
    ],
    ids=["seconds", "http-date", "account-message", "stream-message"],
)
def test_card223_server_wait_is_clamped_and_invalid_wait_uses_floor(local, first, second, scope):
    config, state, root = local
    path = root / "bad.jsonl"
    path.write_bytes(record("bad"))
    state.store_cursor("claude-code", path, "bad", 0, sha(b""))
    now, posts = [NOW], []

    def transport(target, token, method, route, body=None):
        posts.append(now[0])
        return 429, dict(first if len(posts) == 1 else second)

    active = sync_daemon._Session(config, state, lambda: now[0], transport)
    assert active.cycle() == 0
    # Include the legacy shared key so the base comparison fails by assertion.
    due = max(
        state.next_due(active.key),
        *(
            state.next_due(key)
            for key in [
                active.key + ":token:" + TOKEN_ENV["claude-code"],
                active._stream_key("claude-code", "bad")
                if hasattr(active, "_stream_key")
                else active.key,
            ]
        ),
    )
    assert due == NOW + 3600  # RED
    now[0] += 3600
    assert active.cycle() == 0
    key = (
        active._token_key(TOKEN_ENV["claude-code"])
        if scope == "token"
        else active._stream_key("claude-code", "bad")
    )
    assert state.next_due(key) == now[0] + (120 if scope == "token" else 60)
    now[0] += 1
    assert active.cycle() == 0
    assert len(posts) == 2


def test_card223_server_409_stays_rejected_across_restart(local):
    config, state, root = local
    (root / "bad.jsonl").write_bytes(record("bad"))
    hub, posts = Hub(), []

    def transport(target, token, method, route, body=None):
        if method == "POST":
            posts.append(body.copy())
            return 409, {"code": "prefix_conflict"}
        return hub(target, token, method, route, body)

    assert run(local, transport) == 1
    assert [op.state for op in state.outbox_all()] == ["rejected"]  # RED
    assert run(local, transport) == 0
    assert len(posts) == 1
    assert any(row["status"] == "rejected" for row in state.statuses())


def test_card223_guard_get_outage_never_parks_pending_operation(local):
    config, state, root = local
    pending_path = root / "pending.jsonl"
    pending_path.write_bytes(record("pending"))
    state.store_cursor("claude-code", pending_path, "pending", 0, sha(b""))
    (root / "a-unknown.jsonl").write_bytes(record("unknown"))
    sync_sources.scan(config, state, now=NOW)
    now, gets, hub = [NOW], [], Hub()

    def transport(target, token, method, route, body=None):
        if method == "GET":
            gets.append(now[0])
        if now[0] < NOW + 2400:
            return 500, {}
        return hub(target, token, method, route, body)

    active = sync_daemon._Session(config, state, lambda: now[0], transport)
    state.defer(active.bkey + "-done", NOW)
    for step in range(0, 2400, 60):
        now[0] = NOW + step
        active.cycle()
        assert all(op.state == "pending" for op in state.outbox_all())
    for step in range(2400, 3 * 3600 + 1, 60):
        now[0] = NOW + step
        active.cycle()
    assert hub.stored.get("pending") == record("pending")
    assert hub.stored.get("unknown") == record("unknown")
    assert not state.outbox_all()


def test_card223_guard_successful_get_does_not_reset_post_streak(local):
    config, state, root = local
    (root / "new.jsonl").write_bytes(record("new"))
    active = sync_daemon._Session(config, state, lambda: NOW, Hub())
    key = active.key + ":token:" + TOKEN_ENV["claude-code"]
    state.defer(key + ":failures", 3)
    assert active.initialize() == 0
    assert state.next_due(key + ":failures") == 3


def test_card223_suspect_operation_parks_only_after_24_hours(local):
    config, state, root = local
    path = root / "bad.jsonl"
    path.write_bytes(record("bad"))
    state.store_cursor("claude-code", path, "bad", 0, sha(b""))
    now, posts, hub = [NOW], [], Hub()

    def transport(target, token, method, route, body=None):
        if method == "POST":
            posts.append(body.copy())
            return 500, {}
        return hub(target, token, method, route, body)

    active = sync_daemon._Session(config, state, lambda: now[0], transport)
    state.defer(active.bkey + "-done", NOW)
    steps = [0, 60, 180, 420, 900, 1860] + [1860 + 1800 * i for i in range(1, 48)]
    for step in steps:
        now[0] = NOW + step
        active.cycle()
        if step < 24 * 3600:
            assert [op.state for op in state.outbox_all()] == ["pending"]  # RED
        if step == 3660:
            active = sync_daemon._Session(config, state, lambda: now[0], transport)
    assert [op.state for op in state.outbox_all()] == ["failed"]  # RED
    assert all(body == posts[0] for body in posts)
    assert not state.conn.execute("SELECT 1 FROM cadence WHERE key LIKE '%:op:%'").fetchall()
    assert any(row["status"] == "failed" for row in state.statuses())


@pytest.mark.parametrize("health", ["network", "invalid-cursor"])
def test_card223_failed_health_probe_keeps_operation_counter_untouched(local, health):
    parking_sources(local)
    config, state, root = local
    for name, sid in (("a-bad", "bad"), ("b-good", "good")):
        state.store_cursor("claude-code", root / f"{name}.jsonl", sid, 0, sha(b""))
    hub = Hub()

    def transport(target, token, method, route, body=None):
        if token == config["tokens"][TOKEN_ENV["claude-code"]]:
            if method == "POST":
                return 500, {}
            if health == "network":
                raise OSError("Synthetic health probe failure")
            return 200, {"offset": 0, "prefix_sha256": "invalid"}
        return hub(target, token, method, route, body)

    active = sync_daemon._Session(config, state, lambda: NOW, transport)
    assert active.cycle() == 1
    assert "codex-session" in hub.stored  # RED
    assert "good" not in hub.stored
    assert all(op.state == "pending" for op in state.outbox_all())
    assert not state.conn.execute("SELECT 1 FROM cadence WHERE key LIKE '%:op:%'").fetchall()
