"""Rules hook behavior over real HTTP, with controlled publication boundaries."""

import ast
import importlib.util
import json
import os
import stat
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests import test_commonplace_rules as rule_tests
from tests._commonplace_helpers import hub_fixture  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "commonplace/agent-skill/rules_sync.py"
INSTALLER = SCRIPT.with_name("install.sh")
env_fixture = pytest.fixture(name="env")(rule_tests.env.__wrapped__)


@pytest.fixture
def script():
    spec = importlib.util.spec_from_file_location("rules_sync", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def client(hub, env, tmp_path, monkeypatch, script):
    url = f"http://127.0.0.1:{hub.port}"
    monkeypatch.setenv("COMMONPLACE_URL", url)
    monkeypatch.setenv("COMMONPLACE_TOKEN", env.token)
    out = tmp_path / "rules.md"
    args = script.parser().parse_args(["sync", "--out", str(out)])
    paths = script.paths_for(args)
    identity = script.connection(args)[2]
    owner = script.binding(args, paths, identity)
    calls = []
    original = hub.gateway.mediator._tools_call

    def counted(principal, request):
        calls.append(request)
        return original(principal, request)

    monkeypatch.setattr(hub.gateway.mediator, "_tools_call", counted)
    return SimpleNamespace(
        args=args, paths=paths, owner=owner, url=url, token=env.token, calls=calls, out=out
    )


def cli(client, *extra, token=None, url=None, stdin="garbage", timeout=4):
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(client.out.parent),
        "COMMONPLACE_URL": client.url if url is None else url,
        "COMMONPLACE_TOKEN": client.token if token is None else token,
    }
    return subprocess.run(
        [sys.executable, str(SCRIPT), "sync", "--out", str(client.out), *extra],
        input=stdin,
        text=True,
        capture_output=True,
        timeout=timeout,
        env=environment,
        check=False,
    )


def run_sync(script, client):
    return script.sync(
        client.args, client.paths, client.owner, client.url, client.token, time.monotonic() + 2
    )


def add_block(script, client, *, cap=None):
    block = client.out.with_name("AGENTS.md")
    block.write_bytes(b"User text\r\n\xff\r\n")
    block.chmod(0o640)
    client.args.block = str(block)
    client.args.file_cap = cap
    client.paths = script.paths_for(client.args)
    client.owner = script.binding(client.args, client.paths, client.owner["identity"])
    client.args.install_block = True
    script.install(client.args, client.paths, client.owner)
    client.args.install_block = False
    return block


def install(script, client):
    script.install(client.args, client.paths, client.owner)


def state(client):
    return json.loads(client.paths["state"].read_bytes())


def snapshots(client):
    return {key: path.read_bytes() for key, path in client.paths.items() if path.exists()}


def reply(body='## Hard limits\n\n- "Keep exact words."\n', seq="123456789", count=1):
    return f"effective rules: {count}\n{body}\nseq 1\nrules_seq {seq}"


def context(result):
    return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]


def test_first_run_real_hub_exact_md_modes_state_and_unchanged_file(script, client, env):
    env.owner.set_check("*", "warn")
    env.owner.create("*", "Exact words 😺\nnext @line <tag>", "limit")
    installed = cli(client, "--install")
    assert installed.returncode == 0 and not installed.stdout and not installed.stderr
    assert not client.calls  # Installation has no network dependency.
    first = cli(client)
    assert first.returncode == 0 and not first.stderr
    saved = state(client)
    expected = env.worker.rules_get(effective=True, format="md")["reply"]
    md, _, _, seq, _ = script.parse_reply(expected)
    data = client.out.read_bytes()
    assert data.endswith(md.encode())
    assert f"rules_seq {seq}".encode() in data
    assert str(seq) not in context(first)
    assert saved["identity"] == client.owner["identity"]
    assert saved["file_hash"] == script.digest(data)
    assert saved["rules_seq"] == seq
    assert saved["md_hash"] == script.digest(md.encode())
    assert client.calls[0]["params"] == {
        "name": "rules_get",
        "arguments": {"format": "md", "effective": True},
    }
    for path in client.paths.values():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert client.token.encode() not in path.read_bytes()
    before = client.out.stat().st_mtime_ns
    client.calls.clear()
    second = cli(client, stdin="")
    assert second.returncode == 0 and context(second) == context(first)
    assert len(client.calls) == 1
    assert client.out.read_bytes() == data and client.out.stat().st_mtime_ns == before


