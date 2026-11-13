"""Read-only, per-viewer path contracts and admission-lock observations.

Contracts live in board cards' ``paths`` column. A conversation owns contracts
assigned to its bound connections or created through its canonical chat origins.
Nothing is cached in chat history. Source candidates, text and overlap work are
bounded independently of the 200-path response cap.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path, PureWindowsPath

from commonplace import control_db, redact
from commonplace.boards import contracts
from commonplace.control_db import ControlError
from commonplace.extensions import ToolError

from .task_progress import _read

MAX_PATHS = 200
MAX_TASKS = 201
MAX_BYTES = 32_768
LIVE = "'queued','leased','starting','uncertain','started'"


def _empty(available=False):
    return {
        "availability": "available" if available else "unavailable",
        "paths": [],
        "truncated": False,
    }


def _paths(raw):
    """Never widen an unsafe legacy value into a publishable host path."""
    values = contracts.parse(raw)
    if values is None or len(values) > contracts.MAX_STORED:
        return [], True
    paths, omitted = set(), False
    for value in values:
        try:
            if PureWindowsPath(value).drive or value.startswith("~"):
                raise ToolError("unsafe path")
            if redact.redact(value)[1]:
                raise ToolError("unsafe path")
            paths.update(contracts.normalize([value]))
        except ToolError:
            omitted = True
    return sorted(paths), omitted


def _connections(authority, ids):
    # Same live connection predicates as authenticate_connection, batched. Names
    # are the only published connection facts; never fall back to an ID or host.
    rows = authority.gateway.db._conn.execute(
        "SELECT c.id,c.name,c.expires_at FROM connections c JOIN users u ON u.id=c.user_id "
        "JOIN memberships m ON m.user_id=c.user_id AND m.workspace_id=c.workspace_id "
        "WHERE c.workspace_id=? AND c.revoked_at IS NULL AND u.disabled_at IS NULL "
        "AND c.id IN (SELECT value FROM json_each(?))",
        (authority.workspace_id, json.dumps(sorted(ids))),
    )
    result = {}
    for row in rows:
        expires = control_db._parse(row["expires_at"])
        if expires is None or expires > control_db._now():
            name = row["name"]
            result[row["id"]] = redact.redact(name)[0] if 0 < len(name) <= 120 else None
    return result


def _runner(root, slugs):
    """Use exactly the hold states used by runner_bridge.contract_refusal.

    Started jobs hold even after a run-end receipt, until their contract is
    cleared. Missing stores have no jobs; unreadable existing stores are unknown.
    """
    path = root / "runner.sqlite3"
    if not path.is_file():
        return [], set()
    with _read(path) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "runner_jobs" not in tables:
            return [], set()
        ledger = "run_ledger" in tables
        rows = conn.execute(
            "WITH ranked AS (SELECT j.task_number,j.board_slug,"
            + ("l.agent" if ledger else "NULL AS agent")
            + ",row_number() OVER (PARTITION BY j.board_slug ORDER BY j.task_number,j.id) AS n "
            "FROM runner_jobs j "
            + ("LEFT JOIN run_ledger l ON l.job_id=j.id " if ledger else "")
            + f"WHERE j.state IN ({LIVE}) AND j.board_slug IN (SELECT value FROM json_each(?))) "
            "SELECT * FROM ranked WHERE n<=?",
            (json.dumps(slugs), MAX_TASKS + 1),
        ).fetchall()
    return [dict(r) for r in rows if r["n"] <= MAX_TASKS], {
        r["board_slug"] for r in rows if r["n"] > MAX_TASKS
    }


def _wanted(chat, views, boards, names):
    ids = json.dumps([v["id"] for v in views if v["board"] in boards])
    bindings = chat.execute(
        "SELECT cb.conversation_id,b.connection_id FROM conversation_bots cb "
        "JOIN bots b ON b.id=cb.bot_id WHERE cb.conversation_id IN (SELECT value FROM json_each(?))",
        (ids,),
    ).fetchall()
    actors, invalid = {}, set()
    for row in bindings:
        cid, connection = row["conversation_id"], row["connection_id"]
        if connection is not None:
            if connection not in names:
                invalid.add(cid)
            else:
                actors.setdefault(cid, []).append("conn:" + connection)
    jobs = chat.execute(
        "WITH ranked AS (SELECT conversation_id,id,user_id,"
        "row_number() OVER (PARTITION BY conversation_id ORDER BY id DESC) AS n "
        "FROM jobs WHERE conversation_id IN (SELECT value FROM json_each(?))) "
        "SELECT * FROM ranked WHERE n<=?",
        (ids, MAX_TASKS + 1),
    ).fetchall()
    origins, truncated = {}, set()
    for row in jobs:
        cid = row["conversation_id"]
        if row["n"] > MAX_TASKS:
            truncated.add(cid)
        else:
            origins.setdefault(cid, []).append(
                {"key": f"coworker:{cid}:{row['id']}", "actor": "user:" + row["user_id"]}
            )
    return [
        {
            "cid": v["id"],
            "board": boards[v["board"]]["id"],
            "actors": actors.get(v["id"], []),
            "origins": origins.get(v["id"], []),
        }
        for v in views
        if v["board"] in boards and v["id"] not in invalid
    ], truncated


def _owned(conn, wanted):
    return conn.execute(
        "WITH wanted AS (SELECT json_extract(value,'$.cid') AS cid,"
        "json_extract(value,'$.board') AS board,json_extract(value,'$.actors') AS actors,"
        "json_extract(value,'$.origins') AS origins FROM json_each(?)),"
        "ranked AS (SELECT w.cid,t.id,t.board_id,"
        "CASE WHEN length(CAST(t.paths AS BLOB))<=? THEN t.paths ELSE 'null' END AS paths,"
        "row_number() OVER (PARTITION BY w.cid ORDER BY t.id) AS n "
        "FROM wanted w JOIN tasks t ON t.board_id=w.board WHERE t.paths IS NOT NULL AND ("
        "t.assignee IN (SELECT value FROM json_each(w.actors)) OR EXISTS ("
        "SELECT 1 FROM chat_task_origins o JOIN json_each(w.origins) j "
        "ON o.origin_key=json_extract(j.value,'$.key') AND o.actor=json_extract(j.value,'$.actor') "
        "WHERE o.task_id=t.id))) SELECT * FROM ranked WHERE n<=?",
        (json.dumps(wanted), MAX_BYTES, MAX_TASKS + 1),
    ).fetchall()


def _holders(conn, boards, runs):
    return conn.execute(
        "WITH ranked AS (SELECT id,board_id,assignee,status,"
        "CASE WHEN length(CAST(paths AS BLOB))<=? THEN paths ELSE 'null' END AS paths,"
        "row_number() OVER (PARTITION BY board_id ORDER BY id) AS n FROM tasks "
        "WHERE board_id IN (SELECT value FROM json_each(?)) AND paths IS NOT NULL AND "
        "((status='doing' AND assignee LIKE 'conn:%') OR EXISTS (SELECT 1 FROM json_each(?) j "
        "JOIN boards b ON b.id=tasks.board_id WHERE tasks.id=json_extract(j.value,'$.task_number') "
        "AND b.slug=json_extract(j.value,'$.board_slug')))) "
        "SELECT * FROM ranked WHERE n<=?",
        (
            MAX_BYTES,
            json.dumps([b["id"] for b in boards.values()]),
            json.dumps(runs),
            MAX_TASKS + 1,
        ),
    ).fetchall()


def _locks(path, source_ids, holders, names, budget):
    matching = []
    for row in holders:
        # Admission treats unreadable contracts as **; never publish their text.
        globs = contracts.parse(row["paths"])
        budget.take(contracts.CARD_UNITS + contracts.size_units(row["paths"]))
        if contracts.first_overlap([path], globs if globs is not None else contracts.ANY, budget):
            matching.append(row)
    shared = [r for r in matching if r["id"] not in source_ids]
    owner = (shared or matching or [None])[0]
    return {
        "locked": bool(matching),
        "shared_lock": bool(shared) or len(matching) > 1,
        "lock_owner": names.get(owner["actor"]) if owner else None,
    }


def project(authority, chat, user_id, views):
    """Enrich already membership-filtered views; all source failures fail closed."""
    result = [{**view, "owns": _empty(view["board"] is None)} for view in views]
    if authority is None or not result or not any(v["board"] for v in result):
        return result
    try:
        viewer = authority.user(user_id)
        root = Path(authority.gateway.workspace_dir(authority.workspace_id))
        slugs = [v["board"] for v in result if v["board"]]
        with _read(root / "boards.sqlite3") as conn:
            boards = {
                r["slug"]: dict(r)
                for r in conn.execute(
                    "SELECT id,slug,wing FROM boards WHERE archived=0 "
                    "AND slug IN (SELECT value FROM json_each(?))",
                    (json.dumps(slugs),),
                )
                if viewer.policy.can_read_wing(r["wing"])
            }
            if not boards:
                return result
            # Only connection IDs from selected conversations are considered.
            ids = {
                r[0]
                for r in chat.execute(
                    "SELECT b.connection_id FROM bots b JOIN conversation_bots cb ON cb.bot_id=b.id "
                    "WHERE cb.conversation_id IN (SELECT value FROM json_each(?)) AND b.connection_id IS NOT NULL",
                    (json.dumps([v["id"] for v in result if v["board"] in boards]),),
                )
            }
            names = _connections(authority, ids)
            wanted, omitted = _wanted(chat, result, boards, names)
            owned = _owned(conn, wanted)
            try:
                runs, unknown = _runner(root, list(boards))
            except (OSError, sqlite3.Error):
                runs, unknown = [], set(boards)
            holders = _holders(conn, boards, runs)
        actors = {(r["board_slug"], r["task_number"]): r["agent"] for r in runs}
        by_board = {}
        for row in holders:
            slug = next(s for s, b in boards.items() if b["id"] == row["board_id"])
            if row["n"] > MAX_TASKS:
                unknown.add(slug)
                continue
            actor = (
                row["assignee"]
                if row["status"] == "doing" and (row["assignee"] or "").startswith("conn:")
                else actors.get((slug, row["id"]))
            )
            by_board.setdefault(slug, []).append(
                {**dict(row), "actor": (actor or "").removeprefix("conn:")}
            )
        names.update(
            _connections(
                authority, {r["actor"] for rows in by_board.values() for r in rows if r["actor"]}
            )
        )
        paths = {}
        for row in owned:
            if row["n"] > MAX_TASKS:
                omitted.add(row["cid"])
                continue
            values, invalid = _paths(row["paths"])
            if invalid:
                omitted.add(row["cid"])
            for path in values:
                paths.setdefault(row["cid"], {}).setdefault(path, set()).add(row["id"])
        budget = contracts.Budget()
        for view in result:
            if not any(w["cid"] == view["id"] for w in wanted):
                continue
            own = view["owns"] = _empty(True)
            entries = sorted(paths.get(view["id"], {}).items())
            own["truncated"] = view["id"] in omitted or len(entries) > MAX_PATHS
            for path, source_ids in entries[:MAX_PATHS]:
                lock = {"locked": None, "shared_lock": None, "lock_owner": None}
                if view["board"] not in unknown:
                    try:
                        lock = _locks(
                            path, source_ids, by_board.get(view["board"], []), names, budget
                        )
                    except contracts.TooLarge:
                        unknown.add(view["board"])
                own["paths"].append({"path": path, **lock})
        # Fresh board and person read fences before publishing source facts.
        viewer = authority.user(user_id)
        with _read(root / "boards.sqlite3") as conn:
            current = {
                r["slug"]: dict(r)
                for r in conn.execute(
                    "SELECT id,slug,wing FROM boards WHERE archived=0 "
                    "AND slug IN (SELECT value FROM json_each(?))",
                    (json.dumps(list(boards)),),
                )
                if viewer.policy.can_read_wing(r["wing"])
            }
        for view in result:
            if view["board"] and current.get(view["board"]) != boards.get(view["board"]):
                view["owns"] = _empty()
        return result
    except (OSError, sqlite3.Error, ControlError, ToolError, ValueError, TypeError, KeyError):
        return [{**view, "owns": _empty(view["board"] is None)} for view in views]
