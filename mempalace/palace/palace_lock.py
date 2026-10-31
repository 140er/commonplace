# Loaded into mempalace.palace via exec (see __init__.py). Not a standalone module.
if __name__ != "mempalace.palace":
    raise ImportError(f"{__name__} is an implementation fragment; import mempalace.palace")


class MineAlreadyRunning(RuntimeError):
    """Raised when another `mempalace mine` already holds the per-palace lock."""


class MineFileErrors(RuntimeError):
    """Raised at the end of a mine when one or more files could not be filed.

    The mine finished its loop and printed its summary. Each failed file kept
    whatever drawers it had and is retried by the next mine, but the run is
    not a success, and no caller may report it as one.
    """

    def __init__(self, failed_files: list[str], total_files: int) -> None:
        if not failed_files:
            raise ValueError("MineFileErrors requires at least one failed file")
        super().__init__(
            f"{len(failed_files)} of {total_files} file(s) failed to mine; "
            "per-file errors went to stderr (the server's stderr over MCP, the "
            "terminal on the CLI), and the next mine retries them"
        )
        self.failed_files: tuple[str, ...] = tuple(failed_files)
        self.total_files = total_files


def print_files_failed(failed_files: list) -> None:
    """Add the failed-file count to a mine summary; nothing when none failed."""
    if failed_files:
        print(
            f"  Files failed: {len(failed_files)} (not mined; per-file errors went to "
            "stderr, retried on the next mine)"
        )


def raise_if_files_failed(failed_files: list, total_files: int) -> None:
    """End a mine that could not file every file with :class:`MineFileErrors`."""
    if failed_files:
        raise MineFileErrors(failed_files, total_files)


class MineValidationError(RuntimeError):
    """Raised at end of mine when PRAGMA quick_check on the palace reports errors."""

    def __init__(self, palace_path: str, errors: list[str]) -> None:
        if not errors:
            raise ValueError("MineValidationError requires at least one error string")
        if not palace_path:
            raise ValueError("MineValidationError requires a non-empty palace_path")
        # Name the SQLite that produced the verdict: a build that cannot detect a given FTS5
        # fault reports the same "clean" as one that can. The CLI handler
        # renders the abort banner, which carries the version; the MCP `mine`
        # tool and the daemon's job runner render this message instead, so it
        # belongs in the message.
        super().__init__(
            f"FTS5/SQLite quick_check failed: {len(errors)} issue(s) "
            f"(SQLite {sqlite3.sqlite_version})"
        )
        self.palace_path = palace_path
        # Freeze the forensic snapshot so handlers cannot mutate it.
        self.errors: tuple[str, ...] = tuple(errors)


