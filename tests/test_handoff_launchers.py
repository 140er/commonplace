"""Successor launchers use private files, literal arguments and signed HTTP."""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import os
import stat
import subprocess
import sys
import threading
from dataclasses import replace
from http.client import BadStatusLine
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.error import URLError

import pytest

from commonplace.handoff import launcher_policy, launchers
from commonplace.handoff.launchers import LaunchError, launch
from commonplace.handoff.models import Handoff, LauncherConfig, SuccessorRef


@pytest.fixture(autouse=True)
def operator_launchers(monkeypatch):
    monkeypatch.setenv("COMMONPLACE_HANDOFF_LAUNCHERS", "command,webhook")
    # These transport tests use isolated receivers; security tests cover policy.
    monkeypatch.setattr(launcher_policy, "require_public_address", lambda _: None)
    monkeypatch.setattr(launchers, "require_public_address", lambda _: None)


@pytest.fixture
def handoff():
    return Handoff(
        id="hof_launch-test",
        workspace_id="workspace-test",
        idempotency_key="thread:source-thread",
        source_thread_id="source-thread",
        state="stored",
        stored_thread_id="thr_stored-test",
        task_ref="#142",
        branch="feat/context-handoff",
        pr_url="https://example.test/review/142",
    )


@pytest.fixture
def processes(monkeypatch):
    original = subprocess.Popen
    started = []

    def start(*args, **kwargs):
        process = original(*args, **kwargs)
        started.append(process)
        return process

    monkeypatch.setattr(launchers.subprocess, "Popen", start)
    yield started
    for process in started:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


