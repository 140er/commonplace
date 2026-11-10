"""Person-owner HTTP routes for grants and binding attestations."""

import json
import logging

from commonplace.control_db import ControlError
from commonplace.extensions import ToolError
from commonplace.http_util import (
    log_failure,
    path_parts,
    read_json,
    require_principal,
    send_json,
)
from commonplace.web.agents import _read_wings

from . import grant_scope, jobs, registry

LOG = logging.getLogger(__name__)
MAX_REPORT_BYTES = 20 * 1024
MAX_CONFIRM_BYTES = 4 * 1024


def _scope(stored):
    if stored["grant_scope"] is None:
        return None
    return {
        "record": json.loads(stored["grant_scope"]),
        "sha256": stored["grant_scope_sha256"],
        "reported_at": stored["grant_scope_reported_at"],
        "source": "runner-local-owner-submitted",
        "verified": False,
    }


def _view(row):
    result = dict(row)
    stored = {key: result.pop(key) for key in registry.SCOPE_COLUMNS}
    for key in registry.JSON_KEYS:
        if result[key] is not None:
            result[key] = json.loads(result[key])
    result.update(
        machine_id=result["id"],
        enabled=bool(result["enabled"]),
        verified=False,
        scope=_scope(stored),
        scope_epoch=result["binding_epoch"],
    )
    return result


def _refuse(h, status, error):
    send_json(h, status, {"error": error})
    raise ValueError(error)


def _read_attest(h):
    """Read an attest body strictly, replying before raising ValueError on refusal."""
    if h.headers.get("Transfer-Encoding"):
        _refuse(h, 400, "Invalid JSON body")
    try:
        length = int(h.headers.get("Content-Length", "0"))
    except ValueError:
        _refuse(h, 400, "Invalid JSON body")
    if length <= 0:
        _refuse(h, 400, "Invalid JSON body")
    if length > MAX_REPORT_BYTES:
        _refuse(h, 413, "attest body is too large")
    raw = h.rfile.read(length)
    if len(raw) != length:
        _refuse(h, 400, "Invalid JSON body")
    try:
        data = grant_scope.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError):
        _refuse(h, 400, "scope_invalid")
    if not isinstance(data, dict):
        _refuse(h, 400, "Invalid JSON body")
    if "grant_scope" not in data and length > MAX_CONFIRM_BYTES:
        _refuse(h, 413, "attest body is too large")
    return data


def _wings(gateway, owner, data):
    if "wings" not in data:
        return data
    wings = data["wings"]
    if not isinstance(wings, list) or not 1 <= len(wings) <= 8:
        raise ValueError("wings must contain 1 to 8 entries")
    try:
        wings = _read_wings(gateway, owner, {"read_wings": wings})
    except ValueError:
        raise ValueError("choose existing nonprivate workspace wings without patterns") from None
    return {**data, "wings": wings}


def register(gateway):
    def manage(h):
        owner = require_principal(gateway, h)
        if owner is None:
            return
        if not owner.person or owner.role != "owner":
            send_json(h, 403, {"error": "a signed-in workspace owner is required"})
            return
        parts = path_parts(h, "/api/runners")
        method = h.command
        if method == "GET" and not parts:
            send_json(
                h,
                200,
                {
                    "machines": [
                        _view(row) for row in registry.machines(gateway.db, owner.workspace_id)
                    ]
                },
            )
            return
        if not (
            (not parts and method == "POST")
            or (len(parts) == 1 and method in ("PATCH", "DELETE"))
            or (len(parts) == 2 and method == "POST" and parts[1] in ("attest", "rotate"))
        ):
            send_json(h, 404, {"error": "not found"})
            return
        if method != "DELETE":
            try:
                data = _read_attest(h) if parts[1:] == ["attest"] else read_json(h)
            except ValueError:
                return
        try:
            if not parts:
                result = registry.enroll(gateway.db, owner, _wings(gateway, owner, data))
                status = 201
            elif method == "PATCH":
                result = _view(
                    registry.patch(gateway.db, owner, parts[0], _wings(gateway, owner, data))
                )
                status = 200
            elif method == "DELETE":
                result = registry.revoke(gateway.db, owner, parts[0])
                status = 200
                # Authentication is already disabled even if workspace cleanup fails.
                try:
                    jobs.sweep(
                        jobs.open_store(gateway, owner.workspace_id), gateway.db, owner.workspace_id
                    )
                except Exception as exc:
                    log_failure(LOG, "Runner revoke cleanup deferred", exc)
            elif parts[1] == "attest":
                result = registry.attest(gateway.db, owner, parts[0], data)
                status = 200
            else:
                if set(data) != {"credential"} or data["credential"] not in ("machine", "worker"):
                    raise ValueError("credential must be machine or worker")
                result = registry.rotate(gateway.db, owner, parts[0], data["credential"])
                status = 200
        except registry.NotFound as exc:
            send_json(h, 404, {"error": str(exc)})
            return
        except registry.Conflict as exc:
            send_json(h, 409, {"error": str(exc)})
            return
        except (ValueError, ControlError) as exc:
            send_json(h, 400, {"error": str(exc)})
            return
        except ToolError:
            send_json(h, 503, {"error": "workspace wings are unavailable; try again"})
            return
        send_json(h, status, result)

    for method in ("GET", "POST"):
        gateway.add_route(method, "/api/runners", manage)
    for method in ("PATCH", "POST", "DELETE"):
        gateway.add_prefix_route(method, "/api/runners/", manage)