def _validate_palace_fts5_after_mine(
    palace_path: str, *, writes_since: Optional[int] = None
) -> None:
    """Raise MineValidationError if PRAGMA quick_check reports any error after a mine.

    ``writes_since`` is :func:`palace_write_serial` read before the mine's
    first write. When nothing was written since, the check is skipped: a mine
    that filed, purged and registered nothing left the database as it found
    it, and on a 1M-drawer palace the full quick_check costs ~40 s warm, most
    of a no-op mine. ``None`` always checks.

    Reuses the same primitive that `cmd_repair` already runs as preflight so the
    operator sees the same recovery banner regardless of which command surfaces
    the bug.

    An isolated FTS5 inverted-index corruption (the common case after a
    killed-mid-write mine) is auto-healed in place first via the same
    `maybe_autoheal_fts5_index` rebuild `cmd_repair` already runs as its own
    preflight step — so a normal `mine` self-heals the recoverable case
    instead of forcing the operator to run `mempalace repair` by hand for a
    derived index. Nothing re-files afterwards on this path, so the heal is the
    last word here: it checks the content table against `embedding_metadata`
    before rebuilding from it, and declines when it cannot.
    """
    if writes_since is not None and palace_write_serial() == writes_since:
        return
    if resolve_backend_name(palace_path) != "chroma":
        return

    # Defer-import: keeps the repair module graph out of mine's hot import path.
    from ..repair import _close_chroma_handles, maybe_autoheal_fts5_index, sqlite_integrity_errors

    # Pass the live singleton so the writer's cached PersistentClient actually
    # gets closed and WAL flushes before the read-only sqlite3 re-open.
    # A transient ChromaBackend (the default) would only clear its own empty
    # `_clients` dict and leave _DEFAULT_BACKEND's live handle in place,
    # which on Windows keeps the sqlite file mmap'd.
    _close_chroma_handles(palace_path, backend=_DEFAULT_BACKEND)

    errors = sqlite_integrity_errors(palace_path)
    if errors:
        # Not the default print: this runs inside the MCP server process too
        # (mcp_server.tool_mine -> miner.mine), where stdout is the JSON-RPC
        # transport -- a stray print() here would corrupt the protocol stream
        # and crash the connection.
        #
        # warning, not info: this module logs through the `mempalace.palace`
        # logger, which sets no level of its own, and nothing configures logging
        # on the `mempalace mine` path -- so root keeps its default and info
        # records are dropped. Every message this call can emit describes a palace whose
        # quick_check already failed -- a rebuild being attempted, refused, or
        # completed against the operator's data -- so warning is both the level
        # that survives and the level that fits.
        errors = maybe_autoheal_fts5_index(palace_path, errors, progress=logger.warning)
    if errors:
        raise MineValidationError(palace_path, errors)


def palace_write_serial() -> int:
    """A counter that moves whenever this process writes to a Chroma palace.

    A miner reads it before its first write and hands it to
    :func:`_validate_palace_fts5_after_mine` as ``writes_since``.
    """
    from ..backends.chroma import chroma_write_serial

    return chroma_write_serial()


# Process-wide record of palaces this PROCESS already holds the lock for. Used
# by `mine_palace_lock` to short-circuit re-entrant acquisition from the same
# process (e.g. miner.mine() acquires the outer lock then calls
# ChromaCollection.upsert which now also tries to acquire). Without this guard
# the inner call would block on its own outer flock (Linux fcntl locks are per
# open file description, so a second open of the lock file from the same process
# is a distinct lock and self-conflicts / EWOULDBLOCKs).
#
# This MUST be process-wide, not thread-local: the MCP HTTP transport
# (ThreadingHTTPServer) acquires the long-lived writer-lease on one thread
# (`mcp_server._acquire_mcp_writer_lock`) but dispatches each write request on a
# different worker thread. A thread-local guard makes those handlers fail to see
# the process-held lease, re-acquire the flock, and self-conflict
# ("palace ... is held by PID <self>"). HTTP writes are
# serialized by ``_HTTP_REQUEST_LOCK``'s exclusive side, so the process is the
# correct re-entrancy boundary even though palace reads may overlap.
#
# The holder set is tagged with ``pid`` so that a forked child does NOT inherit
# re-entrant credit from its parent: the OS-level flock IS NOT inherited as a
# "we hold it" semantically — the child must reacquire. The pid check clears
# stale state so a forked child correctly hits the fcntl path. Access is guarded
# by ``_palace_lock_guard`` because the registry is shared across threads.
# Acquisition and registration happen under that guard, closing the gap where
# a second thread could conflict with its own not-yet-registered OS lease.
# Every overlapping context retains the descriptor until the last user exits.
#
# Fork safety: ``_palace_lock_guard`` is a real ``threading.Lock``, so a child
# forked while another thread held it would inherit it locked (the holder thread
# does not exist in the child) and deadlock on the next acquire. An at-fork
# handler (registered below) replaces the guard with a fresh unlocked lock and
# clears state in the child, which must reacquire the flock anyway.
_palace_lock_guard = threading.Lock()
_palace_lock_pid = None
_palace_lock_keys = {}


class _PalaceLockHold:
    def __init__(self, lock_file):
        self.file = lock_file
        self.users = 1


