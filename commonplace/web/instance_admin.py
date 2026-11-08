"""HTTP validation and safe projections for the separate instance console."""

import secrets
from urllib.parse import parse_qsl, unquote, urlsplit

from commonplace import instance_admin as service
from commonplace.http_util import require_principal, send_json
from commonplace.web import admin


def install(gateway):
    gateway.instance_cursor_key = secrets.token_bytes(32)

    def inventory(h):
        principal = require_principal(gateway, h)
        if principal is None:
            return
        context = getattr(h, "_instance_context", None) or admin.ReadContext(gateway.admin_clock)
        action, target = None, None
        try:
            if not service.flag(gateway, principal, context):
                send_json(h, 403, {"error": service.DENIAL})
                return
            raw = urlsplit(h.path)
            parts = raw.path.removeprefix(service.PREFIX).split("/")
            # IDs and route segments are canonical ASCII; encoded aliases never bind.
            if any(unquote(p) != p or p in ("", ".", "..") or "\\" in p for p in parts):
                raise ValueError()
            if (
                len(parts) == 1
                and parts[0] in service.ROUTES
                and parts[0] not in ("spaces", "workspace_settings")
            ):
                kind = parts[0]
            elif (
                len(parts) == 3 and parts[0] == "workspaces" and parts[2] in ("spaces", "settings")
            ):
                target = service.opaque(parts[1], "ws")
                kind = "workspace_settings" if parts[2] == "settings" else "spaces"
            else:
                send_json(h, 404, {"error": "not found"})
                return
            action = "instance_admin.read." + kind
            pairs = parse_qsl(
                raw.query, keep_blank_values=True, strict_parsing=True, max_num_fields=16
            )
            query = service.parse_query(pairs, kind, target)
            target = target or query.get("workspace_id")
            result = service.inventory(gateway, principal, context, kind, target, query)
            context.remaining()
        except (ValueError, LookupError) as exc:
            status, code = (
                (400, "invalid_request") if isinstance(exc, ValueError) else (404, "not_found")
            )
            result = {"error": code}
            decision = "refuse"
        except Exception:
            status, code, result, decision = (
                503,
                "source_unavailable",
                {"error": "source_unavailable"},
                "error",
            )
        else:
            status, code, decision = 200, "ok", "allow"
            result = {**result, "recorded_at": service.control_db._iso(context.now)}
        # Invalid path/query text is never an audit target; only validated IDs qualify.
        try:
            service.audit(
                gateway,
                principal,
                context,
                action or "instance_admin.denied",
                workspace=target,
                decision=decision,
                reason=code,
            )
        except Exception:
            send_json(
                h,
                503,
                {
                    "error": "source_unavailable"
                    if code == "source_unavailable"
                    else "audit_unavailable"
                },
            )
            return
        send_json(h, status, result)

    gateway.add_prefix_route("GET", service.PREFIX, inventory)
