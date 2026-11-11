"""Account data for the person it is about: export and deletion.

``GET /api/account`` shows what the server would export and delete.
``GET /api/account/export`` streams everything as a ZIP (:mod:`.export`).
``POST`` and ``DELETE /api/account/deletion`` request and withdraw deletion
(:mod:`.deletion`), which is off unless the operator enables it.

Every route needs a signed-in person. An agent's token never reaches them:
a token must not be able to copy or delete the account it works for.
"""

from __future__ import annotations

import threading
import time

from commonplace import sessions
from commonplace.http_util import read_json, require_principal, send_json

from . import deletion, export

_exporting: set = set()
_exporting_lock = threading.Lock()


def _person(gateway, h):
    principal = require_principal(gateway, h)
    if principal is None:
        return None
    if not principal.person:
        send_json(h, 403, {"error": "Account data needs a signed-in person"})
        return None
    return principal


def install(gateway):
    deletion.ensure_schema(gateway.db)

    def account(h):
        principal = _person(gateway, h)
        if principal is None:
            return
        session = sessions.cookie_value(h.headers.get("Cookie"))
        send_json(
            h,
            200,
            {
                "user": principal.user_id,
                "email": principal.user_email,
                "export": "/api/account/export",
                "deletion": deletion.status(gateway, principal.user_id, session),
            },
        )

    def download(h):
        principal = _person(gateway, h)
        if principal is None:
            return
        user_id = principal.user_id
        with _exporting_lock:
            busy = user_id in _exporting
            _exporting.add(user_id)
        if busy:
            send_json(h, 429, {"error": "An export of this account is already running"})
            return
        try:
            try:
                planned = export.plan(gateway, user_id)
            except export.ExportUnavailable as exc:
                gateway.db.audit(action="account.export", decision="deny", user_id=user_id)
                send_json(h, 409, {"error": f"Export is not possible right now: {exc}"})
                return
            gateway.db.audit(action="account.export", decision="ok", user_id=user_id)
            # The length is unknown until the last row: the close ends the body,
            # and only a complete archive has its directory at the end.
            h.close_connection = True
            h.send_response(200)
            h.send_header("Content-Type", "application/zip")
            h.send_header(
                "Content-Disposition",
                f'attachment; filename="commonplace-account-{time.strftime("%Y%m%d")}.zip"',
            )
            h.send_header("Cache-Control", "no-store")
            h.send_header("X-Content-Type-Options", "nosniff")
            h.send_header("Connection", "close")
            h.end_headers()
            export.write(gateway, planned, h.wfile)
        finally:
            with _exporting_lock:
                _exporting.discard(user_id)

    def delete(h):
        principal = _person(gateway, h)
        if principal is None:
            return
        if h.command == "DELETE":
            if deletion.cancel(gateway, principal.user_id):
                send_json(h, 200, {"ok": True})
            else:
                send_json(h, 404, {"error": "No deletion request to withdraw"})
            return
        try:
            body = read_json(h)
        except ValueError:
            return
        try:
            result = deletion.request(
                gateway,
                principal,
                sessions.cookie_value(h.headers.get("Cookie")),
                body.get("confirm"),
            )
        except deletion.Refused as refused:
            send_json(h, refused.status, refused.body)
            return
        send_json(h, 200, result)

    gateway.add_route("GET", "/api/account", account)
    gateway.add_route("GET", "/api/account/export", download)
    gateway.add_route("POST", "/api/account/deletion", delete)
    gateway.add_route("DELETE", "/api/account/deletion", delete)

    if deletion.enabled() and getattr(gateway, "account_sweeper", None) is None:
        gateway.account_sweeper = deletion.Sweeper(gateway)
        gateway.db.add_pre_close_callback(gateway.account_sweeper.stop)
        gateway.account_sweeper.start()