@pytest.fixture
def webhook_server():
    state = SimpleNamespace(
        status=200,
        reason=None,
        body=b'{"agent_ref":"agent:successor"}',
        headers={},
        received=[],
        slow=False,
        release=threading.Event(),
    )

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            state.received.append((self.command, self.path, dict(self.headers), body))
            if state.slow:
                state.release.wait(timeout=3)
            self.send_response(state.status, state.reason)
            for name, value in state.headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(state.body)))
            self.end_headers()
            try:
                self.wfile.write(state.body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            state.received.append((self.command, self.path, dict(self.headers), b""))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state.url = f"http://127.0.0.1:{server.server_port}/launch"
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield state
    finally:
        state.release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()


def test_client_does_nothing(handoff, tmp_path, monkeypatch):
    def unexpected(*_args, **_kwargs):
        pytest.fail("The client launcher performed work")

    monkeypatch.setattr(launchers, "build_opener", unexpected)
    monkeypatch.setattr(launchers.subprocess, "Popen", unexpected)
    directory = tmp_path / "unused"
    assert launch(LauncherConfig(), handoff, "prompt", prompt_dir=str(directory)) is None
    assert not directory.exists()


def test_command_arguments_environment_logs_and_private_prompt(
    handoff, tmp_path, monkeypatch, processes
):
    directory = tmp_path / "prompts"
    result_file = tmp_path / "arguments.json"
    marker = tmp_path / "shell-ran"
    survivor = tmp_path / "keep-me"
    survivor.write_text("keep")
    literal = f"; rm -rf {survivor}; $(touch {marker})"
    prompt = "Goal: café ☃\r\n  exact text\n{thread_id} and {prompt_file}\n"
    program = (
        "import json,os,sys; from pathlib import Path; "
        "Path(sys.argv[1]).write_text(json.dumps(dict("
        "argv=sys.argv[2:], handoff=os.environ['COMMONPLACE_HANDOFF_ID'], "
        "thread=os.environ['COMMONPLACE_THREAD_ID'], inherited=os.environ['HANDOFF_TEST_VALUE'], "
        "cwd=os.getcwd(), stdin=sys.stdin.read(), pid=os.getpid(), "
        "session=os.getsid(0) if hasattr(os,'getsid') else None))); "
        "print('stdout marker'); print('stderr marker',file=sys.stderr)"
    )
    monkeypatch.setenv("HANDOFF_TEST_VALUE", "inherited")
    monkeypatch.setenv("COMMONPLACE_HANDOFF_ID", "old")
    monkeypatch.setenv("COMMONPLACE_THREAD_ID", "old")
    config = LauncherConfig(
        kind="command",
        argv=(
            sys.executable,
            "-c",
            program,
            str(result_file),
            "--prompt={prompt}",
            "{prompt_file}",
            "h={handoff_id}/t={thread_id}",
            "wd={cwd}",
            "{unknown}",
            '{"ok":true}',
            literal,
            "",
        ),
    )
    result = launch(config, handoff, prompt, prompt_dir=str(directory))
    saved = json.loads(result_file.read_text())
    prompt_file = directory / f"{handoff.id}.md"
    log_file = directory / f"{handoff.id}.log"
    assert saved["argv"] == [
        f"--prompt={prompt}",
        str(prompt_file),
        f"h={handoff.id}/t={handoff.stored_thread_id}",
        f"wd={directory}",
        "{unknown}",
        '{"ok":true}',
        literal,
        "",
    ]
    assert saved["handoff"] == handoff.id
    assert saved["thread"] == handoff.stored_thread_id
    assert saved["inherited"] == "inherited"
    assert saved["cwd"] == str(directory)
    assert saved["stdin"] == ""
    assert result == SuccessorRef(agent_ref=f"pid:{saved['pid']}")
    assert processes[0].returncode == 0
    if os.name == "posix":
        assert saved["session"] == saved["pid"]
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
        assert stat.S_IMODE(prompt_file.stat().st_mode) == 0o600
        assert stat.S_IMODE(log_file.stat().st_mode) == 0o600
    assert prompt_file.read_bytes() == prompt.encode("utf-8")
    assert "stdout marker" in log_file.read_text()
    assert "stderr marker" in log_file.read_text()
    assert survivor.read_text() == "keep"
    assert not marker.exists()
    assert os.environ["COMMONPLACE_HANDOFF_ID"] == "old"


@pytest.mark.parametrize("metadata", ["existing", "missing", "file"])
def test_command_cwd_and_relative_prompt_directory(
    metadata, handoff, tmp_path, monkeypatch, processes
):
    monkeypatch.chdir(tmp_path)
    wanted = tmp_path / "work"
    if metadata == "existing":
        wanted.mkdir()
    elif metadata == "file":
        wanted.write_text("a file")
    handoff = dataclasses.replace(handoff, cwd=str(wanted))
    result_file = tmp_path / "cwd.txt"
    config = LauncherConfig(
        kind="command",
        argv=(
            sys.executable,
            "-c",
            "import os,sys; from pathlib import Path; "
            "Path(sys.argv[1]).write_text(os.getcwd()+'\\n'+sys.argv[2])",
            str(result_file),
            "{cwd}",
        ),
    )
    launch(config, handoff, "prompt", prompt_dir="prompts")
    expected = wanted if metadata == "existing" else tmp_path / "prompts"
    assert result_file.read_text().splitlines() == [str(expected), str(expected)]
    assert (tmp_path / "prompts" / f"{handoff.id}.md").read_text() == "prompt"


def test_command_replaces_prompt_atomically(handoff, tmp_path, monkeypatch, processes):
    prompt_file = tmp_path / f"{handoff.id}.md"
    log_file = tmp_path / f"{handoff.id}.log"
    prompt_file.write_text("old")
    prompt_file.chmod(0o644)
    log_file.write_text("previous\n")
    log_file.chmod(0o644)
    tmp_path.chmod(0o755)
    original = os.replace
    replaced = []

    def atomic(source, destination):
        source = Path(source)
        assert prompt_file.read_text() == "old"
        assert source.read_text() == "new\n"
        assert source.parent == tmp_path
        if os.name == "posix":
            assert stat.S_IMODE(source.stat().st_mode) == 0o600
        original(source, destination)
        replaced.append(destination)

    monkeypatch.setattr(launchers.os, "replace", atomic)
    config = LauncherConfig(kind="command", argv=(sys.executable, "-c", "print('new log')"))
    launch(config, handoff, "new\n", prompt_dir=str(tmp_path))
    assert replaced == [prompt_file]
    assert prompt_file.read_text() == "new\n"
    assert set(tmp_path.iterdir()) == {prompt_file, log_file}
    assert log_file.read_text() == "previous\nnew log\n"
    if os.name == "posix":
        assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
        assert stat.S_IMODE(prompt_file.stat().st_mode) == 0o600
        assert stat.S_IMODE(log_file.stat().st_mode) == 0o600


@pytest.mark.parametrize("failure", ["fsync", "replace"])
def test_command_write_failure_preserves_old_prompt(
    failure, handoff, tmp_path, monkeypatch, processes
):
    prompt_file = tmp_path / f"{handoff.id}.md"
    prompt_file.write_text("old")

    def fail(*_args, **_kwargs):
        raise OSError("disk error")

    monkeypatch.setattr(launchers.os, failure, fail)
    config = LauncherConfig(kind="command", argv=(sys.executable, "-c", "pass"))
    with pytest.raises(LaunchError, match="Could not start"):
        launch(config, handoff, "new", prompt_dir=str(tmp_path))
    assert prompt_file.read_text() == "old"
    assert list(tmp_path.iterdir()) == [prompt_file]
    assert not processes


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="Requires no-follow file opens")
def test_command_rejects_log_symlink(handoff, tmp_path, processes):
    outside = tmp_path / "outside.txt"
    outside.write_text("private")
    (tmp_path / f"{handoff.id}.log").symlink_to(outside)
    config = LauncherConfig(kind="command", argv=(sys.executable, "-c", "pass"))
    with pytest.raises(LaunchError):
        launch(config, handoff, "prompt", prompt_dir=str(tmp_path))
    assert outside.read_text() == "private"
    assert not processes


