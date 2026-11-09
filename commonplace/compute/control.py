"""Durable, inert Compute metadata and exact-action consent.

Public integration: ``open_store(gateway, workspace_id)`` returns a lazy Store.
Every public Store operation takes an actual Principal and keyword-only
``session_hash`` (the existing sessions._hash result, never the cookie). It
rechecks that exact live session and current person-owner membership. Inventory
methods are ``inventory`` and ``list_gateways/nodes/scopes/executions``; individual
reads are ``get_gateway/node/scope/execution``. Mutations are ``create_gateway``,
``create_node``, ``create_scope``, ``revoke_scope``, ``propose_execution`` (returns
``(public_execution, created)``), ``approve_execution`` and ``cancel_execution``.
Their keyword payloads follow docs/compute/control-contract.md. Returned values
are explicit public projections, detached from the stored authority bindings.
Lists return at most 100 records. Initial storage ceilings are 200 gateways,
1000 nodes/scopes and 2000 executions; exhaustion conflicts without pruning.
Scopes expire within one hour, proposals within 15 minutes, and approvals within
five minutes and the proposal deadline. These bounds do not enable dispatch.

There is deliberately no public verification setter or dispatch API. The private
execution service shares this database's journal, separately from action consent.
Trusted startup configuration replaces the central control lock with the local
cross-process coordinator and guards actual control/board/Compute/custody writes.
Without configuration the existing process-local management semantics remain.
The authenticated remote helper callback channel is still required for dispatch.
Custody remains separate in compute-credentials.sqlite3 and is never opened here.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import time
import uuid
from contextlib import contextmanager, nullcontext
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from commonplace import sessions
from commonplace.admission import GATE
from commonplace.boards.service import BoardService
from commonplace.boards.store import active_blockers, require_visible, task_row
from commonplace.control_db import ControlError, Principal
from commonplace.extensions import ToolContext, ToolError
from mempalace.sqlite_crypto import connect, row_factory

from .fence import _attach_connection, _held
from .protocol import (
    EnrolledRoute,
    GatewayEnrollment,
    HostKeyPin,
    Limits,
    ProtocolDenied,
    canonical,
    compile_operation,
    enrollment_digest,
    finite,
    identifier,
    positive_integer,
)
from .scopes import DiscoveryContext, DiscoveryLimits, NetworkScope, ScopeDenied

MAX_LIST = 100
MAX_GATEWAYS = 200
MAX_NODES = 1000
MAX_SCOPES = 1000
MAX_EXECUTIONS = 2000
MAX_SCOPE_SECONDS = 3600
MAX_PROPOSAL_SECONDS = 900
MAX_APPROVAL_SECONDS = 300
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS gateways (
 id TEXT PRIMARY KEY, board TEXT NOT NULL, board_id INTEGER NOT NULL,
 metadata TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS nodes (
 id TEXT PRIMARY KEY, gateway_id TEXT NOT NULL REFERENCES gateways(id),
 metadata TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS scopes (
 id TEXT PRIMARY KEY, gateway_id TEXT NOT NULL REFERENCES gateways(id),
 user_id TEXT NOT NULL, task_number INTEGER NOT NULL, agent_id TEXT,
 metadata TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0,
 created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS executions (
 id TEXT PRIMARY KEY, node_id TEXT NOT NULL REFERENCES nodes(id),
 user_id TEXT NOT NULL, request_id TEXT NOT NULL, request TEXT NOT NULL,
 session_hash TEXT NOT NULL, binding TEXT NOT NULL, digest TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('awaiting_approval','approved','cancelled')),
 reason TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
 proposal_expires_at REAL NOT NULL, approval_expires_at REAL,
 approved_by TEXT, approval_session_hash TEXT,
 UNIQUE(user_id,request_id)
);
CREATE TABLE IF NOT EXISTS audit (
 id INTEGER PRIMARY KEY, object_id TEXT NOT NULL, action TEXT NOT NULL,
 user_id TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS enrollment_verifications (
 node_id TEXT PRIMARY KEY REFERENCES nodes(id), gateway_id TEXT NOT NULL,
 enrollment TEXT NOT NULL, receipt_digest TEXT NOT NULL,
 verified_at REAL NOT NULL, expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS execution_runs (
 execution_id TEXT PRIMARY KEY REFERENCES executions(id),
 attempt_id TEXT NOT NULL UNIQUE, worker_id TEXT NOT NULL,
 gateway_id TEXT NOT NULL, request TEXT NOT NULL, approval_digest TEXT NOT NULL,
 phase TEXT NOT NULL CHECK(phase IN ('admitted','dispatching','running','uncertain','terminal')),
 stage INTEGER NOT NULL DEFAULT 0 CHECK(stage BETWEEN 0 AND 4),
 reason TEXT NOT NULL, certainty TEXT NOT NULL CHECK(certainty IN ('unknown','known')),
 reserved INTEGER NOT NULL CHECK(reserved IN (0,1)),
 created_at REAL NOT NULL, updated_at REAL NOT NULL, run_until REAL NOT NULL,
 cancel_requested_at REAL, cancel_requested_by TEXT,
 exit_code INTEGER, result TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS gateway_admissions (
 execution_id TEXT PRIMARY KEY REFERENCES execution_runs(execution_id),
 attempt_id TEXT NOT NULL UNIQUE, authority_epoch INTEGER NOT NULL,
 issued_at REAL NOT NULL
);
CREATE TRIGGER IF NOT EXISTS gateway_admissions_no_delete BEFORE DELETE ON gateway_admissions BEGIN
 SELECT RAISE(ABORT,'gateway admissions cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS gateway_admissions_no_update BEFORE UPDATE ON gateway_admissions BEGIN
 SELECT RAISE(ABORT,'gateway admissions cannot be rebound'); END;
CREATE TRIGGER IF NOT EXISTS execution_runs_no_delete BEFORE DELETE ON execution_runs BEGIN
 SELECT RAISE(ABORT,'compute execution claims cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS execution_runs_no_rebind BEFORE UPDATE OF
 execution_id,attempt_id,worker_id,gateway_id,request,approval_digest,created_at,run_until
 ON execution_runs BEGIN
 SELECT RAISE(ABORT,'compute execution claims cannot be rebound'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit BEGIN
 SELECT RAISE(ABORT,'compute audit is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit BEGIN
 SELECT RAISE(ABORT,'compute audit is append-only'); END;
"""


