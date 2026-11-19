"""``/api/compute/pairings`` plus the two routes the machine itself calls.

Owner routes: ``routes.handle`` has already authenticated the person, decided owner or member
for the method (members read, only owners write), refused query strings and parsed the path;
the owner check ran before any body was read. Every refusal names a field (when it is one) and
a fixed reason.

Machine routes: ``POST /api/runner/pairing-status`` takes the machine's own credential;
``POST /api/runner-pairing/check`` takes no credential at all, only the pairing id and the
one-time code, and answers with a reason when the code cannot work.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

from commonplace.control_db import ControlError
from commonplace.http_util import log_failure, send_json
from commonplace.runner import enrollment, enrollment_api

from . import pairing
from .control import StoreError

LOG = logging.getLogger(__name__)
STATUS = "/api/runner/pairing-status"
CHECK = "/api/runner-pairing/check"
MAX_BYTES = 2048
_OPTIONAL = pairing.CREATE_FIELDS - pairing.CREATE_REQUIRED
_CONTROL_STATUS = {"cancelled": 200, "expired": 200}


def dispatch(h, store, principal, parts, auth, body_reader):
    """``parts`` is the path after ``/api/compute/pairings``."""
    gateway, db = store.gateway, store.gateway.db
    owner = store._member(principal, auth["session_hash"]).role == "owner"
    if h.command == "GET":
        if not parts:
            return send_json(h, 200, pairing.list_pairings(db, principal, owner=owner))
        if parts == ["options"]:
            return send_json(h, 200, pairing.options(db, gateway, principal, owner=owner))
        if len(parts) == 1:
            return send_json(h, 200, pairing.get(db, principal, parts[0], owner=owner))
    elif h.command == "POST":
        if not parts:
            body = body_reader(h, pairing.CREATE_FIELDS, _OPTIONAL)
            _charge(db, principal.workspace_id, "pairing_create", pairing.CREATE_PER_MINUTE)
            created, item, setup = pairing.create(db, gateway, principal, body)
            payload = {"pairing": item, "repeated": not created}
            if setup:
                payload["setup"] = setup
            return send_json(h, 201 if created else 200, payload)
        if len(parts) == 2 and parts[1] in ("accept", "close"):
            pairing._find(db, principal.workspace_id, parts[0])
            fields = pairing.ACCEPT_FIELDS if parts[1] == "accept" else pairing.CLOSE_FIELDS
            body = body_reader(h, fields, ())
            action = pairing.accept if parts[1] == "accept" else pairing.close
            return send_json(h, 200, {"pairing": action(db, principal, parts[0], body)})
    raise StoreError("not_found", 404, reason="route")


def _charge(db, workspace, purpose, limit):
    """Durable per-workspace counter; a workspace with setup off has none, so it is not limited."""
    try:
        enrollment.charge(db, workspace, purpose, limit)
    except enrollment.Refused as exc:
        if exc.status == 429:
            raise pairing.refuse(None, "rate_limited", "rate_limited", 429) from None


# ---- machine routes ----------------------------------------------------------------------------


def _read(h):
    """The JSON body, or None after the refusal has been sent."""
    return enrollment_api._body(h)


def _refusal(h, exc):
    h.close_connection = True
    detail = {k: v for k, v in (("field", exc.field), ("reason", exc.reason)) if v}
    send_json(h, exc.status, {"error": exc.code, **detail})


def _status(gateway, h):
    """A machine's own step report, or heartbeat."""
    path = urlsplit(h.path).path
    principal = gateway.principal_for(h)
    if enrollment_api.preflight(gateway, h, path, principal):
        return
    if principal is None or getattr(principal, "runner", None) != "machine":
        h.close_connection = True
        send_json(h, 404, {"error": "not found"})
        return
    data = _read(h)
    if data is None:
        return
    try:
        enrollment.charge(
            gateway.db, principal.workspace_id, "pairing_report", pairing.REPORT_PER_MINUTE
        )
        result = pairing.report(gateway.db, principal, data)
    except enrollment.Refused as exc:
        send_json(h, exc.status, {"error": "not found" if exc.status == 404 else "rate limited"})
        return
    except StoreError as exc:
        _refusal(h, exc)
        return
    except (ValueError, ControlError):
        send_json(h, 400, {"error": "invalid_request", "reason": "body"})
        return
    except Exception as exc:
        log_failure(LOG, "Pairing report unavailable", exc)
        send_json(h, 503, {"error": "pairing unavailable"})
        return
    send_json(h, 200, result)


def _check(gateway, h):
    """Is this code usable, and if not, why. No credential: the code is the authority."""
    refused = (
        gateway.principal_for(h) is not None
        or h.headers.get("Authorization")
        or h.headers.get("Cookie")
    )
    if refused:
        h.close_connection = True
        send_json(h, 404, {"error": "not found"})
        return
    data = _read(h)
    if data is None:
        return
    workspace = data.get("workspace_id")
    try:
        if not isinstance(workspace, str) or len(workspace) > 80:
            raise ValueError("invalid check")
        try:
            enrollment.charge(gateway.db, workspace, "pairing_check", pairing.CHECK_PER_MINUTE)
        except enrollment.Refused as exc:
            if exc.status == 429:
                limited = pairing.REASONS["rate_limited"]
                send_json(
                    h,
                    429,
                    {
                        "usable": False,
                        "reason": "rate_limited",
                        "text": limited[0],
                        "next": limited[1],
                    },
                )
                return
        result = pairing.check(gateway.db, data)
    except (ValueError, ControlError):
        send_json(h, 400, {"error": "invalid_request", "reason": "body"})
        return
    except Exception as exc:
        log_failure(LOG, "Pairing check unavailable", exc)
        send_json(h, 503, {"error": "pairing unavailable"})
        return
    send_json(h, 200, result)


def install(gateway):
    pairing.ensure(gateway.db)
    gateway.add_route("POST", STATUS, lambda h: _status(gateway, h))
    gateway.add_route("POST", CHECK, lambda h: _check(gateway, h))