def _reset_palace_lock_state_after_fork() -> None:
    """Reset lock state in a forked child to avoid an inherited-locked deadlock."""
    global _palace_lock_guard, _palace_lock_pid, _palace_lock_keys
    _palace_lock_guard = threading.Lock()
    # Close inherited descriptors without unlocking their shared open-file
    # descriptions: the parent must retain ownership of its OS leases.
    for hold in _palace_lock_keys.values():
        hold.file.close()
    _palace_lock_keys = {}
    _palace_lock_pid = os.getpid()


# Availability: Unix (no-op elsewhere — Windows has no fork()).
if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_palace_lock_state_after_fork)


def _holder_keys_locked():
    """Return the process-wide lease registry, refreshing after fork.

    Caller MUST hold ``_palace_lock_guard``.
    """
    global _palace_lock_pid, _palace_lock_keys
    current_pid = os.getpid()
    if _palace_lock_pid != current_pid:
        _palace_lock_keys = {}
        _palace_lock_pid = current_pid
    return _palace_lock_keys


def _mark_held(lock_key: str, hold: _PalaceLockHold) -> None:
    """Register an acquired lease; caller holds ``_palace_lock_guard``."""
    _holder_keys_locked()[lock_key] = hold


def _format_lock_holder(content: str) -> str:
    """Render a lock-file body as 'PID N (cmdline)' for diagnostic messages."""
    parts = content.split(maxsplit=1)
    if not parts or not parts[0].isdigit():
        return "another writer (identity not recorded)"
    pid = parts[0]
    if len(parts) > 1 and parts[1].strip():
        return f"PID {pid} ({parts[1].strip()})"
    return f"PID {pid}"


# Byte 0 of the lock file is reserved as the OS lock sentinel.
# Holder identity is written from byte 1 onward so contenders can read
# the identity without colliding with byte 0 (Windows msvcrt.locking
# blocks both reads and writes on the locked byte).
_LOCK_SENTINEL_BYTES = 1


def _read_lock_holder(lock_file) -> str:
    """Read the prior holder's identity from the lock-file body, best-effort."""
    try:
        lock_file.seek(_LOCK_SENTINEL_BYTES)
        content = lock_file.read()
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="replace")
        content = content.strip()
    except OSError:
        return "another writer (identity not recorded)"
    if not content:
        return "another writer (identity not recorded)"
    return _format_lock_holder(content)


def _write_lock_holder(lock_file) -> None:
    """Record this process's identity in the lock-file body. Best-effort.

    Writes from byte 1 onward; byte 0 is the lock sentinel and must not
    be touched after acquire (truncating it on Windows can interact
    badly with the active byte-range lock).
    """
    try:
        ident = f"{os.getpid()} {' '.join(sys.argv[:3])}".strip()
        ident_bytes = ident.encode("utf-8")
        lock_file.seek(_LOCK_SENTINEL_BYTES)
        lock_file.truncate(_LOCK_SENTINEL_BYTES + len(ident_bytes))
        lock_file.write(ident_bytes)
        lock_file.flush()
    except (OSError, UnicodeError):
        pass


def _acquire_palace_lock_file(lock_path: str, resolved: str):
    """Acquire an OS lease; caller serializes process-local registration."""
    # Never truncate the prior holder's identity before acquiring the lock.
    # Byte zero is the lock sentinel, including on Windows.
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0), 0o600)
    # No buffered I/O mutex may be inherited locked by a forked child.
    lf = os.fdopen(fd, "r+b", buffering=0)
    try:
        lf.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lf.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        try:
            if os.name != "nt" and not isinstance(exc, BlockingIOError):
                raise
            holder = _read_lock_holder(lf)
        finally:
            lf.close()
        raise MineAlreadyRunning(
            f"palace {resolved} is held by {holder}; "
            "wait for it to finish or stop the holder before retrying"
        ) from exc
    except BaseException:
        lf.close()
        raise
    return lf


