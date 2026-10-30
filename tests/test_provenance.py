"""Verified authorship: the worker stamps only what a token holder vouched for."""

import json
import sqlite3
import threading

import pytest

from mempalace import provenance
from mempalace.knowledge_graph import KnowledgeGraph
from tests._mcp_server_helpers import _patch_mcp_server

IDENTITY = {
    "user": "usr_1",
    "connection": "grok:cos",
    "client": "grok",
    "auth": "pat",
    "trust": "untrusted",
    "workspace": "ws_1",
}


def test_header_round_trip_and_rejects_bad_values():
    assert provenance.decode_identity(provenance.encode_identity(IDENTITY)) == IDENTITY
    unicode_name = {**IDENTITY, "connection": "jeffrey's büro"}
    assert provenance.decode_identity(provenance.encode_identity(unicode_name)) == unicode_name
    for bad in ("", "!!!", provenance.encode_identity(IDENTITY)[:-4]):
        with pytest.raises(ValueError):
            provenance.decode_identity(bad)
    for broken in (
        {**IDENTITY, "trust": ""},
        {k: v for k, v in IDENTITY.items() if k != "user"},
        [],
        {**IDENTITY, "user": "x" * 201},
    ):
        with pytest.raises(ValueError):
            provenance.parse_identity(broken)


def test_stamp_only_inside_a_verified_request():
    meta = {"added_by": "claude"}
    assert provenance.stamp_metadata(dict(meta)) == meta
    assert provenance.stamp_json() is None
    with provenance.verified(IDENTITY):
        stamped = provenance.stamp_metadata(dict(meta))
        stored = provenance.stamp_json()
    assert stamped["added_by"] == "grok:cos" and stamped["claimed_by"] == "claude"
    assert stamped["hub_trust"] == "untrusted" and stamped["hub_written_at"]
    assert provenance.stamp_from_json(stored)["hub_connection"] == "grok:cos"
    assert provenance.current() is None
    assert (
        provenance.stamp_from_json("not json") == {} and provenance.stamp_from_json('["x"]') == {}
    )


def test_identity_does_not_leak_across_threads():
    seen = []
    with provenance.verified(IDENTITY):
        t = threading.Thread(target=lambda: seen.append(provenance.current()))
        t.start()
        t.join()
    assert seen == [None]


def test_old_kg_gains_the_stamp_column(tmp_path):
    path = tmp_path / "kg.sqlite3"
    KnowledgeGraph(str(path)).close()
    with sqlite3.connect(path) as conn:
        conn.execute("ALTER TABLE triples DROP COLUMN hub_stamp")
    kg = KnowledgeGraph(str(path))
    with provenance.verified(IDENTITY):
        kg.add_triple("a", "likes", "b")
    kg.add_triple("a", "likes", "c")
    facts = {f["object"]: f for f in kg.query_entity("a")}
    assert facts["b"]["hub_connection"] == "grok:cos"
    assert "hub_connection" not in facts["c"]
    kg.close()


def _worker(monkeypatch, config, kg, trusted, token="tok"):
    from mempalace import mcp_server as mcp

    _patch_mcp_server(monkeypatch, config, kg)
    if token:
        monkeypatch.setenv("MEMPALACE_MCP_HTTP_TOKEN", token)
    else:
        monkeypatch.delenv("MEMPALACE_MCP_HTTP_TOKEN", raising=False)
        monkeypatch.setenv("MEMPALACE_MCP_HTTP_ALLOW_INSECURE_NO_TOKEN", "1")
    if trusted:
        monkeypatch.setenv(provenance.TRUSTED_IDENTITY_ENV, "1")
    else:
        monkeypatch.delenv(provenance.TRUSTED_IDENTITY_ENV, raising=False)
    httpd = mcp._build_http_server("127.0.0.1", 0)
    threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    ).start()
    return httpd


def _post(port, body, headers):
    import http.client

    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    try:
        conn.request(
            "POST", "/mcp", json.dumps(body), {"Content-Type": "application/json", **headers}
        )
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


ADD = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
        "name": "mempalace_add_drawer",
        "arguments": {"wing": "w", "room": "r", "content": "hi", "added_by": "me"},
    },
}


def test_trusted_worker_needs_an_identity_and_stamps_it(monkeypatch, config, kg):
    httpd = _worker(monkeypatch, config, kg, trusted=True)
    port = httpd.server_address[1]
    try:
        auth = {"Authorization": "Bearer tok"}
        assert _post(port, ADD, auth)[0] == 403
        assert _post(port, ADD, {**auth, provenance.IDENTITY_HEADER: "junk"})[0] == 403
        hdr = {**auth, provenance.IDENTITY_HEADER: provenance.encode_identity(IDENTITY)}
        assert (
            _post(port, ADD, {provenance.IDENTITY_HEADER: hdr[provenance.IDENTITY_HEADER]})[0]
            == 401
        )
        status, body = _post(port, ADD, hdr)
        drawer_id = json.loads(json.loads(body)["result"]["content"][0]["text"])["drawer_id"]
        get = {
            **ADD,
            "params": {"name": "mempalace_get_drawer", "arguments": {"drawer_id": drawer_id}},
        }
        meta = json.loads(json.loads(_post(port, get, hdr)[1])["result"]["content"][0]["text"])[
            "metadata"
        ]
        assert meta["hub_connection"] == "grok:cos" and meta["claimed_by"] == "me"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_untrusted_worker_ignores_the_header(monkeypatch, config, kg):
    httpd = _worker(monkeypatch, config, kg, trusted=False)
    port = httpd.server_address[1]
    try:
        hdr = {
            "Authorization": "Bearer tok",
            provenance.IDENTITY_HEADER: provenance.encode_identity(IDENTITY),
        }
        status, body = _post(port, ADD, hdr)
        drawer_id = json.loads(json.loads(body)["result"]["content"][0]["text"])["drawer_id"]
        get = {
            **ADD,
            "params": {"name": "mempalace_get_drawer", "arguments": {"drawer_id": drawer_id}},
        }
        meta = json.loads(json.loads(_post(port, get, hdr)[1])["result"]["content"][0]["text"])[
            "metadata"
        ]
        assert meta["added_by"] == "me" and "hub_connection" not in meta
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_trusted_worker_refuses_to_start_without_a_token(monkeypatch, config, kg):
    with pytest.raises(RuntimeError, match="needs a bearer token"):
        _worker(monkeypatch, config, kg, trusted=True, token="")
