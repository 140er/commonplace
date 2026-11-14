"""Budgets over the cost ledger: a soft warning at 80% and a hard stop on new starts at 100%.

A budget attaches to one scope (the workspace, an agent connection, a project or a card)
and holds a limit in one currency for the workspace's cost window (``costs.window``). What
counts against it is what machines reported: runs of the scope that started in the window
with a known cost in the budget's currency. A run with unknown cost, or in another currency,
is never added to the spend; it is counted and shown apart. The hub holds no prices and
estimates nothing.

Nothing here moves money. The only effect is that the hub refuses NEW starts on a scope
whose spend has reached its limit, decided inside the start's own admission transaction
(``jobs.create_in``) and again when a queued start would be leased. Because the runner
store has one writer at a time, a start sees every report committed before it and none
after it; there is no reservation, so the overshoot is bounded by the runs already in
flight, which are never stopped: they are counted as ``running_runs`` and flagged.

Marks make the owner's warning appear once per window and scope: a row per budget, level
and window, written when a report, a started run or a budget change crosses the level.

Standard library only, like ``costs``.
"""

import re
import secrets

from . import costs

KINDS = ("workspace", "agent", "project", "card")
LEVELS = ("warning", "limit", "unknown_runs")
WARN_PERCENT = 80
MAX_BUDGETS = 200
MAX_LIMIT = 10**15
MAX_UNKNOWN_RUNS = 10**6
ROLLING = "rolling"
KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@ /-]{0,127}\Z")
CURRENCY = re.compile(r"[A-Z]{3}\Z")
TABLES = (
    """
CREATE TABLE IF NOT EXISTS cost_budgets (
 id TEXT PRIMARY KEY,
 scope_kind TEXT NOT NULL CHECK (scope_kind IN ('workspace','agent','project','card')),
 scope_key TEXT NOT NULL,
 limit_micros INTEGER NOT NULL CHECK (limit_micros > 0),
 currency TEXT NOT NULL,
 unknown_runs_warn INTEGER CHECK (unknown_runs_warn IS NULL OR unknown_runs_warn > 0),
 created_by TEXT NOT NULL, created_at TEXT NOT NULL,
 updated_by TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE (scope_kind, scope_key)
)
""",
    """
CREATE TABLE IF NOT EXISTS cost_budget_marks (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 budget_id TEXT NOT NULL,
 level TEXT NOT NULL CHECK (level IN ('warning','limit','unknown_runs')),
 period TEXT NOT NULL,
 spent_micros INTEGER NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE (budget_id, level, period)
)
""",
)
_SPEND = (
    "SELECT COALESCE(SUM(CASE WHEN e.cost_micros IS NOT NULL AND e.currency=? "
    "THEN e.cost_micros END),0) AS spent, "
    "COALESCE(SUM(e.cost_micros IS NULL),0) AS unknown_runs, "
    "COALESCE(SUM(e.cost_micros IS NOT NULL AND e.currency<>?),0) AS other_currency_runs, "
    "COALESCE(SUM(l.status='started'),0) AS running_runs "
    "FROM run_ledger l LEFT JOIN cost_events e ON e.id=" + costs._LATEST + " "
    "WHERE l.created_at>=? AND l.created_at<? AND l.status IN ("
    + ",".join("?" for _ in costs.COUNTED)
    + ") AND (?='workspace' OR (?='agent' AND l.agent=?) OR (?='project' AND l.project=?) "
    "OR (?='card' AND CAST(l.task_number AS TEXT)=?))"
)
_SCOPED = (
    "SELECT * FROM cost_budgets WHERE scope_kind='workspace' "
    "OR (scope_kind='agent' AND scope_key=?) OR (scope_kind='project' AND scope_key=?) "
    "OR (scope_kind='card' AND scope_key=?) ORDER BY scope_kind, scope_key"
)
_ORDER = {kind: index for index, kind in enumerate(KINDS)}


def ensure(conn):
    for statement in TABLES:
        conn.execute(statement)


def _present(conn):
    return (
        conn.execute("SELECT 1 FROM sqlite_master WHERE name='cost_budgets'").fetchone() is not None
    )


def label(kind, key):
    return "workspace" if kind == "workspace" else kind + " " + key


# --- validation ---------------------------------------------------------------------


def _scope(body):
    scope = body.get("scope") if isinstance(body, dict) else None
    if not isinstance(scope, dict) or scope.keys() - {"kind", "key"}:
        raise ValueError("invalid scope")
    kind, key = scope.get("kind"), scope.get("key")
    if kind not in KINDS:
        raise ValueError("invalid scope kind")
    if kind == "workspace":
        if key not in (None, ""):
            raise ValueError("a workspace scope has no key")
        return kind, ""
    if kind == "card":
        if isinstance(key, str) and key.isdigit():
            key = int(key)
        if type(key) is not int or not 1 <= key <= MAX_LIMIT:
            raise ValueError("invalid card")
        return kind, str(key)
    if not isinstance(key, str) or KEY.match(key) is None:
        raise ValueError("invalid scope key")
    return kind, key


