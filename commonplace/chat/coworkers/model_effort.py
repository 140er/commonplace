"""Workspace-approved coworker choices and append-only selection metadata.

Selections never introduce an endpoint, credential or provider. Omitted fields
continue to inherit the current chat front, including its off/consent checks.
"""

import json
from contextlib import contextmanager
from dataclasses import dataclass, field, replace

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


@dataclass
class SettingsSnapshot:
    default: object = None
    inherited: object = None
    scopes: dict = field(default_factory=dict)
    catalogs: dict = field(default_factory=dict)
    audits: list = field(default_factory=list)

    def front(self, board):
        return self.scopes.get(board, self.inherited) if board else self.default


def snapshot(authority):
    """Read model policy under the control lock, before taking a store lock."""
    if authority is None:
        return SettingsSnapshot()
    db, wid = authority.gateway.db, authority.workspace_id
    with db._lock:
        if (
            db._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_model_settings'"
            ).fetchone()
            is None
        ):
            return SettingsSnapshot()
        scopes = {
            r[0]
            for r in db._conn.execute(
                "SELECT scope FROM chat_model_settings WHERE workspace_id=? "
                "UNION SELECT scope FROM project_model_catalog WHERE workspace_id=?",
                (wid, wid),
            )
        } | {"*"}
        default = settings.front(db, wid)
        has_default = (
            db._conn.execute(
                "SELECT 1 FROM chat_model_settings WHERE workspace_id=? AND scope='*' AND role='front'",
                (wid,),
            ).fetchone()
            is not None
        )
        inherited = (
            replace(
                default,
                inherited=True,
                applies=default.provider == "none" or settings.is_local(default),
            )
            if has_default
            else default
        )
        return SettingsSnapshot(
            default,
            inherited,
            {scope: settings.front(db, wid, scope) for scope in scopes if scope != "*"},
            {scope: tuple(models.catalog(db, wid, scope)) for scope in scopes},
        )


@contextmanager
def context(store, resolved=None):
    resolved = resolved if resolved is not None else snapshot(store._authority)
    # Store serializes these fields. Restore a containing transaction's snapshot
    # on reentrant calls; no thread can observe another transaction's choices.
    with store._lock:
        previous = getattr(store, "_model_settings", None)
        store._model_settings = resolved
        try:
            yield
        finally:
            store._model_settings = previous
    # The private transaction is closed and its writer lock released before
    # publishing control-db audit metadata. Rollbacks never publish these rows.
    if store._authority:
        for user_id, action, reason in resolved.audits:
            store._authority.gateway.db.audit(
                action=action,
                decision="ok",
                workspace_id=store._authority.workspace_id,
                user_id=user_id,
                auth="session",
                reason=reason,
            )


def resolve(resolved, board, requested):
    """Validate against this scope and the already configured provider only."""
    effort = requested.get("reasoning_effort")
    if "reasoning_effort" in requested and (not isinstance(effort, str) or effort not in EFFORTS):
        deny("invalid_reasoning_effort")
    config = resolved.front(board)
    if "model" in requested:
        name = requested["model"]
        if not isinstance(name, str) or not name or len(name) > 120:
            deny("unknown_model")
        known = resolved.catalogs.get(board or "*", resolved.catalogs.get("*", ()))
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


def effective(conn, resolved, conversation, overrides, *, choices=None):
    # Validate this request only. Persist inherited choices for the dispatch
    # fence, so catalog changes cannot reject otherwise valid legacy intake.
    resolve(resolved, conversation["board"], overrides)
    if choices is None:
        choices = {**requested(conn, "conversation", conversation["id"]), **overrides}
    return choices, describe(conn, resolved, conversation, choices=choices)


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


def describe(conn, resolved, conversation, *, choices=None):
    if choices is None:
        choices = requested(conn, "conversation", conversation["id"])
    config = resolved.front(conversation["board"])
    return {
        "model": choices.get("model", config.model or None if config else None),
        "reasoning_effort": choices.get("reasoning_effort"),
    }


def audit(resolved, user_id, action, selected, mode=None):
    resolved.audits.append(
        (
            user_id,
            action,
            json.dumps({**selected, **({"mode": mode} if mode else {})}, sort_keys=True),
        )
    )


def check_retry(conn, kind, object_id, choices):
    if requested(conn, kind, object_id) != choices:
        deny("request_conflict", 409)


def runtime_config(resolved, board, conn, message_id):
    """Revalidate queued choices against current owner settings before dispatch."""
    return resolve(resolved, board, requested(conn, "message", message_id))


def current(store, resolved, board, message_id, expected):
    with store._transaction(model_settings=resolved) as conn:
        return runtime_config(resolved, board, conn, message_id)[0] == expected


def apply_effort(body, provider, effort):
    """Add effort only when explicitly selected; legacy payloads stay identical."""
    from commonplace.chat.types import ModelError

    if effort is None:
        return
    if not isinstance(effort, str) or effort not in EFFORTS:
        raise ModelError("bad_response")
    if provider == "openai_compatible":
        body["reasoning_effort"] = effort
    elif provider == "anthropic":
        body["output_config"] = {"effort": effort}
    else:
        raise ModelError("bad_response")
