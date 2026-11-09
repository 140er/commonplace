"""Local POSIX authority ordering, with durable exclusion of legacy writers.

Lock order is coordinator (flock + reentrant thread lock), ControlDB._lock,
workspace SQLite transaction, custody lock. All waits are bounded. The service
UID and its files must be isolated from workers; arbitrary SQL/schema access by
that UID and filesystem rollback are outside this boundary.

PARTICIPATION maps actual central seams to guarded tables. Persistent triggers
call a connection-local function which requires the coordinator and advances a
FULL-synchronous epoch *before* the authority write. A failed/rolled-back write
may advance the epoch: conservative invalidation is intentional. Legacy/raw
connections have no function and cannot mutate guarded tables, including when
an old prepared statement is invalidated by the new schema.

Custody keeps trusted_schema=OFF. Its guards unconditionally refuse writes;
the participating transaction removes and reinstalls them inside the same
BEGIN IMMEDIATE transaction, under the coordinator. Crash rollback restores the
guards. This requires no unsafe UDF or weakening of custody safe-open rules.

This implements local ordering, not an authenticated remote controller channel.
A local lease deliberately lacks gateway_admission. Execution stays disabled
until that separate integration exists. The existing helper authenticates SSH,
binds request/epoch and consumes its nonce; a pre-revocation issued permit is
already in flight, never an instantaneous remote-revocation promise.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
import uuid
from abc import ABC, abstractmethod
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path

from .protocol import identifier, positive_integer

LOCAL_DOMAINS = frozenset({"control", "boards", "compute", "custody", "enrollment"})
REQUIRED_DOMAINS = LOCAL_DOMAINS | {"gateway_admission"}
PARTICIPATION = {
    "control": (
        "ControlDB._lock / ControlDB._tx; sessions.start_session/end_session; "
        "runner.registry control writes",
        ("users", "workspaces", "memberships", "connections", "sessions"),
    ),
    "boards": (
        "BoardStore.connection/transaction; BoardService.transaction; set_blockers; expire_claims",
        ("boards", "tasks", "task_blockers", "connection_task_types"),
    ),
    "compute": (
        "Store._journal; ExecutionService._accept_enrollment",
        (
            "gateways",
            "nodes",
            "scopes",
            "executions",
            "enrollment_verifications",
            "execution_runs",
            "gateway_admissions",
        ),
    ),
    "custody": (
        "CredentialStore._transaction; generate/activate/revoke; _workspace_key",
        ("compute_credential_meta", "compute_credential_keysets", "compute_credentials"),
    ),
}
_REGISTRY = {}
_REGISTRY_LOCK = threading.Lock()
_FUNCTION = "compute_authority_write_v1"
_PREFIX = "compute_authority_v1_"


class FenceDenied(ValueError):
    """No current participating authority fence is available."""


@dataclass(frozen=True)
class AuthorityLease:
    workspace: str
    epoch: int
    domains: frozenset[str]


class AuthorityFence(ABC):
    @abstractmethod
    def hold(self, workspace):
        """Exclude participating authority writers for a bounded local operation."""


@contextmanager
def _held(fence, workspace, *, local=False):
    if not isinstance(fence, AuthorityFence):
        raise FenceDenied("authority_fence_unavailable")
    with fence.hold(workspace) as lease:
        if (
            type(lease) is not AuthorityLease
            or lease.workspace != workspace
            or type(lease.domains) is not frozenset
            or not (LOCAL_DOMAINS if local else REQUIRED_DOMAINS) <= lease.domains
        ):
            raise FenceDenied("authority_fence_unavailable")
        identifier(lease.workspace)
        positive_integer(lease.epoch)
        yield lease


def _path(value):
    # Never resolve a symlink into an apparently safe service-owned path.
    value = Path(value)
    if not value.is_absolute() or ".." in value.parts:
        raise FenceDenied("authority_path_unsafe")
    return str(value)


def _coordinator(path):
    with _REGISTRY_LOCK:
        value = _REGISTRY.get(os.path.abspath(path))
    if value is not None and value.pid != os.getpid():
        # Forked workers must configure themselves; do not reuse inherited locks.
        raise FenceDenied("authority_process_unconfigured")
    return value


def _mutation(path):
    coordinator = _coordinator(path)
    return coordinator.locked() if coordinator is not None else nullcontext()


def _attach_connection(conn, path, domain):
    coordinator = _coordinator(path)
    if coordinator is not None:
        coordinator.attach(conn, path, domain)


def _guards(domain, identity):
    for table in PARTICIPATION[domain][1]:
        for operation in ("INSERT", "UPDATE", "DELETE"):
            name = f"{_PREFIX}{table}_{operation.lower()}"
            # Stage/result/audit writes are deliberately outside this list.
            # Consent changes invalidate permits; progress bookkeeping does not.
            verb = operation
            if table == "executions" and operation == "UPDATE":
                verb += (
                    " OF node_id,user_id,request,session_hash,binding,digest,state,"
                    "proposal_expires_at,approval_expires_at,approved_by,approval_session_hash"
                )
            function = (
                "compute_authority_current_v1"
                if table in ("execution_runs", "gateway_admissions")
                else _FUNCTION
            )
            body = (
                f"SELECT '{identity}'; SELECT RAISE(ABORT,'authority_writer_unavailable');"
                if domain == "custody"
                else f"SELECT {function}('{identity}');"
            )
            yield name, f"CREATE TRIGGER {name} BEFORE {verb} ON {table} BEGIN {body} END"


class _Coordinator:
    """One service-owned local control plane, shared by all configured workspaces.

    Creation is explicit trusted startup configuration, never HTTP or import.
    An existing state DB is not repaired/reinitialized when corrupt. Inodes are
    pinned; missing/replaced participant files deny leases. No secrets live here.
    """

    def __init__(self, directory, *, timeout=1.0):
        from .credentials import _open_directory, _private_file

        if type(timeout) not in (int, float) or not 0 < timeout <= 5:
            raise FenceDenied("invalid_authority_timeout")
        self.pid = os.getpid()
        self.directory, self.timeout = _path(directory), timeout
        self._mutex = threading.RLock()
        self._local = threading.local()
        self._profiles = {}
        self._participants = {}
        self._dirfd = _open_directory(Path(self.directory), private=True)
        self._files = {}
        try:
            created = []
            for name in ("authority.lock", "authority.sqlite3"):
                flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC
                try:
                    fd = os.open(name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=self._dirfd)
                    created.append(name)
                except FileExistsError:
                    fd = os.open(name, flags, dir_fd=self._dirfd)
                self._files[name] = fd
                _private_file(fd)
            if created and len(created) != 2:
                raise FenceDenied("authority_state_incomplete")
            with self.locked():
                state = self._state()
                try:
                    # Only a genuinely empty new file may be initialized.
                    if len(created) == 2:
                        state.execute("PRAGMA synchronous=FULL")
                        state.execute(
                            "CREATE TABLE authority(singleton INTEGER PRIMARY KEY, epoch INTEGER, identity TEXT)"
                        )
                        state.execute("INSERT INTO authority VALUES(1,1,?)", (uuid.uuid4().hex,))
                        os.fsync(self._dirfd)
                    self.identity = state.execute(
                        "SELECT identity FROM authority WHERE singleton=1"
                    ).fetchone()[0]
                    if (
                        type(self.identity) is not str
                        or len(self.identity) != 32
                        or any(c not in "0123456789abcdef" for c in self.identity)
                    ):
                        raise FenceDenied("authority_epoch_unavailable")
                    self.epoch()
                finally:
                    state.close()
        except BaseException:
            self.close()
            raise

    @property
    def active(self):
        return bool(getattr(self._local, "depth", 0)) and self.pid == os.getpid()

    def _check_files(self):
        from .credentials import _open_directory, _private_file

        fresh = _open_directory(Path(self.directory), private=True)
        try:
            a, b = os.fstat(fresh), os.fstat(self._dirfd)
            if (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino):
                raise FenceDenied("authority_path_changed")
            for name, fd in self._files.items():
                a = _private_file(fd)
                b = os.stat(name, dir_fd=fresh, follow_symlinks=False)
                if (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino):
                    raise FenceDenied("authority_path_changed")
        finally:
            os.close(fresh)

    @contextmanager
    def locked(self, *, timeout=None):
        import fcntl

        if self.pid != os.getpid():
            raise FenceDenied("authority_process_unconfigured")
        wait = self.timeout if timeout is None else min(self.timeout, max(0, timeout))
        deadline = time.monotonic() + wait
        if not self._mutex.acquire(timeout=wait):
            raise FenceDenied("authority_timeout")
        outer = not self.active
        acquired = False
        try:
            if outer:
                self._check_files()
                while True:
                    try:
                        fcntl.flock(self._files["authority.lock"], fcntl.LOCK_EX | fcntl.LOCK_NB)
                        acquired = True
                        break
                    except BlockingIOError:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise FenceDenied("authority_timeout") from None
                        # Internal bounded lock backoff, not a job polling loop.
                        threading.Event().wait(min(0.01, remaining))
            self._local.depth = getattr(self._local, "depth", 0) + 1
            try:
                yield
            finally:
                self._local.depth -= 1
        finally:
            if acquired:
                fcntl.flock(self._files["authority.lock"], fcntl.LOCK_UN)
            self._mutex.release()

    def _state(self):
        if not self.active:
            raise FenceDenied("authority_lock_order")
        conn = sqlite3.connect(
            f"/proc/self/fd/{self._dirfd}/authority.sqlite3",
            timeout=self.timeout,
            isolation_level=None,
        )
        conn.execute("PRAGMA synchronous=FULL")
        return conn

    def epoch(self):
        conn = self._state()
        try:
            rows = conn.execute("SELECT singleton,epoch FROM authority").fetchall()
            if len(rows) != 1 or rows[0][0] != 1:
                raise ValueError
            return positive_integer(rows[0][1], 2**63 - 2)
        except (ValueError, sqlite3.Error):
            raise FenceDenied("authority_epoch_unavailable") from None
        finally:
            conn.close()

    def advance(self):
        if not self.active:
            raise FenceDenied("authority_lock_order")
        conn = self._state()
        try:
            conn.execute("BEGIN IMMEDIATE")
            value = self.epoch() + 1
            conn.execute("UPDATE authority SET epoch=? WHERE singleton=1", (value,))
            conn.commit()
            return value
        finally:
            conn.close()

    def _write(self, identity, *, invalidates=True):
        if identity != self.identity:
            raise FenceDenied("authority_control_plane_conflict")
        return self.advance() if invalidates else self.epoch()

    def register(self, path, domain):
        path = _path(path)
        if domain not in PARTICIPATION:
            raise FenceDenied("authority_domain_unknown")
        with _REGISTRY_LOCK:
            previous = _REGISTRY.get(path)
            if previous is not None and previous is not self:
                raise FenceDenied("authority_control_plane_conflict")
            _REGISTRY[path] = self
        if path in self._participants and self._participants[path][0] != domain:
            raise FenceDenied("authority_domain_conflict")
        self._participants.setdefault(path, (domain, None))

    def attach(self, conn, path, domain):
        path = _path(path)
        if not self.active or self._participants.get(path, (None,))[0] != domain:
            raise FenceDenied("authority_lock_order")
        from .credentials import _open_directory, _private_file

        directory = _open_directory(Path(path).parent, private=True)
        try:
            fd = os.open(Path(path).name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
            try:
                info = _private_file(fd)
            finally:
                os.close(fd)
        finally:
            os.close(directory)
        current = (info.st_dev, info.st_ino)
        old = self._participants[path][1]
        if old is not None and old != current:
            raise FenceDenied("authority_participant_changed")
        if domain != "custody":
            conn.create_function(_FUNCTION, 1, self._write)
            conn.create_function(
                "compute_authority_current_v1",
                1,
                lambda identity: self._write(identity, invalidates=False),
            )
        conn.execute("PRAGMA busy_timeout=250")
        existing = dict(conn.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'"))
        for name, sql in _guards(domain, self.identity):
            if name not in existing:
                if old is not None:
                    raise FenceDenied("authority_guard_missing")
                conn.execute(sql)
            elif existing[name] != sql:
                raise FenceDenied("authority_guard_mismatch")
        self._participants[path] = (domain, current)

    def seal(self, workspace, paths):
        identifier(workspace)
        paths = tuple(_path(path) for path in paths)
        if {self._participants.get(path, (None,))[0] for path in paths} != set(PARTICIPATION):
            raise FenceDenied("authority_participation_incomplete")
        self._profiles[workspace] = paths
        with self.locked():
            self.validate(workspace)
        return _LocalAuthorityFence(self, workspace)

    def validate(self, workspace):
        from mempalace.sqlite_crypto import connect

        if not self.active or workspace not in self._profiles:
            raise FenceDenied("authority_workspace_unconfigured")
        for path in self._profiles[workspace]:
            domain, expected = self._participants[path]
            info = os.stat(path, follow_symlinks=False)
            if expected != (info.st_dev, info.st_ino):
                raise FenceDenied("authority_participant_changed")
            # Read-only schema validation, with no custody/wrapping-key read.
            opener = sqlite3.connect if domain in ("control", "custody") else connect
            conn = opener(Path(path).as_uri() + "?mode=ro", uri=True, timeout=0.1)
            try:
                existing = dict(
                    conn.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'")
                )
                if any(existing.get(name) != sql for name, sql in _guards(domain, self.identity)):
                    raise FenceDenied("authority_guard_missing")
            finally:
                conn.close()

    @contextmanager
    def custody_write(self, conn, path):
        self.attach(conn, path, "custody")
        # Caller already holds BEGIN IMMEDIATE. DDL and authority mutation are
        # atomic in this file; another process never observes absent guards.
        self.advance()
        for name, _ in _guards("custody", self.identity):
            conn.execute(f"DROP TRIGGER {name}")
        yield
        for _, sql in _guards("custody", self.identity):
            conn.execute(sql)

    def close(self):
        with _REGISTRY_LOCK:
            for path in list(_REGISTRY):
                if _REGISTRY[path] is self:
                    del _REGISTRY[path]
        for fd in self._files.values():
            os.close(fd)
        self._files.clear()
        if self._dirfd is not None:
            os.close(self._dirfd)
            self._dirfd = None


class _LocalAuthorityFence(AuthorityFence):
    def __init__(self, coordinator, workspace):
        self._coordinator, self.workspace = coordinator, workspace

    @property
    def active(self):
        return self._coordinator.active

    @contextmanager
    def hold(self, workspace):
        if workspace != self.workspace:
            raise FenceDenied("authority_workspace_unconfigured")
        with self._coordinator.locked():
            self._coordinator.validate(workspace)
            yield AuthorityLease(workspace, self._coordinator.epoch(), LOCAL_DOMAINS)


class _ControlLock:
    """Retain the existing RLock API, always taking the coordinator first."""

    def __init__(self, coordinator, lock):
        self.coordinator, self.lock = coordinator, lock
        self._local = threading.local()

    def acquire(self, blocking=True, timeout=-1):
        wait = self.coordinator.timeout if timeout < 0 else timeout
        if not blocking:
            wait = 0
        deadline = time.monotonic() + wait
        guard = self.coordinator.locked(timeout=wait)
        try:
            guard.__enter__()
        except FenceDenied:
            return False
        if not self.lock.acquire(timeout=max(0, deadline - time.monotonic())):
            guard.__exit__(None, None, None)
            return False
        stack = getattr(self._local, "stack", [])
        self._local.stack = stack
        stack.append(guard)
        return True

    def release(self):
        self.lock.release()
        self._local.stack.pop().__exit__(None, None, None)

    def _is_owned(self):
        return self.lock._is_owned()

    def __enter__(self):
        if not self.acquire():
            raise FenceDenied("authority_timeout")
        return self

    def __exit__(self, *exc):
        self.release()
