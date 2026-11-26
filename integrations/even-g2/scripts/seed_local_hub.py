"""Seed a throwaway local hub for the G2 plugin: boards, cards, and asks for the owner.

Usage: seed_local_hub.py <hub-url> <agent-token-file> [base|late]
  base  two boards, three cards, two asks (run once)
  late  one more ask, to watch a live update arrive on the glasses
Never point this at a real hub.
"""

import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1].rstrip("/")
AGENT = open(sys.argv[2]).read().strip()
if not BASE.startswith(("http://127.0.0.1", "http://localhost")):
    sys.exit("seed only a local throwaway hub")


def call(method, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + AGENT)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, text/event-stream")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.headers, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read().decode()


_sid = {}


def mcp(tool, args):
    if not _sid:
        init = {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "seed", "version": "1"},
        }
        _, hd, _ = call(
            "POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": init}
        )
        _sid["h"] = {"Mcp-Session-Id": hd.get("Mcp-Session-Id", "")}
        call("POST", "/mcp", {"jsonrpc": "2.0", "method": "notifications/initialized"}, _sid["h"])
    rpc = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": tool, "arguments": args},
    }
    st, _, text = call("POST", "/mcp", rpc, _sid["h"])
    return st, text[:200]


def main(what):
    if what in ("all", "base"):
        for slug, name in [("commonplace", "Commonplace"), ("home", "Home renovation")]:
            print(call("POST", "/api/boards", {"name": name, "slug": slug})[0], slug)
        for board, title, status in [
            ("commonplace", "Fix broken footer links on mobile", "doing"),
            ("commonplace", "Rewrite the pricing page copy", "todo"),
            ("home", "Ask the plumber for a second quote", "todo"),
        ]:
            print(
                call("POST", f"/api/boards/{board}/cards", {"title": title, "status": status})[0],
                title,
            )
        print(
            mcp(
                "ask",
                {
                    "board": "commonplace",
                    "question": "Ship the v2 glasses build tonight or wait for Friday?",
                    "options": ["Ship tonight", "Wait for Friday", "Skip v2"],
                    "context": "All checks are green; one reviewer asked for more device testing.",
                },
            )
        )
        print(
            mcp(
                "ask",
                {
                    "board": "home",
                    "question": "Matte or glossy tiles for the bathroom floor?",
                    "options": ["Matte", "Glossy"],
                    "context": "Matte is safer when wet; glossy is 4 euro per square metre cheaper.",
                },
            )
        )
    if what in ("all", "late"):
        print(
            mcp(
                "ask",
                {
                    "board": "commonplace",
                    "question": "Merge the pricing copy rewrite into develop now?",
                    "options": ["Merge", "Hold"],
                    "context": "Copy was reviewed by two people and the gate passed this morning.",
                },
            )
        )


main(sys.argv[3] if len(sys.argv) > 3 else "base")
