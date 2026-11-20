"""Model modes: whose account a run uses (``docs/system/model-modes.md``, card #617).

A mode record (``org``, ``personal`` or ``hybrid``) belongs to the workspace (scope ``*``) or to one
project (scope = the board slug); the project record wins as a whole. With no record the mode is
``unset``: today's behaviour, unchanged. The mode never picks the model, only the account behind it.

A person's *connection* says: this machine of mine is signed in to my own account for a provider.
It holds a provider name, a label of the person's choosing and the machine, never a credential.
A machine behind an active connection is private to that person: it serves no other person's
start and no org run, in every mode.

``decide`` is the one place the precedence matrix lives. It reads the control rows and returns a
``Decision``: the account a run is billed to and its run-record fields, or a refusal with a code,
plain words and the next step. It fails closed: a store it cannot read raises ``ModeUnavailable``
and no start is admitted on a guess.
"""

import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from commonplace import redact

MODES = ("org", "personal", "hybrid")
WORKSPACE = "*"
MAX_CONNECTIONS = 5
LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._()'-]{0,59}")
PROVIDER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
CONNECTION_ID = re.compile(r"mc_[0-9a-f]{16}")
RECORD_KEYS = (
    "mode",
    "mode_source",
    "account_kind",
    "account_label",
    "account_person",
    "account_connection_id",
)
COLUMNS = {key: "TEXT" for key in RECORD_KEYS}

SCHEMA = """
CREATE TABLE IF NOT EXISTS model_mode_settings (
    workspace_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('org','personal','hybrid')),
    allow_person_override INTEGER NOT NULL DEFAULT 0 CHECK (allow_person_override IN (0,1)),
    updated_by TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, scope)
);
CREATE TABLE IF NOT EXISTS model_connections (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    label TEXT NOT NULL,
    machine_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS model_connections_machine
    ON model_connections(machine_id) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS model_connections_person
    ON model_connections(workspace_id, user_id) WHERE revoked_at IS NULL;
CREATE TABLE IF NOT EXISTS model_person_prefs (
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    use_own INTEGER NOT NULL DEFAULT 0 CHECK (use_own IN (0,1)),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, user_id)
);
CREATE TABLE IF NOT EXISTS model_connection_needs (
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    board TEXT NOT NULL,
    provider TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, user_id, board, provider)
);
"""


class ModeDenied(Exception):
    """The caller may not do this (REST answers 404, the same as an absent route)."""


class ModeUnavailable(Exception):
    """The mode or connection rows cannot be read: nothing is admitted on a guess."""


class InvalidMode(ValueError):
    """A request body is not a valid mode record or connection (REST answers 400)."""


@dataclass(frozen=True)
class Refusal:
    code: str
    message: str


@dataclass(frozen=True)
class Decision:
    """The account a start is billed to, or why it is refused.

    ``kind`` is ``org``, ``person`` or ``None`` (a workspace with no mode record and a machine
    that no one registered: nothing to say, the run is recorded as before). ``machine_id`` is
    set when a person's own machine replaces the one asked for; ``machine_ids`` lists the
    machines a surface without a named machine may use for a person's account."""

    mode: str
    mode_source: str
    kind: str | None = None
    label: str | None = None
    person: str | None = None
    machine_id: str | None = None
    machine_ids: tuple = ()
    exclude_private: bool = False
    note: str | None = None
    refusal: Refusal | None = None
    connection_id: str | None = None

    def record(self) -> dict:
        """Internal run-record fields; person and connection ids never enter public views."""
        return {
            "mode": self.mode,
            "mode_source": self.mode_source,
            "account_kind": self.kind,
            "account_label": self.label,
            "account_person": self.person if self.kind == "person" else None,
            "account_connection_id": self.connection_id,
        }

    def public(self) -> dict:
        """What the person who starts the run reads: mode, source, account kind and label."""
        return {
            "mode": self.mode,
            "mode_source": self.mode_source,
            "kind": self.kind,
            "label": self.label,
            "note": self.note,
        }


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_schema(db) -> None:
    with db._lock:
        db._conn.executescript(SCHEMA)


def require_owner(principal) -> None:
    if (
        not principal.person
        or principal.role != "owner"
        or getattr(principal, "runner", None) is not None
    ):
        raise ModeDenied("not found")


def _person(principal) -> None:
    if not principal.person or getattr(principal, "runner", None) is not None:
        raise ModeDenied("not found")


