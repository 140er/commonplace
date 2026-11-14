"""Owner-only setup controls and code-only bootstrap; no tool or UI surface."""

import logging
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature

from commonplace.control_db import ControlError
from commonplace.http_util import log_failure, path_parts, read_json, send_json

from . import enrollment, registry

LOG = logging.getLogger(__name__)
SETTINGS = "/api/runners/install-settings"
ISSUE = "/api/runners/enrollments"
BOOTSTRAP = ("/api/runner-enrollment/exchange", "/api/runner-enrollment/recover")
MAX_BYTES = 16384


def _owner_path(path):
    return path in (SETTINGS, ISSUE) or path.startswith(ISSUE + "/")


def preflight(gateway, h, path, principal):
    """Hide off/foreign/token routes before the gateway's generic token gates."""
    from .setup_approvals import MACHINE_PATHS

    if path in ("/api/runner/install-manifest", "/api/runner/install-status", *MACHINE_PATHS):
        # The existing private prefix supplies authentication for anonymous calls.
        # Existing tokens gain neither discovery nor a machine-self capability.
        if principal is None:
            return False
        if principal.runner == "machine" and enrollment.enabled(gateway.db, principal.workspace_id):
            return False
        h.close_connection = True
        send_json(h, 404, {"error": "not found"})
        return True
    if "/setup-requests/" in path and path.startswith("/api/runners/"):
        try:
            enrollment.owner_now(gateway.db, principal)
            enrollment._require_enabled(gateway.db, principal.workspace_id)
        except enrollment.Refused:
            h.close_connection = True
            send_json(h, 404, {"error": "not found"})
            return True
    if path not in BOOTSTRAP and not _owner_path(path):
        return False
    if path in BOOTSTRAP:
        refused = principal is not None or h.headers.get("Authorization") or h.headers.get("Cookie")
    else:
        try:
            enrollment.owner_now(gateway.db, principal)
            refused = not (path == SETTINGS and h.command == "PUT") and not enrollment.enabled(
                gateway.db, principal.workspace_id
            )
        except enrollment.Refused:
            refused = True
    if refused:
        if principal is not None and enrollment.enabled(gateway.db, principal.workspace_id):
            with gateway.db._tx():
                gateway.db.audit(
                    action="runner.enrollment.denied",
                    decision="deny",
                    workspace_id=principal.workspace_id,
                    user_id=principal.user_id,
                    connection_id=principal.connection_id,
                    auth=principal.auth,
                    reason="refused",
                )
        h.close_connection = True
        send_json(h, 404, {"error": "not found"})
        return True
    return False


def _body(h):
    try:
        if int(h.headers.get("Content-Length", "0")) > MAX_BYTES:
            send_json(h, 413, {"error": "request too large"})
            return None
        return read_json(h)
    except ValueError:
        return None


def _sweeper(gateway):
    if getattr(gateway, "runner_enrollment_sweeper", None) is None:
        sweeper = enrollment.Sweeper(gateway.db)
        gateway.db.add_pre_close_callback(sweeper.stop)
        gateway.runner_enrollment_sweeper = sweeper
        sweeper.start()


def _control(gateway, h):
    from commonplace.gateway import _session_write_allowed
    from commonplace.runner.owner_api import _wings

    owner = gateway.principal_for(h)
    path = urlsplit(h.path).path
    if preflight(gateway, h, path, owner):
        return
    if h.command in ("PUT", "POST", "DELETE") and not _session_write_allowed(gateway, h):
        send_json(h, 403, {"error": "JSON and same-origin request required"})
        return
    try:
        if path == SETTINGS and h.command == "GET":
            row = enrollment.settings(gateway.db, owner.workspace_id)
            result, code = {"enabled": bool(row["enabled"]), "version": row["version"]}, 200
        elif path == SETTINGS and h.command == "PUT":
            data = _body(h)
            if data is None:
                return
            if set(data) != {"enabled"}:
                raise ValueError("invalid settings")
            result, code = enrollment.set_enabled(gateway.db, owner, data["enabled"]), 200
            if result["enabled"]:
                _sweeper(gateway)
        elif path == ISSUE and h.command == "POST":
            data = _body(h)
            if data is None:
                return
            if isinstance(data.get("grant"), dict):
                data["grant"] = _wings(gateway, owner, data["grant"])
            code, result = enrollment.issue(gateway.db, owner, data)
        else:
            parts = path_parts(h, ISSUE)
            if len(parts) != 1 or h.command not in ("GET", "DELETE"):
                raise enrollment.Refused(404)
            result, code = (
                enrollment.inspect(gateway.db, owner, parts[0], revoke=h.command == "DELETE"),
                200,
            )
    except enrollment.Refused:
        send_json(h, 404, {"error": "not found"})
        return
    except registry.Conflict:
        send_json(h, 409, {"error": "enrollment intent conflicts"})
        return
    except (ValueError, ControlError):
        send_json(h, 400, {"error": "invalid enrollment intent"})
        return
    except Exception as exc:
        log_failure(LOG, "Enrollment control unavailable", exc)
        send_json(h, 503, {"error": "enrollment unavailable"})
        return
    send_json(h, code, result)


def _bootstrap(gateway, h):
    path = urlsplit(h.path).path
    if preflight(gateway, h, path, gateway.principal_for(h)):
        return
    data = _body(h)
    if data is None:
        return
    workspace = data.get("workspace_id")
    if not isinstance(workspace, str) or len(workspace) > 80:
        send_json(h, 404, {"error": "not found"})
        return
    purpose = "exchange" if path == BOOTSTRAP[0] else "recover"
    try:
        enrollment.charge(gateway.db, workspace, purpose)
        if purpose == "exchange":
            result, code = enrollment.exchange(gateway.db, data), 201
        else:
            result, code = enrollment.recover(gateway.db, data), 200
    except (enrollment.Refused, ValueError, InvalidSignature, ControlError) as exc:
        status = exc.status if isinstance(exc, enrollment.Refused) else 403
        if status not in (404, 429):
            try:
                enrollment.deny(gateway.db, workspace)
            except Exception as audit_error:
                log_failure(LOG, "Enrollment denial unavailable", audit_error)
                send_json(h, 503, {"error": "enrollment unavailable"})
                return
        send_json(h, status, {"error": "not found" if status == 404 else "enrollment refused"})
        return
    except Exception as exc:
        log_failure(LOG, "Enrollment exchange unavailable", exc)
        send_json(h, 503, {"error": "enrollment unavailable"})
        return
    send_json(h, code, result)


def install(gateway):
    enrollment.ensure(gateway.db)
    for method in ("GET", "PUT"):
        gateway.add_route(method, SETTINGS, lambda h: _control(gateway, h))
    gateway.add_route("POST", ISSUE, lambda h: _control(gateway, h))
    for method in ("GET", "DELETE"):
        gateway.add_prefix_route(method, ISSUE + "/", lambda h: _control(gateway, h))
    for path in BOOTSTRAP:
        gateway.add_route("POST", path, lambda h: _bootstrap(gateway, h))
    with gateway.db._lock:
        active = gateway.db._conn.execute(
            "SELECT 1 FROM runner_install_settings WHERE enabled=1 LIMIT 1"
        ).fetchone()
    if active:
        _sweeper(gateway)
