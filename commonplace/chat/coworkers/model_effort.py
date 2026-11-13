"""Workspace-approved coworker choices and append-only selection metadata.

Selections never introduce an endpoint, credential or provider. Omitted fields
continue to inherit the current chat front, including its off/consent checks.
"""

import json
from dataclasses import replace

from commonplace.chat import settings
from commonplace.projects import models

UNSET = object()
EFFORTS = ("low", "medium", "high", "xhigh", "max")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS coworker_selections (
 kind TEXT NOT NULL, object_id TEXT NOT NULL, requested TEXT NOT NULL,
 model TEXT, reasoning_effort TEXT, PRIMARY KEY(kind,object_id)
);
CREATE TRIGGER IF NOT EXISTS coworker_selections_no_update BEFORE UPDATE ON coworker_selections
BEGIN SELECT RAISE(ABORT, 'coworker selections are append-only'); END;
CREATE TRIGGER IF NOT EXISTS coworker_selections_no_delete BEFORE DELETE ON coworker_selections
BEGIN SELECT RAISE(ABORT, 'coworker selections are append-only'); END;
"""


def deny(code="invalid_request", status=400):
    from .store import StoreError

    raise StoreError(code, status)


def ensure_schema(conn):
    conn.executescript(_SCHEMA)


def options(model=UNSET, reasoning_effort=UNSET):
    return {
        k: v
        for k, v in (("model", model), ("reasoning_effort", reasoning_effort))
        if v is not UNSET
    }


def front(authority, board):
    if authority is None:
        return None
    db = authority.gateway.db
    with db._lock:
        if (
            db._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_model_settings'"
            ).fetchone()
            is None
        ):
            return None
        return settings.front(db, authority.workspace_id, board)


def resolve(authority, board, requested):
    """Validate against this scope and the already configured provider only."""
    effort = requested.get("reasoning_effort")
    if "reasoning_effort" in requested and (not isinstance(effort, str) or effort not in EFFORTS):
        deny("invalid_reasoning_effort")
    config = front(authority, board)
    if "model" in requested:
        name = requested["model"]
        if not isinstance(name, str) or not name or len(name) > 120:
            deny("unknown_model")
        known = (
            models.catalog(authority.gateway.db, authority.workspace_id, board or "*")
            if authority and config
            else []
        )
        if config is None or (
            name != config.model
            and not any(c.provider == config.provider and c.model == name for c in known)
        ):
            deny("unknown_model")
        config = replace(config, model=name)
    return config, {"model": config.model or None if config else None, "reasoning_effort": effort}


def requested(conn, kind, object_id):
    row = conn.execute(
        "SELECT requested FROM coworker_selections WHERE kind=? AND object_id=?",
        (kind, str(object_id)),
    ).fetchone()
    return json.loads(row[0]) if row else {}


def effective(conn, authority, conversation, overrides):
    choices = {**requested(conn, "conversation", conversation["id"]), **overrides}
    return choices, resolve(authority, conversation["board"], choices)[1]


def save(conn, kind, object_id, choices, selected):
    conn.execute(
        "INSERT INTO coworker_selections VALUES (?,?,?,?,?)",
        (
            kind,
            str(object_id),
            json.dumps(choices, sort_keys=True),
            selected["model"],
            selected["reasoning_effort"],
        ),
    )


def describe(conn, authority, conversation):
    choices = requested(conn, "conversation", conversation["id"])
    config = front(authority, conversation["board"])
    return {
        "model": choices.get("model", config.model or None if config else None),
        "reasoning_effort": choices.get("reasoning_effort"),
    }


def audit(authority, user_id, action, selected, mode=None):
    if authority:
        authority.gateway.db.audit(
            action=action,
            decision="ok",
            workspace_id=authority.workspace_id,
            user_id=user_id,
            auth="session",
            reason=json.dumps({**selected, **({"mode": mode} if mode else {})}, sort_keys=True),
        )


def check_retry(conn, kind, object_id, choices):
    if requested(conn, kind, object_id) != choices:
        deny("request_conflict", 409)


def runtime_config(authority, board, conn, message_id):
    """Revalidate queued choices against current owner settings before dispatch."""
    return resolve(authority, board, requested(conn, "message", message_id))


def current(store, authority, board, message_id, expected):
    with store._transaction() as conn:
        return runtime_config(authority, board, conn, message_id)[0] == expected


def payload(body, provider, effort):
    """Add effort only when explicitly selected; legacy payloads stay identical."""
    if effort is None:
        return
    if not isinstance(effort, str) or effort not in EFFORTS:
        deny("invalid_reasoning_effort")
    if provider == "openai_compatible":
        body["reasoning_effort"] = effort
    elif provider == "anthropic":
        body["output_config"] = {"effort": effort}
    else:
        deny("unsupported_reasoning_effort")
