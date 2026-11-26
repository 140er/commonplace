"""Person-session routes for project orchestrators and model preferences."""

import sqlite3

from commonplace import redact
from commonplace.boards.service import BoardService, WingRefused, project_wing, shared_wing
from commonplace.boards.store import CardRefused
from commonplace.control_db import ControlError
from commonplace.extensions import ToolContext, ToolError
from commonplace.http_util import path_parts, read_json, require_principal, send_json
from commonplace.projects import (
    connect,
    escalation,
    limits,
    model_modes,
    models,
    orchestration,
    panel_routes,
    readiness,
    seats,
    wing,
)
from commonplace.projects.types import ProjectDenied
from commonplace.rules.enforce import RulesRefresh, rest_write
from commonplace.runner import registry


def _person(gateway, h):
    principal = require_principal(gateway, h)
    if principal is not None and not principal.person:
        send_json(h, 403, {"error": "Sign in to view projects"})
        return None
    return principal


def _may_write(service, board):
    """Whether this person may write the board: the rule the panel write route enforces."""
    try:
        service._write(board)
    except ToolError:
        return False
    return True


def _effective(gateway, principal, slug):
    """Each role's model and source, and (model modes, card #617) the account it would run on for
    the caller: mode, where the mode comes from, account kind, the caller's own label and (card
    #661) whether the caller has linked their own account for that role's provider."""
    return _with_accounts(
        gateway, principal, slug, models.effective_roles(gateway.db, principal.workspace_id, slug)
    )


def _with_accounts(gateway, principal, slug, roles):
    view = {role: effective.public() for role, effective in roles.items()}
    if getattr(gateway, "runner_installed", False):
        try:
            accounts = model_modes.effective_for(
                gateway.db,
                principal.workspace_id,
                slug,
                principal.user_id if principal.person else None,
                {role: e.choice.provider if e.choice else None for role, e in roles.items()},
                linked_for=principal.user_id,
            )
        except (model_modes.ModeUnavailable, sqlite3.Error):
            accounts = {}
        for role, account in accounts.items():
            view[role]["account"] = account
    return view


def workspace_models_view(gateway, principal) -> dict:
    """The workspace's default model per role (card #661) in the project view's shape."""
    db, ws = gateway.db, principal.workspace_id
    listed = models.catalog(db, ws, models.WORKSPACE)
    return {
        "can_configure": principal.person and principal.role == "owner",
        "models": {
            role: choice.public() if choice is not None else None
            for role, choice in models.get(db, ws, models.WORKSPACE).items()
        },
        "models_effective": _with_accounts(
            gateway, principal, None, models.workspace_roles(db, ws)
        ),
        "model_catalog": [choice.public() for choice in listed],
        "model_suggestions": [choice.public() for choice in models.suggestions(listed)],
    }


def workspace_models_route(gateway, h):
    """``GET|PUT|POST /api/settings/models``: any signed-in person reads the workspace's default
    model per role; only the signed-in owner changes it (``PUT``) or adds a model to the
    workspace's list (``POST``). Anyone else gets the 404 of an unknown route, so the setting's
    editability is not shown. Writes are audited (``models.workspace``, ``project.model_catalog``)."""
    principal = require_principal(gateway, h)
    if principal is None:
        return
    if not principal.person or (h.command != "GET" and principal.role != "owner"):
        send_json(h, 404, {"error": "not found"})
        return
    if h.command in ("PUT", "POST"):
        try:
            body = read_json(h)
        except ValueError:
            return
        if isinstance(body, dict):
            body.pop("rules_seq", None)
        try:
            if h.command == "PUT":
                models.put_workspace(gateway.db, principal, body)
            else:  # add one model to the workspace's own list
                models.catalog_add(gateway.db, principal, models.WORKSPACE, body)
        except models.UnknownModel:
            send_json(h, 400, {"error": "Unknown model", "code": "unknown_model"})
            return
        except ProjectDenied:
            send_json(h, 404, {"error": "not found"})
            return
        except sqlite3.OperationalError:
            send_json(h, 409, {"error": "busy", "code": "busy"})
            return
        except ValueError:
            send_json(h, 400, {"error": "Invalid models"})
            return
    send_json(h, 200, workspace_models_view(gateway, principal))


