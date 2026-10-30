"""``commonplace`` command line: set up accounts and tokens, run the gateway."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from pathlib import Path

from .backup import BackupError, data_lock
from .control_db import ControlDB, ControlError
from .extensions import ToolError
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
    from .bridge import CLIENT_PRESETS

    defaults = CLIENT_PRESETS.get(args.client, {})

    def value(name, fallback):
        explicit = getattr(args, name)
        return explicit if explicit is not None else defaults.get(name, fallback)

    policy = Policy.build(
        profile=value("profile", "slim"),
        tools=_csv(args.tools),
        scopes=_csv(args.scopes),
        read_wings=_csv(value("read_wings", "")),
        write_wings=_csv(value("write_wings", "")),
        write_wing=value("write_wing", ""),
        trust=value("trust", "untrusted"),
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


def cmd_serve(args) -> int:
    from .gateway import Gateway
    from .workers import WorkerSupervisor

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    db = _db(args)
    if not db.has_users():
        raise ControlError("not set up: run `commonplace init` first")
    supervisor = WorkerSupervisor(str(_data_dir(args)), idle_stop_s=args.idle_minutes * 60)
    Gateway(db, supervisor).serve(args.host, args.port)
    return 0


def cmd_login_link(args) -> int:
    from .bridge import create_login_url

    print(create_login_url(_db(args), args.email, args.workspace, args.base_url))
    return 0


def cmd_connect(args) -> int:
    from .bridge import run_bridge

    return run_bridge(args.url, args.token_env)


def cmd_seed(args) -> int:
    from .seed import seed_workspace

    db = _db(args)
    result = seed_workspace(db, _data_dir(args), args.workspace, args.file, _actor(db, args))
    print(json.dumps(result))
    return 0


def cmd_backup(args) -> int:
    from .backup import backup

    backup(_data_dir(args), args.dest_dir)
    print(Path(args.dest_dir).expanduser().resolve() / "manifest.json")
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
    s.add_argument("--profile", choices=sorted(PROFILES))
    s.add_argument("--tools", help="explicit tool list instead of --profile")
    s.add_argument("--scopes")
    s.add_argument("--read-wings")
    s.add_argument("--write-wings")
    s.add_argument("--write-wing")
    s.add_argument("--trust", choices=TRUST_LEVELS)
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

    s = sub.add_parser("serve")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--idle-minutes", type=float, default=15.0)
    s.set_defaults(func=cmd_serve)

    s = sub.add_parser("login-link")
    s.add_argument("email")
    s.add_argument("--workspace")
    s.add_argument("--base-url")
    s.set_defaults(func=cmd_login_link)

    s = sub.add_parser("connect")
    s.add_argument("--url", required=True)
    s.add_argument("--token-env", default="COMMONPLACE_TOKEN")
    s.set_defaults(func=cmd_connect)

    s = sub.add_parser("seed")
    s.add_argument("file")
    s.add_argument("--workspace", required=True)
    s.add_argument("--by")
    s.set_defaults(func=cmd_seed)

    s = sub.add_parser("backup")
    s.add_argument("dest_dir")
    s.set_defaults(func=cmd_backup)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command in {"connect", "backup"}:
            return args.func(args)
        with data_lock(_data_dir(args)):
            return args.func(args)
    except (ControlError, PolicyError, BackupError, ToolError, OSError, sqlite3.Error) as exc:
        print(f"commonplace: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        if args.command not in {"login-link", "seed", "backup"}:
            raise
        print(f"commonplace: {exc}", file=sys.stderr)
        return 2
