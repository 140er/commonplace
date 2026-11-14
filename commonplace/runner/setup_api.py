"""Narrow dispatch beneath existing private prefixes; fixed refusal envelopes."""

from urllib.parse import parse_qs, urlsplit

from cryptography.exceptions import InvalidSignature

from commonplace.http_util import path_parts, send_json

from . import enrollment, enrollment_api, install_receipts, registry, setup_approvals as consent


def _reply(h, operation):
    try:
        result = operation()
    except (enrollment.Refused, registry.NotFound):
        send_json(h, 404, {"error": "not found"})
    except (registry.Conflict, install_receipts.ApprovalRequired):
        send_json(h, 409, {"error": "setup approval unavailable; review current settings"})
    except (ValueError, InvalidSignature, TypeError, KeyError):
        send_json(h, 400, {"error": "invalid setup request"})
    except Exception:
        send_json(h, 503, {"error": "setup unavailable"})
    else:
        send_json(h, 200, result)


def machine_dispatch(gateway, h):
    principal = gateway.principal_for(h)
    url = urlsplit(h.path)
    if enrollment_api.preflight(gateway, h, url.path, principal):
        return

    def operation():
        if url.path == consent.MACHINE_PATHS[0] and h.command == "GET":
            query = parse_qs(url.query, keep_blank_values=True)
            if set(query) != {"nonce"} or len(query["nonce"]) != 1:
                raise ValueError
            return consent.fetch(gateway, principal, query["nonce"][0])
        if h.command != "POST" or url.query:
            raise enrollment.Refused(404)
        body = enrollment_api._body(h)
        if not isinstance(body, dict):
            raise ValueError
        if url.path == consent.MACHINE_PATHS[1]:
            return consent.delivery(gateway, principal, body)
        if body.get("action") == "device_code":
            return consent.present(gateway, principal, body)
        return consent.report(gateway, principal, body)

    _reply(h, operation)


def owner_dispatch(gateway, h):
    if not urlsplit(h.path).path.startswith("/api/runners/") or "/setup-requests/" not in h.path:
        return False
    from commonplace.gateway import _session_write_allowed

    owner = gateway.principal_for(h)
    if enrollment_api.preflight(gateway, h, urlsplit(h.path).path, owner):
        return True
    if not _session_write_allowed(gateway, h):
        send_json(h, 403, {"error": "JSON and same-origin request required"})
        return True

    def operation():
        parts = path_parts(h, "/api/runners")
        if (
            len(parts) != 4
            or parts[1] != "setup-requests"
            or parts[3] not in ("decide", "device", "refuse")
            or h.command != "POST"
            or urlsplit(h.path).query
        ):
            raise enrollment.Refused(404)
        if parts[3] == "device":
            if enrollment_api._body(h) != {}:
                raise ValueError
            return consent.reveal(gateway, owner, parts[0], parts[2])
        if parts[3] == "refuse":
            return consent.refuse(gateway, owner, parts[0], parts[2], enrollment_api._body(h))
        return consent.decide(gateway, owner, parts[0], parts[2], enrollment_api._body(h))

    _reply(h, operation)
    return True


def check_final_write(gateway, h, owner, data):
    if "setup_attestation" not in data:
        return
    from commonplace.gateway import _session_write_allowed

    enrollment.owner_now(gateway.db, owner)
    if not _session_write_allowed(gateway, h):
        raise PermissionError("JSON and same-origin request required")
