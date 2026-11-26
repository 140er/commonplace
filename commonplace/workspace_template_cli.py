"""``commonplace template ...``: export a workspace template, preview an import, apply one.
Only ``import`` writes to a workspace.

- ``export --workspace W [--board B] [--sections a,b] [--include-memory] [-o FILE]`` builds the
  package from the workspace the data directory holds (the named owner, default the first
  admin, must own it). Roles, seats, skills and an orchestrator proposal need ``--board``.
  Memory is out unless ``--include-memory`` is given; this slice has no memory reader, so that
  flag is refused with ``memory_unavailable``. Without ``-o`` the package goes to stdout; with
  it, a new file (an existing file is never overwritten).
- ``preview FILE --workspace W [--board B] [--include-memory]`` prints what an import would
  create, the collisions, the dropped fields and any refusal as JSON; exit 0 when the package
  could be imported, 1 when it is refused. A successful preview carries ``preview_id``.
- ``import FILE --workspace W --board B --preview-id ID [--include-memory]`` applies the package
  only if ``ID`` names a preview of this exact package against the workspace as it is now
  (``preview_stale`` otherwise). The workspace owner only. Goals, routines and seats are
  created; roles and skills (and any orchestrator config) are filed as a proposal that an owner
  accepts later; nothing is overwritten and no runner grant is made.
"""

from __future__ import annotations

import json
import sys

from . import workspace_template as wt


def _main():
    from commonplace import cli

    return cli


def _source(args):
    from commonplace.control_db import ControlError
    from commonplace.workspace_store import workspace_path

    cli = _main()
    db = cli._db(args)
    try:
        workspace = db.workspace(args.workspace)
        if workspace is None:
            raise ControlError("no such workspace")
        user = db.user(cli._actor(db, args))
        db.require_owner(workspace["id"], user["id"])
        directory = workspace_path(cli._data_dir(args), workspace["id"])
        return wt.read_source(db, workspace["id"], directory, args.board)
    finally:
        db.close()


def cmd_export(args) -> int:
    default = wt.DEFAULT_SECTIONS if args.board else ("routines", "goals")
    sections = [s.strip() for s in (args.sections or ",".join(default)).split(",") if s.strip()]
    if args.include_memory:
        sections.append("memory")
    package = wt.build_package(_source(args), sections, include_memory=args.include_memory)
    text = json.dumps(package, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        sys.stdout.write(text)
        return 0
    with open(args.output, "x", encoding="utf-8") as handle:
        handle.write(text)
    print(
        json.dumps(
            {
                "written": args.output,
                "sections": package["sections"],
                "redactions": len(package["redactions"]),
                "digest": package["digest"],
            },
            sort_keys=True,
        )
    )
    return 0


def cmd_preview(args) -> int:
    with open(args.file, "rb") as handle:
        package = wt.load_package(handle.read(wt.MAX_PACKAGE_BYTES + 1))
    result = wt.preview(package, _source(args), include_memory=args.include_memory)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


def cmd_import(args) -> int:
    from commonplace import workspace_template_import as wti
    from commonplace.control_db import ControlError
    from commonplace.workspace_store import workspace_path

    with open(args.file, "rb") as handle:
        package = wt.load_package(handle.read(wt.MAX_PACKAGE_BYTES + 1))
    cli = _main()
    db = cli._db(args)
    try:
        workspace = db.workspace(args.workspace)
        if workspace is None:
            raise ControlError("no such workspace")
        result = wti.apply(
            db,
            workspace["id"],
            workspace_path(cli._data_dir(args), workspace["id"]),
            package,
            board=args.board,
            preview_id=args.preview_id,
            actor=cli._actor(db, args),
            include_memory=args.include_memory,
        )
    finally:
        db.close()
    print(json.dumps(result, sort_keys=True))
    return 0


def add_parsers(sub) -> None:
    template = sub.add_parser("template", help="workspace templates (export, preview, import)")
    actions = template.add_subparsers(dest="action", required=True)

    def common(parser):
        parser.add_argument("--workspace", required=True)
        parser.add_argument("--board", help="board slug (roles, seats, orchestrator proposal)")
        parser.add_argument("--by", help="acting owner (default: the first admin)")

    s = actions.add_parser("export", help="write a template package (no tokens, no memory)")
    common(s)
    s.add_argument(
        "--sections",
        help="comma list of: "
        + ", ".join(wt.SECTIONS)
        + " (default: roles, skills, routines, goals with --board; routines, goals without)",
    )
    s.add_argument("--include-memory", action="store_true", help="also export verbatim memory")
    s.add_argument("-o", "--output", help="new file to write (default: stdout)")
    s.set_defaults(func=cmd_export)

    s = actions.add_parser("preview", help="show collisions and dropped fields; writes nothing")
    s.add_argument("file")
    common(s)
    s.add_argument(
        "--include-memory", action="store_true", help="count memory the import would add"
    )
    s.set_defaults(func=cmd_preview)

    s = actions.add_parser("import", help="apply a previewed package (owner only; no overwrite)")
    s.add_argument("file")
    common(s)
    s.add_argument("--preview-id", required=True, help="the preview_id the preview printed")
    s.add_argument("--include-memory", action="store_true", help="refused: no memory writer")
    s.set_defaults(func=cmd_import)
