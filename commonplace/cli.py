"""``commonplace`` command line: set up accounts and tokens, run the gateway."""

from __future__ import annotations

import argparse
import json
import logging
import os
import secrets
import sys
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import urlsplit

from .control_db import ControlDB, ControlError
from .policy import PROFILES, TRUST_LEVELS, Policy, PolicyError

DATA_ENV = "COMMONPLACE_DATA"


def _data_dir(args) -> Path:
    return Path(args.data or os.environ.get(DATA_ENV) or Path.home() / ".commonplace").expanduser()


def _db(args) -> ControlDB:
    return ControlDB(str(_data_dir(args) / "control.sqlite3"))


def _csv(value):
    if value is None:
        return None
    return [v.strip() for v in value.split(",") if v.strip()]


def _actor(db: ControlDB, args) -> str:
    if getattr(args, "by", None):
        return args.by
    admins = [u for u in db.users() if u["is_admin"]]
    if not admins:
        raise ControlError("no admin yet: run `commonplace init` first")
    return admins[0]["id"]


def cmd_init(args) -> int:
    data = _data_dir(args)
    db = _db(args)
    if db.has_users():
        raise ControlError(f"{data} is already set up")
    uid = db.create_user(args.admin_email, is_admin=True)
    wid = db.create_workspace(args.workspace, uid, personal=True)
    print(f"data       {data}")
    print(f"admin      {args.admin_email} ({uid})")
    print(f"workspace  {args.workspace} ({wid})")
    return 0


def cmd_user_add(args) -> int:
    db = _db(args)
    uid = db.create_user(args.email, args.name or "")
    print(uid)
    if not args.no_workspace:
        print(db.create_workspace(args.email, uid, personal=True))
    return 0


def cmd_user_list(args) -> int:
    for u in _db(args).users():
        print(f"{u['id']}  {u['email']}{'  admin' if u['is_admin'] else ''}")
    return 0


def cmd_workspace_add(args) -> int:
    print(_db(args).create_workspace(args.name, args.owner))
    return 0


def cmd_workspace_list(args) -> int:
    db = _db(args)
    for w in db.workspaces():
        members = ", ".join(f"{m['email']} ({m['role']})" for m in db.members(w["id"]))
        print(f"{w['id']}  {w['name']}  {members}")
    return 0


def cmd_member_add(args) -> int:
    db = _db(args)
    db.add_member(args.workspace, args.email, args.role, actor=_actor(db, args))
    return 0


def cmd_token_add(args) -> int:
    db = _db(args)
    policy = Policy.build(
        profile=args.profile,
        tools=_csv(args.tools),
        scopes=_csv(args.scopes),
        read_wings=_csv(args.read_wings) or [],
        write_wings=_csv(args.write_wings) or [],
        write_wing=args.write_wing or "",
        trust=args.trust,
    )
    actor = _actor(db, args)
    user = args.user or actor
    _cid, token = db.create_token(
        workspace_ref=args.workspace,
        user_ref=user,
        name=args.name,
        client=args.client,
        policy=policy,
        actor=actor,
        expires_days=args.expires_days or None,
    )
    # The only time the token is shown. Stdout carries nothing else, so
    # `commonplace token add ... > file` captures exactly the token.
    print(token)
    print(json.dumps(policy.summary()), file=sys.stderr)
    return 0


def cmd_token_list(args) -> int:
    db = _db(args)
    for c in db.connections(args.workspace):
        state = "revoked" if c["revoked_at"] else f"expires {c['expires_at'] or 'never'}"
        policy = Policy.from_json(c["policy"]).summary()
        print(
            f"{c['name']}  {c['token_hint']}  {c['client']}  {c['workspace_id']}  {state}  "
            f"last used {c['last_used_at'] or 'never'}  {json.dumps(policy)}"
        )
    return 0


def cmd_token_revoke(args) -> int:
    db = _db(args)
    db.revoke(args.name, actor=_actor(db, args), workspace_ref=args.workspace)
    return 0