def _scope(scope) -> str:
    if scope is None or scope == WORKSPACE:
        return WORKSPACE
    if not isinstance(scope, str) or re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", scope) is None:
        raise InvalidMode("invalid project")
    return scope


# ------------------------------------------------------------------ the mode record


def _record(row) -> dict:
    return {
        "mode": row["mode"],
        "allow_person_override": bool(row["allow_person_override"]),
        "scope": "workspace" if row["scope"] == WORKSPACE else "project",
    }


def records(db, workspace_id: str) -> dict:
    """The workspace's own record and every project record, without precedence."""
    with db._lock:
        rows = db._conn.execute(
            "SELECT scope, mode, allow_person_override FROM model_mode_settings "
            "WHERE workspace_id=? ORDER BY scope",
            (workspace_id,),
        ).fetchall()
    result = {"workspace": None, "projects": {}}
    for row in rows:
        if row["scope"] == WORKSPACE:
            result["workspace"] = _record(row)
        else:
            result["projects"][row["scope"]] = _record(row)
    return result


def effective_mode(db, workspace_id: str, board: str | None) -> dict:
    """The record that decides for ``board``: the project's whole record, else the workspace's,
    else ``unset`` (no record: today's behaviour)."""
    with db._lock:
        row = db._conn.execute(
            "SELECT scope, mode, allow_person_override FROM model_mode_settings "
            "WHERE workspace_id=? AND scope IN (?, ?) "
            "ORDER BY CASE WHEN scope=? THEN 0 ELSE 1 END LIMIT 1",
            (workspace_id, board or WORKSPACE, WORKSPACE, board or WORKSPACE),
        ).fetchone()
    if row is None:
        return {"mode": "unset", "source": "legacy", "allow_person_override": False}
    source = "workspace" if row["scope"] == WORKSPACE else "project"
    return {
        "mode": row["mode"],
        "source": source,
        "allow_person_override": bool(row["allow_person_override"]),
    }


def put(db, principal, body, scope=None) -> dict:
    """The owner sets the workspace's or one project's record (validated, audited)."""
    require_owner(principal)
    scope = _scope(scope)
    if (
        not isinstance(body, dict)
        or set(body) - {"mode", "allow_person_override"}
        or body.get("mode") not in MODES
    ):
        raise InvalidMode("invalid mode")
    allow = body.get("allow_person_override", False)
    if type(allow) is not bool or (allow and body["mode"] != "hybrid"):
        raise InvalidMode("person override is only for hybrid")
    with db._lock, db._tx() as conn:
        conn.execute(
            "INSERT INTO model_mode_settings "
            "(workspace_id, scope, mode, allow_person_override, updated_by, updated_at) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(workspace_id, scope) DO UPDATE SET "
            "mode=excluded.mode, allow_person_override=excluded.allow_person_override, "
            "updated_by=excluded.updated_by, updated_at=excluded.updated_at",
            (
                principal.workspace_id,
                scope,
                body["mode"],
                int(allow),
                principal.user_id,
                _now(),
            ),
        )
        db.audit(
            action="models.mode",
            decision="allow",
            workspace_id=principal.workspace_id,
            user_id=principal.user_id,
            connection_id=principal.connection_id,
            auth=principal.auth,
            target_ids=(scope,),
        )
    return records(db, principal.workspace_id)


def clear(db, principal, scope=None) -> dict:
    """The owner removes a record; the project (or the workspace) then follows what is above it."""
    require_owner(principal)
    scope = _scope(scope)
    with db._lock, db._tx() as conn:
        removed = conn.execute(
            "DELETE FROM model_mode_settings WHERE workspace_id=? AND scope=?",
            (principal.workspace_id, scope),
        ).rowcount
        if not removed:
            raise ModeDenied("not found")
        db.audit(
            action="models.mode_clear",
            decision="allow",
            workspace_id=principal.workspace_id,
            user_id=principal.user_id,
            connection_id=principal.connection_id,
            auth=principal.auth,
            target_ids=(scope,),
        )
    return records(db, principal.workspace_id)


# ------------------------------------------------------------------ a person's connections


def _usable(conn, workspace_id: str, user_id: str) -> list:
    return conn.execute(
        "SELECT c.id, c.provider, c.label, c.machine_id FROM model_connections c "
        "JOIN runner_machines m ON m.id=c.machine_id AND m.workspace_id=c.workspace_id "
        "WHERE c.workspace_id=? AND c.user_id=? AND c.revoked_at IS NULL "
        "AND m.enabled=1 AND m.revoked_at IS NULL ORDER BY c.created_at, c.id",
        (workspace_id, user_id),
    ).fetchall()