def test_rule_and_sequence_only_changes_rewrite_header(script, client, env):
    install(script, client)
    env.owner.set_check("*", "warn")
    created = env.owner.create("*", "Before", "instruction")
    assert cli(client).returncode == 0
    first = client.out.read_bytes()
    env.owner.edit(created["id"], created["version"], text="After")
    changed = cli(client)
    assert b'"After"' in client.out.read_bytes() and client.out.read_bytes() != first
    assert state(client)["version"] in context(changed)
    old_version, old_seq = state(client)["version"], state(client)["rules_seq"]
    with env.worker.store.transaction() as conn:
        conn.execute("UPDATE agent_seq SET token=token+1 WHERE agent='*'")
    assert cli(client).returncode == 0
    assert state(client)["version"] == old_version and state(client)["rules_seq"] != old_seq
    assert f"rules_seq {state(client)['rules_seq']}".encode() in client.out.read_bytes()


def test_no_owner_no_writes_and_foreign_identity_no_request(script, client):
    assert cli(client).stdout == "" and not client.out.exists()
    assert not any(path.exists() for path in client.paths.values())
    install(script, client)
    before = snapshots(client)
    result = cli(client, token="different-synthetic-token")
    assert result.returncode == 0 and "belong to another connection" in context(result)
    assert snapshots(client) == before and client.calls == []


def test_off_empty_and_unknown_store_loss_keep_last_copy(script, client, env, monkeypatch):
    block = add_block(script, client)
    run_sync(script, client)
    assert cli(client, "--block", str(block)).returncode == 0
    assert b"rules_seq" not in client.out.read_bytes()
    assert b"not available" not in client.out.read_bytes()
    created = env.owner.create("*", "Accepted", "style")
    assert cli(client, "--block", str(block)).returncode == 0
    assert b'"Accepted"' in block.read_bytes()
    before = snapshots(client)
    # Actual store loss is guarded even while the check mode remains off.
    rules_path = env.worker.store.path
    Path(rules_path).unlink()
    lost = cli(client, "--block", str(block))
    assert lost.returncode == 0 and "could not be refreshed" in context(lost)
    assert snapshots(client) == before
    assert created["text"] == "Accepted"
    for text in (
        "rules_seq unavailable: busy; keep your last copy",
        "rules_seq store_lost: owner must confirm",
        'no: unknown argument effective\n- "Proposed"\nseq 2',
        reply() + "\nnot the final marker",
        reply(seq="unknown"),
    ):
        monkeypatch.setattr(script, "fetch", lambda *_, text=text: text)
        with pytest.raises(ValueError):
            run_sync(script, client)
        assert snapshots(client) == before


def test_unavailable_boards_lock_real_hub_preserves_copy(script, client, env):
    import sqlite3

    from commonplace.boards import BoardService

    boards = BoardService(env.owner.ctx)
    boards.board_add(slug="work", wing="shared")
    env.owner.create("*", "Board instruction", "instruction", "board", "work")
    install(script, client)
    assert cli(client).returncode == 0
    before = snapshots(client)
    with sqlite3.connect(boards.store.path) as conn:
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("BEGIN EXCLUSIVE")
        result = cli(client)
    assert result.returncode == 0 and "could not be refreshed" in context(result)
    assert snapshots(client) == before


