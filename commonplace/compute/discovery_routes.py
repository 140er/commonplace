"""Frozen owner-session discovery REST; installation registers handlers only.

POST commits intake and quota reservations before waking an already configured
service. No key access, network work, credential creation or verified-state setter
is reachable here. The parent installs routes and configures service lifecycle.
"""

from __future__ import annotations

from urllib.parse import unquote, urlsplit

from commonplace import sessions
from commonplace.chat.coworkers.routes import _person, _write_boundary
from commonplace.chat.coworkers.store import StoreError as BoundaryError
from commonplace.control_db import ControlError
from commonplace.http_util import send_json
from mempalace.sqlite_crypto import EncryptionError

from .control import StoreError
from .discovery_service import configured_service
from .discovery_store import open_store
from .fence import FenceDenied
from .protocol import ProtocolDenied
from .routes import _body, _fields
from .scopes import ScopeDenied

_PREFIX = "/api/compute/discoveries"


def handle(gateway, handler):
    h = handler
    try:
        principal, cookie = _person(gateway, h)
        cookies = h.headers.get("Cookie", "").split(";")
        if sum(part.strip().partition("=")[0] == sessions.COOKIE for part in cookies) != 1:
            raise StoreError("sign_in", 401)
        session_hash = sessions._hash(cookie)
        service = configured_service(gateway, principal.workspace_id)
        store = (
            service.store if service is not None else open_store(gateway, principal.workspace_id)
        )
        with gateway.db._lock:
            store.control._owner(principal, session_hash)
        if h.command != "GET":
            _write_boundary(gateway, h)
            if int(h.headers["Content-Length"]) > 4096:
                raise StoreError()
        parsed = urlsplit(h.path)
        if parsed.query or parsed.fragment:
            raise StoreError()
        tail = (
            parsed.path[len(_PREFIX) :]
            if parsed.path == _PREFIX or parsed.path.startswith(_PREFIX + "/")
            else None
        )
        if tail is None or tail.endswith("/") or "//" in tail:
            raise StoreError("not_found", 404)
        parts = [unquote(x) for x in tail.split("/") if x]
        if any(not x or "/" in x for x in parts):
            raise StoreError("not_found", 404)
        auth = {"session_hash": session_hash}
        if h.command == "GET":
            if not parts:
                items = store.list_discoveries(principal, **auth)
                return send_json(
                    h,
                    200,
                    {
                        "discoveries": items,
                        "capabilities": {
                            "passive_discovery": service.available() if service else False
                        },
                    },
                )
            if len(parts) == 1:
                return send_json(
                    h, 200, {"discovery": store.get_discovery(principal, parts[0], **auth)}
                )
        if h.command == "POST":
            if not parts:
                body = _fields(_body(h), ("scope_id", "sources", "request_id"))
                discovery, created = store.submit(principal, **auth, **body)
                if service is not None:
                    service.wake()
                return send_json(h, 201 if created else 200, {"discovery": discovery})
            if len(parts) == 2 and parts[1] == "cancel":
                # Conceal IDs before parsing subordinate body.
                store.get_discovery(principal, parts[0], **auth)
                _fields(_body(h), ())
                discovery = store.cancel(principal, parts[0], **auth)
                if service is not None:
                    service.wake()
                return send_json(h, 200, {"discovery": discovery})
        raise StoreError("not_found", 404)
    except BoundaryError as exc:
        h.close_connection = True
        code = (
            "sign_in"
            if exc.code == "sign_in"
            else "owner_required"
            if exc.status == 403
            else "invalid_request"
        )
        return send_json(
            h,
            401 if code == "sign_in" else 403 if code == "owner_required" else 400,
            {"error": code},
        )
    except ControlError:
        h.close_connection = True
        return send_json(h, 401, {"error": "sign_in"})
    except (FenceDenied, OSError, EncryptionError):
        h.close_connection = True
        return send_json(h, 503, {"error": "discovery_unavailable"})
    except (ProtocolDenied, ScopeDenied, TypeError, OverflowError, RecursionError):
        h.close_connection = True
        return send_json(h, 400, {"error": "invalid_request"})
    except StoreError as exc:
        if h.command != "GET":
            h.close_connection = True
        return send_json(h, exc.status, {"error": exc.code})


def install(gateway):
    """Register only the discovery exact path and its subordinate prefix."""
    for method in ("GET", "POST"):
        gateway.add_route(method, _PREFIX, lambda h: handle(gateway, h))
        gateway.add_prefix_route(method, _PREFIX + "/", lambda h: handle(gateway, h))
