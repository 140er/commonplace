"""The stdio bridge against a real gateway and in-process memory worker."""

import io
import json
import os
import socket

import pytest

from commonplace import bridge
from tests._commonplace_helpers import hub_fixture  # noqa: F401


def _request(req_id=1, method="ping", params=None):
    request = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        request["params"] = params
    return request


def _drive(url, lines, token_env="COMMONPLACE_TOKEN"):
    stdin = io.StringIO("".join(json.dumps(line) + "\n" for line in lines))
    stdout, stderr = io.StringIO(), io.StringIO()
    status = bridge.run_bridge(url, token_env, stdin=stdin, stdout=stdout, stderr=stderr)
    return status, [json.loads(line) for line in stdout.getvalue().splitlines()], stderr.getvalue()


def _url(hub, path="/mcp"):
    return f"http://127.0.0.1:{hub.port}{path}"


def _tool_result(response):
    return json.loads(response["result"]["content"][0]["text"])


def test_real_memory_roundtrip_and_notification(hub, monkeypatch):
    token = hub.owner_token("bridge")
    monkeypatch.setenv("BRIDGE_TEST_TOKEN", token)
    text = "  Exact words: café.\nSecond line stays.  "
    status, replies, error = _drive(
        _url(hub),
        [
            _request("init", "initialize", {}),
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            _request("list", "tools/list"),
            _request(
                "save",
                "tools/call",
                {
                    "name": "mempalace_add_drawer",
                    "arguments": {"wing": "shared", "room": "general", "content": text},
                },
            ),
        ],
        "BRIDGE_TEST_TOKEN",
    )
    assert status == 0 and not error
    assert [reply["id"] for reply in replies] == ["init", "list", "save"]
    assert replies[0]["result"]["serverInfo"]["name"] == "mempalace"
    assert "mempalace_get_drawer" in {t["name"] for t in replies[1]["result"]["tools"]}
    saved = _tool_result(replies[2])
    assert saved["success"]
    status, replies, error = _drive(
        _url(hub),
        [
            _request(
                4,
                "tools/call",
                {"name": "mempalace_get_drawer", "arguments": {"drawer_id": saved["drawer_id"]}},
            )
        ],
        "BRIDGE_TEST_TOKEN",
    )
    assert status == 0 and not error
    assert _tool_result(replies[0])["content"] == text
    assert token not in json.dumps(replies)


def test_defaults_use_stdio_and_flush_each_reply(hub, monkeypatch):
    class Output(io.StringIO):
        flushes = 0

        def flush(self):
            self.flushes += 1

    monkeypatch.setenv("COMMONPLACE_TOKEN", hub.owner_token("stdio"))
    read_fd, write_fd = os.pipe()
    with os.fdopen(write_fd, "w") as writer:
        writer.write(json.dumps(_request()) + "\n")
    output, error = Output(), io.StringIO()
    with os.fdopen(read_fd) as reader:
        monkeypatch.setattr(bridge.sys, "stdin", reader)
        monkeypatch.setattr(bridge.sys, "stdout", output)
        monkeypatch.setattr(bridge.sys, "stderr", error)
        assert bridge.run_bridge(_url(hub)) == 0
    assert json.loads(output.getvalue())["result"] == {}
    assert output.flushes == 1 and not error.getvalue()


def test_malformed_input_recovers_and_final_line_needs_no_newline(hub, monkeypatch):
    monkeypatch.setenv("COMMONPLACE_TOKEN", hub.owner_token("parse"))
    malformed = ["not JSON", "[]", "null", '{"jsonrpc":"2.0","method":NaN}', '{"id":1}']
    stdin = io.StringIO("\n" + "\n".join(malformed) + "\n" + json.dumps(_request(9)))
    output, error = io.StringIO(), io.StringIO()
    assert bridge.run_bridge(_url(hub), stdin=stdin, stdout=output, stderr=error) == 0
    replies = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [r["error"]["code"] for r in replies[:-1]] == [-32700, -32600, -32600, -32700, -32600]
    assert replies[-1] == {"jsonrpc": "2.0", "id": 9, "result": {}}
    assert not error.getvalue()


def test_oversized_line_is_drained_before_the_next_request(hub, monkeypatch):
    monkeypatch.setenv("COMMONPLACE_TOKEN", hub.owner_token("large"))
    monkeypatch.setattr(bridge, "MAX_MESSAGE_BYTES", 100)
    stdin = io.StringIO("x" * 150 + "\n" + json.dumps(_request()) + "\n")
    output, error = io.StringIO(), io.StringIO()
    assert bridge.run_bridge(_url(hub), stdin=stdin, stdout=output, stderr=error) == 0
    replies = [json.loads(line) for line in output.getvalue().splitlines()]
    assert replies[0]["error"]["code"] == -32600
    assert replies[1]["result"] == {}