def clean_budget(body):
    """Validate an owner's budget; return its fields. Raises ValueError with a safe message."""
    if not isinstance(body, dict) or body.keys() - {
        "scope",
        "limit_micros",
        "currency",
        "unknown_runs_warn",
    }:
        raise ValueError("invalid budget")
    kind, key = _scope(body)
    limit = body.get("limit_micros")
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        raise ValueError("invalid limit_micros")
    currency = body.get("currency")
    if not isinstance(currency, str) or CURRENCY.match(currency) is None:
        raise ValueError("invalid currency")
    unknown = body.get("unknown_runs_warn")
    if unknown is not None and (type(unknown) is not int or not 1 <= unknown <= MAX_UNKNOWN_RUNS):
        raise ValueError("invalid unknown_runs_warn")
    return {
        "kind": kind,
        "key": key,
        "limit_micros": limit,
        "currency": currency,
        "unknown_runs_warn": unknown,
    }


def clean_scope(kind, key):
    return _scope({"scope": {"kind": kind, "key": key}})


# --- spend --------------------------------------------------------------------------


def _spend(conn, budget, start, end):
    kind, key = budget["scope_kind"], budget["scope_key"]
    row = conn.execute(
        _SPEND,
        (
            budget["currency"],
            budget["currency"],
            start,
            end,
            *costs.COUNTED,
            kind,
            kind,
            key,
            kind,
            key,
            kind,
            key,
        ),
    ).fetchone()
    return {name: row[name] for name in row.keys()}


def _period(setting, start):
    return ROLLING if setting["window"] == "rolling_days" else start


def _state(spent, limit):
    if spent >= limit:
        return "limit"
    if spent * 100 >= limit * WARN_PERCENT:
        return "warning"
    return "ok"


def _entry(conn, budget, setting, now):
    start, end = costs.window(setting, now)
    spend = _spend(conn, budget, start, end)
    limit = budget["limit_micros"]
    marks = [
        row["level"]
        for row in conn.execute(
            "SELECT level FROM cost_budget_marks WHERE budget_id=? AND period=? ORDER BY id",
            (budget["id"], _period(setting, start)),
        )
    ]
    threshold = budget["unknown_runs_warn"]
    return {
        "id": budget["id"],
        "scope": {"kind": budget["scope_kind"], "key": budget["scope_key"] or None},
        "limit_micros": limit,
        "currency": budget["currency"],
        "unknown_runs_warn": threshold,
        "window": {"start": start, "end": end, **setting},
        "spent_micros": spend["spent"],
        "percent": spend["spent"] * 100 // limit,
        "state": _state(spend["spent"], limit),
        "unknown_cost_runs": spend["unknown_runs"],
        "unknown_runs_high": threshold is not None and spend["unknown_runs"] >= threshold,
        "other_currency_runs": spend["other_currency_runs"],
        "running_runs": spend["running_runs"],
        "marks": marks,
        "updated_at": budget["updated_at"],
    }


def status(conn, now, *, budgets=None):
    """The state of every budget (or the given rows) for the current window; read-only."""
    if not _present(conn):
        return []
    setting = costs.get_setting(conn)
    rows = (
        conn.execute("SELECT * FROM cost_budgets ORDER BY scope_kind, scope_key").fetchall()
        if budgets is None
        else budgets
    )
    return [_entry(conn, row, setting, now) for row in sorted(rows, key=_rank)]


def _rank(row):
    return _ORDER[row["scope_kind"]], row["scope_key"]


# --- marks --------------------------------------------------------------------------


def _mark(conn, entry, now):
    """Write the marks a budget's current state calls for, once per window; on a rolling
    window a mark is dropped when its condition ends, so the next crossing warns again."""
    due = []
    if entry["state"] in ("warning", "limit"):
        due.append("warning")
    if entry["state"] == "limit":
        due.append("limit")
    if entry["unknown_runs_high"]:
        due.append("unknown_runs")
    period = _period(entry["window"], entry["window"]["start"])
    for level in due:
        conn.execute(
            "INSERT OR IGNORE INTO cost_budget_marks(budget_id,level,period,spent_micros,"
            "created_at) VALUES (?,?,?,?,?)",
            (entry["id"], level, period, entry["spent_micros"], now),
        )
    if period == ROLLING:
        for level in LEVELS:
            if level not in due:
                conn.execute(
                    "DELETE FROM cost_budget_marks WHERE budget_id=? AND level=? AND period=?",
                    (entry["id"], level, period),
                )


def _scoped(conn, agent, project, task):
    return conn.execute(
        _SCOPED,
        (agent, project, None if task is None else str(task)),
    ).fetchall()


def after_run(conn, job_id, now):
    """Mark the budgets a run belongs to after its cost or start changed, on the caller's
    write transaction. Does nothing when no budget is set."""
    if not _present(conn):
        return
    run = conn.execute(
        "SELECT agent, project, task_number FROM run_ledger WHERE job_id=?", (job_id,)
    ).fetchone()
    if run is None:
        return
    rows = _scoped(conn, run["agent"], run["project"], run["task_number"])
    if not rows:
        return
    for entry in status(conn, now, budgets=rows):
        _mark(conn, entry, now)