@contextlib.contextmanager
def mine_palace_lock(palace_path: str, *, lock_dir: Optional[str] = None):
    """Per-palace non-blocking lock around the full `mine` pipeline.

    The per-file `mine_lock` only protects delete+insert interleave for a
    single source; it does not prevent N copies of `mempalace mine <dir>`
    from being spawned concurrently by hooks. When that happens, each copy
    drives ChromaDB HNSW inserts in parallel against the same palace,
    which (combined with chromadb's multi-threaded ParallelFor) can
    corrupt the HNSW graph and produce sparse link_lists.bin blowups.

    The lock file is keyed by sha256(palace_path) so mines against
    *different* palaces can still run in parallel — we only serialize
    writes into the same palace, which is the correctness boundary.

    The key is derived from a fully normalized form of the path:
    `realpath` resolves symlinks and `..` segments, and `normcase` folds
    case on Windows (which has a case-insensitive filesystem). Without
    normcase, `C:\\Palace` and `c:\\palace` would hash to different keys
    on Windows and let two concurrent mines touch the same on-disk palace.

    ``lock_dir`` lets offline administration use an isolated worker's lease
    directory without changing process HOME. Re-entrant credit is scoped to
    the resolved lock file, so holds in different namespaces stay independent.

    Non-blocking: if another `mine` is already writing to this palace,
    raise MineAlreadyRunning so the caller can exit cleanly instead of
    piling up as a waiting worker.

    Re-entrant: if the current process already holds the lock for the same
    palace, the context manager retains that lease without re-acquiring it.
    The OS lease is released only after the last overlapping context exits. This
    lets ChromaCollection write methods (which acquire the lock themselves
    to protect MCP/direct callers) compose with miner.mine() (which holds
    the outer lock for the entire mine pipeline) without self-deadlock, and
    lets the threaded MCP HTTP transport write from a worker thread while the
    long-lived writer-lease is held on another thread of the same process.
    """
    lock_dir = paths.lock_dir() if lock_dir is None else lock_dir
    os.makedirs(lock_dir, exist_ok=True)
    resolved = os.path.realpath(os.path.expanduser(palace_path))
    palace_key = paths.palace_key(palace_path, length=16)
    lock_path = os.path.join(lock_dir, f"mine_palace_{palace_key}.lock")

    lock_key = os.path.normcase(os.path.realpath(lock_path))
    hold = None
    try:
        with _palace_lock_guard:
            holds = _holder_keys_locked()
            existing = holds.get(lock_key)
            if existing is not None:
                existing.users += 1
                hold = existing
            else:
                # Keep acquisition and registration atomic with respect to
                # other threads. OS acquisition is non-blocking, so this guard
                # never waits for another process to finish mining.
                hold = _PalaceLockHold(_acquire_palace_lock_file(lock_path, resolved))
                try:
                    _write_lock_holder(hold.file)
                    _mark_held(lock_key, hold)
                except BaseException:
                    holds.pop(lock_key, None)
                    hold.file.close()
                    hold = None
                    raise
        yield
    finally:
        if hold is not None:
            with _palace_lock_guard:
                holds = _holder_keys_locked()
                # A child forked inside this context has no ownership credit.
                if holds.get(lock_key) is hold:
                    hold.users -= 1
                    if hold.users == 0:
                        del holds[lock_key]
                        # A child may inherit a descriptor before registration,
                        # so closing this descriptor alone may retain the flock.
                        try:
                            if os.name == "nt":
                                import msvcrt

                                hold.file.seek(0)
                                msvcrt.locking(hold.file.fileno(), msvcrt.LK_UNLCK, 1)
                            else:
                                import fcntl

                                fcntl.flock(hold.file, fcntl.LOCK_UN)
                        except OSError:
                            pass
                        finally:
                            hold.file.close()


# Backward-compatible alias (previous patch iteration used a single global
# lock). Kept so third-party callers that imported it continue to work; new
# code should use `mine_palace_lock(palace_path)` for per-palace scoping.
mine_global_lock = mine_palace_lock
