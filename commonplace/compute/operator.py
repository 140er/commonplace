"""Trusted local reconciliation CLI; never installed as an HTTP action.

Controller actions use the existing authority fence. Helper actions use the
same protected SQLite BEGIN IMMEDIATE fence as helper admission. Neither opens
credentials or sends remote commands; external quiescence evidence is required.
"""

import argparse
import os
import sqlite3
import threading
from contextlib import ExitStack, closing, contextmanager
from pathlib import Path
from types import SimpleNamespace

from mempalace.sqlite_crypto import connect

from .control import Store, StoreError
from .credentials import CredentialError, _open_directory, _private_file
from .discovery_store import DiscoveryStore
from .executor import ExecutionService
from .fence import FenceDenied, _ControlLock, _Coordinator, _guards
from .protocol import ProtocolDenied, ReplayJournal, identifier


@contextmanager
def _controller(args):
    if any(
        value is None
        for value in (args.control_db, args.authority_directory, args.workspace_directory)
    ):
        raise FenceDenied("authority_provisioning_required")
    paths = {
        "control": args.control_db,
        "boards": args.workspace_directory / "boards.sqlite3",
        "compute": args.workspace_directory / "compute.sqlite3",
        "custody": args.workspace_directory / "compute-credentials.sqlite3",
    }
    if not all(path.is_absolute() and path.is_file() for path in paths.values()) or not all(
        (args.authority_directory / name).is_file()
        for name in ("authority.lock", "authority.sqlite3")
    ):
        raise FenceDenied("authority_provisioning_required")
    coordinator = _Coordinator(args.authority_directory)
    try:
        with ExitStack() as stack:
            with coordinator.locked():
                connections = {}
                # Inspect ALL expected guards before ANY attach can install or
                # migrate one, including control (no ControlDB constructor here).
                for domain, path in paths.items():
                    coordinator.register(str(path), domain)
                    opener = sqlite3.connect if domain in ("control", "custody") else connect
                    conn = stack.enter_context(
                        closing(opener(path.as_uri() + "?mode=rw", uri=True, isolation_level=None))
                    )
                    existing = dict(
                        conn.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'")
                    )
                    legacy = dict(_guards(domain, coordinator.identity, legacy=True))
                    for name, sql in _guards(domain, coordinator.identity):
                        if name not in existing:
                            raise FenceDenied("authority_guard_missing")
                        if existing[name] not in (sql, legacy[name]):
                            raise FenceDenied("authority_guard_mismatch")
                    connections[domain] = conn
                for domain, conn in connections.items():
                    coordinator.attach(conn, str(paths[domain]), domain)
                fence = coordinator.seal(args.workspace, map(str, paths.values()))
            # Operator settlement needs the participating lock, never browser
            # identity reconstruction or startup of a background worker.
            db = SimpleNamespace(_lock=_ControlLock(coordinator, threading.RLock()))
            yield Store(
                paths["compute"],
                gateway=SimpleNamespace(db=db),
                workspace_id=args.workspace,
                authority_fence=fence,
            )
    finally:
        coordinator.close()


def _helper(args):
    if (
        args.journal is None
        or args.gateway is None
        or not args.journal.is_absolute()
        or not args.journal.is_file()
    ):
        raise ProtocolDenied("helper_journal_required")
    identifier(args.gateway)
    directory = _open_directory(args.journal.parent, private=True)
    fd = None
    try:
        fd = os.open(args.journal.name, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
        _private_file(fd)
        with closing(
            sqlite3.connect(
                f"/proc/self/fd/{directory}/{args.journal.name}", timeout=0.25, isolation_level=None
            )
        ) as conn:
            table = (
                "compute_passive_journal" if args.kind == "passive" else "compute_transport_journal"
            )
            if (
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone()
                is None
            ):
                raise ProtocolDenied("helper_journal_required")
            conn.execute("PRAGMA synchronous=FULL")
            ReplayJournal(conn).operator_release(
                args.workspace,
                args.gateway,
                args.object_id,
                args.note,
                passive=args.kind == "passive",
            )
    finally:
        if fd is not None:
            os.close(fd)
        os.close(directory)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Settle an externally reconciled Compute reservation"
    )
    parser.add_argument(
        "--kind", choices=("execution", "discovery", "tunnel", "passive"), default="execution"
    )
    parser.add_argument("--control-db", type=Path)
    parser.add_argument("--authority-directory", type=Path)
    parser.add_argument("--workspace-directory", type=Path)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--gateway", help="Helper journal gateway id")
    parser.add_argument("--journal", type=Path, help="Existing private helper replay journal")
    parser.add_argument(
        "--note",
        required=True,
        help="External evidence and operator decision (max 1000 characters)",
    )
    parser.add_argument("object_id", help="Execution or discovery id in the selected journal")
    args = parser.parse_args(argv)
    try:
        identifier(args.workspace)
        identifier(args.object_id)
        if type(args.note) is not str or not args.note.strip() or len(args.note) > 1000:
            raise ProtocolDenied("invalid_operator_note")
        if args.kind in ("tunnel", "passive"):
            _helper(args)
        else:
            with _controller(args) as store:
                if args.kind == "discovery":
                    path = args.workspace_directory / "compute-discovery.sqlite3"
                    if not path.is_file():
                        raise StoreError("discovery_unavailable", 503)
                    DiscoveryStore(path, control_store=store).settle_uncertain(
                        args.object_id, args.note
                    )
                else:
                    ExecutionService(store, fence=store._authority_fence).settle_uncertain(
                        args.object_id, args.note
                    )
    except (StoreError, FenceDenied, ProtocolDenied, CredentialError, sqlite3.Error, OSError):
        parser.exit(1, "operator_settlement_denied\n")
    print(
        {
            "execution": "operator_settled",
            "discovery": "discovery_operator_settled",
            "tunnel": "helper_operator_closed",
            "passive": "helper_operator_closed",
        }[args.kind]
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
