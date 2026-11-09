"""Crypto-aware, finite, workspace-local passive-discovery admission ledger.

`open_store(gateway, workspace_id, control_store=...)` is lazy. Parent installation
must supply the same participating authority fence through the control Store and
DiscoveryService. No identity/task registry, key initialization or network I/O.

Every admission permanently charges the *reserved ceiling*, including successful,
cancelled and denied jobs. This conservative policy is explicit: unused quota is
not refunded. A fresh scope is required on exhaustion. Uncertain jobs retain their
concurrency and rate reservations; elapsed deadlines never prove remote quiescence.
Backups must preserve this journal; restoring an older ledger requires disabling
collection and independent reconciliation. No automatic reconciliation is offered.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from commonplace.admission import GATE
from mempalace.sqlite_crypto import connect, row_factory

from .control import StoreError, open_store as open_control_store
from .discovery import validate_sources
from .protocol import ProtocolDenied, canonical, identifier
from .scopes import DiscoveryContext, DiscoveryLimits, NetworkScope, ScopeDenied

MAX_DISCOVERIES = 2000
MAX_LIST = 100
_SCHEMA = """
CREATE TABLE IF NOT EXISTS discovery_policy (singleton INTEGER PRIMARY KEY CHECK(singleton=1), document TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS discovery_receipts (gateway_id TEXT PRIMARY KEY, document TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS discoveries (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL, session_hash TEXT NOT NULL,
 request_id TEXT NOT NULL, intake TEXT NOT NULL, scope_id TEXT NOT NULL,
 gateway_id TEXT NOT NULL, board TEXT NOT NULL, task_number INTEGER NOT NULL,
 binding TEXT NOT NULL, state TEXT NOT NULL, reason TEXT,
 created_at REAL NOT NULL, updated_at REAL NOT NULL,
 hosts INTEGER NOT NULL, results INTEGER NOT NULL, seconds INTEGER NOT NULL,
 rate INTEGER NOT NULL, scope_concurrency INTEGER NOT NULL,
 slot INTEGER NOT NULL DEFAULT 0, authority_epoch INTEGER NOT NULL,
 worker TEXT, deadline REAL, ended_at REAL, cancel_requested INTEGER NOT NULL DEFAULT 0,
 observations TEXT NOT NULL DEFAULT '[]', usage TEXT NOT NULL DEFAULT '{}',
 UNIQUE(user_id,request_id));
CREATE INDEX IF NOT EXISTS discovery_queue ON discoveries(state,created_at);
CREATE INDEX IF NOT EXISTS discovery_scope ON discoveries(scope_id);
CREATE TRIGGER IF NOT EXISTS discovery_no_delete BEFORE DELETE ON discoveries
 BEGIN SELECT RAISE(ABORT,'discovery ledger cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS discovery_no_rebind BEFORE UPDATE OF
 id,user_id,session_hash,request_id,intake,scope_id,gateway_id,board,task_number,binding,
 created_at,hosts,results,seconds,rate,scope_concurrency,authority_epoch ON discoveries
 BEGIN SELECT RAISE(ABORT,'discovery admission cannot be rebound'); END;
CREATE TABLE IF NOT EXISTS discovery_attempts (
 discovery_id TEXT PRIMARY KEY REFERENCES discoveries(id), worker TEXT NOT NULL,
 request TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS discovery_permits (
 discovery_id TEXT PRIMARY KEY REFERENCES discovery_attempts(discovery_id),
 purpose TEXT NOT NULL, request_digest TEXT NOT NULL, authority_epoch INTEGER NOT NULL,
 helper_channel TEXT NOT NULL UNIQUE, helper_nonce TEXT NOT NULL UNIQUE,
 permit TEXT NOT NULL, issued_at REAL NOT NULL);
CREATE TRIGGER IF NOT EXISTS discovery_attempt_no_update BEFORE UPDATE ON discovery_attempts
 BEGIN SELECT RAISE(ABORT,'discovery attempt is immutable'); END;
CREATE TRIGGER IF NOT EXISTS discovery_attempt_no_delete BEFORE DELETE ON discovery_attempts
 BEGIN SELECT RAISE(ABORT,'discovery attempt cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS discovery_permit_no_update BEFORE UPDATE ON discovery_permits
 BEGIN SELECT RAISE(ABORT,'discovery permit is immutable'); END;
CREATE TRIGGER IF NOT EXISTS discovery_permit_no_delete BEFORE DELETE ON discovery_permits
 BEGIN SELECT RAISE(ABORT,'discovery permit cannot be deleted'); END;
"""


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def gateway_document(gateway):
    return {
        **{k: v for k, v in asdict(gateway).items() if k != "pin"},
        "pin": gateway.pin.document(),
    }


class DiscoveryStore:
    def __init__(self, path, *, control_store):
        self._path, self.control = path, control_store
        self.workspace_id, self.gateway = control_store.workspace_id, control_store.gateway
        self.service = None

    @contextmanager
    def _journal(self):
        try:
            path = Path(self._path() if callable(self._path) else self._path)
            with GATE.creating(path):
                path.parent.mkdir(parents=True, exist_ok=True)
                conn = connect(str(path), timeout=0.25, isolation_level=None)
            try:
                conn.row_factory = row_factory
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=FULL")
                conn.executescript(_SCHEMA)
                conn.execute("BEGIN IMMEDIATE")
                try:
                    yield conn
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise
            finally:
                conn.close()
        except sqlite3.Error:
            raise StoreError("discovery_unavailable", 503) from None

    @contextmanager
    def _transaction(self, principal, session_hash, *, admit=False):
        service = self.service
        if admit and service is not None and service.running:
            # The service validates the selected concrete admission adapter before
            # obtaining its local lease; never upgrade its declared domains.
            with service._guard() as lease:
                with self.gateway.db._lock:
                    current = self.control._owner(principal, session_hash)
                    with self.control._journal() as control:
                        with self._journal() as conn:
                            yield control, conn, current, lease
        else:
            # Read/history/cancel use the existing control Store's authority
            # transaction. A production local-only fence can still expose status
            # while strict remote discovery participation remains unavailable.
            with self.control._transaction(principal, session_hash) as (control, current):
                with self._journal() as conn:
                    yield control, conn, current, None

    def _scope(self, control, current, scope_id):
        row = self.control._row(control, "scopes", scope_id)
        if row["user_id"] != current.user_id:
            raise StoreError("not_found", 404)
        gateway, metadata = self.control._gateway(control, current, row["gateway_id"])
        authority = self.control._authority(current, gateway["board"], row["task_number"])
        agent = (
            self.control._agent(row["agent_id"], gateway["board"], row["task_number"])
            if row["agent_id"]
            else current
        )
        self.control._execution_task(agent, row["task_number"])
        raw = json.loads(row["metadata"])
        scope = NetworkScope(
            context=DiscoveryContext(**raw["context"]),
            cidrs=raw["cidrs"],
            interfaces=raw["interfaces"],
            methods=frozenset(raw["methods"]),
            approved_by=raw["approved_by"],
            expires_at=raw["expires_at"],
            limits=DiscoveryLimits(**raw["limits"]),
            revoked=bool(row["revoked"]),
        )
        expected = DiscoveryContext(
            self.workspace_id,
            authority["board"],
            gateway["id"],
            current.user_id,
            agent.connection_id or current.actor,
            str(row["task_number"]),
        )
        if scope.context != expected or scope.approved_by != current.user_id:
            raise StoreError("scope_unavailable", 409)
        return row, scope, gateway, self.control._enrollment(gateway, metadata), authority

    def _visible(self, control, current, row):
        if row["user_id"] != current.user_id:
            raise StoreError("not_found", 404)
        gateway, _ = self.control._gateway(control, current, row["gateway_id"])
        authority = self.control._authority(current, row["board"], row["task_number"])
        if gateway["board"] != authority["board"]:
            raise StoreError("not_found", 404)
        # Scope revocation/expiry does not hide historical intake from its owner.
        scope = self.control._row(control, "scopes", row["scope_id"])
        if scope["user_id"] != current.user_id or scope["gateway_id"] != gateway["id"]:
            raise StoreError("not_found", 404)
        return row

    @staticmethod
    def _row(conn, discovery_id):
        try:
            identifier(discovery_id)
        except ProtocolDenied:
            raise StoreError("not_found", 404) from None
        row = conn.execute("SELECT * FROM discoveries WHERE id=?", (discovery_id,)).fetchone()
        if row is None:
            raise StoreError("not_found", 404)
        return row

    @staticmethod
    def _public(row):
        fields = (
            "id scope_id gateway_id board task_number state reason created_at updated_at".split()
        )
        return {
            **{name: row[name] for name in fields},
            "observations": json.loads(row["observations"]),
            "usage": json.loads(row["usage"]),
        }

    def list_discoveries(self, principal, *, session_hash):
        with self._transaction(principal, session_hash) as (control, conn, current, lease):
            items = []
            for row in conn.execute(
                "SELECT * FROM discoveries WHERE user_id=? ORDER BY created_at DESC,id DESC",
                (current.user_id,),
            ):
                try:
                    items.append(self._public(self._visible(control, current, row)))
                except StoreError as exc:
                    if exc.code != "not_found":
                        raise
                if len(items) == MAX_LIST:
                    break
            return items

    def get_discovery(self, principal, discovery_id, *, session_hash):
        with self._transaction(principal, session_hash) as (control, conn, current, lease):
            return self._public(self._visible(control, current, self._row(conn, discovery_id)))

    def submit(self, principal, *, session_hash, scope_id, sources, request_id):
        try:
            identifier(scope_id)
            identifier(request_id)
            sources = validate_sources(sources)
            intake = canonical(
                {"scope_id": scope_id, "sources": sources, "request_id": request_id}
            ).decode()
        except (ProtocolDenied, ScopeDenied):
            raise StoreError() from None
        with self._transaction(principal, session_hash, admit=True) as (
            control,
            conn,
            current,
            lease,
        ):
            replay = conn.execute(
                "SELECT * FROM discoveries WHERE user_id=? AND request_id=?",
                (current.user_id, request_id),
            ).fetchone()
            if replay is not None:
                self._visible(control, current, replay)
                if replay["intake"] != intake:
                    raise StoreError("conflict", 409)
                return self._public(replay), False
            service = self.service
            if service is None or not service.running:
                raise StoreError("discovery_unavailable", 503)
            # Already inside the control Store's exact fence, do not reenter it.
            if lease is None or service.fence is not self.control._authority_fence:
                raise StoreError("discovery_unavailable", 503)
            scope_row, scope, gateway, outer, authority = self._scope(control, current, scope_id)
            binding = service._binding(conn, scope, outer, authority, sources)
            service._policy(conn)
            if conn.execute("SELECT count(*) FROM discoveries").fetchone()[0] >= MAX_DISCOVERIES:
                raise StoreError("discovery_capacity", 409)
            used = conn.execute(
                "SELECT coalesce(sum(hosts),0),coalesce(sum(results),0),coalesce(sum(seconds),0) FROM discoveries WHERE scope_id=?",
                (scope_id,),
            ).fetchone()
            remaining = [
                scope.limits.hosts - used[0],
                scope.limits.results - used[1],
                scope.limits.duration_seconds - used[2],
            ]
            if min(remaining) <= 0:
                raise StoreError("scope_exhausted", 409)
            limits = DiscoveryLimits(
                hosts=min(service.job_limits.hosts, remaining[0]),
                concurrency=1,
                rate_per_second=min(
                    service.job_limits.rate_per_second,
                    scope.limits.rate_per_second,
                    service.policy.workspace_rate,
                    service.policy.gateway_rate,
                ),
                duration_seconds=min(service.job_limits.duration_seconds, remaining[2]),
                results=min(service.job_limits.results, remaining[1]),
            )
            now, discovery_id = self.control._now(), uuid.uuid4().hex
            conn.execute(
                "INSERT INTO discoveries(id,user_id,session_hash,request_id,intake,scope_id,gateway_id,board,task_number,binding,state,created_at,updated_at,hosts,results,seconds,rate,scope_concurrency,authority_epoch,usage) VALUES (?,?,?,?,?,?,?,?,?,?,'queued',?,?,?,?,?,?,?,?,?)",
                (
                    discovery_id,
                    current.user_id,
                    session_hash,
                    request_id,
                    intake,
                    scope_id,
                    gateway["id"],
                    authority["board"],
                    scope_row["task_number"],
                    canonical(binding).decode(),
                    now,
                    now,
                    limits.hosts,
                    limits.results,
                    limits.duration_seconds,
                    limits.rate_per_second,
                    scope.limits.concurrency,
                    lease.epoch,
                    canonical(
                        {
                            "reserved_hosts": limits.hosts,
                            "reserved_results": limits.results,
                            "reserved_seconds": limits.duration_seconds,
                        }
                    ).decode(),
                ),
            )
            return self._public(self._row(conn, discovery_id)), True

    def cancel(self, principal, discovery_id, *, session_hash):
        with self._transaction(principal, session_hash) as (control, conn, current, lease):
            row = self._visible(control, current, self._row(conn, discovery_id))
            if row["state"] == "queued":
                conn.execute(
                    "UPDATE discoveries SET state='cancelled',reason='cancelled_before_dispatch',cancel_requested=1,updated_at=? WHERE id=?",
                    (self.control._now(), discovery_id),
                )
            elif row["state"] in ("claimed", "dispatching", "uncertain"):
                # This records intent; it never asserts remote termination.
                conn.execute(
                    "UPDATE discoveries SET cancel_requested=1,reason='cancellation_requested',updated_at=? WHERE id=?",
                    (self.control._now(), discovery_id),
                )
            return self._public(self._row(conn, discovery_id))


def open_store(gateway, workspace_id, *, control_store=None):
    """Construct lazily using the current crypto-aware workspace generation."""
    identifier(workspace_id)
    control = (
        control_store if control_store is not None else open_control_store(gateway, workspace_id)
    )
    if control.gateway is not gateway or control.workspace_id != workspace_id:
        raise ValueError("discovery_store_mismatch")
    return DiscoveryStore(
        lambda: Path(gateway.workspace_dir(workspace_id)) / "compute-discovery.sqlite3",
        control_store=control,
    )
