"""Encrypted, scoped SSH custody; no HTTP, environment credentials or network I/O.

Public integration API:

* ``CredentialBinding(workspace, owner, gateway, node, account, purpose, version)``
  is immutable validation, never a grant. Gateway bindings require an empty node.
* ``CredentialEnrollment`` contains only credential_id, binding, expires_at and
  public_key. ``CredentialRequest`` adds the exact action for authorization.
* ``initialize_wrapping_key(path, protected_data_root, *, database_path)`` is
  explicit local provisioning into an existing service-private directory, before
  the Compute database exists. It never overwrites or regenerates a lost key.
* ``CredentialStore.initialize(database_path, wrapping_key_path,
  protected_data_root, authority, *, clock=time.time)`` explicitly adds dedicated
  custody tables to compute.sqlite3. ``CredentialStore(...)`` only reopens them.
* ``store.generate(binding, context, *, expires_at=None)`` returns a *pending*
  enrollment (30-day default). ``activate(id, binding, context)`` enables exactly
  that version; it neither replaces nor revokes other enrolled versions.
  ``revoke(id, binding, context)`` permanently disables local use.
* ``acquire(id, binding, context)`` returns a nonserializable ``CredentialHandle``.
  Only ``internal_transport_private_bytes(handle)`` returns in-memory OpenSSH
  private bytes, after rechecking current authority, state, expiry and key files.
  Close handles/stores explicitly or use their context managers.

``authority(request: CredentialRequest, context: object)`` must return exactly
True from current trusted service state for generate/activate/acquire/revoke.
The opaque context is supplied by the trusted adapter, never an owner boolean.
The parent must fence these callbacks and all authority mutations together with
dispatch. This module does not establish user/session/task authority itself.

The protected data root must enclose *all* control/workspace/backup data. The
separate wrapping-key directory and custody directory must be owned by the
service with mode 0700; files require 0600, one link and no symlink components.
This initial safe-open implementation requires POSIX openat/O_NOFOLLOW. Recovery
and wrapping-key rotation require a separate offline procedure; initialization
never repairs/replaces existing custody or lost keys. Filesystem rollback is not
revocation protection. Service compromise or other processes running as its UID
are outside this boundary: deploy with OS isolation from model/task workers.
No temporary plaintext keys, remote-key removal or Python zeroization guarantee.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import sqlite3
import stat
import threading
import time
import uuid
from contextlib import contextmanager, nullcontext
from dataclasses import asdict, dataclass
from pathlib import Path

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_ACCOUNT = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}\Z")
_CREDENTIAL_ID = re.compile(r"cc_[0-9a-f]{32}\Z")
_STATES = frozenset({"pending", "active", "revoked"})
_HANDLE_GUARD = object()
_DEFAULT_LIFETIME = 30 * 24 * 60 * 60
_SCHEMA = (
    """CREATE TABLE compute_credential_meta (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        format_version INTEGER NOT NULL, store_id TEXT NOT NULL,
        wrapping_key_id TEXT NOT NULL, verifier BLOB NOT NULL)""",
    """CREATE TABLE compute_credential_keysets (
        workspace TEXT PRIMARY KEY, version INTEGER NOT NULL,
        wrapped_dek BLOB NOT NULL)""",
    """CREATE TABLE compute_credentials (
        credential_id TEXT PRIMARY KEY, workspace TEXT NOT NULL,
        owner TEXT NOT NULL, gateway TEXT NOT NULL, node TEXT NOT NULL,
        account TEXT NOT NULL, purpose TEXT NOT NULL, version INTEGER NOT NULL,
        expires_at REAL NOT NULL, keyset_version INTEGER NOT NULL,
        public_key TEXT NOT NULL, encrypted_key BLOB NOT NULL,
        state TEXT NOT NULL, state_tag BLOB NOT NULL,
        UNIQUE(workspace,owner,gateway,node,account,purpose,version))""",
)


class CredentialError(ValueError):
    """A fixed, safe reason code; never includes keys, paths or callback errors."""


def _identifier(value):
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise CredentialError("invalid_credential_binding")


def _timestamp(value):
    if type(value) not in (int, float):
        raise CredentialError("invalid_credential_expiry")
    try:
        if not math.isfinite(value) or value < 0:
            raise ValueError
        return float(value)
    except (ValueError, OverflowError):
        raise CredentialError("invalid_credential_expiry") from None


@dataclass(frozen=True, slots=True)
class CredentialBinding:
    workspace: str
    owner: str
    gateway: str
    node: str
    account: str
    purpose: str
    version: int

    def __post_init__(self):
        for value in (self.workspace, self.owner, self.gateway):
            _identifier(value)
        if self.purpose == "gateway":
            if self.node != "":
                raise CredentialError("invalid_credential_binding")
        elif self.purpose == "target":
            _identifier(self.node)
        else:
            raise CredentialError("invalid_credential_binding")
        if not isinstance(self.account, str) or not _ACCOUNT.fullmatch(self.account):
            raise CredentialError("invalid_credential_binding")
        if type(self.version) is not int or not 1 <= self.version <= 2**31 - 1:
            raise CredentialError("invalid_credential_binding")


@dataclass(frozen=True, slots=True)
class CredentialEnrollment:
    credential_id: str
    binding: CredentialBinding
    expires_at: float
    public_key: str


@dataclass(frozen=True, slots=True)
class CredentialRequest:
    action: str
    enrollment: CredentialEnrollment


def _path(value):
    try:
        path = Path(value)
        if not path.is_absolute() or ".." in path.parts or "\x00" in str(path):
            raise ValueError
        return path
    except (TypeError, ValueError):
        raise CredentialError("credential_unavailable") from None


def _open_directory(path, *, private=False):
    """Walk with dirfds: resolving first would conceal symlink components."""
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
        raise CredentialError("credential_unavailable")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open("/", flags)
    try:
        for part in path.parts[1:]:
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            # A root-owned sticky /tmp may contain a private service directory.
            sticky_root = info.st_uid == 0 and info.st_mode & stat.S_ISVTX
            if info.st_uid not in (0, os.geteuid()) or (info.st_mode & 0o022 and not sticky_root):
                raise CredentialError("credential_unavailable")
        info = os.fstat(fd)
        if private and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700):
            raise CredentialError("credential_unavailable")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _private_file(fd):
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
    ):
        raise CredentialError("credential_unavailable")
    return info


def _external_key_path(wrapping_key_path, protected_data_root):
    key, root = _path(wrapping_key_path), _path(protected_data_root)
    source = Path(__file__).absolute().parents[2]
    if key.is_relative_to(root) or key.is_relative_to(source):
        raise CredentialError("credential_unavailable")
    fd = _open_directory(root, private=True)
    os.close(fd)
    return key, root


def _read_key(path):
    parent = _open_directory(path.parent, private=True)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            _private_file(fd)
            raw = os.read(fd, 33)
            if len(raw) != 32:
                raise CredentialError("credential_unavailable")
            return raw
        finally:
            os.close(fd)
    finally:
        os.close(parent)


def _aad(kind, *fields):
    import json

    return json.dumps(
        ["commonplace-compute-custody", 1, kind, *fields],
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def _seal(key, raw, aad):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = os.urandom(12)
    return nonce + AESGCM(key).encrypt(nonce, raw, aad)


def _unseal(key, raw, aad):
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        if not isinstance(raw, bytes) or len(raw) < 28:
            raise ValueError
        return AESGCM(key).decrypt(raw[:12], raw[12:], aad)
    except (InvalidTag, TypeError, ValueError):
        raise CredentialError("credential_unavailable") from None


def initialize_wrapping_key(wrapping_key_path, protected_data_root, *, database_path):
    """Create a new external 256-bit key; caller provisions its private parent.

    No key bytes are returned. Never call this from request handling. Existing
    files (including symlinks) are refused rather than read, repaired or replaced.
    The database must not exist: recovery of existing ciphertext requires its
    original wrapping key, not fresh initialization. Provision before creating
    shared Compute tables; opening/adding custody tables never creates keys.
    """
    parent = None
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        path, root = _external_key_path(wrapping_key_path, protected_data_root)
        database = _path(database_path)
        if not database.is_relative_to(root) or database == root:
            raise ValueError
        database_parent = _open_directory(database.parent, private=True)
        try:
            try:
                os.stat(database.name, dir_fd=database_parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise CredentialError("credential_unavailable")
        finally:
            os.close(database_parent)
        parent = _open_directory(path.parent, private=True)
        fd = os.open(
            path.name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent,
        )
        try:
            _private_file(fd)
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(AESGCM.generate_key(bit_length=256))
                stream.flush()
                os.fsync(fd)
            os.fsync(parent)
        finally:
            os.close(fd)
    except (ImportError, OSError, ValueError):
        raise CredentialError("credential_unavailable") from None
    finally:
        if parent is not None:
            os.close(parent)


class CredentialHandle:
    """Opaque live capability; contains no decrypted private key and cannot copy."""

    __slots__ = ("__store", "__enrollment", "__context", "__closed")

    def __init__(self, store, enrollment, context, *, _guard=None):
        if _guard is not _HANDLE_GUARD:
            raise CredentialError("credential_denied")
        self.__store = store
        self.__enrollment = enrollment
        self.__context = context
        self.__closed = False

    def __repr__(self):
        return "<CredentialHandle redacted>"

    def __reduce_ex__(self, protocol):
        raise TypeError("credential handles cannot be serialized")

    def __getstate__(self):
        raise TypeError("credential handles cannot be serialized")

    def __copy__(self):
        raise TypeError("credential handles cannot be copied")

    def __deepcopy__(self, memo):
        raise TypeError("credential handles cannot be copied")

    def __enter__(self):
        if self.__closed:
            raise CredentialError("credential_closed")
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self.__closed = True
        self.__context = self.__store = self.__enrollment = None

    def _private_bytes_for_transport(self, guard):
        if guard is not _HANDLE_GUARD:
            raise CredentialError("credential_denied")
        if self.__closed:
            raise CredentialError("credential_closed")
        return self.__store._transport_bytes(
            self.__enrollment, self.__context, _guard=_HANDLE_GUARD
        )


def internal_transport_private_bytes(handle):
    """Internal transport boundary only; caller must keep returned bytes in RAM."""
    if type(handle) is not CredentialHandle:
        raise CredentialError("credential_denied")
    return handle._private_bytes_for_transport(_HANDLE_GUARD)


class CredentialStore:
    """Explicit encrypted Compute tables; opening existing custody never creates."""

    def __init__(
        self, database_path, wrapping_key_path, protected_data_root, authority, *, clock=time.time
    ):
        self._setup(database_path, wrapping_key_path, protected_data_root, authority, clock)
        try:
            self._open_database(create=False)
            with self._transaction():
                self._wrapping_key()
        except (ImportError, OSError, sqlite3.Error, ValueError):
            self.close()
            raise CredentialError("credential_unavailable") from None

    @classmethod
    def initialize(
        cls, database_path, wrapping_key_path, protected_data_root, authority, *, clock=time.time
    ):
        """Explicitly add custody tables to a new or existing Compute database.

        Already-present custody tables refuse initialization, including partial
        or corrupt ones. Missing/wrong wrapping keys never cause regeneration.
        """
        store = cls.__new__(cls)
        store._setup(database_path, wrapping_key_path, protected_data_root, authority, clock)
        try:
            # Read before database creation: missing keys must not create state.
            wrapping = _read_key(store._key_path)
            store._open_database(create=True)
            with store._transaction(write=True, initialize=True):
                store_id = uuid.uuid4().hex
                key_id = hashlib.sha256(wrapping).hexdigest()
                verifier = _seal(wrapping, b"compute-custody-v1", _aad("store", store_id, key_id))
                store._conn.execute(
                    "INSERT INTO compute_credential_meta VALUES (1,1,?,?,?)",
                    (store_id, key_id, verifier),
                )
            return store
        except (ImportError, OSError, sqlite3.Error, ValueError):
            store.close()
            raise CredentialError("credential_unavailable") from None

    def _setup(self, database_path, wrapping_key_path, protected_data_root, authority, clock):
        self._conn = self._directory_fd = self._database_fd = None
        self._lock = threading.RLock()
        self._closed = False
        try:
            self._key_path, root = _external_key_path(wrapping_key_path, protected_data_root)
            self._database_path = _path(database_path)
            if not self._database_path.is_relative_to(root) or self._database_path == root:
                raise ValueError
            if not callable(authority) or not callable(clock):
                raise ValueError
            self._authority, self._clock = authority, clock
        except (OSError, ValueError):
            raise CredentialError("credential_unavailable") from None

    def _open_database(self, *, create):
        self._directory_fd = _open_directory(self._database_path.parent, private=True)
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
        try:
            self._database_fd = os.open(self._database_path.name, flags, dir_fd=self._directory_fd)
        except FileNotFoundError:
            if not create:
                raise
            self._database_fd = os.open(
                self._database_path.name,
                flags | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=self._directory_fd,
            )
        _private_file(self._database_fd)
        # The parent dirfd stays pinned for the SQLite connection and its journal.
        path = f"/proc/self/fd/{self._directory_fd}/{self._database_path.name}"
        self._check_files()
        self._conn = sqlite3.connect(path, timeout=1, isolation_level=None, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._check_files()
        self._conn.execute("PRAGMA temp_store=MEMORY")
        self._conn.execute("PRAGMA trusted_schema=OFF")

    def _check_files(self):
        directory = _open_directory(self._database_path.parent, private=True)
        try:
            current_directory = os.fstat(directory)
            original_directory = os.fstat(self._directory_fd)
            if (current_directory.st_dev, current_directory.st_ino) != (
                original_directory.st_dev,
                original_directory.st_ino,
            ):
                raise CredentialError("credential_unavailable")
            original = _private_file(self._database_fd)
            current = os.stat(self._database_path.name, dir_fd=directory, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (original.st_dev, original.st_ino):
                raise CredentialError("credential_unavailable")
            for suffix in ("-journal", "-wal", "-shm"):
                try:
                    fd = os.open(
                        self._database_path.name + suffix,
                        os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                        dir_fd=directory,
                    )
                except FileNotFoundError:
                    continue
                try:
                    _private_file(fd)
                finally:
                    os.close(fd)
        finally:
            os.close(directory)

    @contextmanager
    def _transaction(self, *, write=False, initialize=False):
        from .fence import _mutation

        # Authority callback may acquire ControlDB; always take its coordinator
        # before the custody lock, including final handle-to-key conversion.
        with _mutation(self._database_path):
            with self._fenced_transaction(write=write, initialize=initialize):
                yield

    @contextmanager
    def _fenced_transaction(self, *, write=False, initialize=False):
        from .fence import _coordinator

        with self._lock:
            if self._closed:
                raise CredentialError("credential_closed")
            try:
                self._check_files()
                self._conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                try:
                    # First attachment needs the tables; schema and guards must
                    # still commit together under the same coordinator lock.
                    if initialize:
                        for statement in _SCHEMA:
                            self._conn.execute(statement)
                    coordinator = _coordinator(self._database_path)
                    guard = (
                        coordinator.custody_write(self._conn, str(self._database_path))
                        if coordinator is not None and write
                        else nullcontext()
                    )
                    with guard:
                        yield
                    self._conn.execute("COMMIT")
                except BaseException:
                    self._conn.execute("ROLLBACK")
                    raise
            except (ImportError, OSError, sqlite3.Error):
                raise CredentialError("credential_unavailable") from None

    def _wrapping_key(self):
        wrapping = _read_key(self._key_path)
        meta = self._conn.execute("SELECT * FROM compute_credential_meta").fetchall()
        if len(meta) != 1:
            raise CredentialError("credential_unavailable")
        meta = meta[0]
        if (
            meta["singleton"] != 1
            or meta["format_version"] != 1
            or not isinstance(meta["store_id"], str)
            or not re.fullmatch(r"[0-9a-f]{32}", meta["store_id"])
            or meta["wrapping_key_id"] != hashlib.sha256(wrapping).hexdigest()
            or _unseal(
                wrapping,
                meta["verifier"],
                _aad("store", meta["store_id"], meta["wrapping_key_id"]),
            )
            != b"compute-custody-v1"
        ):
            raise CredentialError("credential_unavailable")
        return wrapping, meta

    def _workspace_key(self, workspace, *, create=False):
        wrapping, meta = self._wrapping_key()
        row = self._conn.execute(
            "SELECT * FROM compute_credential_keysets WHERE workspace=?", (workspace,)
        ).fetchone()
        aad = _aad("workspace-key", meta["store_id"], meta["wrapping_key_id"], workspace, 1)
        if row is None:
            if (
                not create
                or self._conn.execute(
                    "SELECT 1 FROM compute_credentials WHERE workspace=? LIMIT 1", (workspace,)
                ).fetchone()
            ):
                raise CredentialError("credential_unavailable")
            key = os.urandom(32)
            self._conn.execute(
                "INSERT INTO compute_credential_keysets VALUES (?,1,?)",
                (workspace, _seal(wrapping, key, aad)),
            )
            return key
        if row["version"] != 1:
            raise CredentialError("credential_unavailable")
        key = _unseal(wrapping, row["wrapped_dek"], aad)
        if len(key) != 32:
            raise CredentialError("credential_unavailable")
        return key

    def _authorize(self, action, enrollment, context):
        try:
            allowed = self._authority(CredentialRequest(action, enrollment), context)
        except Exception:
            raise CredentialError("credential_denied") from None
        if allowed is not True:
            raise CredentialError("credential_denied")

    @staticmethod
    def _binding(binding):
        if type(binding) is not CredentialBinding:
            raise CredentialError("invalid_credential_binding")
        binding.__post_init__()

    def _now(self):
        try:
            return _timestamp(self._clock())
        except Exception:
            raise CredentialError("credential_unavailable") from None

    def _load(self, credential_id, binding):
        self._binding(binding)
        if not isinstance(credential_id, str) or not _CREDENTIAL_ID.fullmatch(credential_id):
            raise CredentialError("credential_denied")
        row = self._conn.execute(
            "SELECT * FROM compute_credentials WHERE credential_id=?", (credential_id,)
        ).fetchone()
        if row is None or any(row[name] != value for name, value in asdict(binding).items()):
            raise CredentialError("credential_denied")
        if row["keyset_version"] != 1 or row["state"] not in _STATES:
            raise CredentialError("credential_unavailable")
        if any(
            not isinstance(row[name], bytes) or len(row[name]) < 28
            for name in ("encrypted_key", "state_tag")
        ):
            raise CredentialError("credential_unavailable")
        expiry = _timestamp(row["expires_at"])
        public = row["public_key"]
        if (
            not isinstance(public, str)
            or not public.startswith("ssh-ed25519 ")
            or len(public) > 128
        ):
            raise CredentialError("credential_unavailable")
        return row, CredentialEnrollment(credential_id, binding, expiry, public)

    @staticmethod
    def _credential_aad(enrollment):
        return _aad("private-key", asdict(enrollment), 1)

    def _state_aad(self, enrollment, ciphertext, state):
        return _aad("state", asdict(enrollment), 1, hashlib.sha256(ciphertext).hexdigest(), state)

    def _authenticated(self, row, enrollment):
        key = self._workspace_key(enrollment.binding.workspace)
        raw = _unseal(
            key,
            row["state_tag"],
            self._state_aad(enrollment, row["encrypted_key"], row["state"]),
        )
        if raw != b"":
            raise CredentialError("credential_unavailable")
        return key

    def _live(self, row, enrollment, *, active):
        if row["state"] == "revoked":
            raise CredentialError("credential_revoked")
        if self._now() >= enrollment.expires_at:
            raise CredentialError("credential_expired")
        if active and row["state"] != "active":
            raise CredentialError("credential_pending")

    def generate(self, binding, context, *, expires_at=None):
        """Generate and immediately encrypt a dedicated Ed25519 key, pending enrollment."""
        self._binding(binding)
        now = self._now()
        expiry = now + _DEFAULT_LIFETIME if expires_at is None else _timestamp(expires_at)
        if expiry <= now:
            raise CredentialError("credential_expired")
        with self._transaction(write=True):
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

            credential_id = "cc_" + uuid.uuid4().hex
            # Authorization needs no public key; no private material exists yet.
            request = CredentialEnrollment(credential_id, binding, expiry, "")
            self._authorize("generate", request, context)
            key = self._workspace_key(binding.workspace, create=True)
            private = Ed25519PrivateKey.generate()
            public = (
                private.public_key()
                .public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
                .decode("ascii")
            )
            enrollment = CredentialEnrollment(credential_id, binding, expiry, public)
            raw = private.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.OpenSSH,
                serialization.NoEncryption(),
            )
            try:
                ciphertext = _seal(key, raw, self._credential_aad(enrollment))
            finally:
                del raw, private
            tag = _seal(key, b"", self._state_aad(enrollment, ciphertext, "pending"))
            self._conn.execute(
                "INSERT INTO compute_credentials VALUES (?,?,?,?,?,?,?,?,?,1,?,?,?,?)",
                (
                    credential_id,
                    binding.workspace,
                    binding.owner,
                    binding.gateway,
                    binding.node,
                    binding.account,
                    binding.purpose,
                    binding.version,
                    expiry,
                    public,
                    ciphertext,
                    "pending",
                    tag,
                ),
            )
            return enrollment

    def _change_state(self, credential_id, binding, context, state, action):
        with self._transaction(write=True):
            row, enrollment = self._load(credential_id, binding)
            self._authorize(action, enrollment, context)
            key = self._authenticated(row, enrollment)
            if state == "active":
                self._live(row, enrollment, active=False)
            tag = _seal(key, b"", self._state_aad(enrollment, row["encrypted_key"], state))
            self._conn.execute(
                "UPDATE compute_credentials SET state=?,state_tag=? WHERE credential_id=?",
                (state, tag, credential_id),
            )

    def activate(self, credential_id, binding, context):
        """Explicit activation; installation/pin verification belongs to the parent."""
        self._change_state(credential_id, binding, context, "active", "activate")

    def revoke(self, credential_id, binding, context):
        """Permanently deny local acquisition; remote authorized keys need removal."""
        self._change_state(credential_id, binding, context, "revoked", "revoke")

    def acquire(self, credential_id, binding, context):
        with self._transaction():
            row, enrollment = self._load(credential_id, binding)
            self._authorize("acquire", enrollment, context)
            self._authenticated(row, enrollment)
            self._live(row, enrollment, active=True)
            return CredentialHandle(self, enrollment, context, _guard=_HANDLE_GUARD)

    def _transport_bytes(self, expected, context, *, _guard=None):
        if _guard is not _HANDLE_GUARD:
            raise CredentialError("credential_denied")
        with self._transaction():
            row, enrollment = self._load(expected.credential_id, expected.binding)
            if enrollment != expected:
                raise CredentialError("credential_unavailable")
            self._authorize("acquire", enrollment, context)
            key = self._authenticated(row, enrollment)
            self._live(row, enrollment, active=True)
            return _unseal(key, row["encrypted_key"], self._credential_aad(enrollment))

    def close(self):
        with self._lock:
            self._closed = True
            if self._conn is not None:
                self._conn.close()
                self._conn = None
            for name in ("_database_fd", "_directory_fd"):
                fd = getattr(self, name)
                if fd is not None:
                    os.close(fd)
                    setattr(self, name, None)

    def __enter__(self):
        if self._closed:
            raise CredentialError("credential_closed")
        return self

    def __exit__(self, *exc):
        self.close()