@pytest.mark.parametrize("block_mode", [False, True])
def test_size_whole_rules_priority_counts_and_off_too_large(script, client, block_mode):
    if block_mode:
        add_block(script, client)
    else:
        install(script, client)
    body = '## Hard limits\n\n- "' + "😺" * 200 + '"\n\n## Standing instructions\n\n'
    body += "\n".join('- "' + "x" * 400 + f'{i}"' for i in range(40)) + "\n"
    text = reply(body=body, count=41)
    owner = dict(client.owner, max_bytes=2000)
    data, _, _, seq = script.render_file(text, owner)
    assert seq is None and b"rules_seq 123456789" not in data and b"Pass rules_seq=" not in data
    assert b"This list is incomplete" in data and b"Not shown:" in data
    assert "😺" * 200 in data.decode()  # Limits come first.
    actual = script.managed(data, owner["identity"]) if block_mode else data
    assert len(actual) <= 2000
    rules = [line for line in data.decode().splitlines() if line.startswith("- ")]
    for line in rules:
        assert isinstance(json.loads(line[2:]), str)
    assert f"{41 - len(rules)} standing instructions".encode() in data
    partial = (
        'effective rules: 3\n## Hard limits\n\n- "One"\n\nseq 1\n'
        "Incomplete: 2 rules not shown.\nrules_seq too_large: trim"
    )
    data, _, _, seq = script.render_file(partial, client.owner)
    assert seq is None and b"Not shown: 2 other rules." in data
    assert b"This list is incomplete" in data and b"Pass rules_seq=" not in data


def test_parser_only_last_line_and_no_proposal_fallback(script):
    md = '## Hard limits\n\n- "rules_seq 987654321"\n'
    assert script.parse_reply(reply(body=md, seq="off"))[3] is None
    assert script.parse_reply(reply(body=md))[3] == "123456789"
    for bad in (
        reply(body=md, seq="off") + "\n\n",
        reply(body=md).replace("effective rules: 1", "effective rules: 2"),
        reply(body=md).replace("effective rules: 1", "proposed rules: 1"),
        reply(body='## Hard limits\n\n- "@import"\n'),
    ):
        with pytest.raises(ValueError):
            script.parse_reply(bad)


def test_block_preserves_bytes_mode_owner_and_repairs_before_fetch(script, client, monkeypatch):
    block = add_block(script, client)
    metadata = block.stat()
    run_sync(script, client)
    original = block.read_bytes()
    assert original.endswith(b"\n\nUser text\r\n\xff\r\n")
    assert stat.S_IMODE(block.stat().st_mode) == 0o640
    assert (block.stat().st_uid, block.stat().st_gid) == (metadata.st_uid, metadata.st_gid)
    block.write_bytes(b"Generator's replacement\r\n")
    fetched = []

    def offline(*_):
        repaired = block.read_bytes()
        assert client.out.read_bytes() in repaired
        assert repaired.endswith(b"Generator's replacement\r\n")
        fetched.append(True)
        raise OSError("offline")

    monkeypatch.setattr(script, "fetch", offline)
    with pytest.raises(OSError):
        run_sync(script, client)
    assert fetched == [True]
    assert state(client)["block_hash"] == script.digest(
        script.managed(client.out.read_bytes(), client.owner["identity"])
    )


def test_concurrent_outside_edit_is_not_overwritten_or_committed(script, client, env, monkeypatch):
    block = add_block(script, client)
    run_sync(script, client)
    committed = client.paths["state"].read_bytes()
    env.owner.create("*", "Changed", "limit")
    replace = script.os.replace
    external = block.read_bytes() + b"Concurrent user edit\r\n"

    def race(source, destination):
        replace(source, destination)
        if destination == client.out:
            block.write_bytes(external)

    monkeypatch.setattr(script.os, "replace", race)
    with pytest.raises(ValueError, match="Instructions changed"):
        run_sync(script, client)
    assert block.read_bytes() == external
    assert client.paths["state"].read_bytes() == committed


def test_missing_markers_without_committed_proof_and_file_cap_refuse(script, client, env):
    block = add_block(script, client, cap=600)
    env.owner.create("*", "A complete rule " * 20, "limit")
    before = snapshots(client)
    # Stub fits but a real approved-rules header and block do not.
    with pytest.raises(ValueError, match="whole-file cap"):
        run_sync(script, client)
    assert snapshots(client) == before
    block.write_bytes(b"No markers\n")
    before = snapshots(client)
    with pytest.raises(ValueError, match="Install managed block"):
        run_sync(script, client)
    assert snapshots(client) == before