def test_command_detects_fast_failure(handoff, tmp_path, processes):
    config = LauncherConfig(kind="command", argv=(sys.executable, "-c", "raise SystemExit(23)"))
    with pytest.raises(LaunchError, match="status 23"):
        launch(config, handoff, "prompt", prompt_dir=str(tmp_path))
    assert processes[0].returncode == 23


def test_command_returns_while_agent_is_running(handoff, tmp_path, processes):
    gate = tmp_path / "finish"
    program = (
        "import sys,time\nfrom pathlib import Path\n"
        "while not Path(sys.argv[1]).exists():\n time.sleep(0.01)\nraise SystemExit(7)"
    )
    config = LauncherConfig(kind="command", argv=(sys.executable, "-c", program, str(gate)))
    result = launch(config, handoff, "prompt", prompt_dir=str(tmp_path))
    assert result.agent_ref == f"pid:{processes[0].pid}"
    assert processes[0].poll() is None
    gate.write_text("finish")
    assert processes[0].wait(timeout=3) == 7


def test_command_missing_executable(handoff, tmp_path):
    config = LauncherConfig(kind="command", argv=(str(tmp_path / "does-not-exist"),))
    with pytest.raises(LaunchError, match="Could not start"):
        launch(config, handoff, "prompt", prompt_dir=str(tmp_path))


def test_command_wait_error_is_wrapped(handoff, tmp_path, monkeypatch):
    def fail(*_args, **_kwargs):
        raise OSError("wait failed")

    monkeypatch.setattr(
        launchers.subprocess, "Popen", lambda *_args, **_kwargs: SimpleNamespace(wait=fail)
    )
    config = LauncherConfig(kind="command", argv=(sys.executable, "-c", "pass"))
    with pytest.raises(LaunchError, match="Could not check"):
        launch(config, handoff, "prompt", prompt_dir=str(tmp_path))


@pytest.mark.parametrize("argv", [(), "python", (None,), ("",), ("nul\0name",)])
def test_command_invalid_arguments(argv, handoff, tmp_path):
    with pytest.raises(LaunchError):
        launch(
            LauncherConfig(kind="command", argv=argv), handoff, "prompt", prompt_dir=str(tmp_path)
        )


@pytest.mark.parametrize("identifier", ["", ".", "..", "../escape", "a\\b", "a\0b"])
def test_command_rejects_unsafe_filename(identifier, handoff, tmp_path):
    config = LauncherConfig(kind="command", argv=(sys.executable, "-c", "pass"))
    with pytest.raises(LaunchError, match="Invalid handoff id"):
        launch(config, replace(handoff, id=identifier), "prompt", prompt_dir=str(tmp_path))
    assert not list(tmp_path.iterdir())


def test_command_requires_prompt_directory(handoff):
    config = LauncherConfig(kind="command", argv=(sys.executable, "-c", "pass"))
    with pytest.raises(LaunchError, match="directory is required"):
        launch(config, handoff, "prompt", prompt_dir="")


def test_command_prompt_directory_is_file(handoff, tmp_path):
    directory = tmp_path / "file"
    directory.write_text("keep")
    config = LauncherConfig(kind="command", argv=(sys.executable, "-c", "pass"))
    with pytest.raises(LaunchError):
        launch(config, handoff, "prompt", prompt_dir=str(directory))
    assert directory.read_text() == "keep"


@pytest.mark.parametrize("status", [200, 201, 299])
def test_webhook_signature_payload_and_success(
    status, handoff, tmp_path, webhook_server, monkeypatch
):
    secret = "private-launch-secret-☃"
    prompt = "Goal: exact text\r\n café\n{prompt}\n"
    webhook_server.status = status
    webhook_server.body = json.dumps(
        {
            "agent_ref": "t3:successor",
            "url": "https://example.test/agent",
            "thread_id": "new-thread",
        }
    ).encode()
    # Proxy environment must not send the thread to another service.
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("no_proxy", "")
    monkeypatch.setenv("NO_PROXY", "")
    config = LauncherConfig(kind="webhook", url=webhook_server.url, secret=secret)
    result = launch(config, handoff, prompt, prompt_dir=str(tmp_path / "unused"))
    assert result == SuccessorRef(
        agent_ref="t3:successor", url="https://example.test/agent", thread_id="new-thread"
    )
    method, path, headers, body = webhook_server.received[0]
    assert method == "POST"
    assert path == "/launch"
    assert headers["Content-Type"] == "application/json"
    assert headers["X-Commonplace-Event"] == "handoff.launch"
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert headers["X-Commonplace-Signature"] == f"sha256={digest}"
    assert json.loads(body) == {
        "handoff_id": handoff.id,
        "workspace": handoff.workspace_id,
        "stored_thread_id": handoff.stored_thread_id,
        "task_ref": handoff.task_ref,
        "branch": handoff.branch,
        "pr_url": handoff.pr_url,
        "prompt": prompt,
    }
    assert not (tmp_path / "unused").exists()


