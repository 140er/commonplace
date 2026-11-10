"""Stub ``commonplace eval``: every command exists and prints a fixed wrong result."""

from __future__ import annotations

import base64
import json


def add_parsers(sub) -> None:
    parser = sub.add_parser("eval", help="owner-only local eval archives of stored threads")
    actions = parser.add_subparsers(dest="eval_action", required=True)

    def action(name):
        item = actions.add_parser(name)
        item.add_argument("--workspace", required=True)
        if name != "preflight":
            item.add_argument("--by")
        return item

    item = action("archive")
    item.add_argument("--thread", required=True)
    item.add_argument("--key", required=True)
    item.add_argument("--artifacts-stdin", action="store_true")
    action("list")
    action("show").add_argument("run")
    item = action("read")
    item.add_argument("run")
    item.add_argument("--object", required=True)
    item.add_argument("--offset", type=int, default=0)
    item = action("purge")
    item.add_argument("run")
    item.add_argument("--confirm")
    action("preflight")
    parser.set_defaults(func=cmd_eval)


def cmd_eval(args) -> int:
    fixed = {
        "archive": {"run_id": "run_" + "0" * 24, "created_at": "", "reused": False},
        "list": {"runs": [], "limits": {}},
        "show": {"run_id": "", "manifest": {}, "flags": []},
        "read": {
            "object": getattr(args, "object", ""),
            "offset": 0,
            "next": None,
            "total": 0,
            "sha256": "",
            "data_base64": base64.b64encode(b"[]").decode(),
            "flags": [],
        },
        "purge": {"stub": True},
        "preflight": {},
    }
    print(json.dumps(fixed[args.eval_action]))
    return 0
