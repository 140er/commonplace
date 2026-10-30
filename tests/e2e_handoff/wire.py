"""Dependency-free HTTP client shared with the standalone successor."""

import http.client
import json
from urllib.parse import urlsplit


class ThreadClient:
    def __init__(self, url, token):
        address = urlsplit(url)
        assert address.scheme == "http" and address.hostname == "127.0.0.1"
        self.port = address.port
        self.token = token
        self.identifier = 0
        self.calls = []

    def request(self, method, path, body=None, *, headers=None, expected=200):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        fields = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        if isinstance(body, dict):
            body = json.dumps(body).encode("utf-8")
            fields["Content-Type"] = "application/json"
        fields.update(headers or {})
        try:
            connection.request(method, path, body, fields)
            response = connection.getresponse()
            raw = response.read()
            metadata = dict(response.getheaders())
            # Do not put request headers, credentials or full transcripts into errors.
            assert response.status == expected, (method, path, response.status, raw[:500])
            return raw, metadata
        finally:
            connection.close()

    def json(self, method, path, body=None, *, expected=200):
        raw, _ = self.request(method, path, body, expected=expected)
        return json.loads(raw)

    def rpc(self, method, params=None):
        self.identifier += 1
        response = self.json(
            "POST",
            "/mcp",
            {
                "jsonrpc": "2.0",
                "id": self.identifier,
                "method": method,
                "params": params or {},
            },
        )
        assert response.get("id") == self.identifier, "Unexpected protocol response"
        assert "error" not in response, response.get("error")
        return response["result"]

    def initialize(self):
        # The gateway forwards initialize to the real worker. Its HTTP transport
        # is stateless: neither a session header nor an initialized notice is needed.
        result = self.rpc(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "handoff-e2e", "version": "1"},
            },
        )
        assert result.get("protocolVersion") and result.get("serverInfo"), result

    def tool(self, name, **arguments):
        self.calls.append({"name": name, "arguments": arguments})
        result = self.rpc("tools/call", {"name": name, "arguments": arguments})
        assert not result.get("isError"), result
        value = result.get("structuredContent")
        if value is None:
            blocks = result.get("content", [])
            assert len(blocks) == 1 and blocks[0].get("type") == "text", result
            value = json.loads(blocks[0]["text"])
        assert isinstance(value, dict) and not value.get("error"), value
        return value
