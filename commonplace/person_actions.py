"""Explicit phone consent for a person's card actions; disabled unless enabled.

These capabilities grant only their dedicated routes. They never turn an OAuth bearer
into a session, and never authorize other person-only routes or MCP tools.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from contextlib import contextmanager

from commonplace import decide_grant, sessions
from commonplace.boards import rest
from commonplace.boards._http import HTTPError, path_parts, read_json, send_json
from commonplace.boards.service import BoardService
from commonplace.control_db import ControlError
from commonplace.extensions import ToolContext

DECIDE_CAPABILITY = "cards:decide:person"
APPROVE_CAPABILITY = "cards:approve:person"
CHAT_CAPABILITY = "chat:person"
CAPABILITIES = (DECIDE_CAPABILITY, APPROVE_CAPABILITY, CHAT_CAPABILITY)
NATIVE_REDIRECT = "cmmn://auth/callback"
DECIDE_TEXT = (
    "Allow this app to answer card questions and completion reviews as you in this workspace. "
    "It can answer only items you may answer on the website. Each answer records your identity "
    "and this app's name. Disconnect this app to stop access."
)
APPROVE_TEXT = (
    "Allow this app to approve or reject card proposals and plans as you in this workspace. "
    "It can act only on items you may review on the website. Each action records your identity "
    "and this app's name. Disconnect this app to stop access."
)
CHAT_TEXT = (
    "Allow this app to read your project chats and send your own messages as you in this workspace. "
    "It can reach only chats you may read and write on the website. Each message records your "
    "identity and this app's name. Disconnect this app to stop access."
)
CONSENT = (
    (DECIDE_CAPABILITY, "grant_person_decide", DECIDE_TEXT),
    (APPROVE_CAPABILITY, "grant_person_approve", APPROVE_TEXT),
    (CHAT_CAPABILITY, "grant_person_chat", CHAT_TEXT),
)

# Account export/deletion classifies this control table. No bearer is stored here.
SCHEMA = """
CREATE TABLE IF NOT EXISTS oauth_person_grants (
    connection_id TEXT NOT NULL REFERENCES connections(id),
    capability TEXT NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    client_id TEXT NOT NULL REFERENCES oauth_clients(client_id),
    created_at INTEGER NOT NULL,
    revoked_at INTEGER,
    PRIMARY KEY(connection_id, capability)
);
"""

# This receipt lives with the card, so a crash commits both or neither. Account
# export includes workspace databases, as it does for the existing card history.
RECEIPTS = """
CREATE TABLE IF NOT EXISTS person_card_receipts (
    connection_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    response TEXT NOT NULL,
    PRIMARY KEY(connection_id, request_hash)
)
"""


def enabled() -> bool:
    """Instance opt-in; checked at consent, mint and every action."""
    return os.environ.get("COMMONPLACE_PHONE_ACTIONS") == "on"


def eligible(db, principal) -> bool:
    return bool(
        enabled()
        and principal.person
        and principal.auth == "session"
        and not principal.connection_id
        and db.role(principal.workspace_id, principal.user_id) is not None
    )


def record_grants(conn, client, principal, connection_id, capabilities, at, redirect_uri):
    """Called only by authorization-code creation in its control transaction."""
    from commonplace.oauth.http import OAuthError

    requested = set(capabilities)
    if requested - set(CAPABILITIES):
        raise OAuthError("invalid_scope")
    if not requested:
        return
    member = conn.execute(
        "SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=?",
        (principal.workspace_id, principal.user_id),
    ).fetchone()
    if not (
        enabled()
        and member
        and principal.person
        and principal.auth == "session"
        and not principal.connection_id
        and redirect_uri == NATIVE_REDIRECT
        and redirect_uri in json.loads(client["redirect_uris"])
    ):
        raise OAuthError("invalid_scope")
    for capability in sorted(requested):
        conn.execute(
            "INSERT INTO oauth_person_grants "
            "(connection_id,capability,user_id,workspace_id,client_id,created_at) VALUES (?,?,?,?,?,?)",
            (
                connection_id,
                capability,
                principal.user_id,
                principal.workspace_id,
                client["client_id"],
                at,
            ),
        )
        # Same transaction as the grant and code. No token or user content.
        conn.execute(
            "INSERT INTO audit_log(at,workspace_id,user_id,connection_id,auth,action,decision,reason,target_ids) "
            "VALUES (strftime('%Y-%m-%dT%H:%M:%SZ','now'),?,?,?,'oauth','person_action.grant','ok',?,'[]')",
            (
                principal.workspace_id,
                principal.user_id,
                connection_id,
                json.dumps(
                    {
                        "capability": capability,
                        "client": client["client_name"],
                        "client_id": client["client_id"],
                    }
                ),
            ),
        )


def authority(gateway, h, capability, board_conn=None):
    """A live consented OAuth app and its own current person; no other bearer kind."""
    from commonplace.oauth import store

    header = h.headers.get("Authorization", "")
    app = gateway.authenticate(header)
    if not (
        enabled()
        and app
        and not app.person
        and app.auth == "oauth"
        and app.runner is None
        and app.parent_connection_id is None
        and capability in CAPABILITIES
        and app.policy.has_scope("memory:read")
    ):
        raise HTTPError(404, "not found")
    try:
        row = gateway.db.connection(app.connection_id, app.workspace_id)
        if row["user_id"] != app.user_id or decide_grant.machinery(gateway.db, row):
            raise HTTPError(404, "not found")
        if board_conn is None:
            service = BoardService(ToolContext(principal=app, gateway=gateway))
            coordinator = app.actor in service.coordinator_actors()
        else:
            coordinator = board_conn.execute(
                "SELECT 1 FROM boards WHERE coordinator=? LIMIT 1", (app.actor,)
            ).fetchone()
        if coordinator:
            raise HTTPError(404, "not found")
        with gateway.db._lock:
            grant = gateway.db._conn.execute(
                "SELECT g.client_id,c.client_name FROM oauth_person_grants g "
                "JOIN oauth_clients c ON c.client_id=g.client_id "
                "JOIN oauth_tokens t ON t.connection_id=g.connection_id AND t.client_id=g.client_id "
                "WHERE g.connection_id=? AND g.capability=? AND g.user_id=? AND g.workspace_id=? "
                "AND g.revoked_at IS NULL AND t.token_hash=? AND t.kind='access' "
                "AND t.revoked_at IS NULL AND t.expires_at>?",
                (
                    app.connection_id,
                    capability,
                    app.user_id,
                    app.workspace_id,
                    store.digest(header.removeprefix("Bearer ")),
                    store.now(),
                ),
            ).fetchone()
        person = sessions.person_principal(gateway.db, app.user_id, app.workspace_id)
        if grant is None or person is None:
            raise HTTPError(404, "not found")
        return app, person, dict(grant)
    except (ControlError, LookupError):
        raise HTTPError(404, "not found") from None


def revoke(db, row, *, user_id=None):
    """Machinery conversion permanently withdraws the separate phone capabilities."""
    if not db._conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='oauth_person_grants'"
    ).fetchone():
        return False
    grants = db._conn.execute(
        "SELECT capability FROM oauth_person_grants WHERE connection_id=? AND revoked_at IS NULL",
        (row["id"],),
    ).fetchall()
    db._conn.execute(
        "UPDATE oauth_person_grants SET revoked_at=strftime('%s','now') "
        "WHERE connection_id=? AND revoked_at IS NULL",
        (row["id"],),
    )
    for grant in grants:
        db.audit(
            action="person_action.revoke",
            decision="ok",
            workspace_id=row["workspace_id"],
            user_id=user_id or row["user_id"],
            connection_id=row["id"],
            auth=row["auth"],
            reason=grant["capability"],
        )
    return bool(grants)


def chat_post_path(method, path):
    """Only the existing board chat POST alias; no settings, tools or other aliases."""
    return (
        method == "POST" and re.fullmatch(r"/api/boards/[a-z0-9][a-z0-9_-]*/chat", path) is not None
    )


def chat_person(gateway, h):
    """Optional phone capability for an existing chat route, never a global principal change."""
    if not enabled():
        return None
    try:
        return authority(gateway, h, CHAT_CAPABILITY)[1]
    except HTTPError:
        return None


def post_chat(gateway, h, board, body):
    """The exact person's normal post, with its client audit before the board commit."""
    if set(body) != {"text"}:
        raise HTTPError(400, "only text is accepted")
    person = chat_person(gateway, h)
    if person is None:
        raise HTTPError(403, "Sign in to send chat")
    service = PersonCardService(ToolContext(principal=person, gateway=gateway))
    audit_seq = None
    try:
        with BoardService.transaction(service) as conn:
            service.app, service.principal, service.origin = authority(
                gateway, h, CHAT_CAPABILITY, conn
            )
            service.ctx = ToolContext(principal=service.principal, gateway=gateway)
            service._action_conn = conn
            try:
                result = service.space_post(board, body["text"])
            finally:
                service._action_conn = None
            audit_seq = gateway.db.audit(
                action="person_action.chat.post",
                decision="ok",
                workspace_id=person.workspace_id,
                user_id=person.user_id,
                connection_id=service.app.connection_id,
                auth="oauth",
                tool="space_post",
                reason=json.dumps(service.origin),
                target_ids=[str(result["id"])],
            )
        return result
    except BaseException:
        if audit_seq is not None:
            gateway.db.audit(
                action="person_action.rolled_back",
                decision="rolled_back",
                workspace_id=person.workspace_id,
                user_id=person.user_id,
                connection_id=service.app.connection_id,
                auth="oauth",
                tool="space_post",
                reason=f"audit_seq={audit_seq}",
            )
        raise


