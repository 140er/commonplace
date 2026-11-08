"""Dedicated, audited management metadata reads; never membership or content authority."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re

from commonplace import control_db, plans, ratelimit
from commonplace.policy import Policy
from commonplace.web import admin

PREFIX = "/api/instance-admin/"
DENIAL = "Instance administrator access required"
ROUTES = (
    "workspaces",
    "people",
    "connections",
    "spaces",
    "settings",
    "workspace_settings",
    "audit",
)
ACTIONS = frozenset("instance_admin.read." + name for name in ROUTES) | {"instance_admin.denied"}
DECISIONS = frozenset(
    ("allow", "deny", "refuse", "unchanged", "replay", "intent", "error", "pending_audit")
)
STATES = ("active", "revoked", "expired", "disabled", "paused", "invalid")
ID = re.compile(r"(?:ws|usr|conn)_[a-f0-9]{16}")


def flag(gateway, principal, context):
    """One bounded exact flag lookup per request, shared with operational navigation."""
    if principal is None or not principal.person or principal.auth != "session":
        return False
    if not hasattr(context, "instance_flag"):
        with admin.read_control(gateway.db, context) as conn:
            row = admin.select(
                conn, context, "SELECT is_admin FROM users WHERE id=?", (principal.user_id,)
            ).fetchone()
        context.instance_flag = row is not None and row["is_admin"] == 1
    return context.instance_flag


def audit(gateway, principal, context, action, *, workspace=None, decision="allow", reason="ok"):
    gateway.db.audit_bounded(
        context,
        action=action,
        decision=decision,
        reason=reason,
        workspace_id=workspace,
        user_id=principal.user_id,
        connection_id=principal.connection_id or None,
        auth=principal.auth,
        target_ids=[]
        if decision in ("deny", "refuse")
        else ["workspace:" + workspace]
        if workspace
        else ["instance"],
    )


def opaque(value, prefix=None):
    if (
        not isinstance(value, str)
        or not ID.fullmatch(value)
        or prefix
        and not value.startswith(prefix + "_")
    ):
        raise ValueError()
    return value


def parse_query(pairs, kind, workspace):
    allowed = set() if kind == "settings" else {"limit", "cursor"}
    if kind in ("workspaces", "people", "connections", "audit"):
        allowed.add("workspace_id")
    if kind == "people":
        allowed |= {"user_id", "role", "disabled"}
    elif kind == "connections":
        allowed |= {"user_id", "state"}
    elif kind == "audit":
        allowed |= {"actor_user_id", "actor_connection_id", "action", "decision", "from", "to"}
    if len(pairs) != len(dict(pairs)) or any(k not in allowed for k, v in pairs):
        raise ValueError()
    query = dict(pairs)
    for key in ("workspace_id", "user_id", "actor_user_id", "actor_connection_id"):
        if key in query:
            opaque(
                query[key],
                "ws"
                if key == "workspace_id"
                else "conn"
                if key == "actor_connection_id"
                else "usr",
            )
    if workspace and "workspace_id" in query and workspace != query["workspace_id"]:
        raise LookupError()
    for key, values in (
        ("role", ("owner", "member")),
        ("disabled", ("true", "false")),
        ("state", STATES),
        ("action", ACTIONS),
        ("decision", DECISIONS),
    ):
        if key in query and query[key] not in values:
            raise ValueError()
    for key in ("from", "to"):
        if key in query:
            value = query[key]
            if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value) or not admin.timestamp(
                value
            ):
                raise ValueError()
    if "from" in query and "to" in query and query["from"] > query["to"]:
        raise ValueError()
    limit = query.get("limit", "50")
    if not re.fullmatch(r"[1-9]\d{0,2}", limit) or int(limit) > 100:
        raise ValueError()
    query["limit"] = int(limit)
    return query


def cursor(gateway, value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    signature = hmac.digest(gateway.instance_cursor_key, raw, "sha256")
    return base64.urlsafe_b64encode(signature + raw).decode().rstrip("=")


def uncursor(gateway, text, binding):
    if not isinstance(text, str) or len(text) > 2048 or not re.fullmatch(r"[A-Za-z0-9_-]+", text):
        raise ValueError()
    try:
        raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
        if not hmac.compare_digest(
            raw[:32], hmac.digest(gateway.instance_cursor_key, raw[32:], "sha256")
        ):
            raise ValueError()
        data = json.loads(raw[32:])
        if (
            not isinstance(data, list)
            or len(data) != 4
            or data[0] != binding
            or not isinstance(data[1], str)
            or type(data[2]) is not int
            or type(data[3]) is not int
        ):
            raise ValueError()
        return data[1:]
    except (TypeError, UnicodeError, json.JSONDecodeError):
        raise ValueError() from None


def page(
    gateway, context, conn, binding, query, sql, args, key, project, *, ceiling=0, descending=False
):
    after, offset = "", 0
    if query.get("cursor"):
        after, offset, pinned = uncursor(gateway, query["cursor"], binding)
        if pinned != ceiling:
            raise ValueError()
    count = admin.select(conn, context, "SELECT count(*) FROM (" + sql + ")", args).fetchone()[0]
    direction, operator = ("DESC", "<") if descending else ("ASC", ">")
    columns = admin.select(conn, context, sql + " LIMIT 0", args).description
    projection = ",".join('"' + c[0].replace('"', '""') + '"' for c in columns)
    rows = admin.select(
        conn,
        context,
        f"SELECT {projection} FROM ({sql}) WHERE {key}{operator}? ORDER BY {key} {direction} LIMIT ?",
        (
            *args,
            int(after) if descending and after else 9223372036854775807 if descending else after,
            query["limit"] + 1,
        ),
    ).fetchall()
    more = len(rows) > query["limit"]
    rows = rows[: query["limit"]]
    items = []
    for row in rows:
        context.remaining()
        items.append(project(row))
    return {
        "items": items,
        "count": count,
        "has_more": more,
        "next_cursor": cursor(gateway, [binding, str(rows[-1][key]), offset + len(rows), ceiling])
        if more
        else None,
    }


def limits(conn, context, workspace):
    row = admin.select(
        conn,
        context,
        "SELECT w.personal_of,p.plan FROM workspaces w LEFT JOIN account_plans p ON p.user_id=w.personal_of WHERE w.id=?",
        (workspace,),
    ).fetchone()
    if row is None:
        raise LookupError()
    enabled = plans.enabled()
    plan = plans.PLANS.get(row["plan"]) if enabled else None
    return {
        "account_id": admin.identifier(row["personal_of"]),
        "configured_plan": row["plan"],
        "effective_plan": plan.name if plan else None,
        "source": "account_plan"
        if plan
        else "plans_disabled"
        if not enabled
        else "no_account_plan",
        "storage_bytes": plan.storage_bytes if plan else None,
        "transcription_seconds": plan.transcription_seconds if plan else None,
    }


def safe_policy(raw):
    try:
        value = Policy.from_json(raw)
        return value.trust, list(value.scopes)
    except (TypeError, ValueError, KeyError):
        return "unknown", []


def connection(row, context):
    trust, scopes = safe_policy(row["policy"])
    state = (
        "revoked"
        if row["revoked_at"]
        else "disabled"
        if row["disabled_at"]
        else "paused"
        if row["paused_at"]
        else "active"
    )
    if state == "active" and row["expires_at"]:
        try:
            state = "expired" if control_db._parse(row["expires_at"]) <= context.now else "active"
        except (TypeError, ValueError):
            state = "invalid"
    return {
        "id": admin.identifier(row["id"]),
        "workspace_id": admin.identifier(row["workspace_id"]),
        "user_id": admin.identifier(row["user_id"]),
        "name": admin.display(row["name"]),
        "client": admin.display(row["client"]),
        "auth": row["auth"] if row["auth"] in ("pat", "oauth") else "unknown",
        "parent_id": admin.identifier(row["parent_id"]),
        "origin": row["started_by"] if row["started_by"] in ("user", "agent") else "unknown",
        "trust": trust,
        "scopes": scopes,
        "state": state,
        "runner": row["kind"],
        "machine_id": admin.identifier(row["machine_id"], r"mach_[a-f0-9]{16}"),
        **{
            key: admin.timestamp(row[key])
            for key in ("created_at", "last_used_at", "expires_at", "revoked_at", "paused_at")
        },
    }


def safe_targets(raw):
    try:
        values = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(values, list):
        return []
    return [
        v
        for v in values[:500]
        if isinstance(v, str)
        and (
            v == "instance"
            or re.fullmatch(r"(?:workspace:ws|user:usr|connection:conn)_[a-f0-9]{16}", v)
        )
    ]


def inventory(gateway, principal, context, kind, workspace, query):
    target = workspace or query.get("workspace_id")
    binding = hashlib.sha256(
        json.dumps(
            [principal.user_id, kind, target, {k: v for k, v in query.items() if k != "cursor"}],
            sort_keys=True,
        ).encode()
    ).hexdigest()
    with admin.read_control(gateway.db, context) as conn:
        if (
            target
            and not admin.select(
                conn, context, "SELECT id FROM workspaces WHERE id=?", (target,)
            ).fetchone()
        ):
            raise LookupError()
        sections = {
            "audit": audit_inventory,
            "settings": operator_settings,
            "spaces": space_inventory,
            "workspace_settings": space_inventory,
        }
        if kind in sections:
            return sections[kind](gateway, context, conn, target, binding, query)
        conditions, args = [], []

        def condition(sql, value):
            conditions.append(sql)
            args.append(value)

        if kind == "workspaces":
            if target:
                condition("w.id=?", target)
            sql = "SELECT w.id,w.name,w.created_at,w.personal_of,w.created_at||w.id AS page_key FROM workspaces w"

            def project(r):
                return {
                    "id": admin.identifier(r["id"]),
                    "name": admin.display(r["name"]),
                    "created_at": admin.timestamp(r["created_at"]),
                    "personal_of": admin.identifier(r["personal_of"]),
                    "limits": limits(conn, context, r["id"]),
                    "orchestration_enabled": admin.orchestration_setting(conn, r["id"], context),
                }

            key = "page_key"
        elif kind == "people":
            if target:
                condition("m.workspace_id=?", target)
            if "user_id" in query:
                condition("u.id=?", query["user_id"])
            if "role" in query:
                condition("m.role=?", query["role"])
            if "disabled" in query:
                conditions.append(
                    "u.disabled_at IS " + ("NOT NULL" if query["disabled"] == "true" else "NULL")
                )
            sql = "SELECT u.id,u.name,u.email,u.created_at,u.disabled_at,u.is_admin,m.workspace_id,m.role,m.created_at AS joined_at,u.created_at||u.id||coalesce(m.workspace_id,'') AS page_key FROM users u LEFT JOIN memberships m ON m.user_id=u.id"

            def project(r):
                return {
                    "id": admin.identifier(r["id"]),
                    "name": admin.display(r["name"]),
                    "email": admin.display(r["email"]),
                    "is_admin": r["is_admin"] == 1,
                    "workspace_id": admin.identifier(r["workspace_id"]),
                    "role": r["role"],
                    "removed_at": None,
                    **{
                        k: admin.timestamp(r[k]) for k in ("created_at", "joined_at", "disabled_at")
                    },
                }

            key = "page_key"
        elif kind == "connections":
            if target:
                condition("c.workspace_id=?", target)
            if "user_id" in query:
                condition("c.user_id=?", query["user_id"])
            paused = (
                "c.paused_at"
                if "paused_at" in admin.columns(conn, context, "connections")
                else "NULL"
            )
            state = f"CASE WHEN c.revoked_at IS NOT NULL THEN 'revoked' WHEN u.disabled_at IS NOT NULL THEN 'disabled' WHEN {paused} IS NOT NULL THEN 'paused' WHEN c.expires_at IS NOT NULL AND c.expires_at<=? THEN 'expired' ELSE 'active' END"
            sql = f"SELECT c.created_at||c.id AS page_key,c.id,c.workspace_id,c.user_id,c.name,c.client,c.auth,c.parent_id,c.started_by,c.created_at,c.last_used_at,c.expires_at,c.revoked_at,c.policy,u.disabled_at,{paused} AS paused_at,r.machine_id,r.kind,{state} AS lifecycle FROM connections c JOIN users u ON u.id=c.user_id LEFT JOIN runner_connections r ON r.connection_id=c.id"
            args.insert(0, control_db._iso(context.now))
            if "state" in query:
                condition("lifecycle=?", query["state"])

            def project(r):
                return connection(r, context)

            key = "page_key"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        return page(gateway, context, conn, binding, query, sql, tuple(args), key, project)


def audit_inventory(gateway, context, conn, target, binding, query):
    ceiling = admin.select(conn, context, "SELECT coalesce(max(seq),0) FROM audit_log").fetchone()[
        0
    ]
    if query.get("cursor"):
        ceiling = uncursor(gateway, query["cursor"], binding)[2]
    conditions, args = ["seq<=?"], [ceiling]
    for name, column in (
        ("workspace_id", "workspace_id"),
        ("actor_user_id", "user_id"),
        ("actor_connection_id", "connection_id"),
        ("action", "action"),
        ("decision", "decision"),
        ("from", "at"),
        ("to", "at"),
    ):
        if name in query:
            conditions.append(
                column + (">=?" if name == "from" else "<=?" if name == "to" else "=?")
            )
            args.append(query[name])
    sql = (
        "SELECT seq,at,user_id,connection_id,workspace_id,action,decision,target_ids FROM audit_log WHERE "
        + " AND ".join(conditions)
    )

    def project(r):
        return {
            "seq": r["seq"],
            "at": admin.timestamp(r["at"]),
            "actor_user_id": admin.identifier(r["user_id"]),
            "actor_connection_id": admin.identifier(r["connection_id"]),
            "workspace_id": admin.identifier(r["workspace_id"]),
            "action": r["action"] if r["action"] in ACTIONS else "legacy",
            "decision": r["decision"] if r["decision"] in DECISIONS else "legacy",
            "target_ids": safe_targets(r["target_ids"]),
        }

    return {
        **page(
            gateway,
            context,
            conn,
            binding,
            query,
            sql,
            tuple(args),
            "seq",
            project,
            ceiling=ceiling,
            descending=True,
        ),
        "snapshot": ceiling,
    }


def operator_settings(gateway, context, conn, target, binding, query):
    if query.get("cursor"):
        raise ValueError()
    return {
        "editable": False,
        "source": "operator",
        "plans_enabled": plans.enabled(),
        "rate_limits_enabled": gateway.rate_limits,
        "authenticated_requests": ratelimit.AUTHENTICATED.capacity,
        "authenticated_window_seconds": ratelimit.AUTHENTICATED.seconds,
        "untrusted_requests": ratelimit.UNTRUSTED.capacity,
        "request_bytes": 1048576,
        "actions_available": False,
    }


def space_inventory(gateway, context, conn, target, binding, query):
    if query.get("cursor"):
        uncursor(gateway, query["cursor"], binding)
    row = admin.select(
        conn, context, "SELECT id,name,personal_of FROM workspaces WHERE id=?", (target,)
    ).fetchone()
    workspace = {
        "id": admin.identifier(row["id"]),
        "name": admin.display(row["name"]),
        "personal_of": admin.identifier(row["personal_of"]),
    }
    metadata = limits(conn, context, target)
    on = admin.orchestration_setting(conn, target, context)
    with admin.read_store(gateway, target, "boards.sqlite3", context) as boards:
        if boards is None:
            return {
                "workspace": workspace,
                "state": "unknown",
                "reason": "not_configured",
                "items": [],
                "count": None,
                "has_more": False,
                "next_cursor": None,
                "limits": metadata,
                "orchestration_enabled": on,
            }
        names = admin.columns(boards, context, "boards")
        coordinator = "coordinator" if "coordinator" in names else "NULL AS coordinator"
        sql = f"SELECT id,slug,name,archived,created_at,done_needs_review,claim_minutes,{coordinator},printf('%020d',id) AS page_key FROM boards"
        with admin.read_store(gateway, target, "runner.sqlite3", context) as runner:

            def project(r):
                from commonplace.projects import limits as project_limits, orchestration

                configured = project_limits.get(boards, r["id"])["max_agents"]
                accepted = None
                if on and admin.has_table(runner, context, "orch_configs"):
                    accepted = accepted_cap(runner, context, r["slug"])
                effective = (
                    accepted
                    if configured is None
                    else min(configured, accepted)
                    if accepted is not None
                    else configured
                )
                if effective is None and on:
                    effective = orchestration.DEFAULT_MAX_AGENTS
                return {
                    "id": r["id"],
                    "slug": admin.slug(r["slug"]),
                    "name": admin.display(r["name"]),
                    "archived": bool(r["archived"]),
                    "created_at": admin.timestamp(r["created_at"]),
                    "coordinator": safe_actor(r["coordinator"]),
                    "done_needs_review": bool(r["done_needs_review"]),
                    "claim_minutes": r["claim_minutes"],
                    "configured_max_agents": configured,
                    "accepted_config_max_agents": accepted,
                    "effective_max_agents": effective,
                    "cap_source": "accepted_config"
                    if accepted is not None
                    else "project"
                    if configured is not None
                    else "default"
                    if on
                    else "unlimited",
                }

            result = page(gateway, context, boards, binding, query, sql, (), "page_key", project)
    return {
        "workspace": workspace,
        "state": "ready",
        **result,
        "limits": metadata,
        "orchestration_enabled": on,
        "actions_available": False,
    }


def safe_actor(value):
    return (
        value
        if isinstance(value, str)
        and re.fullmatch(
            r"(?:user:usr_[a-f0-9]{16}|conn:[A-Za-z0-9][A-Za-z0-9._:@-]{0,119})", value
        )
        else None
    )


def accepted_cap(conn, context, slug):
    # The existing accepted configuration is TOML, so parse it using its shared reader.
    from commonplace.orch import schema

    context.remaining()
    row = admin.select(
        conn,
        context,
        "SELECT body FROM orch_configs WHERE board=? AND channel='live' AND status='accepted'",
        (slug,),
    ).fetchone()
    config = schema.parse(row["body"]) if row else None
    context.remaining()
    return config.max_agents if config and config.runner is not None else None
