"""An in-memory chat platform for the bridge tests: signs and verifies requests, turns a small
JSON event into an ``Inbound``, and records every post so the engine is tested with no platform."""

import hashlib
import hmac
import http.client
import json
import threading
import time

from commonplace.chat_bridge import adapter

SIGNING = "SENTINEL-signing-secret-9f8e7d"
TOKEN = "xoxb-SENTINEL-0123456789-ABCDEF"


def sign(secret, ts, body) -> str:
    return hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()


class FakePlatform(adapter.Adapter):
    platform = "fake"
    secret_fields = ("token", "signing_secret")
    caps = adapter.Caps(max_text=120)

    def __init__(self):
        self.result = adapter.TestResult(True)
        self.posts = []
        self.script = []  # PostResults handed out before the default success, one per call
        self.lock = threading.Lock()
        self.found = None  # when set, find_posted answers with it

    def test(self, install):
        return self.result

    def verify(self, install, headers, body):
        ts, given = headers.get("x-fake-ts", ""), headers.get("x-fake-sig", "")
        wanted = sign(install.secrets["signing_secret"], ts, body)
        if not hmac.compare_digest(given, wanted):
            return adapter.Reject("signature")
        if not ts.isdigit() or abs(time.time() - int(ts)) > 300:
            return adapter.Reject("stale")
        payload = json.loads(body)
        if payload.get("type") == "handshake":
            return adapter.Challenge({"challenge": payload["challenge"]})
        return adapter.Verified(payload["team"], payload)

    def normalize(self, install, verified):
        p = verified.payload
        if p.get("tap"):
            return adapter.Interaction(p["event"], p["user"], p["channel"], p["tap"])
        if p.get("edited") or p.get("deleted"):
            return None
        return adapter.Inbound(
            ext_event=p["event"],
            ext_user=p["user"],
            ext_channel=p["channel"],
            ext_thread=p.get("thread", ""),
            ext_message=p["ts"],
            text=p["text"].replace("<@APP>", "").strip(),
            kind=p["kind"],
            from_bot=p.get("bot", False),
            shared_channel=p.get("shared", False),
        )

    def post(self, install, target, outbound, key):
        with self.lock:
            if self.script:
                return self.script.pop(0)
            self.posts.append(
                {
                    "channel": target.ext_channel,
                    "thread": target.ext_thread,
                    "text": outbound.text,
                    "key": key,
                    "private": False,
                    "buttons": outbound.buttons,
                }
            )
            return adapter.PostResult(True, ext_post=f"p{len(self.posts)}")

    def post_private(self, install, ext_user, ext_channel, outbound):
        with self.lock:
            self.posts.append(
                {
                    "channel": ext_channel,
                    "thread": "",
                    "text": outbound.text,
                    "key": "",
                    "private": True,
                    "user": ext_user,
                    "buttons": outbound.buttons,
                }
            )
            return adapter.PostResult(True, ext_post=f"p{len(self.posts)}")

    def find_posted(self, install, ext_channel, key):
        return self.found

    def texts(self):
        return [p["text"] for p in self.posts]


def event(name, user, channel, text, kind="dm", **extra):
    return {
        "team": "T-ONE",
        "event": name,
        "user": user,
        "channel": channel,
        "ts": f"ts-{name}",
        "text": text,
        "kind": kind,
        **extra,
    }


def tap(name, user, channel, payload):
    """An interaction payload: one button tap."""
    return {"team": "T-ONE", "event": name, "user": user, "channel": channel, "tap": payload}


def send(hub, install, payload, route="events", secret=SIGNING, ts=None, sig=None, body=None):
    """POST one signed request to the public intake; the status code."""
    raw = body if body is not None else json.dumps(payload).encode()
    ts = str(int(time.time())) if ts is None else ts
    headers = {
        "Content-Type": "application/json",
        "X-Fake-Ts": ts,
        "X-Fake-Sig": sign(secret, ts, raw) if sig is None else sig,
    }
    conn = http.client.HTTPConnection("127.0.0.1", hub.hub.port, timeout=10)
    try:
        conn.request("POST", f"/api/bridge/in/{install}/{route}", raw, headers)
        response = conn.getresponse()
        response.read()
        return response.status
    finally:
        conn.close()