def _view(gateway, principal, board):
    service = BoardService(ToolContext(principal=principal, gateway=gateway))
    with service.store.connection() as conn:
        project_limits = limits.get(conn, board["id"])
    choices = models.get(gateway.db, principal.workspace_id, board["slug"])
    listed = models.catalog(gateway.db, principal.workspace_id, board["slug"])
    rule = escalation.get(gateway.db, principal.workspace_id, board["slug"])
    result = {
        "board": board["slug"],
        "memory": {
            "wing": board["wing"],
            "shared": shared_wing(board["wing"]) and not any(c in board["wing"] for c in "*?[]"),
            "own_wing": project_wing(board["slug"]),
        },
        "name": redact.redact(board["name"])[0],
        "main": seats.main(gateway, principal, board["slug"]).public(),
        "limits": project_limits,
        "models": {
            role: choice.public() if choice is not None else None
            for role, choice in choices.items()
        },
        "models_effective": _effective(gateway, principal, board["slug"]),
        "model_catalog": [choice.public() for choice in listed],
        "model_suggestions": [choice.public() for choice in models.suggestions(listed)],
        "escalation": rule.public() if rule is not None else None,
        "can_configure": principal.person and principal.role == "owner",
        "can_edit_panel": _may_write(service, board),
    }
    from commonplace.runner import sessions

    result["session"] = sessions.status(gateway, principal.workspace_id, board["slug"])
    if not result["can_configure"]:
        result["session"] = {
            k: v for k, v in result["session"].items() if k in ("state", "reason", "pending")
        }
    if orchestration.enabled(gateway.db, principal.workspace_id):
        seat = seats.ensure_main(gateway.db, principal.workspace_id, board["slug"])
        live = seats.active_connection(gateway.db, principal.workspace_id, seat.connection_id)
        from commonplace.policy import Policy

        result["setup"] = {
            "webhook_add": {"events": "space_message,card.created", "board": board["slug"]}
        }
        result["orchestration"] = {
            "default_max_agents": orchestration.DEFAULT_MAX_AGENTS,
            "trusted": bool(live and Policy.from_json(live["policy"]).trust == "trusted"),
        }
        if result["can_configure"]:
            result["orchestration"]["machines"] = [
                {"id": m["id"], "label": redact.redact(m["label"])[0]}
                for m in registry.machines(gateway.db, principal.workspace_id)
                if m["enabled"] and not m["revoked_at"]
            ]
    result["readiness"] = readiness.snapshot(gateway, principal, board["slug"], main=result["main"])
    return result


def orchestration_route(gateway, h):
    principal = _person(gateway, h)
    if principal is None:
        return
    if h.command != "GET" and principal.role != "owner":
        send_json(h, 403, {"error": "Only workspace owners can configure Coworker management"})
        return
    try:
        if h.command == "GET":
            payload = {"enabled": orchestration.enabled(gateway.db, principal.workspace_id)}
        else:
            try:
                body = read_json(h)
            except ValueError:
                return
            if set(body) != {"enabled"}:
                raise ValueError("Invalid Coworker management settings")
            payload = orchestration.set_enabled(gateway.db, principal, body["enabled"])
    except orchestration.AdmissionRefused as exc:
        send_json(h, exc.status, {"error": exc.code})
        return
    except ValueError:
        send_json(h, 400, {"error": "Invalid Coworker management settings"})
        return
    send_json(h, 200, payload)