def cmd_audit(args) -> int:
    for e in _db(args).audit_entries(args.workspace, args.limit):
        print(
            f"{e['seq']}  {e['at']}  {e['action']}  {e['tool'] or '-'}  {e['decision']}  "
            f"conn={e['connection_id'] or '-'}  wing={e['wing'] or '-'}  ids={e['target_ids']}"
            + (f"  {e['reason']}" if e["reason"] else "")
        )
    return 0


def _owner(db, args, workspace_id):
    user = db.user(_actor(db, args))
    db.require_owner(workspace_id, user["id"])
    return user


def _settings(db, handoff_db):
    from .handoff.events import Events
    from .handoff.settings import SettingsService

    return SettingsService(handoff_db, Events(db, handoff_db))


def _setting_scope(args):
    if args.agent is not None:
        return "agent", args.agent
    if args.task is not None:
        return "task", args.task
    return "workspace", ""


def cmd_handoff_status(args) -> int:
    from .handoff.db import HandoffDB

    db = _db(args)
    workspace = db.workspace(args.workspace)["id"]
    setting = _settings(db, HandoffDB(db)).effective(workspace, args.agent or "", args.task or "")
    print(
        f"handoff: {'on' if setting.enabled else 'off'} ({setting.source}), "
        f"threshold {setting.threshold:.2f}, window {setting.context_window or 'auto'}, "
        f"launcher {setting.launcher.kind}"
    )
    return 0


def cmd_handoff_set(args) -> int:
    from .handoff.db import HandoffDB
    from .handoff.models import LauncherConfig, SettingRow

    db = _db(args)
    workspace = db.workspace(args.workspace)["id"]
    user = _owner(db, args, workspace)
    scope, ref = _setting_scope(args)
    launcher = None
    if args.launcher is None:
        if any(
            value is not None
            for value in (args.launcher_argv, args.launcher_url, args.launcher_secret_env)
        ):
            raise ControlError("--launcher is required with launcher options")
    else:
        secret = ""
        if args.launcher_secret_env is not None:
            secret = os.environ.get(args.launcher_secret_env, "")
            if not secret:
                raise ControlError(f"environment variable {args.launcher_secret_env} is not set")
        launcher = LauncherConfig(
            kind=args.launcher,
            argv=tuple(args.launcher_argv or ()),
            url=args.launcher_url or "",
            secret=secret,
        )
    row = SettingRow(
        workspace_id=workspace,
        scope=scope,
        ref=ref,
        enabled=args.action == "enable",
        threshold=args.threshold,
        context_window=args.window,
        launcher=launcher,
    )
    _settings(db, HandoffDB(db)).set(row, actor=user["id"], is_owner=True)
    print(f"handoff: {'on' if row.enabled else 'off'} ({scope})")
    return 0


def cmd_handoff_clear(args) -> int:
    from .handoff.db import HandoffDB

    db = _db(args)
    workspace = db.workspace(args.workspace)["id"]
    user = _owner(db, args, workspace)
    scope, ref = _setting_scope(args)
    _settings(db, HandoffDB(db)).clear(workspace, scope, ref, actor=user["id"], is_owner=True)
    print(f"handoff: cleared ({scope})")
    return 0


def _handoff_record(handoff_db, handoff_id):
    record = handoff_db.get(handoff_id)
    if record is None:
        raise ControlError("handoff not found")
    return record


def _print_handoff(record):
    successor = record.successor.agent_ref if record.successor is not None else "-"
    print(
        f"{record.id}  {record.state}  thread {record.stored_thread_id or '-'}  successor {successor}"
    )


def cmd_handoff_list(args) -> int:
    from .handoff.db import HandoffDB

    db = _db(args)
    workspace = db.workspace(args.workspace)["id"]
    for record in HandoffDB(db).list(workspace, task_ref=args.task or "", limit=args.limit):
        _print_handoff(record)
    return 0


def cmd_handoff_show(args) -> int:
    from .handoff.db import HandoffDB

    db = _db(args)
    with ExitStack() as stack:
        documents = _stores(args, stack, root=Path(db.path).parent)
        record = _handoff_record(HandoffDB(db, document_store_for=documents), args.handoff_id)
        _print_handoff(record)
        sys.stdout.write(record.doc_markdown)
    return 0


