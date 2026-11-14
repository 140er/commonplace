"""Read-only phone projection of the person's existing web change feed."""

import base64
import hashlib
import hmac
import json
import re
from urllib.parse import parse_qsl, urlsplit

from commonplace import events, person_actions
from commonplace.boards._http import HTTPError
from commonplace.chat.phone_list import _instant, preview
from commonplace.http_util import send_json

MAX_LIMIT = 100
DEFAULT_LIMIT = 50


def _signature(key, binding, value):
    return hmac.new(key, json.dumps([binding, value]).encode(), hashlib.sha256).hexdigest()


def _encode(key, binding, seq, epoch):
    value = [1, seq, epoch]
    return (
        base64.urlsafe_b64encode(
            json.dumps([value, _signature(key, binding, value)], separators=(",", ":")).encode()
        )
        .decode()
        .rstrip("=")
    )


def _decode(key, binding, token):
    try:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,512}", token):
            raise ValueError
        value, signature = json.loads(
            base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True)
        )
        if (
            not isinstance(value, list)
            or len(value) != 3
            or value[0] != 1
            or type(value[1]) is not int
            or not 0 <= value[1] <= 9223372036854775807
            or not isinstance(value[2], str)
            or not isinstance(signature, str)
            or not hmac.compare_digest(signature, _signature(key, binding, value))
        ):
            raise ValueError
        return value[1], value[2]
    except (ValueError, TypeError, UnicodeError):
        raise HTTPError(400, "Invalid event cursor") from None


def _event(event, key, binding):
    data = event["data"]
    number = str(data.get("number", ""))
    board = data.get("board")
    if re.fullmatch(r"[1-9][0-9]{0,17}", number):
        target = "#/card/" + number
    elif isinstance(board, str) and re.fullmatch(r"[a-z0-9][a-z0-9_-]*", board):
        target = "#/space/" + board
    else:
        target = "#/"
    title = data.get("title")
    return {
        "id": "evt_" + _signature(key, binding, ["event", event["seq"]])[:32],
        "kind": event["kind"],
        "at": _instant(event["at"]).isoformat().replace("+00:00", "Z"),
        "summary": preview(title if isinstance(title, str) else event["kind"]),
        "target": target,
    }


def handle(gateway, h):
    """Return whether this is a consented phone request; other callers keep web policy."""
    try:
        app, person, origin = person_actions.authority(gateway, h, person_actions.EVENTS_CAPABILITY)
    except HTTPError:
        return False
    try:
        pairs = parse_qsl(urlsplit(h.path).query, keep_blank_values=True)
        params = dict(pairs)
        if len(params) != len(pairs) or set(params) - {"cursor", "limit"}:
            raise HTTPError(400, "Only cursor and limit are accepted")
        limit = params.get("limit", str(DEFAULT_LIMIT))
        if not re.fullmatch(r"[1-9][0-9]{0,2}", limit) or int(limit) > MAX_LIMIT:
            raise HTTPError(400, "Invalid event limit")
        with gateway.db._lock:
            row = gateway.db._conn.execute(
                "SELECT cursor_secret FROM oauth_person_grants WHERE connection_id=? "
                "AND capability='events:person' AND revoked_at IS NULL",
                (app.connection_id,),
            ).fetchone()
        if row is None or not row[0]:
            raise HTTPError(403, "Workspace event access required")
        key = bytes.fromhex(row[0])
        binding = [person.user_id, person.workspace_id, origin["client_id"], app.connection_id]
        seq, epoch = _decode(key, binding, params["cursor"]) if "cursor" in params else (0, "")
        page = events.person_page(
            gateway.db, gateway, person, seq, epoch, int(limit), initial="cursor" not in params
        )
        response = {
            "events": [_event(event, key, binding) for event in page["events"]],
            "cursor": _encode(key, binding, page["seq"], page["epoch"]),
            "has_more": page["has_more"],
            "reset": page["reset"],
        }
        # Consent and current membership may change while the source is being read.
        person_actions.authority(gateway, h, person_actions.EVENTS_CAPABILITY)
    except HTTPError as exc:
        send_json(h, exc.status, {"error": str(exc)})
        return True
    send_json(h, 200, response)
    return True