def stop_reason(conn, now, *, agent, project, task_number):
    """The budget that forbids a new start, or None.

    A start is forbidden while a budget of its workspace, agent connection, project or card
    has spent its limit in the current window. Reads only; the caller holds the runner
    write transaction, so no report can land between this check and the insert.
    """
    if not _present(conn):
        return None
    rows = _scoped(conn, agent, project, task_number)
    if not rows:
        return None
    start, end = costs.window(costs.get_setting(conn), now)
    for row in sorted(rows, key=_rank):
        if _spend(conn, row, start, end)["spent"] >= row["limit_micros"]:
            return {
                "scope_kind": row["scope_kind"],
                "scope_key": row["scope_key"],
                "budget": row["id"],
                "window_start": start,
                "window_end": end,
            }
    return None


def describe(stop):
    return "%s budget %s, window %s to %s" % (
        label(stop["scope_kind"], stop["scope_key"]),
        stop["budget"],
        stop["window_start"],
        stop["window_end"],
    )


# --- owner changes ------------------------------------------------------------------


def set_budget(conn, fields, actor, now):
    """Create or replace the budget of a scope on the caller's write transaction.

    A changed limit starts a fresh warning cycle: the scope's marks are cleared and the new
    state is marked at once. Returns the budget's state.
    """
    ensure(conn)
    existing = conn.execute(
        "SELECT * FROM cost_budgets WHERE scope_kind=? AND scope_key=?",
        (fields["kind"], fields["key"]),
    ).fetchone()
    if existing is None:
        if conn.execute("SELECT COUNT(*) FROM cost_budgets").fetchone()[0] >= MAX_BUDGETS:
            raise ValueError("too many budgets")
        budget = "bud_" + secrets.token_hex(6)
        conn.execute(
            "INSERT INTO cost_budgets(id,scope_kind,scope_key,limit_micros,currency,"
            "unknown_runs_warn,created_by,created_at,updated_by,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                budget,
                fields["kind"],
                fields["key"],
                fields["limit_micros"],
                fields["currency"],
                fields["unknown_runs_warn"],
                actor,
                now,
                actor,
                now,
            ),
        )
    else:
        budget = existing["id"]
        conn.execute(
            "UPDATE cost_budgets SET limit_micros=?, currency=?, unknown_runs_warn=?, "
            "updated_by=?, updated_at=? WHERE id=?",
            (
                fields["limit_micros"],
                fields["currency"],
                fields["unknown_runs_warn"],
                actor,
                now,
                budget,
            ),
        )
        conn.execute("DELETE FROM cost_budget_marks WHERE budget_id=?", (budget,))
    row = conn.execute("SELECT * FROM cost_budgets WHERE id=?", (budget,)).fetchone()
    [entry] = status(conn, now, budgets=[row])
    _mark(conn, entry, now)
    [entry] = status(conn, now, budgets=[row])
    return entry


def clear_budget(conn, kind, key):
    """Remove a scope's budget and its marks; return the removed budget id or None."""
    if not _present(conn):
        return None
    row = conn.execute(
        "SELECT id FROM cost_budgets WHERE scope_kind=? AND scope_key=?", (kind, key)
    ).fetchone()
    if row is None:
        return None
    conn.execute("DELETE FROM cost_budget_marks WHERE budget_id=?", (row["id"],))
    conn.execute("DELETE FROM cost_budgets WHERE id=?", (row["id"],))
    return row["id"]


# --- Needs me -----------------------------------------------------------------------


def attention(conn, now):
    """One item per budget that was marked in its current window and still holds.

    Read-only; ``conn`` may be a read-only connection. Empty when no budget is set.
    """
    items = []
    for entry in status(conn, now):
        if not entry["marks"]:
            continue
        reasons = []
        if entry["state"] != "ok":
            reasons.append(entry["state"])
        if entry["unknown_runs_high"]:
            reasons.append("unknown_runs")
        if not reasons:
            continue
        who = label(entry["scope"]["kind"], entry["scope"]["key"] or "")
        why = "The %s budget has spent %d%% of its limit in this window." % (who, entry["percent"])
        if "limit" in reasons:
            why = (
                "The %s budget is spent: new starts on it are refused until the window ends." % who
            )
            if entry["running_runs"]:
                why += " %d run(s) already running will finish." % entry["running_runs"]
        if "unknown_runs" in reasons:
            why += " %d run(s) have no known cost." % entry["unknown_cost_runs"]
        items.append(
            {
                "kind": "budget",
                "budget": entry["id"],
                "scope": entry["scope"],
                "reasons": reasons,
                "percent": entry["percent"],
                "spent_micros": entry["spent_micros"],
                "limit_micros": entry["limit_micros"],
                "currency": entry["currency"],
                "unknown_cost_runs": entry["unknown_cost_runs"],
                "running_runs": entry["running_runs"],
                "window": entry["window"],
                "why": why,
            }
        )
    return items