@pytest.mark.parametrize("boundary", ["out", "block", "state"])
def test_crash_at_each_publication_boundary_recovers(script, client, env, monkeypatch, boundary):
    block = add_block(script, client)
    run_sync(script, client)
    env.owner.create("*", "New approved words", "limit")
    original = script.atomic_write

    class Crash(BaseException):
        pass

    def crash(path, *args, **kwargs):
        if path == client.paths[boundary]:
            raise Crash
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(script, "atomic_write", crash)
        with pytest.raises(Crash):
            run_sync(script, client)
    run_sync(script, client)
    assert b'"New approved words"' in client.out.read_bytes()
    assert script.artifacts_match(client.paths, state(client), client.owner)
    assert client.out.read_bytes() in block.read_bytes()


def test_rebind_crash_stays_old_owner_then_install_completes(script, client, monkeypatch):
    block = add_block(script, client)
    run_sync(script, client)
    old_owner = client.paths["owner"].read_bytes()
    new_owner = dict(client.owner, identity="a" * 64)
    original = script.atomic_write

    class Crash(BaseException):
        pass

    def crash(path, *args, **kwargs):
        if path == client.out:
            raise Crash
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(script, "atomic_write", crash)
        with pytest.raises(Crash):
            script.install(client.args, client.paths, new_owner)
    assert client.paths["owner"].read_bytes() == old_owner
    assert b"Identity aaaaaaaaaaaa" in block.read_bytes()
    script.install(client.args, client.paths, new_owner)
    assert json.loads(client.paths["owner"].read_bytes()) == new_owner
    assert not client.paths["state"].exists()
    assert script.STUB.encode() in client.out.read_bytes()


def test_other_destination_block_identity_refused_before_stubs(script, client):
    add_block(script, client)
    before = snapshots(client)
    foreign = dict(client.owner, identity="b" * 64, out=str(client.out.with_name("foreign.md")))
    paths = {
        **client.paths,
        "out": Path(foreign["out"]),
        "owner": client.out.with_name("foreign.owner"),
    }
    with pytest.raises(ValueError, match="another connection"):
        script.install(client.args, paths, foreign)
    assert snapshots(client) == before and not paths["out"].exists()


def test_owner_is_rechecked_before_each_rename(script, client, monkeypatch):
    install(script, client)
    original = script.os.fsync

    def rebind(fd):
        original(fd)
        client.paths["owner"].write_text('{"identity":"new binding"}')

    monkeypatch.setattr(script.os, "fsync", rebind)
    old = client.out.read_bytes()
    with pytest.raises(ValueError, match="binding changed"):
        run_sync(script, client)
    assert client.out.read_bytes() == old and not client.paths["state"].exists()


def test_install_waits_for_fetch_then_old_binding_cannot_publish(script, client, monkeypatch):
    import fcntl

    install(script, client)
    entered, release, waiting = threading.Event(), threading.Event(), threading.Event()
    fetch = script.fetch
    flock = fcntl.flock

    def controlled(*args):
        entered.set()
        assert release.wait(2)
        return fetch(*args)

    def observed(fd, operation):
        if operation == fcntl.LOCK_EX:
            waiting.set()
        return flock(fd, operation)

    monkeypatch.setattr(script, "fetch", controlled)
    monkeypatch.setattr(fcntl, "flock", observed)
    new_owner = dict(client.owner, identity="c" * 64)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run_sync, script, client)
        assert entered.wait(2)
        second = pool.submit(script.install, client.args, client.paths, new_owner)
        assert waiting.wait(2) and not second.done()
        release.set()
        first.result(timeout=2)
        second.result(timeout=2)
    assert script.STUB.encode() in client.out.read_bytes()
    before = snapshots(client)
    assert (
        "belong to another connection"
        in run_sync(script, client)["hookSpecificOutput"]["additionalContext"]
    )
    assert snapshots(client) == before


