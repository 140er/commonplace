"""``commonplace bridge status`` and ``commonplace bridge disable``: the operator's view and kill
switch. Neither reads or accepts a credential, and neither creates a workspace file."""

from pathlib import Path

from commonplace.chat_bridge import index, service, store
from commonplace.control_db import ControlError


def _targets(db, args) -> list:
    index.ensure_schema(db)
    if getattr(args, "all", False) or not getattr(args, "workspace", None):
        return index.workspaces(db)
    return [db.workspace(args.workspace)["id"]]


def cmd_status(args, db, data_dir: Path) -> int:
    from commonplace.workers import WorkerSupervisor

    supervisor = WorkerSupervisor(str(data_dir))
    shown = 0
    for workspace_id in _targets(db, args):
        bridge = store.BridgeStore(supervisor, workspace_id)
        try:
            for row in bridge.status():
                shown += 1
                print(
                    f"{workspace_id}  {row['id']}  {row['platform']}  "
                    f"{'on' if row['enabled'] else 'off'}  "
                    f"tested={'yes' if row['tested_at'] else 'no'}  channels={row['channels']}  "
                    f"last={row['last_intake'] or '-'}"
                )
        finally:
            bridge.close()
    if not shown:
        print("No chat-platform installs.")
    return 0


def cmd_disable(args, db, data_dir: Path) -> int:
    from commonplace.workers import WorkerSupervisor

    if not args.all and not args.workspace:
        raise ControlError("name a workspace with --workspace, or pass --all")
    done = service.kill_switch(db, WorkerSupervisor(str(data_dir)), _targets(db, args))
    print(f"Switched off {len(done)} install(s)." if done else "Nothing was on.")
    return 0
