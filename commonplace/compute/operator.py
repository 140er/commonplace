"""Trusted local reconciliation CLI; never installed as an HTTP action.

Run as the service operator, with its private existing authority/participant
files and workspace crypto context. No credentials or wrapping keys are opened.
"""

import argparse
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

from commonplace.control_db import ControlDB
from mempalace.sqlite_crypto import connect

from . import ExecutionConfig
from .control import Store, StoreError
from .executor import ExecutionService
from .fence import FenceDenied
from .protocol import identifier


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Settle one externally reconciled uncertain execution"
    )
    parser.add_argument("--control-db", type=Path, required=True)
    parser.add_argument("--authority-directory", type=Path, required=True)
    parser.add_argument("--workspace-directory", type=Path, required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument(
        "--note",
        required=True,
        help="External evidence and operator decision (max 1000 characters)",
    )
    parser.add_argument("execution", help="Uncertain execution id")
    args = parser.parse_args(argv)
    identifier(args.workspace)
    paths = {
        "control": args.control_db,
        "boards": args.workspace_directory / "boards.sqlite3",
        "compute": args.workspace_directory / "compute.sqlite3",
        "custody": args.workspace_directory / "compute-credentials.sqlite3",
    }
    # This command only opens an existing provisioned control plane.
    if not all(path.is_absolute() and path.is_file() for path in paths.values()) or not all(
        (args.authority_directory / name).is_file()
        for name in ("authority.lock", "authority.sqlite3")
    ):
        parser.exit(1, "authority_provisioning_required\n")
    try:
        with closing(
            ControlDB(
                str(args.control_db), compute_config=ExecutionConfig(args.authority_directory)
            )
        ) as db:
            coordinator = db._compute_coordinator
            with coordinator.locked():
                for domain in ("boards", "compute", "custody"):
                    path = paths[domain]
                    coordinator.register(str(path), domain)
                    opener = sqlite3.connect if domain == "custody" else connect
                    with closing(
                        opener(path.as_uri() + "?mode=rw", uri=True, isolation_level=None)
                    ) as conn:
                        coordinator.attach(conn, str(path), domain)
                fence = coordinator.seal(args.workspace, map(str, paths.values()))
            store = Store(
                paths["compute"],
                gateway=SimpleNamespace(db=db),
                workspace_id=args.workspace,
                authority_fence=fence,
            )
            ExecutionService(store, fence=fence).settle_uncertain(args.execution, args.note)
    except (StoreError, FenceDenied):
        parser.exit(1, "operator_settlement_denied\n")
    print("operator_settled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
