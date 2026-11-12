"""``commonplace card-start``: the owner path for runner starts that need a person.

A start whose outcome is uncertain (the runner could not tell whether it launched a thread) counts as in
flight and holds the machine's single slot until a workspace owner resolves it. The web UI and the
``card_start_resolve`` tool do that for a signed-in owner; this command is the same decision for the owner
of the data directory, through the same ``jobs.resolve`` (same owner check, same state rules) and the same
audit row. Like ``commonplace orch`` it acts as the local owner of the data directory; ``--by`` names the
acting user (default: the first admin), who must own the workspace.
"""

from __future__ import annotations

import json

from . import jobs


def _main():
    from commonplace import cli

    return cli


class _LocalGateway:
    """The two things a job store takes from a gateway: the control database and the workspace directory."""

    def __init__(self, db, data_dir):
        self.db, self._data_dir = db, data_dir

    def workspace_dir(self, workspace_id):
        from commonplace.workspace_store import workspace_path

        return workspace_path(self._data_dir, workspace_id)


def _principal(user, workspace):
    from commonplace.control_db import Principal
    from commonplace.policy import Policy

    return Principal(
        user_id=user["id"],
        user_email=user["email"],
        workspace_id=workspace["id"],
        connection_id="",
        connection="cli",
        client="commonplace",
        auth="local",
        role="owner",
        policy=Policy.build(
            profile="full",
            scopes=["memory:read", "memory:write", "admin"],
            read_wings=["*"],
            trust="trusted",
        ),
        person=True,  # the owner decision a signed-in owner makes in the web UI; authority = data directory
    )


def cmd_resolve(args) -> int:
    from commonplace.control_db import ControlError

    cli = _main()
    db = cli._db(args)
    try:
        workspace = db.workspace(args.workspace)
        if workspace is None:
            raise ControlError("no such workspace")
        user = db.user(cli._actor(db, args))
        db.require_owner(workspace["id"], user["id"])
        principal = _principal(user, workspace)
        store = jobs.open_store(_LocalGateway(db, cli._data_dir(args)), workspace["id"])
        try:
            with store.transaction() as conn, db._lock, db._tx():
                db.audit(
                    action="runner.resolve",
                    decision="allow",
                    workspace_id=workspace["id"],
                    user_id=user["id"],
                    connection_id="",
                    auth="local",
                    target_ids=(args.job,),
                )
                row = jobs.resolve(store, args.job, principal, args.outcome, args.thread, conn=conn)
        except (jobs.Conflict, ValueError, LookupError) as exc:
            raise ControlError(str(exc) or "cannot resolve that start") from None
    finally:
        db.close()
    print(
        json.dumps(
            {key: row[key] for key in ("state", "thread_id", "resolved_by", "resolved_at")},
            sort_keys=True,
        )
    )
    return 0


def add_parsers(sub) -> None:
    """Register ``card-start`` on the main subparsers."""
    group = sub.add_parser("card-start", help="runner starts that need a person").add_subparsers(
        dest="start_action", required=True
    )
    s = group.add_parser(
        "resolve",
        help="resolve an uncertain start (nothing launched: failed; a thread exists: started)",
    )
    s.add_argument("--workspace", required=True)
    s.add_argument("--job", required=True, help="the start job id, job_...")
    s.add_argument("--outcome", required=True, choices=("started", "failed"))
    s.add_argument("--thread", help="the launched thread's id (required for started)")
    s.add_argument("--by", help="acting owner (default: the first admin)")
    s.set_defaults(func=cmd_resolve)