class PersonCardService(BoardService):
    """Run the existing web operation with a receipt in its own board transaction."""

    _action_conn = None

    @contextmanager
    def transaction(self, **kwargs):
        if self._action_conn is not None:
            yield self._action_conn
        else:
            with super().transaction(**kwargs) as conn:
                yield conn

    def _log(self, conn, number, kind, **fields):
        fields["context"] = json.dumps(
            {
                "client": self.origin["client_name"],
                "client_id": self.origin["client_id"],
                "connection_id": self.app.connection_id,
                "context": fields.get("context"),
            }
        )
        super()._log(conn, number, kind, **fields)

    def perform(self, h, capability, task, action, body, key):
        gateway = self.ctx.gateway
        payload_hash = hashlib.sha256(
            json.dumps([task, action, body], sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        request_hash = hashlib.sha256(key.encode()).hexdigest()
        audit_seq = None
        try:
            with super().transaction() as conn:
                # Revalidate after waiting for the board write lock; never cache authority.
                self.app, self.principal, self.origin = authority(gateway, h, capability, conn)
                self.ctx = ToolContext(principal=self.principal, gateway=gateway)
                self._get(conn, task)  # Visibility checked again, including on a retry.
                conn.execute(RECEIPTS)
                saved = conn.execute(
                    "SELECT * FROM person_card_receipts WHERE connection_id=? AND request_hash=?",
                    (self.app.connection_id, request_hash),
                ).fetchone()
                if saved:
                    if (
                        saved["payload_hash"] != payload_hash
                        or saved["user_id"] != self.principal.user_id
                        or saved["client_id"] != self.origin["client_id"]
                    ):
                        raise HTTPError(409, "idempotency key already used")
                    return json.loads(saved["response"])
                self._action_conn = conn
                try:
                    result = (
                        self.decide(task, **body)
                        if action == "decide"
                        else self.task_approve(task, **body)
                    )
                finally:
                    self._action_conn = None
                conn.execute(
                    "INSERT INTO person_card_receipts "
                    "(connection_id,user_id,client_id,request_hash,payload_hash,task_id,response) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (
                        self.app.connection_id,
                        self.principal.user_id,
                        self.origin["client_id"],
                        request_hash,
                        payload_hash,
                        task,
                        json.dumps(result),
                    ),
                )
                audit_seq = gateway.db.audit(
                    action="person_action." + action,
                    decision="ok",
                    workspace_id=self.principal.workspace_id,
                    user_id=self.principal.user_id,
                    connection_id=self.app.connection_id,
                    auth="oauth",
                    tool=action,
                    target_ids=[str(task)],
                    reason=json.dumps(self.origin),
                )
            return result
        except BaseException:
            if audit_seq is not None:
                gateway.db.audit(
                    action="person_action.rolled_back",
                    decision="rolled_back",
                    workspace_id=self.principal.workspace_id,
                    user_id=self.principal.user_id,
                    connection_id=self.app.connection_id,
                    auth="oauth",
                    tool=action,
                    target_ids=[str(task)],
                    reason=f"audit_seq={audit_seq}",
                )
            raise


def install(gateway):
    if not enabled() or getattr(gateway, "_person_actions_installed", False):
        return
    gateway._person_actions_installed = True

    def route(h):
        service = None
        try:
            parts = path_parts(h, "/api/person/")
            if len(parts) != 3 or parts[0] != "cards" or parts[2] not in ("decide", "approve"):
                raise HTTPError(404, "not found")
            action = parts[2]
            capability = DECIDE_CAPABILITY if action == "decide" else APPROVE_CAPABILITY
            app, person, origin = authority(gateway, h, capability)
            service = PersonCardService(ToolContext(principal=person, gateway=gateway))
            if not re.fullmatch(r"[1-9][0-9]{0,17}", parts[1]):
                raise HTTPError(404, "not found")
            keys = h.headers.get_all("Idempotency-Key", [])
            if len(keys) != 1 or not re.fullmatch(r"[A-Za-z0-9._~-]{1,128}", keys[0]):
                raise HTTPError(400, "one Idempotency-Key is required")
            body = read_json(h)
            allowed = (
                {"version", "choice", "reply", "then"}
                if action == "decide"
                else {"version", "approve", "reason"}
            )
            if set(body) - allowed or type(body.get("version")) is not int or body["version"] < 1:
                raise HTTPError(400, "valid version and action arguments required")
            if action == "approve" and type(body.get("approve")) is not bool:
                raise HTTPError(400, "approve must be true or false")
            result = service.perform(h, capability, int(parts[1]), action, body, keys[0])
        except rest.LEGACY_REFUSALS as exc:
            rest._send_refusal(h, service, exc)
            return
        send_json(h, 200, result)

    gateway.add_prefix_route("POST", "/api/person/cards/", route)
