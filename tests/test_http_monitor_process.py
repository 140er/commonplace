"""Short fixture-only process smoke; no production process is signalled."""

import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import threading

import pytest


class Fixture:
    def __init__(self, tmp_path, *, held=False):
        self.root = tmp_path
        self.condition = threading.Condition()
        self.ready = 0
        self.accepted = False
        self.log = []
        self.children = {}
        self.reader_finished = False
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.palace = str(tmp_path / "palace")
        self.env = {
            **os.environ,
            "HOME": str(tmp_path),
            "USERPROFILE": str(tmp_path),
            "MEMPALACE_CONFIG_DIR": str(tmp_path / "config"),
            "MEMPALACE_WORKER_MEMORY_OBSERVE": "0",
            "MEMPALACE_HUB_FORWARD": "0",
        }
        for name in (
            "MEMPALACE_HTTP_CHILD",
            "MEMPALACE_HTTP_LIVENESS",
            "MEMPALACE_HTTP_FACTS",
            "MEMPALACE_HTTP_GENERATION",
            "MEMPALACE_MCP_HTTP_TOKEN",
            "MEMPALACE_MCP_TRUSTED_IDENTITY",
        ):
            self.env.pop(name, None)
        argv = [
            "--transport",
            "http",
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
            "--palace",
            self.palace,
            "--read-only",
        ]
        if held:
            child = tmp_path / "fixture_child.py"
            child.write_text(
                """import os, sys
from mempalace import mcp_server as server
original = server._http_dispatch
def dispatch(request):
    if request.get("method") == "tools/call":
        os.write(2, b"FIXTURE_ACCEPTED\\n")
        assert sys.stdin.buffer.read(1) == b"x"
        return {"jsonrpc": "2.0", "id": request["id"], "result": "verbatim fixture"}
    return original(request)
server._http_dispatch = dispatch
sys.argv = ["fixture", *"""
                + repr(argv)
                + """]
server.main()
"""
            )
            monitor = tmp_path / "fixture_monitor.py"
            monitor.write_text(
                """import os, sys
from mempalace.mcp_http_supervisor import Child, HTTPMonitor
from mempalace.server_registry import write_monitor_marker
from functools import partial
palace = """
                + repr(self.palace)
                + """
HTTPMonitor(palace_path=palace,
    launcher=lambda: Child([sys.executable, """
                + repr(str(child))
                + """], dict(os.environ)),
    publish_marker=partial(write_monitor_marker, palace, host="127.0.0.1", port="""
                + str(self.port)
                + """)).run()
"""
            )
            command = [sys.executable, str(monitor)]
        else:
            command = [sys.executable, "-m", "mempalace.mcp_server", *argv]
        self.monitor = subprocess.Popen(
            command,
            env=self.env,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        try:
            self.wait(lambda: self.ready >= 1)
            self.child_info()
        except BaseException:
            self.close()
            raise

    def _read(self):
        for raw in self.monitor.stderr:
            line = raw.decode(errors="replace")
            with self.condition:
                self.log.append(line)
                if "HTTP server listening on" in line:
                    self.ready += 1
                if "FIXTURE_ACCEPTED" in line:
                    self.accepted = True
                self.condition.notify_all()
        with self.condition:
            self.reader_finished = True
            self.condition.notify_all()

    def wait(self, predicate):
        with self.condition:
            signalled = self.condition.wait_for(lambda: predicate() or self.reader_finished, 30)
            assert signalled and predicate(), "invalid process fixture: " + "".join(self.log)

    def child_info(self):
        from mempalace.paths import palace_key

        path = self.root / ".mempalace" / "server" / palace_key(self.palace) / "serverinfo.json"
        info = json.loads(path.read_text())
        self.children[info["pid"]] = info.get("start_identity")
        return info

    def post(self, request=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(
                "POST",
                "/mcp",
                json.dumps(request or {"jsonrpc": "2.0", "id": 1, "method": "ping"}),
                {"Content-Type": "application/json", "Host": f"127.0.0.1:{self.port}"},
            )
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    def close(self):
        if self.monitor.poll() is None:
            self.monitor.terminate()
            try:
                self.monitor.wait(10)
            except subprocess.TimeoutExpired:
                self.monitor.kill()
                self.monitor.wait(5)
        if self.monitor.stdin is not None:
            self.monitor.stdin.close()
        # Every pid came from this fixture's isolated registry. Only emergency
        # cleanup uses SIGKILL; the assertions exercise graceful EOF first.
        for pid, identity in self.children.items():
            if pid == self.monitor.pid:
                continue
            from mempalace.worker_lifecycle import identity_alive

            if identity_alive(pid, identity):
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        self.reader.join(5)
        self.monitor.stderr.close()


@pytest.mark.skipif(os.name == "nt", reason="SIGKILL-specific process smoke")
def test_real_child_kill_restarts_same_endpoint(tmp_path):
    fixture = Fixture(tmp_path)
    try:
        old = fixture.child_info()
        assert fixture.post()[0] == 200
        os.kill(old["pid"], signal.SIGKILL)
        fixture.wait(lambda: fixture.ready >= 2)
        new = fixture.child_info()
        assert new["pid"] != old["pid"]
        assert new["generation"] != old["generation"]
        assert fixture.post() == (200, {"jsonrpc": "2.0", "id": 1, "result": {}})
        from mempalace.worker_lifecycle import identity_alive

        assert not identity_alive(old["pid"], old["start_identity"])
    finally:
        fixture.close()


@pytest.mark.skipif(os.name == "nt", reason="SIGKILL-specific process smoke")
def test_real_monitor_death_preserves_accepted_response(tmp_path):
    fixture = Fixture(tmp_path, held=True)
    result = []
    response_done = threading.Event()

    def request():
        try:
            result.append(
                fixture.post(
                    {
                        "jsonrpc": "2.0",
                        "id": 9,
                        "method": "tools/call",
                        "params": {"name": "fixture"},
                    }
                )
            )
        finally:
            response_done.set()

    thread = threading.Thread(target=request)
    try:
        old = fixture.child_info()
        thread.start()
        fixture.wait(lambda: fixture.accepted)
        fixture.monitor.kill()
        fixture.monitor.wait(5)
        from mempalace.worker_lifecycle import identity_alive

        assert identity_alive(old["pid"], old["start_identity"])
        assert not response_done.is_set()
        fixture.monitor.stdin.write(b"x")
        fixture.monitor.stdin.flush()
        assert response_done.wait(5), "invalid fixture: response missing"
        assert result == [(200, {"jsonrpc": "2.0", "id": 9, "result": "verbatim fixture"})]
        # EOF on stderr is an event proving all inheriting children closed it.
        fixture.reader.join(5)
        assert not fixture.reader.is_alive()
        assert not identity_alive(old["pid"], old["start_identity"])
    finally:
        if thread.is_alive():
            fixture.monitor.stdin.write(b"x")
            fixture.monitor.stdin.flush()
            thread.join(5)
        fixture.close()
