"""Cost events and read-only sums over the run ledger.

A cost event is one append-only row about one run: the tokens, model, provider and cost
the run's machine reported, or an explicit "unknown". Events are written on the same
transaction as the ledger update they follow (``ledger.report``), so there is no second
reporting path and no estimate: the hub holds no prices. A run's current cost is its
latest event; earlier events stay as history.

Sums are computed on read from ``run_ledger`` joined to the latest event. A run that
started and has no known cost is counted as unknown, never as zero, and is listed apart.
Costs are summed per currency and never converted.

Standard library only, like ``ledger``.
"""

import re
from datetime import datetime, timedelta, timezone

TS = "%Y-%m-%dT%H:%M:%SZ"
SOURCES = ("machine_report", "run_end", "backfill", "recorded")
# Runs that have a cost to report: started, or maybe started (uncertain).
COUNTED = ("started", "completed", "errored", "stopped", "uncertain")
WINDOWS = ("calendar_month", "calendar_week", "calendar_day", "rolling_days")
DEFAULT_SETTING = {"window": "calendar_month", "days": None}
MAX_DAYS = 366
UNKNOWN_LIST_LIMIT = 50
DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
TABLES = (
    """
CREATE TABLE IF NOT EXISTS cost_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 job_id TEXT NOT NULL,
 source TEXT NOT NULL,
 model_provider TEXT, model TEXT,
 input_tokens INTEGER, output_tokens INTEGER,
 cost_micros INTEGER, currency TEXT,
 cost_unknown INTEGER NOT NULL DEFAULT 0 CHECK (cost_unknown IN (0,1)),
 recorded_at TEXT NOT NULL,
 CHECK (cost_unknown = 0 OR cost_micros IS NULL),
 CHECK ((cost_micros IS NULL) = (currency IS NULL))
)
""",
    """
CREATE TABLE IF NOT EXISTS cost_settings (
 id INTEGER PRIMARY KEY CHECK (id = 1),
 window TEXT NOT NULL, days INTEGER,
 updated_by TEXT NOT NULL, updated_at TEXT NOT NULL
)
""",
    "CREATE INDEX IF NOT EXISTS cost_events_job ON cost_events(job_id, id)",
)
_LATEST = "(SELECT MAX(id) FROM cost_events WHERE job_id=l.job_id)"
_BACKFILL = (
    "INSERT INTO cost_events(job_id,source,model_provider,model,input_tokens,output_tokens,"
    "cost_micros,currency,cost_unknown,recorded_at) "
    "SELECT l.job_id,'backfill',l.model_provider,l.model,l.input_tokens,l.output_tokens,"
    "l.cost_micros,l.currency,0,l.reported_at FROM run_ledger l "
    "WHERE l.reported_at IS NOT NULL AND NOT EXISTS "
    "(SELECT 1 FROM cost_events e WHERE e.job_id=l.job_id)"
)


def ensure(conn):
    """Create the tables and give reports that predate them an event. Autocommit connection."""
    for statement in TABLES:
        conn.execute(statement)
    pending = conn.execute(
        "SELECT 1 FROM run_ledger l WHERE l.reported_at IS NOT NULL AND NOT EXISTS "
        "(SELECT 1 FROM cost_events e WHERE e.job_id=l.job_id) LIMIT 1"
    ).fetchone()
    if pending is None:
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(_BACKFILL)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def _count(value, name):
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError("invalid " + name)
    return value