def test_twenty_parallel_calls_reuse_waited_result_without_partial_artifacts(
    script, client, monkeypatch
):
    import fcntl

    block = add_block(script, client)
    entered, release, all_waiting = threading.Event(), threading.Event(), threading.Event()
    fetch, flock = script.fetch, fcntl.flock
    waiting_count = [0]
    guard = threading.Lock()

    def controlled(*args):
        entered.set()
        assert release.wait(3)
        return fetch(*args)

    def observed(fd, operation):
        if operation == fcntl.LOCK_EX:
            with guard:
                waiting_count[0] += 1
                if waiting_count[0] == 19:
                    all_waiting.set()
        return flock(fd, operation)

    versions = []
    replace = script.os.replace

    def inspect(source, destination):
        replace(source, destination)
        if destination == client.out:
            versions.append(client.out.read_bytes())

    monkeypatch.setattr(script, "fetch", controlled)
    monkeypatch.setattr(fcntl, "flock", observed)
    monkeypatch.setattr(script.os, "replace", inspect)
    with ThreadPoolExecutor(max_workers=20) as pool:
        first = pool.submit(run_sync, script, client)
        assert entered.wait(2)
        others = [pool.submit(run_sync, script, client) for _ in range(19)]
        assert all_waiting.wait(2)
        release.set()
        results = [first.result(timeout=3), *[job.result(timeout=3) for job in others]]
    assert len(client.calls) == 1 and all(result == results[0] for result in results)
    assert versions == [client.out.read_bytes()]
    assert client.out.read_bytes() in block.read_bytes()
    assert script.artifacts_match(client.paths, state(client), client.owner)


