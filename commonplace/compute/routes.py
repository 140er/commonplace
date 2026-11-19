"""Cookie-person owner Compute REST v1. No remote effects or custody access.

The parent opts in by installing this module; importing it or calling install
only registers handlers. Every mutation uses the existing coworker boundary and
then rechecks live authority inside the Store transaction.
"""

from __future__ import annotations

import json
from urllib.parse import unquote, urlsplit

from commonplace import sessions
from commonplace.chat.coworkers.routes import _person, _write_boundary
from commonplace.chat.coworkers.store import StoreError as BoundaryError
from commonplace.control_db import ControlError
from commonplace.http_util import send_json

from .control import StoreError, open_store
from .fence import FenceDenied

_WORKSPACE_OPTIONAL = frozenset(
    "cpu_cores memory_mib storage_gib network node_id private_registry request_id".split()
)
_CREATE_FIELDS = {
    "gateways": frozenset(
        "board name address port account host_key credential_id credential_version pin_confirmed".split()
    ),
    "nodes": frozenset(
        "gateway_id name address port interface account host_key credential_id credential_version pin_confirmed".split()
    ),
    "scopes": frozenset(
        "gateway_id task_number agent_id cidrs interfaces methods expires_at limits".split()
    ),
    "executions": frozenset("node_id task_number operation arguments request_id".split()),
    "workspaces": frozenset("name image_source image".split()),
    "scripts": frozenset("node_id task_number request_id source arguments output_paths".split()),
}


def _fields(body, fields, *, optional=()):
    if set(body) - set(fields) or set(fields) - set(optional) - set(body):
        raise StoreError()
    return body


def _pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _constant(value):
    raise ValueError


def _body(h, maximum=8192):
    # The shared boundary already bounds/framing-checks Content-Length. Parse
    # here to preserve this API's fixed invalid_request code on every failure.
    try:
        length = int(h.headers.get("Content-Length", "0"))
        if not 0 < length <= maximum:
            raise ValueError
        raw = h.rfile.read(length)
        if len(raw) != length:
            raise ValueError
        body = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
        if type(body) is not dict:
            raise ValueError
        return body
    except (ValueError, UnicodeError, RecursionError):
        raise StoreError() from None


def _workspace_body(h, allowed, optional):
    """Parse a workspace write body; every refusal names a reason (and the field, when it is one)."""
    from . import workspaces

    try:
        body = _body(h)
    except StoreError:
        raise workspaces._bad(None, "body") from None
    if set(body) - set(allowed):
        raise workspaces._bad(None, "unknown_field")
    missing = sorted(set(allowed) - set(optional) - set(body))
    if missing:
        raise workspaces._bad(missing[0], "required")
    return body


def _workspaces(h, store, principal, parts, auth):
    """Cloud workspace records: reads for any member, writes for owners.

    The owner check runs before any body is read (``handle`` does it for every write), and a
    record of another workspace is concealed before the body of an action on it is read.
    """
    from . import workspaces

    if h.command == "GET":
        if not parts:
            return send_json(h, 200, store.list_workspaces(principal, **auth))
        if parts == ["images"]:
            return send_json(h, 200, workspaces.options())
        if len(parts) == 1:
            return send_json(h, 200, store.get_workspace(principal, parts[0], **auth))
    elif h.command == "POST":
        if not parts:
            body = _workspace_body(
                h, _CREATE_FIELDS["workspaces"] | _WORKSPACE_OPTIONAL, _WORKSPACE_OPTIONAL
            )
            item, created = store.create_workspace(principal, **auth, **body)
            return send_json(h, 201 if created else 200, {"workspace": item})
        if len(parts) == 1:
            store.get_workspace(principal, parts[0], **auth)
            body = _workspace_body(
                h, workspaces._EDITABLE | {"expected_revision"}, workspaces._EDITABLE
            )
            item = store.update_workspace(principal, parts[0], **auth, **body)
            return send_json(h, 200, {"workspace": item})
        if len(parts) == 2 and parts[1] == "discard":
            store.get_workspace(principal, parts[0], **auth)
            body = _workspace_body(h, {"expected_revision"}, ())
            item = store.discard_workspace(principal, parts[0], **auth, **body)
            return send_json(h, 200, {"discarded": item})
        if len(parts) == 2 and parts[1] == "credential":
            # Registry logins cannot be stored yet; the store refuses after the same checks.
            store.get_workspace(principal, parts[0], **auth)
            item = store.bind_workspace_credential(principal, parts[0], **auth)
            return send_json(h, 200, {"workspace": item})
    raise StoreError("not_found", 404)