def _split_route(gateway, h, principal, parts, *, inboxing):
    from commonplace.projects import split

    try:
        if inboxing:
            payload = split.inbox(gateway, principal, parts[0])
        else:
            body = read_json(h)
            body.pop("rules_seq", None)
            if (
                set(body) != {"task", "state"}
                or type(body["task"]) is not int
                or not 1 <= body["task"] < 2**63
            ):
                raise CardRefused("invalid_arguments", status=400)
            # Bind the task reference to this authorized board before mutation.
            split.inbox(gateway, principal, parts[0])
            with orchestration.board_read(gateway.workspace_dir(principal.workspace_id)) as conn:
                row = conn.execute(
                    "SELECT b.slug FROM tasks t JOIN boards b ON b.id=t.board_id WHERE t.id=?",
                    (body["task"],),
                ).fetchone()
                if row is None or row[0] != parts[0]:
                    raise CardRefused("card_not_found", status=404)
            with rest_write(ToolContext(principal, gateway), h, "card_split_done", body):
                payload = split.mark(gateway, principal, body["task"], body["state"])
    except sqlite3.OperationalError as exc:
        orchestration.require_lock_timeout(exc)
        send_json(h, 409, {"error": "busy", "code": "busy"})
        return
    except RulesRefresh as exc:
        send_json(h, 428, exc.payload)
        return
    except CardRefused as exc:
        send_json(
            h, exc.status, {"error": str(exc).removeprefix("no: "), "code": exc.code, **exc.details}
        )
        return
    except (ValueError, TypeError):
        send_json(h, 400, {"error": "invalid_arguments"})
        return
    except orchestration.SettingsUnavailable as exc:
        send_json(h, exc.status, {"error": exc.code, "code": exc.code})
        return
    except ToolError:
        send_json(h, 403, {"error": "not_allowed"})
        return
    send_json(h, 200, payload)


def _project_action(method, parts):
    if len(parts) == 1 and method == "GET":
        return "view"
    routes = {
        ("POST", ("orchestrator", "stop")): "stop",
        ("POST", ("orchestrator", "resume")): "resume",
        ("POST", ("orchestrator", "connect")): "connect",
        ("POST", ("orchestrator", "trust")): "trust",
        ("GET", ("orchestrator", "inbox")): "inbox",
        ("POST", ("orchestrator", "split")): "split",
        ("PUT", ("models",)): "models",
        ("POST", ("models", "catalog")): "model_catalog",
        ("PUT", ("escalation",)): "escalation",
        ("PUT", ("limits",)): "limits",
        ("POST", ("wing",)): "wing",
        ("GET", ("panel",)): "panel_view",
        ("PUT", ("panel",)): "panel_set",
    }
    return routes.get((method, tuple(parts[1:])))


def _valid_project_body(h, action, body):
    if action in ("wing", "stop", "resume") and (not isinstance(body, dict) or body):
        send_json(
            h,
            400,
            {
                "error": "Own-wing body must be exactly {}"
                if action == "wing"
                else "Session control body must be exactly {}"
            },
        )
        return False
    if action == "escalation" and (not isinstance(body, dict) or set(body) != {"rule"}):
        send_json(h, 400, {"error": "Invalid escalation rule"})
        return False
    if action == "connect" and (
        not isinstance(body, dict)
        or set(body) - {"replace", "allow_shared"}
        or any(type(value) is not bool for value in body.values())
    ):
        send_json(h, 400, {"error": "Invalid project connection settings"})
        return False
    return True


