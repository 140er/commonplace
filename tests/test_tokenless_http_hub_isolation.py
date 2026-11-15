"""A tokenless loopback hub is not affected by a trusted-identity setting left in the process.

The in-process worker used by the gateway tests sets MEMPALACE_MCP_TRUSTED_IDENTITY for
the length of one test. When a background start lands after that test has been undone,
the value stays in the worker process, and the next tokenless server fails to start with
"MEMPALACE_MCP_TRUSTED_IDENTITY=1 needs a bearer token" (seen under parallel runs).
"""

import os

import pytest


@pytest.fixture
def left_over_identity(monkeypatch):
    monkeypatch.setenv("MEMPALACE_MCP_TRUSTED_IDENTITY", "1")
    monkeypatch.delenv("MEMPALACE_MCP_HTTP_TOKEN", raising=False)


def test_tokenless_hub_starts_although_a_trusted_identity_was_left_set(
    left_over_identity, request, monkeypatch, config, kg
):
    from mempalace import mcp_server

    request.getfixturevalue("tokenless_http_hub")  # requested after the value was left behind
    monkeypatch.setattr(mcp_server, "_config", config)
    monkeypatch.setattr(mcp_server, "_get_kg", lambda *a, **kw: kg)

    httpd = mcp_server._build_http_server("127.0.0.1", 0)
    try:
        assert httpd.server_address[1] > 0
        assert "MEMPALACE_MCP_TRUSTED_IDENTITY" not in os.environ
    finally:
        httpd.server_close()