class StoreError(ValueError):
    """A fixed public error code, without request text or private bindings."""

    def __init__(self, code="invalid_request", status=400):
        self.code, self.status = code, status
        super().__init__(code)


def _text(value, limit=100):
    if type(value) is not str or not value.strip() or len(value) > limit:
        raise StoreError()
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise StoreError()
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise StoreError() from None
    return value


def _digest(value):
    if type(value) is not str or not _DIGEST.fullmatch(value):
        raise StoreError()
    return value


def _pin(value):
    if type(value) is not dict or set(value) != {"algorithm", "key"}:
        raise StoreError()
    if type(value["algorithm"]) is not str or type(value["key"]) is not str:
        raise StoreError()
    if len(value["key"]) > 5464:
        raise StoreError()
    try:
        key = base64.b64decode(value["key"], validate=True)
        # Validate complete SSH public wire fields without loading a key or an
        # SSH runtime. A display fingerprint or truncated blob is not a pin.
        fields = []
        offset = 0
        while offset < len(key):
            if offset + 4 > len(key) or len(fields) >= 4:
                raise ValueError
            size = int.from_bytes(key[offset : offset + 4], "big")
            offset += 4
            if size < 1 or offset + size > len(key):
                raise ValueError
            fields.append(key[offset : offset + size])
            offset += size
        if not fields or fields[0] != value["algorithm"].encode("ascii"):
            raise ValueError
        algorithm = value["algorithm"]
        if algorithm == "ssh-ed25519":
            if len(fields) != 2 or len(fields[1]) != 32:
                raise ValueError
        elif algorithm == "ssh-rsa":
            if (
                len(fields) != 3
                or not 1 <= len(fields[1]) <= 8
                or not 128 <= len(fields[2]) <= 1025
            ):
                raise ValueError
            if any(field[0] & 128 or int.from_bytes(field, "big") == 0 for field in fields[1:]):
                raise ValueError
        else:
            curves = {
                "ecdsa-sha2-nistp256": (b"nistp256", 65),
                "ecdsa-sha2-nistp384": (b"nistp384", 97),
                "ecdsa-sha2-nistp521": (b"nistp521", 133),
            }
            curve, size = curves.get(algorithm, (None, 0))
            if (
                len(fields) != 3
                or fields[1] != curve
                or len(fields[2]) != size
                or fields[2][0] != 4
            ):
                raise ValueError
        return HostKeyPin(value["algorithm"], key)
    except (ValueError, UnicodeError, binascii.Error):
        raise StoreError() from None