def test_webhook_minimal_success(handoff, tmp_path, webhook_server):
    result = launch(
        LauncherConfig(kind="webhook", url=webhook_server.url),
        handoff,
        "prompt",
        prompt_dir=str(tmp_path),
    )
    assert result == SuccessorRef(agent_ref="agent:successor")


@pytest.mark.parametrize(
    "body",
    [
        b"not JSON",
        b"\xff",
        b"[]",
        b"null",
        b'"text"',
        b"{}",
        b'{"agent_ref":null}',
        b'{"agent_ref":123}',
        b'{"agent_ref":""}',
        b'{"agent_ref":"   "}',
        b'{"agent_ref":"agent","url":null}',
        b'{"agent_ref":"agent","thread_id":123}',
        b"[" * (sys.getrecursionlimit() + 10) + b"0" + b"]" * (sys.getrecursionlimit() + 10),
    ],
)
def test_webhook_rejects_invalid_reply(body, handoff, tmp_path, webhook_server):
    webhook_server.body = body
    with pytest.raises(LaunchError):
        launch(
            LauncherConfig(kind="webhook", url=webhook_server.url),
            handoff,
            "prompt",
            prompt_dir=str(tmp_path),
        )


@pytest.mark.parametrize("status", [204, 400, 401, 404, 500])
def test_webhook_errors_never_include_secret(status, handoff, tmp_path, webhook_server):
    secret = "private-launch-secret"
    webhook_server.status = status
    webhook_server.reason = secret
    webhook_server.body = secret.encode()
    with pytest.raises(LaunchError) as error:
        launch(
            LauncherConfig(kind="webhook", url=webhook_server.url, secret=secret),
            handoff,
            "prompt",
            prompt_dir=str(tmp_path),
        )
    assert secret not in str(error.value)
    assert error.value.__suppress_context__


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_webhook_never_follows_redirects(status, handoff, tmp_path, webhook_server):
    webhook_server.status = status
    webhook_server.headers["Location"] = webhook_server.url + "/redirected"
    with pytest.raises(LaunchError, match=f"status {status}"):
        launch(
            LauncherConfig(kind="webhook", url=webhook_server.url, secret="secret"),
            handoff,
            "prompt",
            prompt_dir=str(tmp_path),
        )
    assert len(webhook_server.received) == 1


def test_webhook_times_out(handoff, tmp_path, webhook_server):
    webhook_server.slow = True
    with pytest.raises(LaunchError, match="request failed"):
        launch(
            LauncherConfig(kind="webhook", url=webhook_server.url, secret="secret"),
            handoff,
            "prompt",
            prompt_dir=str(tmp_path),
            timeout=0.05,
        )
    assert len(webhook_server.received) == 1


@pytest.mark.parametrize(
    "failure", [URLError("secret"), OSError("secret"), BadStatusLine("secret")]
)
def test_webhook_transport_failure_is_sanitized(failure, handoff, tmp_path, monkeypatch):
    def fail(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(launchers, "build_opener", lambda *_args: SimpleNamespace(open=fail))
    with pytest.raises(LaunchError, match="request failed") as error:
        launch(
            LauncherConfig(kind="webhook", url="http://127.0.0.1/launch", secret="secret"),
            handoff,
            "prompt",
            prompt_dir=str(tmp_path),
        )
    assert "secret" not in str(error.value)
    assert error.value.__suppress_context__


@pytest.mark.parametrize(
    "url", ["", "file:///private", "ftp://127.0.0.1/file", "http://", "http://["]
)
def test_webhook_rejects_invalid_url(url, handoff, tmp_path):
    with pytest.raises(LaunchError):
        launch(LauncherConfig(kind="webhook", url=url), handoff, "prompt", prompt_dir=str(tmp_path))


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), "10"])
def test_webhook_rejects_invalid_timeout(timeout, handoff, tmp_path):
    with pytest.raises(LaunchError, match="timeout"):
        launch(
            LauncherConfig(kind="webhook", url="http://127.0.0.1/launch"),
            handoff,
            "prompt",
            prompt_dir=str(tmp_path),
            timeout=timeout,
        )


def test_unknown_launcher_kind(handoff, tmp_path):
    with pytest.raises(LaunchError, match="Unknown"):
        launch(LauncherConfig(kind="unknown"), handoff, "prompt", prompt_dir=str(tmp_path))
