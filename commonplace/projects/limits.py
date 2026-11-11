"""Project concurrency limits stored alongside the cards they govern."""

from commonplace.extensions import ToolError

MAX_AGENTS = 100
LEGACY_MAX_AGENTS = 20  # the bound older tables enforce in their CHECK
_TABLE = (
    "CREATE TABLE {name} ("
    "board_id INTEGER PRIMARY KEY REFERENCES boards(id), "
    "max_agents INTEGER CHECK(max_agents BETWEEN 1 AND %d))" % MAX_AGENTS
)


def get(conn, board_id):
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='project_limits'"
    ).fetchone():
        return {"max_agents": None}
    row = conn.execute(
        "SELECT max_agents FROM project_limits WHERE board_id=?", (board_id,)
    ).fetchone()
    return {"max_agents": row["max_agents"] if row else None}


def put(conn, board_id, body):
    if not isinstance(body, dict) or set(body) != {"max_agents"}:
        raise ValueError("Invalid project limits")
    value = body["max_agents"]
    if value is not None and (type(value) is not int or not 1 <= value <= MAX_AGENTS):
        raise ValueError("Invalid maximum agents")
    _ensure_table(conn)
    conn.execute(
        "INSERT OR REPLACE INTO project_limits(board_id,max_agents) VALUES (?,?)",
        (board_id, value),
    )


def _ensure_table(conn):
    """Create the table, or widen one that still carries the old upper bound."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='project_limits'"
    ).fetchone()
    if row is None:
        conn.execute(_TABLE.format(name="project_limits"))
    elif f"AND {LEGACY_MAX_AGENTS})" in (row["sql"] or ""):
        # SQLite cannot alter a CHECK: copy the rows into a table with the new bound.
        conn.execute("ALTER TABLE project_limits RENAME TO project_limits_old")
        conn.execute(_TABLE.format(name="project_limits"))
        conn.execute(
            "INSERT INTO project_limits SELECT board_id, max_agents FROM project_limits_old"
        )
        conn.execute("DROP TABLE project_limits_old")


def working(conn, board_id):
    return {
        row["assignee"]
        for row in conn.execute(
            "SELECT DISTINCT assignee FROM tasks WHERE board_id=? "
            "AND status='doing' AND assignee LIKE 'conn:%'",
            (board_id,),
        )
    }


def check(conn, board, actor, *, service=None, card_id=None):
    """Call inside the card's write transaction, before admitting an agent."""
    if service is not None:
        from commonplace.projects import orchestration as orch
        from commonplace.runner import jobs

        state = service._orchestration_on
        if isinstance(state, Exception):
            raise state
        if state:
            if service._admission_conn is None:
                service._admission_conn = service._admissions.enter_context(
                    orch.admission(service.ctx.gateway, service.principal.workspace_id)
                )
            used = orch.usage(
                conn,
                service._admission_conn,
                board_id=board["id"],
                board_slug=board["slug"],
                workers=service._workers,
                now=jobs._now(),
            )
            if actor and actor.startswith("conn:"):
                orch.admit(used, actor, card_id)
            return
    if not actor or not actor.startswith("conn:"):
        return
    cap = get(conn, board["id"])["max_agents"]
    if cap is None:
        return
    active = working(conn, board["id"])
    if actor not in active and len(active) >= cap:
        raise ToolError(
            f"no: {board['name']} already has its maximum of {cap} agents working; "
            "the card waits in Todo"
        )
