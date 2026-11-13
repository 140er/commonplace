"""Per-person coworker read cursors. Team-bot cursors remain in their own store."""

from pathlib import Path

from mempalace.sqlite_crypto import connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS coworker_reads (
 conversation_id TEXT NOT NULL, user_id TEXT NOT NULL, last_read INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(conversation_id,user_id),
 FOREIGN KEY(conversation_id,user_id) REFERENCES members(conversation_id,user_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS coworker_reads_user ON coworker_reads(user_id);
"""


def mark_read(conn, user_id, conversation_id, messages):
    """Advance only through messages actually delivered to this authorized reader."""
    if not messages:
        return
    conn.execute(
        "INSERT INTO coworker_reads(conversation_id,user_id,last_read) VALUES (?,?,?) "
        "ON CONFLICT(conversation_id,user_id) DO UPDATE SET "
        "last_read=max(last_read,excluded.last_read)",
        (conversation_id, user_id, max(row["id"] for row in messages)),
    )


def purge_account(gateway, user_id):
    """Purge cursors even when the person's workspace survives their departure.

    Account deletion disables the person first, so no authorized read can race
    this cleanup. Absent stores/tables are left absent; a failed cleanup raises
    and prevents the account purge from reporting success.
    """
    from commonplace.account import tables

    with gateway.db._lock:
        # Former memberships count too: removal from a workspace must not leave
        # that person's cursor behind when their account is later purged.
        workspaces = gateway.db._conn.execute("SELECT id FROM workspaces").fetchall()
    rules = tables.WORKSPACE_RULES["coworkers.sqlite3"]
    for row in workspaces:
        path = Path(gateway.supervisor.workspace_dir(row[0])) / "coworkers.sqlite3"
        if not path.is_file():
            continue
        conn = connect(path.resolve().as_uri() + "?mode=rw", uri=True, timeout=0.25)
        try:
            clause = tables.account_clause("coworker_reads", tables.present(conn), rules=rules)
            if clause is not None:
                conn.execute("DELETE FROM coworker_reads WHERE " + clause, {"uid": user_id})
                conn.commit()
        finally:
            conn.close()
