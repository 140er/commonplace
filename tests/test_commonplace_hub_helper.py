"""The shared hub test client waits as long as the caller allows for a slow tool call."""

import http.server
import threading
import time

import pytest

from tests._commonplace_helpers import Hub


@pytest.fixture
def slow_server():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            time.sleep(1.0)
            body = b'{"jsonrpc": "2.0", "id": 1, "result": {}}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        hub = Hub.__new__(Hub)
        hub.port, hub._ids = server.server_address[1], 0
        yield hub
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_a_caller_can_allow_a_slow_call_more_time_than_the_default(slow_server):
    with pytest.raises(TimeoutError):
        slow_server.raw("token", {"method": "ping"}, timeout=0.2)
    status, text = slow_server.raw("token", {"method": "ping"}, timeout=10)
    assert status == 200 and '"result"' in text
    assert slow_server.rpc("token", "ping", timeout=10) == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {},
    }
