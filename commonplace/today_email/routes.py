"""Email-for-Today settings route."""

from commonplace.http_util import read_json, require_principal, send_json

NOT_SET_UP = "no: email is not set up on this server"


def _view(notifier, principal):
    return {
        "available": notifier.available,
        "enabled": notifier.enabled(principal),
        "address": principal.user_email,
    }


def register(gateway):
    def route(h):
        principal = require_principal(gateway, h)
        if principal is None:
            return
        notifier = gateway.today_email
        if not principal.person:
            send_json(h, 403, {"error": "no: email for Today needs a signed-in person"})
            return
        if "?" in h.path:
            send_json(h, 400, {"error": "no: no query parameters"})
            return
        if h.command == "GET":
            send_json(h, 200, _view(notifier, principal))
            return
        try:
            data = read_json(h)
        except ValueError:
            return
        if (
            not isinstance(data, dict)
            or set(data) != {"enabled"}
            or type(data["enabled"]) is not bool
        ):
            send_json(h, 400, {"error": "no: send enabled as true or false"})
            return
        if data["enabled"] and not notifier.available:
            send_json(h, 409, {"error": NOT_SET_UP})
            return
        notifier.set_enabled(principal, data["enabled"])
        send_json(h, 200, _view(notifier, principal))

    gateway.add_route("GET", "/api/today-email", route)
    gateway.add_route("POST", "/api/today-email", route)
