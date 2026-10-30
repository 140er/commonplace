"""Browser-bound provider sign-in ending in the existing web session."""

from __future__ import annotations

import os
import re
import secrets
import time
from urllib.parse import quote, urlencode, urlsplit

from .. import sessions, urls
from ..oauth.http import OAuthError, body, query, redirect, send_json
from ..oauth.store import digest
from . import accounts
from .providers import SignInError, configured

COOKIE = "cmmn_signin"
SCHEMA = """
CREATE TABLE IF NOT EXISTS signin_flows (
    state_hash TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    browser_hash TEXT NOT NULL,
    verifier TEXT NOT NULL,
    nonce TEXT NOT NULL,
    next TEXT NOT NULL,
    expires_at INTEGER NOT NULL
);
"""


def _next(value: str) -> str:
    if len(value) > 4096:
        return ""
    try:
        urls.app_link(value)
    except ValueError:
        return ""
    if value.startswith("/oauth/authorize?") or (
        value.startswith("/app/")
        and not value.startswith(("/app/auth/", "/app/login", "/app/logout"))
    ):
        return value
    return ""


class SignIn:
    def __init__(self, *, providers, signup):
        self.providers = {provider.id: provider for provider in providers}
        self.signup = signup

    def links(self, next_path="") -> list[tuple[str, str]]:
        suffix = "?" + urlencode({"next": next_path}, quote_via=quote) if next_path else ""
        return [
            (p.name, urls.app_link(f"/app/auth/{p.id}") + suffix) for p in self.providers.values()
        ]

    def handle(self, gateway, h):
        path = urlsplit(h.path).path
        if path == "/app/auth/providers":
            if h.command != "GET":
                send_json(h, 405, {"error": "use GET"})
                return
            send_json(
                h,
                200,
                {
                    "providers": [
                        {"id": p.id, "name": p.name, "url": urls.app_link(f"/app/auth/{p.id}")}
                        for p in self.providers.values()
                    ]
                },
            )
            return
        parts = path.removeprefix("/app/auth/").split("/")
        provider = self.providers.get(parts[0])
        if provider is None or len(parts) > 2 or len(parts) == 2 and parts[1] != "callback":
            send_json(h, 404, {"error": "not found"})
            return
        callback = len(parts) == 2
        method = "POST" if callback and provider.id == "apple" else "GET"
        if h.command != method:
            send_json(h, 405, {"error": "use " + method})
        elif callback:
            self._callback(gateway, h, provider)
        else:
            try:
                self._start(gateway, h, provider)
            except OAuthError:
                self._refuse(gateway, h, provider.id, "failed", "", audit=False)

    def _start(self, gateway, h, provider):
        next_path = _next(query(h.path).get("next", ""))
        browser = sessions.cookie_value(h.headers.get("Cookie"), COOKIE) or ""
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", browser):
            browser = secrets.token_urlsafe(32)
        state, verifier, nonce = (secrets.token_urlsafe(32) for _ in range(3))
        now = int(time.time())
        with gateway.db._lock, gateway.db._tx() as conn:
            conn.execute("DELETE FROM signin_flows WHERE expires_at <= ?", (now,))
            conn.execute(
                "INSERT INTO signin_flows(state_hash, provider, browser_hash, verifier, nonce, next, expires_at) VALUES (?,?,?,?,?,?,?)",
                (
                    digest(state),
                    provider.id,
                    digest(browser),
                    verifier,
                    nonce,
                    next_path,
                    now + 600,
                ),
            )
        cookie = f"{COOKIE}={browser}; Path=/app/auth/; HttpOnly; Max-Age=600"
        cookie += "; SameSite=None; Secure" if gateway.secure_cookies else "; SameSite=Lax"
        redirect(
            h, provider.authorize_url(state, verifier, nonce), headers=(("Set-Cookie", cookie),)
        )

    def _callback(self, gateway, h, provider):
        next_path = ""
        matched = False
        try:
            parameters = body(h) if h.command == "POST" else query(h.path)
            with gateway.db._lock, gateway.db._tx() as conn:
                row = conn.execute(
                    "SELECT * FROM signin_flows WHERE state_hash = ?",
                    (digest(parameters.get("state", "")),),
                ).fetchone()
                if row is not None:
                    conn.execute(
                        "DELETE FROM signin_flows WHERE state_hash = ?", (row["state_hash"],)
                    )
            browser = sessions.cookie_value(h.headers.get("Cookie"), COOKIE)
            if (
                row is None
                or row["provider"] != provider.id
                or not browser
                or not secrets.compare_digest(row["browser_hash"], digest(browser))
            ):
                raise SignInError("expired")
            matched = True
            next_path = row["next"]
            if row["expires_at"] <= time.time():
                raise SignInError("expired")
            if "error" in parameters:
                raise SignInError(
                    "cancelled"
                    if parameters["error"] in ("access_denied", "user_cancelled_authorize")
                    else "failed"
                )
            code = parameters.get("code", "")
            if not code or len(code) > 2048:
                raise SignInError("failed")
            profile = provider.profile(
                code, row["verifier"], row["nonce"], parameters.get("user", "")
            )
            with gateway.db._lock:
                uid = accounts.resolve(gateway.db, provider.id, profile, signup=self.signup)
                workspace = sessions.home_workspace(gateway.db, uid)
                if workspace is None:
                    raise SignInError("disabled")
                session = sessions.start_session(gateway.db, uid, workspace, via=provider.id)
        except (OAuthError, SignInError) as exc:
            self._refuse(
                gateway,
                h,
                provider.id,
                exc.code if isinstance(exc, SignInError) else "failed",
                next_path,
                audit=matched,
            )
            return
        landing = (
            urls.AGENT_URL + next_path if next_path.startswith("/oauth/") else next_path or "/app/"
        )
        redirect(
            h,
            landing,
            status=303,
            headers=(("Set-Cookie", sessions.set_cookie(session, gateway.secure_cookies)),),
        )

    def _refuse(self, gateway, h, provider, code, next_path, *, audit):
        if audit:
            gateway.db.audit(action="signin.refuse", decision="deny", reason=f"{provider}:{code}")
        parameters = {"signin": code}
        if next_path:
            parameters["next"] = next_path
        redirect(h, "/app/?" + urlencode(parameters, quote_via=quote), status=303)


def install(gateway):
    if hasattr(gateway, "sign_in"):
        return
    accounts.ensure_schema(gateway.db)
    with gateway.db._lock:
        gateway.db._conn.executescript(SCHEMA)
    gateway.sign_in = SignIn(
        providers=configured(),
        signup=os.environ.get("COMMONPLACE_SIGNUP", "").strip().lower() == "open",
    )
    for method in ("GET", "POST"):
        gateway.add_prefix_route(method, "/app/auth/", lambda h: gateway.sign_in.handle(gateway, h))
