"""Chat-relay jobs in the run ledger, so the cost ledger and budgets count chat spend.

A chat job (``chat_relay``) has its own queue in the control database; this module gives it
the one record the cost ledger wants: a ``run_ledger`` row keyed by the chat job id
(``source = 'chat'``, card number 0, the board as its project, the leasing machine's worker
connection as its agent) and one cost event when the job ends, written by the same
``costs.snapshot`` and marked by the same ``budgets.after_run`` as a card run's report.

The hub holds no prices: tokens and cost are only what the runner reported. A job that
reports no cost ends with an explicit unknown event, never a zero. The job id is the
idempotency key: a row that already ended is never written again.

Everything runs on the caller's open runner write transaction. Standard library only.
"""

from . import budgets, costs, ledger

SOURCE = "chat"
TOPIC = "chat"
# How a chat job ended, as a ledger status: a lease that expired may or may not have run.
ENDS = ("completed", "errored", "uncertain")


def start(conn, job, machine, now, account=None):
    """Open the ledger row of a chat job a machine just leased (idempotent). ``account`` holds
    the model mode's run-record fields (``jobs.ACCOUNT_COLUMNS``), pinned when the machine leases."""
    conn.execute(ledger.TABLE)
    account = account or {}
    conn.execute(
        "INSERT INTO run_ledger(job_id,task_number,board_slug,project,topic,machine_id,agent,"
        "requested_by,model_provider,model,status,started_at,created_at,updated_at,source,"
        "mode,mode_source,account_kind,account_label,account_person) "
        "VALUES (?,0,?,?,?,?,?,?,?,?,'started',?,?,?,?,?,?,?,?,?) ON CONFLICT(job_id) DO NOTHING",
        (
            job["id"],
            job["board"],
            job["board"],
            TOPIC,
            machine["id"],
            machine["worker_connection_id"],
            "user:" + job["user_id"],
            job["cli"],
            job["model"],
            now,
            now,
            now,
            SOURCE,
            account.get("mode"),
            account.get("mode_source"),
            account.get("account_kind"),
            account.get("account_label"),
            account.get("account_person"),
        ),
    )
    budgets.after_run(conn, job["id"], now)


def finish(conn, job, machine, status, now, *, provider=None, model=None, usage=None):
    """End the chat job's run and append its one cost event; return the ledger row, or None
    when the run had already ended (a retry writes nothing more). ``machine`` opens the row
    first when the lease did not (None: the row must exist).

    ``usage`` holds what the runner reported (``ledger.clean`` keys): tokens, ``cost_micros``
    with ``currency``, or ``cost_unknown``. Without a cost the event is an explicit unknown.
    """
    if status not in ENDS:
        raise ValueError("invalid status")
    if machine is not None:
        start(conn, job, machine, now)
    row = conn.execute("SELECT * FROM run_ledger WHERE job_id=?", (job["id"],)).fetchone()
    if row is None or row["source"] != SOURCE or row["ended_at"] is not None:
        return None
    usage = usage or {}
    values = {key: usage[key] for key in ledger.USAGE_KEYS if key in usage and key != "log_ref"}
    values.update(
        status=status,
        ended_at=now,
        reported_at=now,
        updated_at=now,
        model_provider=provider or row["model_provider"],
        model=model or row["model"],
    )
    conn.execute(
        "UPDATE run_ledger SET " + ",".join(key + "=?" for key in values) + " WHERE job_id=?",
        (*values.values(), job["id"]),
    )
    row = conn.execute("SELECT * FROM run_ledger WHERE job_id=?", (job["id"],)).fetchone()
    costs.snapshot(conn, row, now, source=SOURCE, unknown=row["cost_micros"] is None)
    budgets.after_run(conn, job["id"], now)
    return row
