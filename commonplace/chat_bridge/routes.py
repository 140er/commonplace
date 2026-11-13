"""``/api/bridge``: the chat-platform bridge. Install management is for the signed-in workspace
owner; the intake routes are public but answer 404 until a later slice gives them an adapter.

    GET    /api/bridge/installs             {installs, platforms}
    POST   /api/bridge/installs             {platform, ext_team, label, secrets, daily_cap?} -> 201
    PATCH  /api/bridge/installs/<id>        {enabled?, label?, daily_cap?, secrets?} -> {install}
    DELETE /api/bridge/installs/<id>        {ok}
    POST   /api/bridge/installs/<id>/test   {ok, code, text}
    POST   /api/bridge/in/<id>/events       intake (404 for now)
    POST   /api/bridge/in/<id>/interact     intake (404 for now)
    GET    /api/bridge/in/<id>/oauth        intake (404 for now)

Anything else under ``/api/bridge/`` is 404, and so is every owner route for a caller who is not
the signed-in owner (agents, tokens, members): the feature does not exist for them. A request
never creates ``chat_bridge.sqlite3``; only creating an install does. No response carries the
install's credentials, which are write-only.
"""

from commonplace.chat_bridge import adapter, service, store
from commonplace.http_util import path_parts, read_json, send_json

PREFIX = "/api/bridge/"
INSTALLS = "/api/bridge/installs"
CREATE_KEYS = {"platform", "ext_team", "label", "secrets", "daily_cap"}
PATCH_KEYS = {"enabled", "label", "daily_cap", "secrets"}


def _error(h, status, code, text):
    send_json(h, status, {"error": text, "code": code})


def _not_found(h):
    _error(h, 404, "not_found", "Not found")


def _owner(gateway, h):
    """The signed-in owner's principal, or None after the reply (401 anonymous, 404 anyone else)."""
    principal = gateway.principal_for(h)
    if principal is None:
        send_json(h, 401, {"error": "Sign in"})
        return None
    if not principal.person or principal.role != "owner":
        _not_found(h)
        return None
    return principal


def _body(h, allowed):
    try:
        body = read_json(h)
    except ValueError:
        return None
    if not set(body) <= allowed:
        _error(h, 400, "invalid", "Invalid request")
        return None
    return body


def _listing(gateway, h, principal, rest, body=None):
    bridge = store.open_bridge(gateway, principal.workspace_id)
    send_json(h, 200, {"installs": bridge.installs(), "platforms": sorted(adapter.ADAPTERS)})


def _create(gateway, h, principal, rest, body):
    send_json(h, 201, {"install": service.create(gateway, principal, body)})


def _patch(gateway, h, principal, rest, body):
    send_json(h, 200, {"install": service.update(gateway, principal, rest[0], body)})


def _delete(gateway, h, principal, rest, body=None):
    service.delete(gateway, principal, rest[0])
    send_json(h, 200, {"ok": True})


def _test(gateway, h, principal, rest, body=None):
    send_json(h, 200, service.test(gateway, principal, rest[0]))


# (method, path parts after /installs, tail) -> (handler, body keys or None)
OWNER = {
    ("GET", 0, None): (_listing, None),
    ("POST", 0, None): (_create, CREATE_KEYS),
    ("PATCH", 1, None): (_patch, PATCH_KEYS),
    ("DELETE", 1, None): (_delete, None),
    ("POST", 2, "test"): (_test, None),
}


def _owner_route(gateway, h, method):
    principal = _owner(gateway, h)
    if principal is None:
        return None
    parts = path_parts(h, INSTALLS)
    entry = OWNER.get((method, len(parts), parts[1] if len(parts) == 2 else None))
    if entry is None:
        return _not_found(h)
    handler, keys = entry
    body = None
    if keys is not None:
        body = _body(h, keys)
        if body is None:
            return None
    try:
        handler(gateway, h, principal, parts, body)
    except service.Refused as refused:
        _error(h, refused.status, refused.code, refused.text)
    except store.Missing:
        _not_found(h)
    except TimeoutError:
        _error(h, 429, "busy", "Busy, try again")


def _intake(h):
    """Public intake, and every other unknown path: until an adapter can verify a request there
    is nothing to answer, and nothing that tells an unknown install from a known one."""
    h.close_connection = True
    _not_found(h)


def install(gateway) -> None:
    for method in ("GET", "POST", "PATCH", "DELETE"):
        gateway.add_prefix_route(
            method, INSTALLS + "/", lambda h, m=method: _owner_route(gateway, h, m)
        )
        gateway.add_prefix_route(method, PREFIX, lambda h: _intake(h))
