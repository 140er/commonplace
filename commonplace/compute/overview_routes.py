"""``/api/compute/overview`` routes: reads for any member, actions for the owner.

``routes.handle`` has already authenticated the caller, decided owner or member for the method,
refused query strings and parsed the path; this module only maps the remaining path to
``overview`` calls. A write reads its body only after the record of this workspace is confirmed
to exist, and every refusal names a reason.
"""

from __future__ import annotations

from commonplace.http_util import send_json

from . import overview, workspaces
from .control import StoreError

_ACTION_FIELDS = {
    "workspace": {"expected_seq", "expected_version", "request_id"},
    "runner": {"expected_seq", "request_id"},
}
_OPTIONAL = {"workspace": {"request_id"}, "runner": {"request_id"}}


def dispatch(h, store, principal, parts, auth, body_reader):
    """``parts`` is the path after ``/api/compute/overview``."""
    if h.command == "GET":
        if not parts:
            return send_json(h, 200, overview.read(store, principal, **auth))
        if len(parts) == 2:
            return send_json(h, 200, overview.detail(store, principal, *parts, **auth))
        if len(parts) == 4 and parts[2] == "preview":
            return send_json(
                h, 200, overview.preview(store, principal, parts[0], parts[1], parts[3], **auth)
            )
    elif h.command == "POST" and len(parts) == 3:
        kind, item_id, action = parts
        if kind not in _ACTION_FIELDS or action not in (
            overview.WORKSPACE_ACTIONS if kind == "workspace" else overview.RUNNER_ACTIONS
        ):
            raise StoreError("not_found", 404, reason="route")
        overview.require(store, principal, kind, item_id, **auth)
        body = body_reader(h, _ACTION_FIELDS[kind], _OPTIONAL[kind])
        for key, value in body.items():
            if key != "request_id" and type(value) is not int:
                raise workspaces.refuse(key, "invalid")
        answer, repeated = overview.act(store, principal, kind, item_id, action, **auth, **body)
        accepted = kind == "workspace" and action != "withdraw" and not repeated
        return send_json(h, 202 if accepted else 200, {**answer, "repeated": repeated})
    raise StoreError("not_found", 404, reason="route")