def connection_of_machine(conn, workspace_id: str, machine_id: str):
    """The active connection that makes a machine private to one person, or ``None``."""
    return conn.execute(
        "SELECT id, user_id, provider, label, machine_id FROM model_connections "
        "WHERE workspace_id=? AND machine_id=? AND revoked_at IS NULL",
        (workspace_id, machine_id),
    ).fetchone()


def private_machine_ids(db, workspace_id: str) -> frozenset:
    """Machines that are some person's own: never part of the workspace pool."""
    with db._lock:
        rows = db._conn.execute(
            "SELECT machine_id FROM model_connections WHERE workspace_id=? AND revoked_at IS NULL",
            (workspace_id,),
        ).fetchall()
    return frozenset(row["machine_id"] for row in rows)


def _clean_label(label) -> str:
    if not isinstance(label, str) or LABEL.fullmatch(label) is None:
        raise InvalidMode("invalid label")
    if redact.redact(label)[0] != label:
        raise InvalidMode("invalid label")
    return label


def add_connection(db, principal, body) -> dict:
    """A person registers one of their own machines as their account for a provider."""
    _person(principal)
    if (
        not isinstance(body, dict)
        or set(body) != {"provider", "label", "machine"}
        or not isinstance(body["provider"], str)
        or PROVIDER.fullmatch(body["provider"]) is None
        or not isinstance(body["machine"], str)
    ):
        raise InvalidMode("invalid connection")
    label = _clean_label(body["label"])
    with db._lock, db._tx() as conn:
        machine = conn.execute(
            "SELECT id FROM runner_machines WHERE workspace_id=? AND revoked_at IS NULL "
            "AND (id=? OR label=?) AND enrolled_by=? ORDER BY id LIMIT 1",
            (principal.workspace_id, body["machine"], body["machine"], principal.user_id),
        ).fetchone()
        if machine is None:  # not theirs and not there look the same
            raise ModeDenied("not found")
        held = conn.execute(
            "SELECT COUNT(*) FROM model_connections WHERE workspace_id=? AND user_id=? "
            "AND revoked_at IS NULL",
            (principal.workspace_id, principal.user_id),
        ).fetchone()[0]
        if held >= MAX_CONNECTIONS:
            raise InvalidMode("too many connections")
        if connection_of_machine(conn, principal.workspace_id, machine["id"]) is not None:
            raise InvalidMode("this machine already has a connection")
        connection = "mc_" + secrets.token_hex(8)
        conn.execute(
            "INSERT INTO model_connections "
            "(id, workspace_id, user_id, provider, label, machine_id, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                connection,
                principal.workspace_id,
                principal.user_id,
                body["provider"],
                label,
                machine["id"],
                _now(),
            ),
        )
        db.audit(
            action="models.connection",
            decision="allow",
            workspace_id=principal.workspace_id,
            user_id=principal.user_id,
            connection_id=principal.connection_id,
            auth=principal.auth,
            target_ids=(connection,),
        )
    return {
        "id": connection,
        "provider": body["provider"],
        "label": label,
        "machine": machine["id"],
    }


def list_connections(db, principal) -> dict:
    """The caller's own connections and switch; nobody else's is ever returned."""
    _person(principal)
    with db._lock:
        rows = db._conn.execute(
            "SELECT id, provider, label, machine_id FROM model_connections "
            "WHERE workspace_id=? AND user_id=? AND revoked_at IS NULL ORDER BY created_at, id",
            (principal.workspace_id, principal.user_id),
        ).fetchall()
        pref = db._conn.execute(
            "SELECT use_own FROM model_person_prefs WHERE workspace_id=? AND user_id=?",
            (principal.workspace_id, principal.user_id),
        ).fetchone()
    return {
        "connections": [
            {
                "id": r["id"],
                "provider": r["provider"],
                "label": r["label"],
                "machine": r["machine_id"],
            }
            for r in rows
        ],
        "use_own": bool(pref["use_own"]) if pref else False,
    }


