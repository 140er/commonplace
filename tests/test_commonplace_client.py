"""Canonical client operations against a local, credential-isolated MCP peer."""

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

from commonplace import client as client_module
from commonplace.client import Client, ClientError


@pytest.fixture
def peer(monkeypatch):
    monkeypatch.delenv("COMMONPLACE_URL", raising=False)
    monkeypatch.delenv("COMMONPLACE_TOKEN", raising=False)
    monkeypatch.setenv("CLIENT_TEST_TOKEN", "test-scoped-bearer")
    state = SimpleNamespace(calls=[], replies=[])

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            request = json.loads(raw)
            state.calls.append(
                {"path": self.path, "headers": dict(self.headers), "request": request, "raw": raw}
            )
            spec = state.replies.pop(0) if state.replies else {"status": 500}
            if spec.get("disconnect"):
                self.close_connection = True
                return
            response = spec.get(
                "rpc",
                {
                    "jsonrpc": "2.0",
                    "id": request["id"],
                    "result": spec.get(
                        "result",
                        {
                            "content": [{"type": "text", "text": spec.get("text", "ok")}],
                            "isError": spec.get("isError", False),
                        },
                    ),
                },
            )
            body = spec.get("raw", json.dumps(response).encode())
            self.send_response(spec.get("status", 200))
            for key, value in spec.get("headers", {}).items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    state.url = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _client(peer, **kwargs):
    return Client(peer.url, token_env="CLIENT_TEST_TOKEN", **kwargs)


def _calls(peer):
    return [call["request"]["params"] for call in peer.calls]


def test_defaults_and_explicit_endpoint_precedence(peer, monkeypatch):
    monkeypatch.setenv("COMMONPLACE_TOKEN", "test-default-bearer")
    assert Client()._endpoint == ("https", "app.cmmn.dev", None, "/mcp")
    monkeypatch.setenv("COMMONPLACE_URL", peer.url + "/custom/rpc?version=1")
    configured = Client()
    peer.replies.append({"text": "configured"})
    assert configured.call_tool("session_start") == "configured"
    assert peer.calls[0]["path"] == "/custom/rpc?version=1"
    assert _client(peer)._endpoint[-1] == "/mcp"
    assert Client(peer.url + "/", token_env="CLIENT_TEST_TOKEN")._endpoint[-1] == "/mcp"


def test_combines_text_and_keeps_credential_scoped(peer, monkeypatch):
    monkeypatch.setenv("COMMONPLACE_TOKEN", "another-connection")
    client = _client(peer)
    monkeypatch.setenv("CLIENT_TEST_TOKEN", "later-connection")
    peer.replies.append(
        {
            "result": {
                "content": [
                    {"type": "text", "text": " exact first "},
                    {"type": "image", "data": "ignored", "mimeType": "image/png"},
                    {"type": "text", "text": "second\nline"},
                ]
            }
        }
    )
    assert client.call_tool("memory_get", {"id": "m-1"}) == " exact first \nsecond\nline"
    call = peer.calls[0]
    assert call["headers"]["Authorization"] == "Bearer test-scoped-bearer"
    assert call["headers"]["Content-Type"] == "application/json"
    assert call["request"]["method"] == "tools/call"
    assert b"bearer" not in call["raw"]
    assert "bearer" not in call["path"]


@pytest.mark.parametrize(
    "url",
    [
        "",
        "file:///private-token",
        "ftp://example.test/mcp",
        "http:///private-token",
        "http://user:private-token@example.test/mcp",
        "http://private-token@example.test/mcp",
        "http://example.test:private-token/mcp",
        "https://example.test:99999/mcp",
        "http://example.test/mcp#private-token",
        "http://example.test/mcp#",
        "http://example.test/private-token\n",
        "http://example.test/\\private-token",
        "http://[::1/mcp",
    ],
)
def test_invalid_urls_are_sanitized(peer, url):
    with pytest.raises(ClientError, match="Invalid Commonplace URL") as caught:
        Client(url, token_env="CLIENT_TEST_TOKEN")
    assert "private-token" not in str(caught.value)
    assert not peer.calls