@contextmanager
def http_server(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("status", [401, 500, 302, 200])
def test_http_errors_bad_json_redirect_never_followed(script, client, monkeypatch, status):
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            hits.append((self.path, self.headers.get("Authorization")))
            self.send_response(status)
            self.send_header("Location", "/credential-sink")
            self.end_headers()
            self.wfile.write(b"not JSON")

        def log_message(self, *_):
            pass

    with http_server(Handler) as url:
        client.url = url
        monkeypatch.setenv("COMMONPLACE_URL", url)
        client.owner = script.binding(client.args, client.paths, script.connection(client.args)[2])
        install(script, client)
        before = snapshots(client)
        result = cli(client)
    assert result.returncode == 0 and "could not be refreshed" in context(result)
    assert snapshots(client) == before
    assert hits == [("/mcp", "Bearer " + client.token)]
    assert client.token not in result.stdout + result.stderr


def test_process_watchdog_bounds_slow_hub_and_open_stdin_pipe(script, client, monkeypatch):
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            release.wait(2)

        def log_message(self, *_):
            pass

    with http_server(Handler) as url:
        client.url = url
        monkeypatch.setenv("COMMONPLACE_URL", url)
        client.owner = script.binding(client.args, client.paths, script.connection(client.args)[2])
        install(script, client)
        before = snapshots(client)
        started = time.monotonic()
        result = cli(client)
        elapsed = time.monotonic() - started
        release.set()
    assert result.returncode == 0 and elapsed < 0.5
    assert "could not be refreshed" in context(result)
    assert snapshots(client) == before
    # No waiting for EOF on a still-open hook input pipe.
    process = subprocess.Popen(
        [sys.executable, str(SCRIPT), "sync", "--out", str(client.out.with_name("unbound.md"))],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert process.wait(timeout=0.5) == 0
    finally:
        process.communicate(timeout=1)


def test_environment_proxy_is_ignored_and_plain_remote_http_refused(script, client, monkeypatch):
    install(script, client)
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:1")
    monkeypatch.setenv("no_proxy", "")
    run_sync(script, client)
    assert len(client.calls) == 1
    for url in (
        "http://hub.example",
        "https://user:password@hub.example",
        "https://hub.example?q=x",
        "https://hub.example/#x",
        "file:///tmp/socket",
    ):
        before = snapshots(client)
        result = cli(client, url=url)
        assert result.returncode == 0 and "could not be refreshed" in context(result)
        assert snapshots(client) == before and len(client.calls) == 1


@pytest.mark.parametrize("path_key", ["out", "block", "owner", "state", "lock"])
def test_all_git_realpaths_refused_before_any_write(script, client, tmp_path, path_key):
    repo = tmp_path / "repository"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: synthetic-linked-worktree")
    if path_key == "out":
        client.out = repo / "rules.md"
    elif path_key == "block":
        client.args.block = str(repo / "AGENTS.md")
    else:
        client.paths[path_key].symlink_to(repo / "sidecar")
    extra = ["--block", client.args.block] if client.args.block else []
    before = {p: p.read_bytes() for p in repo.iterdir() if p.is_file()}
    result = cli(client, "--install", *extra)
    assert result.returncode != 0
    assert {p: p.read_bytes() for p in repo.iterdir() if p.is_file()} == before
    assert not client.out.exists()
    assert not client.paths["lock"].exists()


def test_output_symlink_into_git_tree_refused(script, client, tmp_path):
    repo = tmp_path / "repository"
    repo.mkdir()
    (repo / ".git").mkdir()
    client.out.symlink_to(repo / "rules.md")
    assert cli(client, "--install").returncode != 0
    assert not (repo / "rules.md").exists()
    assert not client.paths["lock"].exists()


def test_token_file_mode_and_no_token_in_artifacts(script, client, tmp_path):
    credential = tmp_path / "credential"
    credential.write_text(client.token + "\n")
    credential.chmod(0o644)
    assert cli(client, "--install", "--token-file", str(credential), token="").returncode != 0
    credential.chmod(0o600)
    assert cli(client, "--install", "--token-file", str(credential), token="").returncode == 0
    run_sync(script, client)  # Warm the real hub's first rules-store creation.
    result = cli(client, "--token-file", str(credential), token="")
    assert result.returncode == 0 and "version" in context(result)
    assert client.token not in result.stdout + result.stderr
    assert all(client.token.encode() not in raw for raw in snapshots(client).values())


def test_sync_bad_arguments_fail_open_and_manual_errors_nonzero(
    script, client, monkeypatch, capsys
):
    assert cli(client, "--bad-argument").returncode == 0
    assert cli(client, "--install", "--bad-argument").returncode != 0
    install(script, client)

    def unexpected(*_):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(script, "sync", unexpected)
    monkeypatch.setattr(script, "STARTED", time.monotonic())
    assert script.main(["sync", "--out", str(client.out)]) == 0
    assert "could not be refreshed" in capsys.readouterr().out


def test_standalone_python39_syntax_and_installer_rules_dry_run(tmp_path):
    ast.parse(SCRIPT.read_text(), feature_version=(3, 9))
    environment = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "COMMONPLACE_TOKEN": "synthetic-secret",
    }
    result = subprocess.run(
        [str(INSTALLER), "--rules", "--dry-run"],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    assert list(tmp_path.iterdir()) == []
    assert ".commonplace/bin/rules_sync.py" in result.stdout
    assert "synthetic-secret" not in result.stdout + result.stderr
    subprocess.run([str(INSTALLER), "--rules"], env=environment, capture_output=True, check=True)
    target = tmp_path / ".commonplace/bin/rules_sync.py"
    assert target.read_bytes() == SCRIPT.read_bytes()
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


@pytest.mark.parametrize("boundary", ["out", "block", "state"])
def test_process_termination_after_each_atomic_rename_recovers(script, client, env, boundary):
    block = add_block(script, client)
    run_sync(script, client)
    env.owner.create("*", "Recover after process termination", "limit")
    # Terminate after the kernel rename, without unwinding Python context
    # managers. The kernel must release the lock, and state must remain last.
    harness = """
import importlib.util, os, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location('rules_sync', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
original = module.os.replace
def terminate(source, destination):
    original(source, destination)
    if Path(destination) == Path(sys.argv[2]):
        os._exit(73)
module.os.replace = terminate
module.main(sys.argv[3:])
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            harness,
            str(SCRIPT),
            str(client.paths[boundary]),
            "sync",
            "--out",
            str(client.out),
            "--block",
            str(block),
        ],
        env={
            "PATH": os.environ["PATH"],
            "COMMONPLACE_URL": client.url,
            "COMMONPLACE_TOKEN": client.token,
        },
        capture_output=True,
        timeout=3,
        check=False,
    )
    assert result.returncode == 73
    run_sync(script, client)
    assert b'"Recover after process termination"' in client.out.read_bytes()
    assert script.artifacts_match(client.paths, state(client), client.owner)


def test_real_hub_off_over_reply_cap_remains_incomplete(script, client, env):
    install(script, client)
    for index in range(8):
        env.owner.create("*", f"{index}: " + "x" * 3500, "instruction")
    run_sync(script, client)
    data = client.out.read_bytes()
    assert len(data) <= script.CLAUDE_BYTES
    assert b"This list is incomplete" in data and b"Not shown:" in data
    assert b"Pass rules_seq=" not in data and state(client)["rules_seq"] is None
    with env.worker.store.read() as conn:
        assert (
            conn.execute(
                "SELECT state FROM agent_served WHERE agent=?", (env.agent.actor,)
            ).fetchone()[0]
            == "too_large"
        )


def test_watchdog_bounds_lock_wait_without_a_request(script, client):
    import fcntl

    install(script, client)
    before = snapshots(client)
    with client.paths["lock"].open("rb") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        started = time.monotonic()
        result = cli(client)
        elapsed = time.monotonic() - started
    assert result.returncode == 0 and elapsed < 0.5
    assert "could not be refreshed" in context(result)
    assert snapshots(client) == before and client.calls == []


def test_watchdog_bounds_dns_work_not_covered_by_socket_timeout(script, client):
    install(script, client)
    before = snapshots(client)
    harness = """
import runpy, socket, sys, threading
def stalled_dns(*args, **kwargs):
    threading.Event().wait(2)
    raise OSError('synthetic DNS stall')
socket.getaddrinfo = stalled_dns
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name='__main__')
"""
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, "-c", harness, str(SCRIPT), "sync", "--out", str(client.out)],
        env={
            "PATH": os.environ["PATH"],
            "COMMONPLACE_URL": client.url,
            "COMMONPLACE_TOKEN": client.token,
        },
        capture_output=True,
        text=True,
        timeout=2,
        check=False,
    )
    assert result.returncode == 0 and time.monotonic() - started < 0.5
    assert "could not be refreshed" in context(result)
    assert snapshots(client) == before and client.calls == []


def test_waiter_reuses_state_committed_after_its_timestamp_was_prepared(
    script, client, monkeypatch
):
    import fcntl

    install(script, client)
    staged, release, waiting = threading.Event(), threading.Event(), threading.Event()
    write, flock = script.atomic_write, fcntl.flock

    def pause_state(path, *args, **kwargs):
        if path == client.paths["state"]:
            staged.set()
            assert release.wait(2)
        return write(path, *args, **kwargs)

    def observe(fd, operation):
        if operation == fcntl.LOCK_EX:
            waiting.set()
        return flock(fd, operation)

    monkeypatch.setattr(script, "atomic_write", pause_state)
    monkeypatch.setattr(fcntl, "flock", observe)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run_sync, script, client)
        assert staged.wait(2)
        second = pool.submit(run_sync, script, client)
        assert waiting.wait(2)
        release.set()
        assert first.result(timeout=2) == second.result(timeout=2)
    assert len(client.calls) == 1


def test_process_termination_between_install_stubs_preserves_old_binding(script, client):
    block = add_block(script, client)
    run_sync(script, client)
    before = client.paths["owner"].read_bytes()
    harness = """
import importlib.util, os, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location('rules_sync', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
original = module.os.replace
def terminate(source, destination):
    original(source, destination)
    if Path(destination) == Path(sys.argv[2]):
        os._exit(74)
module.os.replace = terminate
module.main(sys.argv[3:])
"""
    new_token = "another-synthetic-connection-token"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            harness,
            str(SCRIPT),
            str(block),
            "sync",
            "--install",
            "--out",
            str(client.out),
            "--block",
            str(block),
        ],
        env={
            "PATH": os.environ["PATH"],
            "COMMONPLACE_URL": client.url,
            "COMMONPLACE_TOKEN": new_token,
        },
        capture_output=True,
        timeout=3,
        check=False,
    )
    assert result.returncode == 74
    assert client.paths["owner"].read_bytes() == before
    assert cli(client, "--install", "--block", str(block), token=new_token).returncode == 0
    assert client.paths["owner"].read_bytes() != before
    assert (
        script.STUB.encode() in client.out.read_bytes()
        and script.STUB.encode() in block.read_bytes()
    )
    assert not client.paths["state"].exists()