def _stores(args, stack, *, root=None):
    from .handoff.thread_store import ThreadStore

    stores = {}

    def store_for(workspace_id):
        if workspace_id not in stores:
            path = (root or _data_dir(args)) / "workspaces" / workspace_id / "threads.sqlite3"
            path.parent.mkdir(parents=True, exist_ok=True)
            store = stores[workspace_id] = ThreadStore(str(path))
            stack.callback(store.close)
        return stores[workspace_id]

    return store_for


def cmd_handoff_relaunch(args) -> int:
    from .control_db import Principal
    from .handoff.db import HandoffDB
    from .handoff.events import Events
    from .handoff.service import HandoffService

    db = _db(args)
    handoff_db = HandoffDB(db)
    record = _handoff_record(handoff_db, args.handoff_id)
    user = _owner(db, args, record.workspace_id)
    principal = Principal(
        user_id=user["id"],
        user_email=user["email"],
        workspace_id=record.workspace_id,
        connection_id="",
        connection="cli",
        client="commonplace",
        auth="local",
        role="owner",
        policy=Policy.build(
            profile="full",
            scopes=["memory:read", "private:read", "threads:read", "handoff:write", "admin"],
            read_wings=["*"],
            trust="trusted",
        ),
    )
    events = Events(db, handoff_db)
    with ExitStack() as stack:
        service = HandoffService(
            control_db=db,
            handoff_db=handoff_db,
            settings=_settings(db, handoff_db),
            store_for=_stores(args, stack),
            events=events,
            prompt_dir=str(_data_dir(args) / "handoff-prompts"),
        )
        result = service.relaunch(principal, record.id)
    if (
        result.action != "handoff"
        or result.handoff is None
        or result.handoff.state in ("failed", "launch_failed")
    ):
        raise ControlError(
            result.reason
            or (result.handoff.error if result.handoff is not None else "")
            or "handoff relaunch failed"
        )
    if result.launch:
        sys.stdout.write(result.prompt)
    elif result.handoff is not None:
        _print_handoff(result.handoff)
    return 0


def cmd_thread(args) -> int:
    db = _db(args)
    workspace = db.workspace(args.workspace)["id"]
    if args.action == "get" and args.end is not None and args.end < args.start:
        raise ControlError("end must be at least start")
    with ExitStack() as stack:
        store = _stores(args, stack)(workspace)
        thread = store.get(args.thread_id)
        if thread is None or thread.workspace_id != workspace:
            raise ControlError("thread not found")
        if args.action == "get":
            entries = store.messages(args.thread_id, args.start, args.end)
        else:
            entries = store.search([args.thread_id], args.query)
        for entry in entries:
            sys.stdout.write(entry.text)
    return 0


def cmd_webhook_add(args) -> int:
    from .handoff.db import HandoffDB

    db = _db(args)
    workspace = db.workspace(args.workspace)["id"]
    user = _owner(db, args, workspace)
    secret = secrets.token_urlsafe(32)
    webhook_id = HandoffDB(db).add_webhook(
        workspace, args.url, secret, args.events, actor=user["id"]
    )
    print(f"{webhook_id}  {secret}")
    return 0


def cmd_webhook_read(args) -> int:
    from .handoff.db import HandoffDB

    db = _db(args)
    user = db.user(_actor(db, args))
    if args.workspace is not None:
        workspaces = [db.workspace(args.workspace)]
    else:
        workspaces = [w for w in db.workspaces() if db.role(w["id"], user["id"]) == "owner"]
    handoff_db = HandoffDB(db)
    for workspace in workspaces:
        db.require_owner(workspace["id"], user["id"])
        for webhook in handoff_db.webhooks(workspace["id"]):
            if args.action == "list":
                print(f"{webhook['id']}  {webhook['url']}  {','.join(webhook['events'])}")
            elif webhook["id"] == args.webhook_id:
                if not handoff_db.remove_webhook(args.webhook_id, actor=user["id"]):
                    raise ControlError("webhook not found")
                print(args.webhook_id)
                return 0
    if args.action == "remove":
        raise ControlError("webhook not found")
    return 0