@pytest.mark.parametrize("token", [None, "", "token secret", "token\nsecret", "café"])
def test_only_named_valid_credential_is_used(peer, monkeypatch, token):
    monkeypatch.setenv("COMMONPLACE_TOKEN", "unrelated-valid-bearer")
    if token is None:
        monkeypatch.delenv("CLIENT_TEST_TOKEN")
    else:
        monkeypatch.setenv("CLIENT_TEST_TOKEN", token)
    with pytest.raises(ClientError, match="Missing or invalid token environment variable"):
        _client(peer)
    assert not peer.calls


@pytest.mark.parametrize("name", ["", "bad=name", "bad\nname", "bad name", None])
def test_invalid_token_environment_names(peer, name):
    with pytest.raises(ClientError, match="Invalid token environment variable name"):
        Client(peer.url, token_env=name)


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), True, "3", None])
def test_invalid_timeouts(peer, timeout):
    with pytest.raises(ClientError, match="Invalid Commonplace timeout"):
        _client(peer, timeout=timeout)


def test_timeout_is_per_connection_and_network_errors_are_sanitized(peer, monkeypatch):
    calls = []
    original_timeout = socket.getdefaulttimeout()

    class BrokenConnection:
        def __init__(self, host, port, timeout):
            calls.append((host, port, timeout))

        def request(self, *_args, **_kwargs):
            raise TimeoutError("test-scoped-bearer and private-source-text")

        def close(self):
            calls.append("closed")

    monkeypatch.setattr(client_module.http.client, "HTTPSConnection", BrokenConnection)
    client = Client("https://example.test/mcp", "CLIENT_TEST_TOKEN", timeout=0.4)
    with pytest.raises(ClientError, match="Cannot reach Commonplace") as caught:
        client.call_tool("memory_save", {"text": "private-source-text"})
    assert calls == [("example.test", None, 0.4), "closed"]
    assert socket.getdefaulttimeout() is original_timeout
    assert "private-source-text" not in str(caught.value)
    assert "test-scoped-bearer" not in str(caught.value)
    assert caught.value.__cause__ is None


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308, 401, 403, 404, 500])
def test_http_errors_and_redirects_are_not_followed_or_reflected(peer, status, capsys):
    peer.replies.append(
        {
            "status": status,
            "raw": b"test-scoped-bearer private-source-text",
            "headers": {"Location": peer.url + "/credential-leak"},
        }
    )
    with pytest.raises(ClientError) as caught:
        _client(peer).call_tool("memory_save", {"text": "private-source-text"})
    assert len(peer.calls) == 1
    assert peer.calls[0]["path"] == "/mcp"
    assert "test-scoped-bearer" not in str(caught.value)
    assert "private-source-text" not in str(caught.value)
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    "spec",
    [
        {"raw": b"private-source-text"},
        {"raw": b"[NaN]"},
        {"rpc": []},
        {"rpc": {"jsonrpc": "2.0", "id": 99, "result": {}}},
        {"rpc": {"jsonrpc": "2.0", "id": True, "result": {}}},
        {"rpc": {"jsonrpc": "1.0", "id": 1, "result": {}}},
        {"rpc": {"jsonrpc": "2.0", "id": 1, "result": {}, "error": "private-source-text"}},
        {"rpc": {"jsonrpc": "2.0", "id": 1, "error": {"message": "private-source-text"}}},
        {"result": None},
        {"result": {"content": "private-source-text"}},
        {"result": {"content": ["private-source-text"]}},
        {"result": {"content": [{"type": "text", "text": None}]}},
        {"result": {"content": [], "error": "private-source-text"}},
        {"text": "private-source-text", "isError": True},
    ],
)
def test_protocol_and_tool_errors_are_sanitized(peer, spec):
    peer.replies.append(spec)
    with pytest.raises(ClientError) as caught:
        _client(peer).call_tool("memory_get", {"id": "m-1"})
    assert "private-source-text" not in str(caught.value)
    assert len(peer.calls) == 1