def revoke_connection(db, principal, connection) -> None:
    _person(principal)
    if not isinstance(connection, str) or CONNECTION_ID.fullmatch(connection) is None:
        raise ModeDenied("not found")
    with db._lock, db._tx() as conn:
        done = conn.execute(
            "UPDATE model_connections SET revoked_at=? WHERE id=? AND workspace_id=? "
            "AND user_id=? AND revoked_at IS NULL",
            (_now(), connection, principal.workspace_id, principal.user_id),
        ).rowcount
        if not done:
            raise ModeDenied("not found")
        db.audit(
            action="models.connection_revoke",
            decision="allow",
            workspace_id=principal.workspace_id,
            user_id=principal.user_id,
            connection_id=principal.connection_id,
            auth=principal.auth,
            target_ids=(connection,),
        )


def set_use_own(db, principal, body) -> dict:
    """A person's switch: prefer their own account where a hybrid project allows it."""
    _person(principal)
    if not isinstance(body, dict) or set(body) != {"use_own"} or type(body["use_own"]) is not bool:
        raise InvalidMode("invalid switch")
    with db._lock, db._tx() as conn:
        conn.execute(
            "INSERT INTO model_person_prefs (workspace_id, user_id, use_own, updated_at) "
            "VALUES (?,?,?,?) ON CONFLICT(workspace_id, user_id) DO UPDATE SET "
            "use_own=excluded.use_own, updated_at=excluded.updated_at",
            (principal.workspace_id, principal.user_id, int(body["use_own"]), _now()),
        )
        db.audit(
            action="models.use_own",
            decision="allow",
            workspace_id=principal.workspace_id,
            user_id=principal.user_id,
            connection_id=principal.connection_id,
            auth=principal.auth,
            target_ids=(),
        )
    return list_connections(db, principal)


# ------------------------------------------------------------------ the decision


def _missing(board: str, provider: str | None) -> Refusal:
    what = f"your own {provider} account" if provider else "your own account"
    return Refusal(
        "connection_missing",
        f"This project uses each person's own account, and you have not connected {what}. "
        "Connect it in Settings under Your own accounts, or ask the workspace owner to switch "
        f"{board} to hybrid.",
    )


def _not_found() -> Refusal:
    return Refusal("machine_not_found", "machine not found")


def decide(
    db,
    workspace_id: str,
    board: str,
    *,
    person: str | None,
    provider: str | None = None,
    machine=None,
    substitute: bool = False,
) -> Decision:
    """The account a start runs on (the matrix of ``docs/system/model-modes.md``).

    ``person`` is the requesting person's user id (``None``: no person). ``machine`` is the
    registry row a start names, or ``None`` for a surface that has not picked one (chat, the
    settings read). ``substitute`` lets a start that must use a person's account swap the named
    machine for that person's own (a config-driven start names a workspace machine)."""
    try:
        return _decide(db, workspace_id, board, person, provider, machine, substitute)
    except sqlite3.Error as exc:
        raise ModeUnavailable("model modes cannot be read") from exc


def _decide(db, workspace_id, board, person, provider, machine, substitute) -> Decision:
    rule = effective_mode(db, workspace_id, board)
    mode, source = rule["mode"], rule["source"]
    with db._lock:
        conn = db._conn
        own = [
            c
            for c in (_usable(conn, workspace_id, person) if person else [])
            if provider is None or c["provider"] == provider
        ]
        pref = (
            conn.execute(
                "SELECT use_own FROM model_person_prefs WHERE workspace_id=? AND user_id=?",
                (workspace_id, person),
            ).fetchone()
            if person
            else None
        )
        use_own = bool(pref and pref["use_own"])
        private = (
            connection_of_machine(conn, workspace_id, machine["id"])
            if machine is not None
            else None
        )
    want, note = None, None
    if mode == "org":
        want = "org"
    elif mode == "personal":
        want = "person"
    elif mode == "hybrid":
        if rule["allow_person_override"] and use_own and own:
            want, source = "person", "person"
        else:
            want = "org"
            if rule["allow_person_override"] and use_own:
                note = (
                    "You have no connection for "
                    f"{provider or 'this provider'}, so the workspace account was used."
                )
    label_mode = "legacy" if mode == "unset" else mode
    base = {"mode": label_mode, "mode_source": source, "person": person, "note": note}
    if private is not None and private["user_id"] != person:
        return Decision(
            **base, refusal=_not_found()
        )  # another person's machine does not exist here
    if want is None:
        if private is not None:
            return Decision(
                **base, kind="person", label=private["label"], connection_id=private["id"]
            )
        return Decision(**base, exclude_private=machine is not None)
    if want == "org":
        if private is not None:
            return Decision(
                **base,
                refusal=Refusal(
                    "machine_not_org_account",
                    "This machine is your own account, but this project runs on the workspace "
                    "account. Pick one of the workspace's machines.",
                ),
            )
        return Decision(
            **base,
            kind="org",
            label=machine["label"] if machine is not None else None,
            exclude_private=True,
        )
    if person is None:
        return Decision(
            **base,
            refusal=Refusal(
                "no_requesting_person",
                "This project uses each person's own account, and this start has no person. "
                "The owner accepts the Coworker config, or switches the project to org.",
            ),
        )
    if not own:
        return Decision(**base, refusal=_missing(board, provider))
    if machine is None:
        return Decision(
            **base,
            kind="person",
            label=own[0]["label"],
            machine_ids=tuple(c["machine_id"] for c in own),
        )
    picked = next((c for c in own if c["machine_id"] == machine["id"]), None)
    if picked is not None:
        return Decision(
            **base,
            kind="person",
            label=picked["label"],
            machine_id=machine["id"],
            connection_id=picked["id"],
        )
    if not substitute:
        return Decision(
            **base,
            refusal=Refusal(
                "machine_not_your_account",
                "This project runs on each person's own account, and this machine is not "
                "yours. Pick one of your own machines.",
            ),
        )
    return Decision(
        **base,
        kind="person",
        label=own[0]["label"],
        machine_id=own[0]["machine_id"],
        connection_id=own[0]["id"],
    )