def _positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def _nonnegative_int(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be nonnegative")
    return number


def _threshold(value):
    from .handoff.settings import THRESHOLD_MAX, THRESHOLD_MIN

    number = float(value)
    if not THRESHOLD_MIN <= number <= THRESHOLD_MAX:
        raise argparse.ArgumentTypeError(f"must be between {THRESHOLD_MIN} and {THRESHOLD_MAX}")
    return number


def _launcher_argv(value):
    try:
        argv = json.loads(value)
    except json.JSONDecodeError:
        raise argparse.ArgumentTypeError("must be a JSON array of strings") from None
    if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
        raise argparse.ArgumentTypeError("must be a JSON array of strings")
    return argv


def _webhook_url(value):
    try:
        url = urlsplit(value)
        valid = (
            url.scheme in ("http", "https")
            and url.hostname
            and url.port != 0
            and url.username is None
            and url.password is None
            and not url.fragment
            and not any(char.isspace() for char in value)
        )
    except ValueError:
        valid = False
    if not valid:
        raise argparse.ArgumentTypeError("must be an HTTP URL without credentials")
    return value


def _webhook_events(value):
    from .handoff.models import EVENTS

    events = tuple(dict.fromkeys(_csv(value)))
    if not events or any(event != "handoff.*" and event not in EVENTS for event in events):
        raise argparse.ArgumentTypeError("unknown handoff event")
    return events


def _handoff_parsers(sub):
    handoff = sub.add_parser("handoff").add_subparsers(dest="action", required=True)
    s = handoff.add_parser("status")
    s.add_argument("--workspace", required=True)
    s.add_argument("--agent")
    s.add_argument("--task")
    s.set_defaults(func=cmd_handoff_status)
    for action in ("enable", "disable", "clear"):
        s = handoff.add_parser(action)
        s.add_argument("--workspace", required=True)
        scope = s.add_mutually_exclusive_group(required=action == "clear")
        scope.add_argument("--agent")
        scope.add_argument("--task")
        s.add_argument("--by")
        if action == "clear":
            s.set_defaults(func=cmd_handoff_clear)
            continue
        s.add_argument("--threshold", type=_threshold)
        s.add_argument("--window", type=_positive_int)
        s.add_argument("--launcher", choices=("client", "command", "webhook"))
        s.add_argument("--launcher-argv", type=_launcher_argv)
        s.add_argument("--launcher-url", type=_webhook_url)
        s.add_argument("--launcher-secret-env")
        s.set_defaults(func=cmd_handoff_set)
    s = handoff.add_parser("list")
    s.add_argument("--workspace", required=True)
    s.add_argument("--task")
    s.add_argument("--limit", type=_positive_int, default=50)
    s.set_defaults(func=cmd_handoff_list)
    s = handoff.add_parser("show")
    s.add_argument("handoff_id")
    s.set_defaults(func=cmd_handoff_show)
    s = handoff.add_parser("relaunch")
    s.add_argument("handoff_id")
    s.add_argument("--by")
    s.set_defaults(func=cmd_handoff_relaunch)

    thread = sub.add_parser("thread").add_subparsers(dest="action", required=True)
    for action in ("get", "recall"):
        s = thread.add_parser(action)
        s.add_argument("thread_id")
        s.add_argument("--workspace", required=True)
        if action == "get":
            s.add_argument("--start", type=_nonnegative_int, default=0)
            s.add_argument("--end", type=_nonnegative_int)
        else:
            s.add_argument("query")
        s.set_defaults(func=cmd_thread)

    webhook = sub.add_parser("webhook").add_subparsers(dest="action", required=True)
    s = webhook.add_parser("add")
    s.add_argument("url", type=_webhook_url)
    s.add_argument("--workspace", required=True)
    s.add_argument("--events", type=_webhook_events, default=("handoff.*",))
    s.add_argument("--by")
    s.set_defaults(func=cmd_webhook_add)
    for action in ("list", "remove"):
        s = webhook.add_parser(action)
        if action == "remove":
            s.add_argument("webhook_id")
        s.add_argument("--workspace")
        s.add_argument("--by")
        s.set_defaults(func=cmd_webhook_read)


def cmd_serve(args) -> int:
    from .gateway import Gateway
    from .workers import WorkerSupervisor

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    db = _db(args)
    if not db.has_users():
        raise ControlError("not set up: run `commonplace init` first")
    supervisor = WorkerSupervisor(str(_data_dir(args)), idle_stop_s=args.idle_minutes * 60)
    from .handoff.wiring import install

    gateway = Gateway(db, supervisor)
    install(gateway, _data_dir(args))
    gateway.serve(args.host, args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="commonplace")
    p.add_argument("--data", help=f"data directory (default ${DATA_ENV} or ~/.commonplace)")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("init", help="create the control database and the first admin")
    s.add_argument("--admin-email", required=True)
    s.add_argument("--workspace", default="personal")
    s.set_defaults(func=cmd_init)

    users = sub.add_parser("user").add_subparsers(dest="action", required=True)
    s = users.add_parser("add")
    s.add_argument("email")
    s.add_argument("--name")
    s.add_argument("--no-workspace", action="store_true")
    s.set_defaults(func=cmd_user_add)
    users.add_parser("list").set_defaults(func=cmd_user_list)

    ws = sub.add_parser("workspace").add_subparsers(dest="action", required=True)
    s = ws.add_parser("add")
    s.add_argument("name")
    s.add_argument("--owner", required=True)
    s.set_defaults(func=cmd_workspace_add)
    ws.add_parser("list").set_defaults(func=cmd_workspace_list)

    member = sub.add_parser("member").add_subparsers(dest="action", required=True)
    s = member.add_parser("add")
    s.add_argument("workspace")
    s.add_argument("email")
    s.add_argument("--role", choices=["owner", "member"], default="member")
    s.add_argument("--by")
    s.set_defaults(func=cmd_member_add)

    tok = sub.add_parser("token").add_subparsers(dest="action", required=True)
    s = tok.add_parser("add", help="create a connection; prints its token once")
    s.add_argument("name")
    s.add_argument("--workspace", required=True)
    s.add_argument("--user", help="token holder (default: --by)")
    s.add_argument("--client", default="other")
    s.add_argument("--profile", choices=sorted(PROFILES), default="slim")
    s.add_argument("--tools", help="explicit tool list instead of --profile")
    s.add_argument("--scopes")
    s.add_argument("--read-wings", default="")
    s.add_argument("--write-wings", default="")
    s.add_argument("--write-wing", default="")
    s.add_argument("--trust", choices=TRUST_LEVELS, default="untrusted")
    s.add_argument("--expires-days", type=int, default=365, help="0 for no expiry")
    s.add_argument("--by")
    s.set_defaults(func=cmd_token_add)
    s = tok.add_parser("list")
    s.add_argument("--workspace")
    s.set_defaults(func=cmd_token_list)
    s = tok.add_parser("revoke")
    s.add_argument("name")
    s.add_argument("--workspace")
    s.add_argument("--by")
    s.set_defaults(func=cmd_token_revoke)

    s = sub.add_parser("audit")
    s.add_argument("--workspace")
    s.add_argument("--limit", type=int, default=50)
    s.set_defaults(func=cmd_audit)

    _handoff_parsers(sub)

    s = sub.add_parser("serve")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--idle-minutes", type=float, default=15.0)
    s.set_defaults(func=cmd_serve)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    errors = (ControlError, PolicyError)
    if args.command in ("handoff", "thread", "webhook"):
        from .handoff.db import HandoffDBError
        from .handoff.recall import ThreadNotFound
        from .handoff.settings import SettingsError

        errors += (HandoffDBError, SettingsError, ThreadNotFound)
    try:
        return args.func(args)
    except errors as exc:
        print(f"commonplace: {exc}", file=sys.stderr)
        return 2