def test_message_size_limits(peer, monkeypatch):
    client = _client(peer)
    monkeypatch.setattr(client_module, "MAX_MESSAGE_BYTES", 256)
    with pytest.raises(ClientError, match="request is too large"):
        client.call_tool("memory_save", {"text": "x" * 257})
    assert not peer.calls
    peer.replies.append({"raw": b"x" * 257})
    with pytest.raises(ClientError, match="Invalid response"):
        client.call_tool("memory_get", {"id": "m-1"})


@pytest.mark.parametrize(
    "arguments", [["private-source-text"], {"text": object()}, {"text": float("nan")}]
)
def test_non_json_arguments_are_sanitized_before_transport(peer, arguments):
    with pytest.raises(ClientError, match="Invalid Commonplace tool arguments"):
        _client(peer).call_tool("memory_save", arguments)
    assert not peer.calls


def test_fetch_context_omits_unspecified_filters(peer):
    peer.replies.extend([{"text": "initial context"}, {"text": "filtered context"}])
    client = _client(peer)
    assert client.fetch_context() == "initial context"
    assert (
        client.fetch_context("current task", board="work", repo="example.test/team/repo")
        == "filtered context"
    )
    assert _calls(peer) == [
        {"name": "session_start", "arguments": {}},
        {
            "name": "session_start",
            "arguments": {
                "query": "current task",
                "board": "work",
                "repo": "example.test/team/repo",
            },
        },
    ]


@pytest.mark.parametrize(
    "marker,expected", [("42", {"rules_seq": 42}), ("0", {"rules_seq": 0}), ("off", {})]
)
def test_write_obtains_effective_rules_and_does_not_mutate_arguments(peer, marker, expected):
    peer.replies.extend(
        [{"text": "effective rules: 0\nseq 1\nrules_seq " + marker}, {"text": "saved m-1"}]
    )
    arguments = {"text": "  Exact words.\n café  ", "rules_seq": 999}
    original = dict(arguments)
    assert _client(peer).write_tool("memory_save", arguments) == "saved m-1"
    assert arguments == original
    assert _calls(peer) == [
        {"name": "rules_get", "arguments": {"effective": True, "format": "md"}},
        {"name": "memory_save", "arguments": {"text": arguments["text"], **expected}},
    ]
    assert peer.calls[1]["path"] == "/mcp"


@pytest.mark.parametrize(
    "rules",
    [
        "rules_seq unavailable: failure",
        "rules_seq too_large",
        "rules_seq store_lost",
        "effective rules: 0\nseq 0",
        'quoted data: "rules_seq 5"',
        "rules_seq 4\nIncomplete: 1 rule",
    ],
)
def test_incomplete_or_unavailable_rules_prevent_write(peer, rules):
    peer.replies.append({"text": rules})
    with pytest.raises(ClientError, match="unavailable or incomplete"):
        _client(peer).remember("private-source-text")
    assert [call["name"] for call in _calls(peer)] == ["rules_get"]


def test_one_rules_changed_refusal_refreshes_and_retries(peer):
    peer.replies.extend(
        [
            {"text": "rules_seq 1"},
            {"text": "no: rules_changed: read effective rules", "isError": True},
            {"text": "rules_seq 2"},
            {"text": "saved m-1"},
        ]
    )
    assert _client(peer).remember("exact text", theme="private", topic="decisions") == "saved m-1"
    assert [call["name"] for call in _calls(peer)] == [
        "rules_get",
        "memory_save",
        "rules_get",
        "memory_save",
    ]
    assert [call["arguments"]["rules_seq"] for call in _calls(peer)[1::2]] == [1, 2]
    assert _calls(peer)[-1]["arguments"] == {
        "text": "exact text",
        "theme": "private",
        "topic": "decisions",
        "rules_seq": 2,
    }


def test_second_explicit_refusal_is_not_retried(peer):
    peer.replies.extend(
        [{"text": "rules_seq 1"}, {"text": "no: rules_changed", "isError": True}] * 2
    )
    with pytest.raises(ClientError, match="rules changed; write refused"):
        _client(peer).remember("exact text")
    assert len(peer.calls) == 4