def _project_result(gateway, principal, service, board, action, body):
    slug = board["slug"]
    seats.ensure_main(gateway.db, principal.workspace_id, slug)
    if action in ("stop", "resume"):
        from commonplace.runner import sessions

        return 200, sessions.control(gateway, principal, slug, action)
    if action == "connect":
        return connect.connect(
            gateway,
            principal,
            slug,
            replace=body.get("replace", False),
            allow_shared=body.get("allow_shared", False),
        )
    if action == "trust":
        return 200, connect.trust(gateway, principal, slug, body)
    if action == "wing":
        result = wing.own_wing(gateway, principal, slug)
        with service.store.connection() as conn:
            board = service._board(conn, slug)
        return 200, {**_view(gateway, principal, board), **result}
    if action == "limits":
        with service.transaction() as conn:
            limits.put(conn, board["id"], body)
    if action == "models":
        models.put(gateway.db, principal, slug, body)
    if action == "model_catalog":
        models.catalog_add(gateway.db, principal, slug, body)
    if action == "escalation":
        escalation.put(gateway.db, principal, slug, body["rule"])
    return 200, _view(gateway, principal, board)


def _project_route(gateway, h, principal, parts, action):
    if action is None:
        send_json(h, 404, {"error": "Not found"})
        return
    if action != "view" and principal.role != "owner":
        send_json(h, 403, {"error": "Only workspace owners can configure projects"})
        return
    try:
        service = BoardService(ToolContext(principal=principal, gateway=gateway))
        with service.store.connection() as conn:
            board = service._board(conn, parts[0])
        body = None
        if action != "view":
            try:
                body = read_json(h)
            except ValueError:
                return
            if not _valid_project_body(h, action, body):
                return
        status, payload = _project_result(gateway, principal, service, board, action, body)
    except ProjectDenied:
        send_json(h, 403, {"error": "Project settings not allowed"})
        return
    except WingRefused as exc:
        send_json(h, 409, exc.payload)
        return
    except registry.NotFound:
        send_json(h, 404, {"error": "Machine not found"})
        return
    except sqlite3.OperationalError as exc:
        orchestration.require_lock_timeout(exc)
        send_json(h, 409, {"error": "busy", "code": "busy"})
        return
    except ControlError:
        send_json(h, 409, {"error": "Trust settings conflict"})
        return
    except orchestration.AdmissionRefused as exc:
        send_json(h, exc.status, {"error": str(exc).removeprefix("no: "), "code": exc.code})
        return
    except models.UnknownModel:
        send_json(
            h,
            400,
            {
                "error": "That model is not in this workspace's list. Pick one from the list "
                "or add it first.",
                "code": "unknown_model",
            },
        )
        return
    except ValueError:
        send_json(h, 400, {"error": "Invalid project settings"})
        return
    except ToolError as exc:
        missing = str(exc).removeprefix("no: ") == "board not found"
        send_json(
            h, 404 if missing else 403, {"error": "Board not found" if missing else "Forbidden"}
        )
        return
    send_json(h, status, payload)


def _route(gateway, h):
    parts = path_parts(h, "/api/projects/")
    action = _project_action(h.command, parts)
    if action in ("panel_view", "panel_set"):
        panel_routes.handle(gateway, h, parts, action)
        return
    if (parts[1:] == ["learning"] and h.command in ("GET", "PATCH")) or (
        parts[1:] == ["learned"] and h.command == "GET"
    ):
        from commonplace.bots import learned_routes

        learned_routes.project_route(gateway, h, parts[0], parts[1:])
        return
    splitting = action in ("inbox", "split")
    principal = require_principal(gateway, h) if splitting else _person(gateway, h)
    if principal is None:
        return
    if splitting:
        _split_route(gateway, h, principal, parts, inboxing=action == "inbox")
    else:
        _project_route(gateway, h, principal, parts, action)


def install(gateway) -> None:
    for method in ("GET", "PUT"):
        gateway.add_route(
            method, "/api/settings/orchestration", lambda h: orchestration_route(gateway, h)
        )
    for method in ("GET", "PUT", "POST"):
        gateway.add_route(
            method, "/api/settings/models", lambda h: workspace_models_route(gateway, h)
        )
    for method in ("GET", "POST", "PUT", "PATCH"):
        gateway.add_prefix_route(method, "/api/projects/", lambda h: _route(gateway, h))
