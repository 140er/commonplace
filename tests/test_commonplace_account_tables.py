"""Every control table is classified for account export and deletion."""

import re
import sqlite3

from commonplace import continuity
from commonplace.account import tables
from commonplace.skills import service as skills
from tests._commonplace_account_helpers import site_fixture  # noqa: F401

# Created on first use rather than at gateway start.
LAZY = {"skill_decks", "skill_previews", "skill_reports", "sync_import_submissions"}


def test_every_control_table_of_a_full_gateway_is_classified(site):
    with site.db._lock:
        conn = site.db._conn
        assert LAZY.isdisjoint(tables.present(conn))
        conn.executescript(skills.PUBLIC_SCHEMA)
        continuity._mark_part_submission(conn, site.team, "stream")
        present = tables.present(conn)
    assert tables.unclassified(present) == [], (
        "classify new control tables in commonplace/account/tables.py: "
        "export and deletion must know whose rows they hold"
    )
    assert set(tables.RULES) == set(present), "a rule names a table that no longer exists"
    assert tables.KEPT <= set(present)


def test_rules_only_name_parents_that_are_classified():
    for name, rule in tables.RULES.items():
        if rule.parent is not None:
            assert rule.parent[1] in tables.RULES, name
    ordered = tables.children_first({name: [] for name in tables.RULES})
    for name, rule in tables.RULES.items():
        if rule.parent is not None:
            assert ordered.index(name) < ordered.index(rule.parent[1])


def test_missing_column_or_parent_makes_a_table_unclassified():
    present = {"sessions": ["id_hash"], "webhook_attempts": ["delivery_id"], "new": ["x"]}
    assert tables.unclassified(present) == ["new", "sessions", "webhook_attempts"]
    guessed = tables.guessed(["user_id", "note", "connection_id"])
    assert guessed.user == ("user_id",) and guessed.connection == ("connection_id",)
    assert guessed.workspace == ()


def test_clauses_select_through_users_connections_emails_and_parents():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE connections (id TEXT, workspace_id TEXT, user_id TEXT);
        CREATE TABLE webhooks (id TEXT, workspace_id TEXT, connection_id TEXT);
        CREATE TABLE webhook_deliveries (id TEXT, webhook_id TEXT);
        CREATE TABLE webhook_attempts (delivery_id TEXT, attempt INTEGER);
        CREATE TABLE email_challenges (email TEXT);
        CREATE TABLE oauth_tokens (connection_id TEXT);
        INSERT INTO connections VALUES ('c1', 'w1', 'u1'), ('c2', 'w2', 'u2');
        INSERT INTO webhooks VALUES ('h1', 'w1', 'c1'), ('h2', 'w2', 'c2');
        INSERT INTO webhook_deliveries VALUES ('d1', 'h1'), ('d2', 'h2');
        INSERT INTO webhook_attempts VALUES ('d1', 1), ('d2', 1);
        INSERT INTO email_challenges VALUES ('U1@example.test'), ('u2@example.test');
        INSERT INTO oauth_tokens VALUES ('c1'), ('c2');
        """
    )
    present = tables.present(conn)
    scope = {"uid": "u1", "email": "u1@example.test"}

    def account(table, **kwargs):
        clause = tables.account_clause(table, present, **kwargs)
        return clause and conn.execute(f"SELECT * FROM {table} WHERE {clause}", scope).fetchall()

    assert account("webhook_attempts") == [("d1", 1)]
    assert account("email_challenges") == [("U1@example.test",)]
    assert account("oauth_tokens", credentials_only=True) == [("c1",)]
    # A purge keeps rows that only point at a connection: the workspace still uses them.
    assert account("webhooks", credentials_only=True) is None
    assert account("webhook_attempts", credentials_only=True) is None
    assert tables.account_clause("meta", {"meta": ["key", "value"]}) is None
    assert tables.account_clause("absent", present) is None

    clause = tables.workspace_clause("webhook_attempts", present)
    assert conn.execute(
        f"SELECT * FROM webhook_attempts WHERE {clause}", {"wid": "w2"}
    ).fetchall() == [("d2", 1)]
    clause = tables.workspace_clause("oauth_tokens", present)
    assert conn.execute(f"SELECT * FROM oauth_tokens WHERE {clause}", {"wid": "w2"}).fetchall() == [
        ("c2",)
    ]


def test_secret_columns():
    for column in ("token_hash", "id_hash", "secret", "wrapped", "auth_header", "lease_token"):
        assert tables.secret(column), column
    for column in ("token_hint", "email", "request_sha256", "topic", "name"):
        assert not tables.secret(column), column


# Columns whose name suggests a credential but that hold none: kinds, counters,
# times, content digests, a prefix shown in the app, the name of a key.
REVIEWED_PLAIN = {
    "auth",
    "authorize_path",
    "idempotency_key",
    "key",
    "key_ref",
    "lease_until",
    "release_at",
    "report_auth_retry_count",
    "report_auth_retry_since",
    "request_sha256",
    "session_sha256",
    "token_hint",
    "used_tokens",
    "window_tokens",
}
SUSPICIOUS = re.compile(
    r"token|key|secret|password|passwd|auth|hash|nonce|verifier|wrapped|lease|credential|cookie|sha"
)


def test_every_credential_like_control_column_is_redacted_or_reviewed(site):
    with site.db._lock:
        present = tables.present(site.db._conn)
    unreviewed = sorted(
        f"{table}.{column}"
        for table, columns in present.items()
        for column in columns
        if SUSPICIOUS.search(column) and not tables.secret(column) and column not in REVIEWED_PLAIN
    )
    assert unreviewed == [], "redact these in tables._SECRET or review them as plain here"
    assert not any(tables.secret(column) for column in REVIEWED_PLAIN)