def record(
    conn,
    job_id,
    *,
    now,
    source="recorded",
    input_tokens=None,
    output_tokens=None,
    cost_micros=None,
    currency=None,
    unknown=False,
    model_provider=None,
    model=None,
):
    """Append one cost event for a run on the caller's transaction; return its id.

    ``unknown=True`` says the cost cannot be told and excludes ``cost_micros``. A run with
    neither a cost nor ``unknown`` is not recorded with a zero: that is a ValueError.
    """
    if source not in SOURCES:
        raise ValueError("invalid source")
    _count(input_tokens, "input_tokens")
    _count(output_tokens, "output_tokens")
    _count(cost_micros, "cost_micros")
    if unknown and cost_micros is not None:
        raise ValueError("unknown excludes a cost")
    if (cost_micros is None) != (currency is None):
        raise ValueError("cost_micros and currency go together")
    if cost_micros is None and not unknown and input_tokens is None and output_tokens is None:
        raise ValueError("nothing to record")
    for statement in TABLES:
        conn.execute(statement)
    if conn.execute("SELECT 1 FROM run_ledger WHERE job_id=?", (job_id,)).fetchone() is None:
        raise ValueError("run not found")
    return conn.execute(
        "INSERT INTO cost_events(job_id,source,model_provider,model,input_tokens,output_tokens,"
        "cost_micros,currency,cost_unknown,recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            job_id,
            source,
            model_provider,
            model,
            input_tokens,
            output_tokens,
            cost_micros,
            currency,
            int(bool(unknown)),
            now,
        ),
    ).lastrowid


def snapshot(conn, row, now, *, source, unknown=False):
    """Append the event that mirrors a ledger row (its totals so far).

    A run keeps its explicit "unknown" while its cost stays unreported, so a later token
    report does not turn a declared unknown into a silent gap.
    """
    if row["cost_micros"] is None:
        previous = conn.execute(
            "SELECT cost_unknown FROM cost_events WHERE job_id=? ORDER BY id DESC LIMIT 1",
            (row["job_id"],),
        ).fetchone()
        unknown = unknown or bool(previous and previous["cost_unknown"])
    else:
        unknown = False
    if not (unknown or row["cost_micros"] is not None or _tokens(row)):
        return None
    return record(
        conn,
        row["job_id"],
        now=now,
        source=source,
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        cost_micros=row["cost_micros"],
        currency=row["currency"],
        unknown=unknown,
        model_provider=row["model_provider"],
        model=row["model"],
    )


def _tokens(row):
    return row["input_tokens"] is not None or row["output_tokens"] is not None


# --- window -------------------------------------------------------------------------


def get_setting(conn):
    row = conn.execute("SELECT window, days FROM cost_settings WHERE id=1").fetchone()
    return dict(DEFAULT_SETTING) if row is None else {"window": row["window"], "days": row["days"]}


def clean_setting(body):
    """Validate an owner's window setting; return it. Raises ValueError with a safe message."""
    if not isinstance(body, dict) or body.keys() - {"window", "days"}:
        raise ValueError("invalid settings")
    window = body.get("window")
    if window not in WINDOWS:
        raise ValueError("invalid window")
    days = body.get("days")
    if window == "rolling_days":
        if type(days) is not int or not 1 <= days <= MAX_DAYS:
            raise ValueError("days must be 1 to %d" % MAX_DAYS)
    elif days is not None:
        raise ValueError("days only goes with rolling_days")
    return {"window": window, "days": days}


def set_setting(conn, setting, actor, now):
    """Store the workspace window (caller has checked it is the owner) on the caller's
    transaction."""
    conn.execute(TABLES[1])
    conn.execute(
        "INSERT INTO cost_settings(id,window,days,updated_by,updated_at) VALUES (1,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET window=excluded.window, days=excluded.days, "
        "updated_by=excluded.updated_by, updated_at=excluded.updated_at",
        (setting["window"], setting["days"], actor, now),
    )


def _stamp(moment):
    return moment.strftime(TS)


def _bound(text, name):
    if not isinstance(text, str):
        raise ValueError("invalid " + name)
    try:
        if DATE.match(text):
            return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return datetime.strptime(text, TS).replace(tzinfo=timezone.utc)
    except ValueError:
        raise ValueError("invalid " + name) from None


