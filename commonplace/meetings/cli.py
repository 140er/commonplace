"""``commonplace meetings status`` and ``commonplace meetings disable``: the operator's view and
kill switch. Neither prints a title, a link or any text; ``disable`` leaves a workspace that never
used meetings untouched (no file is created)."""

from __future__ import annotations

from pathlib import Path

from commonplace.control_db import ControlError
from commonplace.meetings import store
from commonplace.workspace_store import workspace_path


def _stores(db, data_dir, workspace):
    for row in db.workspaces():
        if workspace is None or workspace in (row["id"], row["name"]):
            yield (
                row["id"],
                store.MeetingStore(
                    lambda wid=row["id"]: workspace_path(data_dir, wid), str(data_dir), row["id"]
                ),
            )


def cmd_status(args, db, data_dir) -> int:
    for workspace_id, meetings in _stores(db, Path(data_dir), args.workspace):
        if not meetings.exists():
            print(f"{workspace_id}  meetings: off (never used)")
            continue
        overview = meetings.overview()
        states = " ".join(f"{k}={v}" for k, v in sorted(overview["states"].items())) or "none"
        print(
            f"{workspace_id}  meetings: {'on' if meetings.enabled() else 'off'}  "
            f"connections: {overview['connections_enabled']}/{overview['connections']} on  "
            f"meetings: {states}"
        )
    return 0


def cmd_disable(args, db, data_dir) -> int:
    if bool(args.all) == bool(args.workspace):
        raise ControlError("choose one workspace with --workspace, or every workspace with --all")
    ended = 0
    for workspace_id, meetings in _stores(db, Path(data_dir), args.workspace):
        count = meetings.disable()
        ended += count
        db.audit(
            action="meetings.disable",
            decision="ok",
            workspace_id=workspace_id,
            auth="cli",
            reason=f"ended={count}",
        )
        print(f"{workspace_id}  meetings off, {count} active meeting(s) ended and discarded")
    return 0


def add_parsers(sub, run) -> None:
    meetings = sub.add_parser("meetings").add_subparsers(dest="action", required=True)
    s = meetings.add_parser("status", help="meetings switch and counts per workspace (no text)")
    s.add_argument("--workspace")
    s.set_defaults(func=lambda args: run(cmd_status, args))
    s = meetings.add_parser(
        "disable", help="kill switch: meetings off, active meetings leave and discard"
    )
    s.add_argument("--workspace")
    s.add_argument("--all", action="store_true", help="every workspace on this hub")
    s.set_defaults(func=lambda args: run(cmd_disable, args))