def test_missing_token_and_empty_eof(monkeypatch):
    monkeypatch.delenv("COMMONPLACE_TOKEN", raising=False)
    status, replies, error = _drive("http://127.0.0.1:1/mcp", [])
    assert status == 1 and not replies and "token environment variable" in error
    monkeypatch.setenv("COMMONPLACE_TOKEN", "secret")
    assert _drive("http://127.0.0.1:1/mcp", []) == (0, [], "")


@pytest.mark.parametrize("line", ["[" * 100000, "9" * 5000], ids=["deep-nesting", "integer-limit"])
def test_json_decoder_limits_are_parse_errors(monkeypatch, line):
    monkeypatch.setenv("COMMONPLACE_TOKEN", "secret")
    output, error = io.StringIO(), io.StringIO()
    assert (
        bridge.run_bridge(
            "http://127.0.0.1:1/mcp", stdin=io.StringIO(line), stdout=output, stderr=error
        )
        == 0
    )
    assert json.loads(output.getvalue())["error"]["code"] == -32700
    assert not error.getvalue()


@pytest.mark.parametrize("token", ["secret\ninjected", "secret\rvalue", "secret value", "café"])
def test_invalid_token_never_reaches_headers_or_diagnostics(monkeypatch, token):
    monkeypatch.setenv("COMMONPLACE_TOKEN", token)
    status, replies, error = _drive("http://127.0.0.1:1/mcp", [_request()])
    assert status == 1 and not replies
    assert "secret" not in error and "café" not in error


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://host/mcp",
        "http:///mcp",
        "http://host:bad/mcp",
        "http://u:secret@host/mcp",
        "http://host/mcp#secret",
        "http://host/secret\n",
    ],
)
def test_invalid_url_is_sanitized(monkeypatch, url):
    monkeypatch.setenv("COMMONPLACE_TOKEN", "bearer-secret")
    status, replies, error = _drive(url, [_request()])
    assert status == 1 and not replies
    assert "Invalid MCP URL" in error and "secret" not in error


def test_auth_failure_returns_rpc_error_without_leaking_token(hub, monkeypatch):
    monkeypatch.setenv("COMMONPLACE_TOKEN", "secret-invalid-token")
    status, replies, error = _drive(_url(hub), [_request(7), _request(8)])
    assert status == 1 and len(replies) == 1
    assert replies[0]["id"] == 7 and replies[0]["error"]["code"] == -32000
    assert "401" in error and "secret-invalid-token" not in error + json.dumps(replies)


@pytest.mark.parametrize("http_status", [302, 403, 500])
def test_http_errors_and_redirects_never_reveal_response_bodies(hub, monkeypatch, http_status):
    # A valid token gets past the gateway's auth to the failing route.
    token = hub.owner_token("bridge")
    monkeypatch.setenv("COMMONPLACE_TOKEN", token)

    def fail(handler):
        handler._send_json(http_status, {"error": token}, {"Location": "http://127.0.0.1:1/mcp"})

    hub.gateway.add_route("POST", "/bridge-error", fail)
    status, replies, error = _drive(_url(hub, "/bridge-error"), [_request("failed")])
    assert status == 1 and str(http_status) in error
    assert replies[0]["id"] == "failed"
    assert token not in error + json.dumps(replies)


def test_failed_notification_stays_silent(hub, monkeypatch):
    monkeypatch.setenv("COMMONPLACE_TOKEN", "bad-token")
    status, replies, error = _drive(
        _url(hub), [{"jsonrpc": "2.0", "method": "notifications/initialized"}]
    )
    assert status == 1 and not replies and "401" in error


def test_network_failure_is_sanitized(monkeypatch):
    monkeypatch.setenv("COMMONPLACE_TOKEN", "network-secret")
    with socket.socket() as unused:
        unused.bind(("127.0.0.1", 0))
        port = unused.getsockname()[1]
    status, replies, error = _drive(f"http://127.0.0.1:{port}/mcp", [_request()])
    assert status == 1 and "Cannot reach memory hub" in error
    assert replies[0]["error"]["code"] == -32000
    assert "network-secret" not in error + json.dumps(replies)


@pytest.mark.parametrize(
    "response", ["secret-token", [], {"jsonrpc": "2.0", "id": 99, "result": {}}]
)
def test_invalid_http_response_is_sanitized(hub, monkeypatch, response):
    token = hub.owner_token("bridge")
    monkeypatch.setenv("COMMONPLACE_TOKEN", token)
    hub.gateway.add_route("POST", "/bad-response", lambda h: h._send_json(200, response))
    status, replies, error = _drive(_url(hub, "/bad-response"), [_request()])
    assert status == 1 and "Invalid response from memory hub" in error
    assert token not in error + json.dumps(replies)
    assert "secret-token" not in error