@pytest.mark.parametrize(
    "failure",
    [
        {"disconnect": True},
        {"status": 500},
        {"raw": b"invalid JSON"},
        {"text": "no: could not link saved memory", "isError": True},
        {"text": "saved m-1; rules_changed", "isError": True},
        {"text": "rules_changed", "isError": True},
        {"text": "no: rules_changed_extra", "isError": True},
        {
            "result": {
                "content": [{"type": "text", "text": "no: rules_changed"}],
                "error": "unknown",
            }
        },
        {"rpc": {"jsonrpc": "2.0", "id": 2, "error": {"message": "no: rules_changed"}}},
    ],
)
def test_ambiguous_failed_writes_are_never_retried(peer, failure):
    peer.replies.extend([{"text": "rules_seq 1"}, failure])
    with pytest.raises(ClientError):
        _client(peer).remember("exact text")
    assert [call["name"] for call in _calls(peer)] == ["rules_get", "memory_save"]


def test_successful_stored_text_cannot_trigger_a_retry(peer):
    peer.replies.extend([{"text": "rules_seq 1"}, {"text": "no: rules_changed"}])
    assert _client(peer).write_tool("example_tool", {}) == "no: rules_changed"
    assert len(peer.calls) == 2


@pytest.mark.parametrize(
    "scope,scope_ref", [("global", None), ("board", "work"), ("repo", "example.test/team/project")]
)
def test_publish_rule_preserves_one_verbatim_proposal_and_scope(peer, scope, scope_ref):
    text = "  Keep all words.\nNever summarize café.  "
    peer.replies.extend([{"text": "rules_seq off"}, {"text": "imported proposed 1\nseq 1"}])
    assert (
        _client(peer).publish_rule(
            text, agent="*", scope=scope, scope_ref=scope_ref, key="imported"
        )
        == "imported proposed 1\nseq 1"
    )
    rule = {"text": text, "kind": "instruction", "scope": scope, "key": "imported"}
    if scope_ref is not None:
        rule[scope] = scope_ref
    assert _calls(peer)[-1] == {
        "name": "rules_publish",
        "arguments": {"rules": [rule], "agent": "*"},
    }


def test_publish_rule_defaults_to_self(peer):
    peer.replies.extend([{"text": "rules_seq off"}, {"text": "r-1 proposed 1"}])
    _client(peer).publish_rule("verbatim")
    assert _calls(peer)[-1]["arguments"] == {
        "rules": [{"text": "verbatim", "kind": "instruction", "scope": "global"}],
        "agent": "self",
    }


@pytest.mark.parametrize(
    "optional",
    [
        {"agent": "conn:other"},
        {"scope": "repo"},
        {"scope": "private"},
        {"scope_ref": "example.test/team/project"},
        {"kind": "unknown"},
    ],
)
def test_publish_rule_never_silently_broadens_scope(peer, optional):
    with pytest.raises(ClientError):
        _client(peer).publish_rule("verbatim", **optional)
    assert not peer.calls


def test_exact_memory_readback_preserves_whitespace_unicode_and_newlines(peer):
    text = '  café\r\n"quoted" \\ tab\t\u2028line\u2029paragraph\nquoted data: not framing\n  '
    peer.replies.append(
        {"text": 'id="m-1" theme="private"\nquoted data: ' + json.dumps(text, ensure_ascii=False)}
    )
    assert _client(peer).get_memory_text("m-1") == text
    assert _calls(peer) == [{"name": "memory_get", "arguments": {"id": "m-1"}}]


@pytest.mark.parametrize(
    "reply",
    [
        "no memories",
        "quoted data: truncated",
        "quoted data: null",
        "quoted data: {}",
        'quoted data: "first"\nquoted data: "second"',
        'quoted data: "first"\nIncomplete',
        'quoted data: "literal\nnewline"',
        'quoted data: "complete" trailing-text',
    ],
)
def test_readback_refuses_missing_ambiguous_or_incomplete_text(peer, reply):
    peer.replies.append({"text": reply})
    with pytest.raises(ClientError, match="Invalid complete memory readback"):
        _client(peer).get_memory_text("m-1")
