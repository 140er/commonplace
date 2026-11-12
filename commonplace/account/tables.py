"""How every control-database table relates to one account.

Export and deletion both walk :data:`RULES`, so a table is either classified
here or it stops them: export marks itself incomplete and a purge is refused.
``tests/test_commonplace_account_tables.py`` builds a full gateway and fails
when a control table or one of its columns is missing from this file.

A rule names the columns that tie a row to a user, a workspace, a connection
or an email address. A table that only hangs off another one (deliveries of a
subscription) names its ``parent`` instead. ``Rule()`` means instance-wide:
nothing in the table belongs to one account.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Rule:
    user: tuple = ()
    workspace: tuple = ()
    connection: tuple = ()
    email: tuple = ()
    parent: Optional[tuple] = None  # (column, parent table, parent column)
    # Credentials of a connection: deleted for every connection of a leaving
    # account, also where the connection row itself stays as a tombstone.
    credential: bool = False

    def columns(self) -> tuple:
        own = self.user + self.workspace + self.connection + self.email
        return own + ((self.parent[0],) if self.parent else ())


_U = ("user_id",)
_W = ("workspace_id",)
_C = ("connection_id",)

RULES = {
    "account_deletions": Rule(user=_U),
    "account_plans": Rule(user=_U),
    "agent_schedule_runs": Rule(parent=("schedule_id", "agent_schedules", "id")),
    # A schedule runs for the person who made it, so it leaves with them.
    "agent_schedules": Rule(user=("created_by",), workspace=_W, connection=("agent_id",)),
    "answer_holds": Rule(user=_U, workspace=_W),
    "audit_log": Rule(user=_U, workspace=_W, connection=_C),
    # scope_id is a workspace id or a connection id, by scope.
    "backfill_allowance": Rule(workspace=("scope_id",), connection=("scope_id",)),
    # actor_id is the user id for a person and the connection id for an agent.
    "bot_model_calls": Rule(user=_U, workspace=_W),
    "capture_ops": Rule(user=("actor_id",), workspace=_W, connection=("actor_id",)),
    "capture_settings": Rule(workspace=_W),
    "chat_front_jobs": Rule(user=_U, workspace=_W),
    "chat_handoffs": Rule(user=_U, workspace=_W),
    "chat_live_posts": Rule(workspace=_W),
    # Read marks name their reader as an actor ref (``user:<id>``), like card history.
    "chat_live_reads": Rule(workspace=_W),
    "chat_model_settings": Rule(workspace=_W),
    "chat_runner_jobs": Rule(user=_U, workspace=_W),
    "chat_runners_seen": Rule(user=_U, workspace=_W),
    "connections": Rule(user=_U, workspace=_W),
    "console_settings": Rule(workspace=_W, connection=_C),
    "email_challenges": Rule(email=("email",)),
    "events": Rule(workspace=_W),
    "github_account_owners": Rule(user=_U),
    "github_confirmations": Rule(user=_U),
    "github_connect_flows": Rule(user=_U),
    "github_grants": Rule(user=_U),
    "github_keys": Rule(user=_U),
    "github_lifecycle": Rule(user=_U),
    "github_worker_lease": Rule(),
    "handoff_outbox": Rule(user=_U, workspace=_W, connection=_C),
    "handoff_settings": Rule(workspace=_W),
    "handoffs": Rule(workspace=_W, connection=_C),
    "ingest_cadence": Rule(user=("account_id",)),
    "ingest_requests": Rule(user=("account_id",), connection=_C),
    "login_links": Rule(user=_U),
    "memberships": Rule(user=_U, workspace=_W),
    "meta": Rule(),
    "oauth_clients": Rule(),
    "oauth_codes": Rule(connection=_C, credential=True),
    "oauth_requests": Rule(user=_U, workspace=_W),
    "oauth_tokens": Rule(connection=_C, credential=True),
    "orchestration_settings": Rule(workspace=_W),
    "phone_push_deliveries": Rule(
        workspace=_W, parent=("subscription_id", "phone_push_subscriptions", "id")
    ),
    "phone_push_state": Rule(parent=("subscription_id", "phone_push_subscriptions", "id")),
    "phone_push_subscriptions": Rule(user=_U, workspace=_W),
    "project_model_catalog": Rule(workspace=_W),
    "project_model_escalation": Rule(workspace=_W),
    "project_orchestrators": Rule(workspace=_W, connection=_C),
    "project_report_activation": Rule(workspace=_W),
    "project_report_bindings": Rule(workspace=_W, connection=_C),
    "project_report_dispatch_retry": Rule(workspace=_W),
    "project_report_receipts": Rule(workspace=_W),
    "routine_runs": Rule(parent=("routine_id", "routines", "id")),
    # A routine runs for its owner, so it leaves with them.
    "routines": Rule(user=("owner_user_id",), workspace=_W),
    "rule_checks": Rule(workspace=_W),
    "rule_stores": Rule(workspace=_W),
    "runner_connections": Rule(connection=_C),
    "runner_machines": Rule(workspace=_W),
    "sessions": Rule(user=_U),
    "short_links": Rule(workspace=_W),
    "signin_flows": Rule(),
    "signin_usage": Rule(),
    "signup_waitlist": Rule(email=("email",)),
    "skill_decks": Rule(workspace=_W),
    "skill_previews": Rule(user=_U, workspace=_W, parent=("deck_id", "skill_decks", "id")),
    "skill_reports": Rule(parent=("deck_id", "skill_decks", "id")),
    "sync_backfill": Rule(user=_U, workspace=_W),
    "sync_events": Rule(workspace=_W),
    "sync_groups": Rule(user=_U, workspace=_W),
    "sync_import_submissions": Rule(workspace=_W),
    "sync_parts": Rule(workspace=_W, parent=("stream_id", "sync_streams", "stream_id")),
    "sync_streams": Rule(user=_U, workspace=_W, connection=_C),
    "tool_approvals": Rule(workspace=_W, connection=_C),
    "tool_policy": Rule(workspace=_W),
    "transcription_usage": Rule(user=_U),
    "user_identities": Rule(user=_U),
    "users": Rule(user=("id",)),
    "webhook_attempts": Rule(parent=("delivery_id", "webhook_deliveries", "id")),
    "webhook_deliveries": Rule(parent=("webhook_id", "webhooks", "id")),
    "webhooks": Rule(workspace=_W, connection=_C),
    "workspace_voice": Rule(workspace=_W),
    "workspaces": Rule(user=("personal_of",), workspace=("id",)),
}

# A purge handles these itself: tombstones, the append-only audit log and the
# rows that must go last because others point at them.
KEPT = frozenset(
    {"account_deletions", "audit_log", "connections", "memberships", "users", "workspaces"}
)

# Credentials and their digests are never exported, also not to their owner.
# ``tests/test_commonplace_account_tables.py`` lists every control column
# whose name suggests a credential and fails on one that is neither matched
# here nor reviewed there.
_SECRET = re.compile(r"hash|secret|wrapped|verifier|nonce|auth_header|lease_token|event_id_key")
REDACTED = "[redacted]"


def secret(column: str) -> bool:
    return _SECRET.search(column) is not None


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def present(conn) -> dict:
    """Every table in the database with its column names."""
    names = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        )
    ]
    return {
        name: [row[1] for row in conn.execute(f"PRAGMA table_info({quote(name)})")]
        for name in names
    }


def unclassified(tables: dict) -> list:
    """Tables this file does not describe, or describes with a missing column."""
    return sorted(
        name
        for name, columns in tables.items()
        if name not in RULES
        or not set(RULES[name].columns()) <= set(columns)
        or (RULES[name].parent is not None and RULES[name].parent[1] not in tables)
    )


def guessed(columns) -> Rule:
    """The best reading of an unclassified table, for export only."""
    return Rule(
        user=tuple(c for c in ("user_id",) if c in columns),
        workspace=tuple(c for c in ("workspace_id",) if c in columns),
        connection=tuple(c for c in ("connection_id",) if c in columns),
    )


def account_clause(table: str, tables: dict, *, credentials_only: bool = False, rules=RULES):
    """SQL selecting the rows of ``table`` that belong to user ``:uid``.

    Rows count through a user column, an email column (``:email``), a
    connection of that user, or a parent row that counts. A purge passes
    ``credentials_only``: rows that only reference a connection then stay,
    unless the table holds that connection's credentials. Returns ``None``
    when no row of the table can belong to an account.
    """
    rule = rules.get(table)
    if rule is None or table not in tables:
        return None
    parts = [f"{quote(c)} = :uid" for c in rule.user]
    parts += [f"{quote(c)} = :email COLLATE NOCASE" for c in rule.email]
    if rule.credential or not credentials_only:
        parts += [
            f"{quote(c)} IN (SELECT id FROM connections WHERE user_id = :uid)"
            for c in rule.connection
        ]
    if rule.parent is not None:
        column, parent, key = rule.parent
        inner = account_clause(parent, tables, credentials_only=credentials_only, rules=rules)
        if inner is not None:
            parts.append(
                f"{quote(column)} IN (SELECT {quote(key)} FROM {quote(parent)} WHERE {inner})"
            )
    return "(" + " OR ".join(parts) + ")" if parts else None


def workspace_clause(table: str, tables: dict, *, rules=RULES):
    """SQL selecting the rows of ``table`` that belong to workspace ``:wid``."""
    rule = rules.get(table)
    if rule is None or table not in tables:
        return None
    parts = [f"{quote(c)} = :wid" for c in rule.workspace]
    parts += [
        f"{quote(c)} IN (SELECT id FROM connections WHERE workspace_id = :wid)"
        for c in rule.connection
    ]
    if rule.parent is not None:
        column, parent, key = rule.parent
        inner = workspace_clause(parent, tables, rules=rules)
        if inner is not None:
            parts.append(
                f"{quote(column)} IN (SELECT {quote(key)} FROM {quote(parent)} WHERE {inner})"
            )
    return "(" + " OR ".join(parts) + ")" if parts else None


def children_first(tables: dict) -> list:
    """Classified tables ordered so a row is deleted before the row it hangs off."""

    def depth(name, seen=()):
        rule = RULES[name]
        if rule.parent is None or rule.parent[1] in seen or rule.parent[1] not in RULES:
            return 0
        return 1 + depth(rule.parent[1], seen + (name,))

    names = [name for name in tables if name in RULES]
    return sorted(names, key=lambda name: (-depth(name), name))