def window(setting, now, since=None, until=None):
    """The half-open ``[start, end)`` UTC range to sum, as timestamps.

    Calendar windows are the UTC month, ISO week (Monday) or day that contains ``now``;
    ``rolling_days`` is the last N days up to now. ``since`` and ``until`` (a date or a UTC
    timestamp) replace either end for one read.
    """
    moment = _bound(now, "now")
    kind = setting["window"]
    if kind == "calendar_month":
        start = moment.replace(day=1, hour=0, minute=0, second=0)
        end = (start + timedelta(days=32)).replace(day=1)
    elif kind == "calendar_week":
        start = (moment - timedelta(days=moment.weekday())).replace(hour=0, minute=0, second=0)
        end = start + timedelta(days=7)
    elif kind == "calendar_day":
        start = moment.replace(hour=0, minute=0, second=0)
        end = start + timedelta(days=1)
    else:
        end = moment + timedelta(seconds=1)
        start = end - timedelta(days=setting["days"])
    if since is not None:
        start = _bound(since, "since")
    if until is not None:
        end = _bound(until, "until")
    if not start < end:
        raise ValueError("since must be before until")
    return _stamp(start), _stamp(end)


# --- sums ---------------------------------------------------------------------------


def _blank():
    return {
        "runs": 0,
        "cost_known_runs": 0,
        "unknown_cost_runs": 0,
        "token_unreported_runs": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_micros": {},
    }


def _add(total, row):
    total["runs"] += 1
    if row["cost_micros"] is None:
        total["unknown_cost_runs"] += 1
    else:
        total["cost_known_runs"] += 1
        by_currency = total["cost_micros"]
        by_currency[row["currency"]] = by_currency.get(row["currency"], 0) + row["cost_micros"]
    if row["input_tokens"] is None and row["output_tokens"] is None:
        total["token_unreported_runs"] += 1
    total["input_tokens"] += row["input_tokens"] or 0
    total["output_tokens"] += row["output_tokens"] or 0


def _order(key):
    parts = key if isinstance(key, tuple) else (key,)
    return tuple((part is not None, "" if part is None else part) for part in parts)


def _groups(rows, key, label):
    groups = {}
    for row in rows:
        entry = groups.setdefault(key(row), {**label(row), **_blank()})
        _add(entry, row)
    return [groups[name] for name in sorted(groups, key=_order)]


def summarize(conn, start, end, *, boards=None, listed=UNKNOWN_LIST_LIMIT):
    """Sums over the runs that started in ``[start, end)``.

    ``boards`` limits the read to those board slugs (None reads every run). Returns the
    workspace sum, the sums per agent, project, board and card, and the runs whose cost is
    unknown. ``agent`` is the worker connection of the machine (None when a run has none).
    """
    for statement in TABLES:
        conn.execute(statement)
    marks = ",".join("?" for _ in COUNTED)
    rows = conn.execute(
        "SELECT l.job_id, l.task_number, l.board_slug, l.project, l.agent, l.status, "
        "l.created_at, e.input_tokens, e.output_tokens, e.cost_micros, e.currency, "
        "COALESCE(e.cost_unknown,0) AS declared "
        "FROM run_ledger l LEFT JOIN cost_events e ON e.id=" + _LATEST + " "
        "WHERE l.created_at>=? AND l.created_at<? AND l.status IN (" + marks + ") "
        "ORDER BY l.created_at, l.job_id",
        (start, end, *COUNTED),
    ).fetchall()
    if boards is not None:
        rows = [row for row in rows if row["board_slug"] in boards]
    total = _blank()
    for row in rows:
        _add(total, row)
    unknown = [row for row in rows if row["cost_micros"] is None]
    return {
        "window": {"start": start, "end": end},
        "workspace": total,
        "by_agent": _groups(rows, lambda r: r["agent"], lambda r: {"agent": r["agent"]}),
        "by_project": _groups(rows, lambda r: r["project"], lambda r: {"project": r["project"]}),
        "by_board": _groups(rows, lambda r: r["board_slug"], lambda r: {"board": r["board_slug"]}),
        "by_card": _groups(
            rows,
            lambda r: (r["board_slug"], r["task_number"]),
            lambda r: {"board": r["board_slug"], "task": r["task_number"]},
        ),
        "unknown_cost": {
            "runs": len(unknown),
            "listed": [
                {
                    "job": row["job_id"],
                    "board": row["board_slug"],
                    "task": row["task_number"],
                    "agent": row["agent"],
                    "status": row["status"],
                    "started_at": row["created_at"],
                    "declared_unknown": bool(row["declared"]),
                }
                for row in unknown[:listed]
            ],
        },
    }