def _document(binding):
    result = asdict(binding)
    result["pin"] = binding.pin.document()
    return result


class Store:
    """Lazy workspace storage; constructors perform no I/O or remote work."""

    def __init__(self, path, *, gateway, workspace_id, clock=time.time, authority_fence=None):
        self._path = path
        self.gateway = gateway
        self.workspace_id = workspace_id
        self.clock = clock
        # Optional for management; execution requires this exact participating
        # fence. Other control/board/custody writers must be wired by the host.
        self._authority_fence = authority_fence

    def _now(self):
        return finite(self.clock())

    def _owner(self, principal, session_hash):
        if (
            not isinstance(principal, Principal)
            or not principal.person
            or principal.auth != "session"
        ):
            raise StoreError("owner_required", 403)
        if principal.workspace_id != self.workspace_id:
            raise StoreError("not_found", 404)
        _digest(session_hash)
        row = self.gateway.db._conn.execute(
            "SELECT user_id,workspace_id,expires_at,revoked_at FROM sessions WHERE id_hash=?",
            (session_hash,),
        ).fetchone()
        now = datetime.fromtimestamp(self._now(), timezone.utc).strftime(sessions._FMT)
        if (
            row is None
            or row["revoked_at"]
            or row["expires_at"] <= now
            or row["workspace_id"] != self.workspace_id
            or row["user_id"] != principal.user_id
        ):
            raise StoreError("sign_in", 401)
        try:
            current = sessions.person_principal(
                self.gateway.db, principal.user_id, self.workspace_id
            )
        except ControlError:
            current = None
        if current is None:
            raise StoreError("sign_in", 401)
        if current.role != "owner":
            raise StoreError("owner_required", 403)
        return current

    @contextmanager
    def _transaction(self, principal, session_hash):
        guard = (
            _held(self._authority_fence, self.workspace_id, local=True)
            if self._authority_fence is not None
            else nullcontext()
        )
        with guard:
            with self.gateway.db._lock:
                current = self._owner(principal, session_hash)
                with self._journal() as conn:
                    yield conn, current

    @contextmanager
    def _journal(self):
        """Private local transaction, never held across credential/network work.

        The executor must acquire its participating authority fence first. This
        process-local control lock alone is not a revocation/admission fence.
        """
        try:
            # Always acquire control before any workspace writer, matching the
            # existing coworker authority order. No cached allow decisions.
            with self.gateway.db._lock:
                path = Path(self._path() if callable(self._path) else self._path)
                with GATE.creating(path):
                    path.parent.mkdir(parents=True, exist_ok=True)
                    conn = connect(str(path), timeout=0.25, isolation_level=None)
                try:
                    conn.row_factory = row_factory
                    conn.execute("PRAGMA foreign_keys=ON")
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("PRAGMA synchronous=FULL")
                    conn.executescript(_SCHEMA)
                    _attach_connection(conn, str(path), "compute")
                    conn.execute("BEGIN IMMEDIATE")
                    try:
                        yield conn
                    except BaseException:
                        conn.rollback()
                        raise
                    else:
                        conn.commit()
                finally:
                    conn.close()
        except (ProtocolDenied, ScopeDenied, TypeError, OverflowError):
            raise StoreError() from None

    def _authority(self, principal, board, task_number=None):
        """Use the canonical board's current read/write and task visibility."""
        _text(board, 100)
        if task_number is not None:
            positive_integer(task_number, 2**63 - 1)
        try:
            service = BoardService(ToolContext(principal=principal, gateway=self.gateway))
            with service.store.connection() as conn:
                b = service._board(conn, board)
                task = task_row(conn, task_number) if task_number is not None else None
                if task is not None:
                    require_visible(task, principal)
                    if task["board_id"] != b["id"]:
                        raise ToolError("no: task not found")
                service._write(b, row=task)
                context = {"board": b["slug"], "board_id": b["id"], "wing": b["wing"]}
                if task is not None:
                    # Progress text/version is intentionally excluded. Only
                    # permission-relevant canonical task state binds consent.
                    fields = (
                        "status",
                        "assignee",
                        "claim_until",
                        "claim_expired_at",
                        "agent_parent",
                        "proposal_status",
                        "proposed_to",
                        "approved_by",
                        "restricted_origin",
                        "ask",
                        "ask_to",
                        "return_to",
                        "task_type",
                        "review_kind",
                        "escalated_to",
                    )
                    context["task"] = {name: task[name] for name in fields}
                    context["task"]["number"] = task["id"]
                    context["task"]["blockers"] = [
                        r["number"] for r in active_blockers(conn, task["id"])
                    ]
                return context
        except (ToolError, ControlError):
            raise StoreError("not_found", 404) from None

    def _execution_task(self, principal, number):
        """Apply canonical card gates without granting or changing a runner.

        Compute approval is consent to one probe, never origin release, plan
        approval, reassignment, or permission to run a blocked/review card.
        """
        from commonplace.boards.service import _plan_waiting, _waiting_on_person

        service = BoardService(ToolContext(principal=principal, gateway=self.gateway))
        with service.store.connection() as conn:
            task = task_row(conn, number)
            require_visible(task, principal)
            board = service._board(conn, task["board"])
            service._write(board, row=task)
            if (
                board["archived"]
                or task["status"] not in ("todo", "doing")
                or task["assignee"] not in (None, principal.actor)
                or _waiting_on_person(task)
                or _plan_waiting(task)
                or task["proposal_status"] in ("pending", "rejected")
                or active_blockers(conn, number)
                or (service._restricted_origin(task) and not service._origin_released(task))
            ):
                raise StoreError("task_not_startable", 409)

    @staticmethod
    def _row(conn, table, object_id):
        identifier(object_id)
        row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (object_id,)).fetchone()
        if row is None:
            raise StoreError("not_found", 404)
        return row

    def _gateway(self, conn, principal, gateway_id):
        row = self._row(conn, "gateways", gateway_id)
        context = self._authority(principal, row["board"])
        if context["board_id"] != row["board_id"]:
            raise StoreError("not_found", 404)
        return row, json.loads(row["metadata"])

    def _node(self, conn, principal, node_id):
        row = self._row(conn, "nodes", node_id)
        gateway, metadata = self._gateway(conn, principal, row["gateway_id"])
        return row, json.loads(row["metadata"]), gateway, metadata

    @staticmethod
    def _capacity(conn, table, limit):
        if conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] >= limit:
            raise StoreError("conflict", 409)

    def _audit(self, conn, principal, object_id, action):
        conn.execute(
            "INSERT INTO audit(object_id,action,user_id,created_at) VALUES (?,?,?,?)",
            (object_id, action, principal.user_id, self._now()),
        )

    def _expiry(self, value, ceiling):
        finite(value)
        now = self._now()
        if not now < value <= now + ceiling:
            raise StoreError()
        return value

    @staticmethod
    def _enrollment(row, metadata):
        return GatewayEnrollment(
            workspace=metadata["workspace"],
            gateway=row["id"],
            address=metadata["address"],
            port=metadata["port"],
            account=metadata["account"],
            pin=_pin(metadata["host_key"]),
            epoch=metadata["epoch"],
            credential_id=metadata["credential_id"],
            credential_version=metadata["credential_version"],
        )

    def _route(self, row, metadata, gateway_id):
        return EnrolledRoute(
            workspace=self.workspace_id,
            gateway=gateway_id,
            node=row["id"],
            address=metadata["address"],
            port=metadata["port"],
            interface=metadata["interface"],
            account=metadata["account"],
            pin=_pin(metadata["host_key"]),
            epoch=metadata["epoch"],
            credential_id=metadata["credential_id"],
            credential_version=metadata["credential_version"],
        )

    @staticmethod
    def _public_gateway(row, metadata):
        fields = (
            "name",
            "address",
            "port",
            "account",
            "host_key",
            "credential_id",
            "credential_version",
            "epoch",
            "state",
        )
        return {
            "id": row["id"],
            "board": row["board"],
            "created_at": row["created_at"],
            **{key: metadata[key] for key in fields},
        }

    def _public_node(self, row, metadata, gateway):
        result = self._public_gateway({**dict(row), "board": gateway["board"]}, metadata)
        result.update(gateway_id=gateway["id"], interface=metadata["interface"])
        return result

    @staticmethod
    def _public_scope(row):
        metadata = json.loads(row["metadata"])
        return {
            "id": row["id"],
            **{
                key: metadata[key]
                for key in ("context", "cidrs", "interfaces", "methods", "expires_at", "limits")
            },
            "revoked": bool(row["revoked"]),
        }

    @staticmethod
    def _public_execution(row):
        binding = json.loads(row["binding"])
        return {
            "id": row["id"],
            "node_id": row["node_id"],
            "gateway_id": binding["gateway"]["gateway"],
            "board": binding["authority"]["board"],
            "task_number": binding["task_number"],
            "operation": binding["operation"],
            "arguments": binding["arguments"],
            "digest": row["digest"],
            "state": row["state"],
            "reason": row["reason"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "limits": binding["limits"],
        }

    def _execution_view(self, conn, row):
        result = self._public_execution(row)
        run = conn.execute(
            "SELECT * FROM execution_runs WHERE execution_id=?", (row["id"],)
        ).fetchone()
        if run is not None:
            result["run"] = {
                name: run[name]
                for name in (
                    "attempt_id",
                    "phase",
                    "reason",
                    "certainty",
                    "created_at",
                    "updated_at",
                    "cancel_requested_at",
                    "cancel_requested_by",
                    "exit_code",
                )
            }
            result["run"]["capacity_reserved"] = bool(run["reserved"])
            result["run"]["result"] = json.loads(run["result"])
        return result

    def create_gateway(
        self,
        principal,
        *,
        session_hash,
        board,
        name,
        address,
        port,
        account,
        host_key,
        credential_id,
        credential_version,
        pin_confirmed,
    ):
        with self._transaction(principal, session_hash) as (conn, current):
            context = self._authority(current, board)
            if pin_confirmed is not True:
                raise StoreError()
            gateway_id = uuid.uuid4().hex
            binding = GatewayEnrollment(
                self.workspace_id,
                gateway_id,
                address,
                port,
                account,
                _pin(host_key),
                1,
                credential_version,
                credential_id,
            )
            metadata = {**_document(binding), "name": _text(name), "state": "pending_verification"}
            metadata["host_key"] = metadata.pop("pin")
            self._capacity(conn, "gateways", MAX_GATEWAYS)
            conn.execute(
                "INSERT INTO gateways VALUES (?,?,?,?,?)",
                (
                    gateway_id,
                    context["board"],
                    context["board_id"],
                    canonical(metadata).decode(),
                    self._now(),
                ),
            )
            self._audit(conn, current, gateway_id, "gateway.created")
            row, metadata = self._gateway(conn, current, gateway_id)
            return self._public_gateway(row, metadata)

    def create_node(
        self,
        principal,
        *,
        session_hash,
        gateway_id,
        name,
        address,
        port,
        interface,
        account,
        host_key,
        credential_id,
        credential_version,
        pin_confirmed,
    ):
        with self._transaction(principal, session_hash) as (conn, current):
            gateway, gm = self._gateway(conn, current, gateway_id)
            if pin_confirmed is not True:
                raise StoreError()
            node_id = uuid.uuid4().hex
            route = EnrolledRoute(
                self.workspace_id,
                gateway_id,
                node_id,
                address,
                port,
                interface,
                account,
                _pin(host_key),
                1,
                credential_version,
                credential_id,
            )
            enrollment_digest(self._enrollment(gateway, gm), route)
            metadata = {**_document(route), "name": _text(name), "state": "pending_verification"}
            metadata["host_key"] = metadata.pop("pin")
            self._capacity(conn, "nodes", MAX_NODES)
            conn.execute(
                "INSERT INTO nodes VALUES (?,?,?,?)",
                (node_id, gateway_id, canonical(metadata).decode(), self._now()),
            )
            self._audit(conn, current, node_id, "node.created")
            row = self._row(conn, "nodes", node_id)
            return self._public_node(row, metadata, gateway)

    def _agent(self, agent_id, board, task_number):
        identifier(agent_id)
        principal = self.gateway.db.authenticate_connection(agent_id)
        if principal is None or principal.workspace_id != self.workspace_id:
            raise StoreError("not_found", 404)
        self._authority(principal, board, task_number)
        return principal

    def create_scope(
        self,
        principal,
        *,
        session_hash,
        gateway_id,
        task_number,
        cidrs,
        interfaces,
        methods,
        expires_at,
        limits,
        agent_id=None,
    ):
        with self._transaction(principal, session_hash) as (conn, current):
            gateway, _ = self._gateway(conn, current, gateway_id)
            authority = self._authority(current, gateway["board"], task_number)
            agent = (
                self._agent(agent_id, authority["board"], task_number)
                if agent_id is not None
                else current
            )
            if type(methods) is not list or methods != ["passive"]:
                raise StoreError()
            if type(limits) is not dict or set(limits) != set(DiscoveryLimits.__dataclass_fields__):
                raise StoreError()
            scope = NetworkScope(
                context=DiscoveryContext(
                    self.workspace_id,
                    authority["board"],
                    gateway_id,
                    current.user_id,
                    agent.connection_id or current.actor,
                    str(task_number),
                ),
                cidrs=cidrs,
                interfaces=interfaces,
                methods=frozenset(methods),
                approved_by=current.user_id,
                expires_at=self._expiry(expires_at, MAX_SCOPE_SECONDS),
                limits=DiscoveryLimits(**limits),
            )
            metadata = asdict(scope)
            metadata["methods"] = sorted(scope.methods)
            scope_id = uuid.uuid4().hex
            self._capacity(conn, "scopes", MAX_SCOPES)
            conn.execute(
                "INSERT INTO scopes VALUES (?,?,?,?,?,?,0,?)",
                (
                    scope_id,
                    gateway_id,
                    current.user_id,
                    task_number,
                    agent_id,
                    canonical(metadata).decode(),
                    self._now(),
                ),
            )
            self._audit(conn, current, scope_id, "scope.created")
            return self._public_scope(self._row(conn, "scopes", scope_id))

    def revoke_scope(self, principal, scope_id, *, session_hash):
        with self._transaction(principal, session_hash) as (conn, current):
            row = self._row(conn, "scopes", scope_id)
            # Revocation remains possible when the granted agent lost access.
            gateway, _ = self._gateway(conn, current, row["gateway_id"])
            self._authority(current, gateway["board"], row["task_number"])
            if not row["revoked"]:
                conn.execute("UPDATE scopes SET revoked=1 WHERE id=?", (scope_id,))
                self._audit(conn, current, scope_id, "scope.revoked")
            return self._public_scope(self._row(conn, "scopes", scope_id))

    def _binding(
        self,
        conn,
        principal,
        session_hash,
        node_id,
        task_number,
        operation,
        arguments,
        proposal_expires_at,
        execution_id,
        request_id,
    ):
        compile_operation(operation, arguments)
        node, nm, gateway, gm = self._node(conn, principal, node_id)
        authority = self._authority(principal, gateway["board"], task_number)
        outer, inner = self._enrollment(gateway, gm), self._route(node, nm, gateway["id"])
        binding = {
            "version": 1,
            "workspace": self.workspace_id,
            "user": principal.user_id,
            "execution": execution_id,
            "request_id": request_id,
            "agent": principal.actor,
            "session_hash": session_hash,
            "authority": authority,
            "task_number": task_number,
            "gateway": _document(outer),
            "node": _document(inner),
            "gateway_state": gm["state"],
            "node_state": nm["state"],
            "enrollment": enrollment_digest(outer, inner),
            "operation": operation,
            "arguments": arguments,
            "limits": asdict(Limits()),
            "expires_at": proposal_expires_at,
        }
        verification = conn.execute(
            "SELECT receipt_digest,expires_at FROM enrollment_verifications WHERE node_id=?",
            (node_id,),
        ).fetchone()
        if verification is not None:
            binding["verification"] = dict(verification)
        return binding

    def propose_execution(
        self, principal, *, session_hash, node_id, task_number, operation, arguments, request_id
    ):
        with self._transaction(principal, session_hash) as (conn, current):
            node, _, gateway, _ = self._node(conn, current, node_id)
            self._authority(current, gateway["board"], task_number)
            identifier(request_id)
            request = canonical(
                {
                    "node_id": node["id"],
                    "task_number": task_number,
                    "operation": operation,
                    "arguments": arguments,
                }
            ).decode()
            existing = conn.execute(
                "SELECT * FROM executions WHERE user_id=? AND request_id=?",
                (current.user_id, request_id),
            ).fetchone()
            if existing is not None:
                if existing["request"] != request:
                    raise StoreError("conflict", 409)
                return self._execution_view(conn, existing), False
            try:
                compile_operation(operation, arguments)
            except ProtocolDenied:
                raise StoreError("unsupported_operation", 400) from None
            now = self._now()
            expiry = now + MAX_PROPOSAL_SECONDS
            execution_id = uuid.uuid4().hex
            binding = self._binding(
                conn,
                current,
                session_hash,
                node_id,
                task_number,
                operation,
                arguments,
                expiry,
                execution_id,
                request_id,
            )
            digest = hashlib.sha256(canonical(binding)).hexdigest()
            self._capacity(conn, "executions", MAX_EXECUTIONS)
            conn.execute(
                "INSERT INTO executions(id,node_id,user_id,request_id,request,session_hash,binding,digest,"
                "state,reason,created_at,updated_at,proposal_expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    execution_id,
                    node_id,
                    current.user_id,
                    request_id,
                    request,
                    session_hash,
                    canonical(binding).decode(),
                    digest,
                    "awaiting_approval",
                    "transport_unavailable",
                    now,
                    now,
                    expiry,
                ),
            )
            self._audit(conn, current, execution_id, "execution.proposed")
            return self._public_execution(self._row(conn, "executions", execution_id)), True

    def _execution_access(self, conn, principal, execution_id):
        row = self._row(conn, "executions", execution_id)
        binding = json.loads(row["binding"])
        _, _, gateway, _ = self._node(conn, principal, row["node_id"])
        context = self._authority(principal, gateway["board"], binding["task_number"])
        if (context["board_id"], context["board"]) != (
            binding["authority"]["board_id"],
            binding["authority"]["board"],
        ):
            raise StoreError("not_found", 404)
        return row

    def approve_execution(
        self, principal, execution_id, *, session_hash, expected_digest, expires_at
    ):
        with self._transaction(principal, session_hash) as (conn, current):
            row = self._execution_access(conn, current, execution_id)
            _digest(expected_digest)
            expiry = self._expiry(expires_at, MAX_APPROVAL_SECONDS)
            if (
                row["state"] != "awaiting_approval"
                or self._now() >= row["proposal_expires_at"]
                or expiry > row["proposal_expires_at"]
            ):
                raise StoreError("conflict", 409)
            try:
                origin = sessions.person_principal(
                    self.gateway.db, row["user_id"], self.workspace_id
                )
                origin = self._owner(origin, row["session_hash"])
                stored = json.loads(row["binding"])
                fresh = self._binding(
                    conn,
                    origin,
                    row["session_hash"],
                    row["node_id"],
                    stored["task_number"],
                    stored["operation"],
                    stored["arguments"],
                    row["proposal_expires_at"],
                    row["id"],
                    row["request_id"],
                )
                digest = hashlib.sha256(canonical(fresh)).hexdigest()
                if not all(
                    hmac.compare_digest(digest, value) for value in (row["digest"], expected_digest)
                ) or canonical(stored) != canonical(fresh):
                    raise StoreError("conflict", 409)
            except (ControlError, ProtocolDenied, StoreError):
                raise StoreError("conflict", 409) from None
            conn.execute(
                "UPDATE executions SET state='approved',updated_at=?,approval_expires_at=?,"
                "approved_by=?,approval_session_hash=? WHERE id=?",
                (self._now(), expiry, current.user_id, session_hash, execution_id),
            )
            self._audit(conn, current, execution_id, "execution.approved")
            return self._public_execution(self._row(conn, "executions", execution_id))

    def cancel_execution(self, principal, execution_id, *, session_hash):
        with self._transaction(principal, session_hash) as (conn, current):
            row = self._execution_access(conn, current, execution_id)
            run = conn.execute(
                "SELECT * FROM execution_runs WHERE execution_id=?", (execution_id,)
            ).fetchone()
            if run is not None:
                if run["cancel_requested_at"] is None:
                    now = self._now()
                    conn.execute(
                        "UPDATE execution_runs SET cancel_requested_at=?,cancel_requested_by=?,"
                        "updated_at=? WHERE execution_id=?",
                        (now, current.user_id, now, execution_id),
                    )
                    if run["phase"] == "admitted" and run["stage"] == 0:
                        conn.execute(
                            "UPDATE execution_runs SET phase='terminal',reason='cancelled_before_start',"
                            "certainty='known',reserved=0 WHERE execution_id=?",
                            (execution_id,),
                        )
                    self._audit(conn, current, execution_id, "execution.cancel_requested")
                return self._execution_view(conn, row)
            if row["state"] != "cancelled":
                if row["state"] not in ("awaiting_approval", "approved"):
                    raise StoreError("conflict", 409)
                conn.execute(
                    "UPDATE executions SET state='cancelled',reason='cancelled_locally',updated_at=? WHERE id=?",
                    (self._now(), execution_id),
                )
                self._audit(conn, current, execution_id, "execution.cancelled")
            return self._public_execution(self._row(conn, "executions", execution_id))

    def _get_public(self, conn, current, kind, object_id):
        if kind == "gateways":
            return self._public_gateway(*self._gateway(conn, current, object_id))
        if kind == "nodes":
            row, metadata, gateway, _ = self._node(conn, current, object_id)
            return self._public_node(row, metadata, gateway)
        if kind == "scopes":
            row = self._row(conn, kind, object_id)
            # Inventories and revoke remain available after agent revocation;
            # they confer no right to use the stored discovery grant.
            gateway, _ = self._gateway(conn, current, row["gateway_id"])
            self._authority(current, gateway["board"], row["task_number"])
            return self._public_scope(row)
        return self._execution_view(conn, self._execution_access(conn, current, object_id))

    def _list(self, conn, current, kind):
        results = []
        # Tables have hard size caps; inspect the bounded inventory so hidden
        # IDs cannot consume the whole visible result page.
        for row in conn.execute(f"SELECT id FROM {kind} ORDER BY created_at DESC,id DESC"):
            try:
                results.append(self._get_public(conn, current, kind, row["id"]))
            except StoreError as exc:
                if exc.code != "not_found":
                    raise
            if len(results) == MAX_LIST:
                break
        return results

    def inventory(self, principal, *, session_hash):
        with self._transaction(principal, session_hash) as (conn, current):
            return {
                **{
                    kind: self._list(conn, current, kind)
                    for kind in ("gateways", "nodes", "scopes", "executions")
                },
                "capabilities": {"transport": False, "passive_discovery": False, "scripts": False},
                "can_manage": True,
            }

    def list_gateways(self, principal, *, session_hash):
        with self._transaction(principal, session_hash) as (conn, current):
            return self._list(conn, current, "gateways")

    def list_nodes(self, principal, *, session_hash):
        with self._transaction(principal, session_hash) as (conn, current):
            return self._list(conn, current, "nodes")

    def list_scopes(self, principal, *, session_hash):
        with self._transaction(principal, session_hash) as (conn, current):
            return self._list(conn, current, "scopes")

    def list_executions(self, principal, *, session_hash):
        with self._transaction(principal, session_hash) as (conn, current):
            return self._list(conn, current, "executions")

    def get_gateway(self, principal, gateway_id, *, session_hash):
        with self._transaction(principal, session_hash) as (conn, current):
            return self._get_public(conn, current, "gateways", gateway_id)

    def get_node(self, principal, node_id, *, session_hash):
        with self._transaction(principal, session_hash) as (conn, current):
            return self._get_public(conn, current, "nodes", node_id)

    def get_scope(self, principal, scope_id, *, session_hash):
        with self._transaction(principal, session_hash) as (conn, current):
            return self._get_public(conn, current, "scopes", scope_id)

    def get_execution(self, principal, execution_id, *, session_hash):
        with self._transaction(principal, session_hash) as (conn, current):
            return self._get_public(conn, current, "executions", execution_id)


def open_store(gateway, workspace_id, *, authority_fence=None):
    """Construct a lazy crypto-aware store at the current workspace generation."""
    identifier(workspace_id)
    if authority_fence is None:
        authority_fence = getattr(gateway, "_compute_fences", {}).get(workspace_id)
    return Store(
        lambda: Path(gateway.workspace_dir(workspace_id)) / "compute.sqlite3",
        gateway=gateway,
        workspace_id=workspace_id,
        authority_fence=authority_fence,
    )