def handle(gateway, handler):
    """Serve only the frozen /api/compute management surface."""
    h = handler
    try:
        principal, cookie = _person(gateway, h)
        # Reject ambiguous cookies in a single header too.
        cookies = h.headers.get("Cookie", "").split(";")
        if sum(part.strip().partition("=")[0] == sessions.COOKIE for part in cookies) != 1:
            raise StoreError("sign_in", 401)
        session_hash = sessions._hash(cookie)
        store = open_store(gateway, principal.workspace_id)
        path = urlsplit(h.path).path
        # Cloud workspace records are readable by every current member; all else is owner-only.
        member_read = h.command == "GET" and (
            path == "/api/compute/workspaces" or path.startswith("/api/compute/workspaces/")
        )
        with gateway.db._lock:
            (store._member if member_read else store._owner)(principal, session_hash)
        write = h.command != "GET"
        if write:
            _write_boundary(gateway, h)
        parsed = urlsplit(h.path)
        prefix = "/api/compute"
        tail = (
            parsed.path[len(prefix) :]
            if parsed.path == prefix or parsed.path.startswith(prefix + "/")
            else None
        )
        if tail is None or tail.endswith("/") or "//" in tail:
            raise StoreError("not_found", 404)
        if parsed.query or parsed.fragment:
            raise StoreError()
        parts = [unquote(part) for part in tail.split("/") if part]
        if any(not part or "/" in part for part in parts):
            raise StoreError("not_found", 404)
        auth = {"session_hash": session_hash}
        if parts and parts[0] == "workspaces":
            return _workspaces(h, store, principal, parts[1:], auth)
        if h.command == "GET":
            if not parts:
                return send_json(h, 200, store.inventory(principal, **auth))
            if parts == ["credentials"]:
                return send_json(h, 200, store.list_credentials(principal, **auth))
            if len(parts) == 1 and parts[0] in _CREATE_FIELDS:
                kind = parts[0]
                return send_json(h, 200, {kind: getattr(store, "list_" + kind)(principal, **auth)})
            if len(parts) == 2 and parts[0] == "scripts":
                return send_json(h, 200, {"script": store.get_script(principal, parts[1], **auth)})
        if h.command == "POST":
            if len(parts) == 1 and parts[0] in _CREATE_FIELDS:
                kind = parts[0]
                body = _fields(
                    _body(h, 65_536 if kind == "scripts" else 8192),
                    _CREATE_FIELDS[kind],
                    optional=("agent_id",)
                    if kind == "scopes"
                    else ("credential_id", "credential_version")
                    if kind in ("gateways", "nodes")
                    else (),
                )
                if kind == "scopes" and "agent_id" in body and type(body["agent_id"]) is not str:
                    raise StoreError()
                if kind == "executions":
                    execution, created = store.propose_execution(principal, **auth, **body)
                    return send_json(h, 201 if created else 200, {"execution": execution})
                if kind == "scripts":
                    script, created = store.create_script(principal, **auth, **body)
                    return send_json(h, 201 if created else 200, {"script": script})
                singular = kind[:-1]
                item = getattr(store, "create_" + singular)(principal, **auth, **body)
                return send_json(h, 201, {singular: item})
            if len(parts) == 3 and parts[0] in ("gateways", "nodes") and parts[2] == "credential":
                kind, object_id, _ = parts
                singular = kind[:-1]
                # Conceal foreign IDs before inspecting the body, as for the other actions.
                getattr(store, "get_" + singular)(principal, object_id, **auth)
                body = _fields(_body(h), ("credential_id", "credential_version"))
                item = store.bind_credential(principal, kind, object_id, **auth, **body)
                return send_json(h, 200, {singular: item})
            if len(parts) == 3 and parts[0] in ("scopes", "executions"):
                kind, object_id, action = parts
                if (kind, action) not in (
                    ("scopes", "revoke"),
                    ("executions", "approve"),
                    ("executions", "cancel"),
                ):
                    raise StoreError("not_found", 404)
                singular = kind[:-1]
                # Conceal foreign IDs before inspecting subordinate body fields.
                getattr(store, "get_" + singular)(principal, object_id, **auth)
                fields = ("expected_digest", "expires_at") if action == "approve" else ()
                body = _fields(_body(h), fields)
                method = {
                    "revoke": store.revoke_scope,
                    "approve": store.approve_execution,
                    "cancel": store.cancel_execution,
                }[action]
                return send_json(h, 200, {singular: method(principal, object_id, **auth, **body)})
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
    except FenceDenied:
        h.close_connection = True
        return send_json(h, 503, {"error": "authority_unavailable"})
    except ControlError:
        h.close_connection = True
        return send_json(h, 401, {"error": "sign_in"})
    except StoreError as exc:
        if h.command != "GET":
            h.close_connection = True
        detail = {
            key: value for key, value in (("field", exc.field), ("reason", exc.reason)) if value
        }
        return send_json(h, exc.status, {"error": exc.code, **detail})


def install(gateway):
    """Register inert REST handlers only; extension-list opt-in is parent-owned."""
    from . import discovery_routes

    for method in ("GET", "POST"):
        gateway.add_route(method, "/api/compute", lambda h: handle(gateway, h))
        gateway.add_prefix_route(method, "/api/compute/", lambda h: handle(gateway, h))
    discovery_routes.install(gateway)