def visible_label(row, viewer: str | None):
    """The account label a reader may see: an org label to everyone, a person's own only to
    that person, never to the owner or another member."""
    if row["account_kind"] == "person" and row["account_person"] != viewer:
        return None
    return row["account_label"]


def run_view(row, viewer: str | None) -> dict | None:
    """The run's mode and account as a reader sees them (``None`` for a run from before modes)."""
    if row["mode"] is None:
        return None
    return {
        "mode": row["mode"],
        "mode_source": row["mode_source"],
        "kind": row["account_kind"] or "org",
        "label": visible_label(row, viewer),
    }


# ------------------------------------------------------------------ never silent


def note_missing(db, workspace_id: str, user_id: str, board: str, provider: str | None) -> None:
    """Remember that a person was refused for a missing connection: one Needs me row each."""
    with db._lock, db._tx() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO model_connection_needs "
            "(workspace_id, user_id, board, provider, created_at) VALUES (?,?,?,?,?)",
            (workspace_id, user_id, board, provider or "", _now()),
        )


def needs(db, principal) -> list[dict]:
    """The person's own Needs me items: connections they still lack, each clearing itself once
    the project no longer asks for one or a connection exists. Only the person concerned sees
    them; the owner's Needs me never lists a member's missing connection."""
    if not principal.person or getattr(principal, "runner", None) is not None:
        return []
    with db._lock:
        rows = db._conn.execute(
            "SELECT board, provider FROM model_connection_needs WHERE workspace_id=? AND user_id=? "
            "ORDER BY board, provider",
            (principal.workspace_id, principal.user_id),
        ).fetchall()
    items = []
    for row in rows:
        decision = decide(
            db,
            principal.workspace_id,
            row["board"],
            person=principal.user_id,
            provider=row["provider"] or None,
        )
        if decision.refusal is None or decision.refusal.code != "connection_missing":
            with db._lock, db._tx() as conn:
                conn.execute(
                    "DELETE FROM model_connection_needs WHERE workspace_id=? AND user_id=? "
                    "AND board=? AND provider=?",
                    (principal.workspace_id, principal.user_id, row["board"], row["provider"]),
                )
            continue
        items.append(
            {
                "kind": "model_connection",
                "board": row["board"],
                "provider": row["provider"] or None,
                "message": decision.refusal.message,
                "action": {"label": "Connect your account", "type": "model_connections"},
            }
        )
    return items


def effective_for(db, workspace_id: str, board: str, person: str | None, roles: dict) -> dict:
    """The account each role would run on for ``person``: ``roles`` maps a role to its model's
    provider (or ``None``). A role with nothing usable carries the refusal's reason."""
    result = {}
    for role, provider in roles.items():
        decision = decide(db, workspace_id, board, person=person, provider=provider)
        entry = decision.public()
        entry["reason"] = decision.refusal.message if decision.refusal else None
        result[role] = entry
    return result
