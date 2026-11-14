"""Account deletion: an explicit request, a grace period, then a purge.

Off unless the operator sets ``COMMONPLACE_ACCOUNT_DELETION=on``.

A signed-in person asks for deletion from a session started less than
:data:`REAUTH_MINUTES` ago and types their email address to confirm. The
request only records a date, ``COMMONPLACE_ACCOUNT_DELETION_GRACE_DAYS`` (14)
days ahead. Until then the account works as before and the person can cancel.

A purge runs once that date has passed, and only while nothing blocks it:

- A workspace with no other member is deleted: its directory, every store
  generation and every control row.
- A workspace the account owns alone, with other members left, passes to its
  longest-standing active member (see :func:`successor`): they become an owner
  in the transaction that starts the purge, with an audit entry, a
  ``workspace.owner_promoted`` event in that workspace and a mail when the
  server can send one. Cancelling a request promotes nobody, and repeating a
  purge promotes nobody twice.
- A workspace with other members keeps everything that belongs to the
  workspace: its directory, its settings and every row keyed to it or to one
  of its connections. The account's membership and connections stay as
  tombstones (member role, revoked, no credential), so earlier work keeps one
  unambiguous author.
- Rows keyed to the account itself are deleted wherever they are, also when
  they name a shared workspace: sessions, sign-in identities, plan and usage,
  push subscriptions, and what ran on the person's behalf there (their sync
  streams, chat jobs, held answers, agent schedules).
- The ``users`` row stays as a tombstone without name or address, disabled
  for good. The audit log is append-only and holds ids, not content; it stays.

It is refused, and retried later, while the account is the only owner of a
workspace whose other members are all disabled or have asked to leave (nobody
can take it over), is the last instance administrator, still holds a GitHub
grant, or while the control database has a table that
:mod:`.tables` does not classify. Refusing is the safe side: no other
person's data is ever deleted or left without an owner.

A purge that stops halfway is resumed, also after the operator switched
deletion off again: the account is disabled and marked ``purging`` before the
first file is removed, and every later step can be repeated.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
from datetime import datetime, timedelta, timezone

from commonplace import events, sessions
from commonplace.admission import GATE
from commonplace.http_util import log_failure
from commonplace.workspace_store import workspace_root

from . import tables

LOG = logging.getLogger(__name__)

REAUTH_MINUTES = 10
DEFAULT_GRACE_DAYS = 14
MAX_GRACE_DAYS = 90
SWEEP_SECONDS = 3600.0
_FMT = "%Y-%m-%dT%H:%M:%SZ"

SCHEMA = """
CREATE TABLE IF NOT EXISTS account_deletions (
    user_id TEXT PRIMARY KEY REFERENCES users(id),
    state TEXT NOT NULL CHECK (state IN ('pending', 'purging', 'purged')),
    requested_at TEXT NOT NULL,
    purge_after TEXT NOT NULL,
    workspaces TEXT NOT NULL DEFAULT '[]',
    blocked TEXT,
    purged_at TEXT
);
"""


class Refused(Exception):
    """A request the person should read: HTTP status, code and message."""

    def __init__(self, status: int, code: str, message: str, **extra):
        super().__init__(message)
        self.status = status
        self.body = {"error": message, "code": code, **extra}


def ensure_schema(db) -> None:
    with db._lock:
        db._conn.executescript(SCHEMA)


def enabled() -> bool:
    return os.environ.get("COMMONPLACE_ACCOUNT_DELETION", "") == "on"


def grace_days() -> int:
    try:
        days = int(os.environ.get("COMMONPLACE_ACCOUNT_DELETION_GRACE_DAYS", ""))
    except ValueError:
        return DEFAULT_GRACE_DAYS
    return days if 0 <= days <= MAX_GRACE_DAYS else DEFAULT_GRACE_DAYS


def _now(gateway) -> datetime:
    return datetime.fromtimestamp(gateway.wall_clock(), timezone.utc).replace(microsecond=0)


def successor(conn, workspace_id: str, user_id: str):
    """The member who takes over a workspace ``user_id`` leaves as its only owner.

    The longest-standing active member: a plain member whose account is not
    disabled and has no deletion request of its own, by the date they joined,
    then by id, so ties always resolve the same way. ``None`` when there is none.
    """
    return conn.execute(
        "SELECT m.user_id AS id FROM memberships m JOIN users u ON u.id = m.user_id "
        "WHERE m.workspace_id = ? AND m.user_id != ? AND m.role = 'member' "
        "AND u.disabled_at IS NULL "
        "AND NOT EXISTS (SELECT 1 FROM account_deletions d WHERE d.user_id = m.user_id) "
        "ORDER BY m.created_at, m.user_id LIMIT 1",
        (workspace_id, user_id),
    ).fetchone()


def effects(conn, present: dict, user_id: str) -> dict:
    """What a purge of ``user_id`` would do right now, and what blocks it.

    Accounts that were purged before are not "other members": only their
    tombstones are left. A workspace the account owns alone, with other members
    left, passes to its :func:`successor` (``promote_owner``); it only blocks
    when none of them can take it.
    """
    others = (
        "(SELECT count(*) FROM memberships o JOIN users ou ON ou.id = o.user_id "
        "WHERE o.workspace_id = m.workspace_id AND o.user_id != m.user_id AND o.user_id NOT IN "
        "(SELECT user_id FROM account_deletions WHERE state = 'purged'){})"
    )
    members, owners = (
        others.format(""),
        others.format(" AND o.role = 'owner' AND ou.disabled_at IS NULL"),
    )
    rows = conn.execute(
        f"SELECT m.workspace_id AS id, w.name AS name, m.role AS role, "
        f"{members} AS others, {owners} AS owners "
        "FROM memberships m JOIN workspaces w ON w.id = m.workspace_id "
        "WHERE m.user_id = ? ORDER BY w.created_at, w.id",
        (user_id,),
    ).fetchall()
    delete, leave, promote, blockers = [], [], [], []
    for row in rows:
        workspace = {"id": row["id"], "name": row["name"]}
        if row["others"] == 0:
            delete.append(workspace)
        elif row["role"] == "owner" and row["owners"] == 0:
            heir = successor(conn, row["id"], user_id)
            if heir is None:
                blockers.append({"code": "sole_owner", "workspace": workspace})
            else:
                promote.append({**workspace, "to": heir["id"]})
        else:
            leave.append(workspace)
    user = conn.execute("SELECT is_admin FROM users WHERE id = ?", (user_id,)).fetchone()
    if (
        user is not None
        and user["is_admin"]
        and conn.execute(
            "SELECT 1 FROM users WHERE is_admin = 1 AND disabled_at IS NULL AND id != ?",
            (user_id,),
        ).fetchone()
        is None
    ):
        blockers.append({"code": "last_admin"})
    if (
        "github_grants" in present
        and conn.execute("SELECT 1 FROM github_grants WHERE user_id = ?", (user_id,)).fetchone()
    ):
        blockers.append({"code": "github_connected"})
    unknown = tables.unclassified(present)
    if unknown:
        blockers.append({"code": "unsupported_data", "tables": unknown})
    return {
        "delete_workspaces": delete,
        "leave_workspaces": leave,
        "promote_owner": promote,
        "blockers": blockers,
    }


def _view(row) -> dict:
    return {
        "state": row["state"],
        "requested_at": row["requested_at"],
        "purge_after": row["purge_after"],
        "blocked": json.loads(row["blocked"]) if row["blocked"] else [],
    }


def _row(conn, user_id):
    return conn.execute("SELECT * FROM account_deletions WHERE user_id = ?", (user_id,)).fetchone()


def status(gateway, user_id: str, session=None) -> dict:
    """What the person sees before they decide: the switch, the plan, the request."""
    db = gateway.db
    started = sessions.session_created_at(db, session)
    with db._lock:
        row = _row(db._conn, user_id)
        found = effects(db._conn, tables.present(db._conn), user_id)
    return {
        "enabled": enabled(),
        "grace_days": grace_days(),
        "reauth_minutes": REAUTH_MINUTES,
        "session_is_recent": started is not None
        and _now(gateway) - started <= timedelta(minutes=REAUTH_MINUTES),
        "request": _view(row) if row is not None else None,
        **{
            **found,
            "promote_owner": [
                {k: v for k, v in w.items() if k != "to"} for w in found["promote_owner"]
            ],
        },
    }


def request(gateway, principal, session, confirm) -> dict:
    """Record a deletion request. Raises :class:`Refused` with the reason."""
    if not enabled():
        raise Refused(404, "disabled", "Account deletion is not enabled on this server")
    db = gateway.db
    current = _now(gateway)
    started = sessions.session_created_at(db, session)
    if started is None or current - started > timedelta(minutes=REAUTH_MINUTES):
        raise Refused(
            403,
            "reauth_required",
            f"Sign in again: deleting an account needs a sign-in from the last "
            f"{REAUTH_MINUTES} minutes",
        )
    return _record_request(gateway, principal, current, confirm)


def _record_request(gateway, principal, current, confirm):
    """Shared deletion rules, after the route has verified its person's proof."""
    db = gateway.db
    if (
        not isinstance(confirm, str)
        or confirm.strip().casefold() != principal.user_email.casefold()
    ):
        raise Refused(400, "confirm_mismatch", "Type your account's email address to confirm")
    with db._lock, db._tx() as conn:
        row = _row(conn, principal.user_id)
        if row is None:
            found = effects(conn, tables.present(conn), principal.user_id)
            if found["blockers"]:
                raise Refused(
                    409,
                    "blocked",
                    "This account cannot be deleted yet",
                    blockers=found["blockers"],
                )
            conn.execute(
                "INSERT INTO account_deletions(user_id, state, requested_at, purge_after) "
                "VALUES (?, 'pending', ?, ?)",
                (
                    principal.user_id,
                    current.strftime(_FMT),
                    (current + timedelta(days=grace_days())).strftime(_FMT),
                ),
            )
            db.audit(action="account.delete.request", decision="ok", user_id=principal.user_id)
            row = _row(conn, principal.user_id)
    return _view(row)


def cancel(gateway, user_id: str) -> bool:
    """Withdraw a request that has not started purging."""
    db = gateway.db
    with db._lock, db._tx() as conn:
        done = (
            conn.execute(
                "DELETE FROM account_deletions WHERE user_id = ? AND state = 'pending'",
                (user_id,),
            ).rowcount
            == 1
        )
        if done:
            db.audit(action="account.delete.cancel", decision="ok", user_id=user_id)
    return done


def purge_due(gateway, *, resume_only: bool = False) -> list:
    """Purge every request whose grace period is over. One failure stops only itself.

    ``resume_only`` finishes purges that already started and starts no new one.
    """
    db = gateway.db
    with db._lock:
        due = [
            row["user_id"]
            for row in db._conn.execute(
                "SELECT user_id FROM account_deletions WHERE state = 'purging' "
                "OR (state = 'pending' AND purge_after <= ? AND NOT ?) "
                "ORDER BY purge_after, user_id",
                (_now(gateway).strftime(_FMT), int(resume_only)),
            )
        ]
    results = []
    for user_id in due:
        try:
            results.append({"user": user_id, **purge(gateway, user_id)})
        except Exception as exc:
            log_failure(LOG, "Account purge failed", exc)
            results.append({"user": user_id, "state": "error"})
    return results


OWNER_PROMOTED = "workspace.owner_promoted"


def _promote(db, conn, user_id, found) -> list:
    """Hand workspaces the account owns alone to their successors, in the caller's
    transaction. Repeating it changes nothing: once the successor is an owner the
    workspace no longer lists a promotion. Returns what to announce after commit."""
    announce = []
    for item in found["promote_owner"]:
        changed = conn.execute(
            "UPDATE memberships SET role = 'owner' WHERE workspace_id = ? AND user_id = ? "
            "AND role = 'member'",
            (item["id"], item["to"]),
        ).rowcount
        if changed != 1:
            continue
        db.audit(
            action="workspace.owner_promote",
            decision="ok",
            workspace_id=item["id"],
            user_id=user_id,
            reason=f"{item['to']}:owner",
            target_ids=[item["to"]],
        )
        event = events.record(
            db,
            item["id"],
            OWNER_PROMOTED,
            subject=item["to"],
            actor=f"user:{user_id}",
            data={"user": item["to"], "reason": "previous_owner_left"},
        )
        email = conn.execute("SELECT email FROM users WHERE id = ?", (item["to"],)).fetchone()[0]
        announce.append({"event": event, "email": email, "name": item["name"]})
    return announce


def _announce(gateway, announce) -> None:
    """Tell the promoted members: the stored event goes to the app and webhook
    listeners, and a mail when the server has one configured. A failed mail
    never undoes the promotion; the event is the durable notice."""
    mail = getattr(getattr(gateway, "email_auth", None), "mailer", None)
    for item in announce:
        events.notify(item["event"])
        if mail is None or not mail.configured:
            continue
        try:
            mail.send(
                item["email"],
                f"You now own {item['name']}",
                f"The previous owner of the workspace {item['name']} has closed their account. "
                "As its longest-standing member you are now an owner of the workspace.",
            )
        except Exception as exc:
            log_failure(LOG, "Owner promotion mail failed", exc)


def _blocked(db, conn, row, blockers) -> dict:
    recorded = json.dumps(blockers, sort_keys=True)
    if row["blocked"] != recorded:
        conn.execute(
            "UPDATE account_deletions SET blocked = ? WHERE user_id = ?",
            (recorded, row["user_id"]),
        )
        db.audit(
            action="account.purge",
            decision="blocked",
            user_id=row["user_id"],
            reason=",".join(sorted({b["code"] for b in blockers})),
        )
    return {"state": row["state"], "blocked": blockers}


def settle_invites(db, conn, user_id: str, email: str, current: str) -> None:
    """Revoke the pending invites the account sent and delete the ones addressed to its
    email, each with an audit entry. Accepted invites stay as history."""
    if "workspace_invites" not in tables.present(conn):
        return
    pending = conn.execute(
        "SELECT id, workspace_id FROM workspace_invites WHERE created_by = ? "
        "AND accepted_at IS NULL AND revoked_at IS NULL",
        (user_id,),
    ).fetchall()
    conn.execute(
        "UPDATE workspace_invites SET revoked_at = ?, revoked_by = ? WHERE created_by = ? "
        "AND accepted_at IS NULL AND revoked_at IS NULL",
        (current, user_id, user_id),
    )
    addressed = conn.execute(
        "SELECT id, workspace_id FROM workspace_invites WHERE email = ? COLLATE NOCASE",
        (email,),
    ).fetchall()
    conn.execute("DELETE FROM workspace_invites WHERE email = ? COLLATE NOCASE", (email,))
    for action, rows in (("invite.revoke", pending), ("invite.delete", addressed)):
        for workspace_id in sorted({row["workspace_id"] for row in rows}):
            db.audit(
                action=action,
                decision="ok",
                workspace_id=workspace_id,
                user_id=user_id,
                reason="account_deleted",
                target_ids=[row["id"] for row in rows if row["workspace_id"] == workspace_id],
            )


def purge(gateway, user_id: str) -> dict:
    """Delete one account now, whatever its date. Callers check the date."""
    db = gateway.db
    current = _now(gateway).strftime(_FMT)
    with db._lock, db._tx() as conn:
        row = _row(conn, user_id)
        if row is None or row["state"] == "purged":
            return {"state": row["state"] if row is not None else None}
        found = effects(conn, tables.present(conn), user_id)
        if found["blockers"]:
            return _blocked(db, conn, row, found["blockers"])
        doomed = [workspace["id"] for workspace in found["delete_workspaces"]]
        announce = _promote(db, conn, user_id, found)
        # From here on the account cannot act: no request of its own can race the purge.
        conn.execute(
            "UPDATE users SET disabled_at = COALESCE(disabled_at, ?) WHERE id = ?",
            (current, user_id),
        )
        conn.execute(
            "UPDATE account_deletions SET state = 'purging', workspaces = ?, blocked = NULL "
            "WHERE user_id = ?",
            (json.dumps(doomed), user_id),
        )
    _announce(gateway, announce)
    from commonplace.chat.coworkers.unread import purge_account

    purge_account(gateway, user_id)
    for workspace_id in doomed:
        # Checked again right before the files go: someone may have joined since.
        with db._lock:
            found = effects(db._conn, tables.present(db._conn), user_id)
        if workspace_id in {w["id"] for w in found["delete_workspaces"]}:
            remove_workspace(gateway, workspace_id)
    with db._lock, db._tx() as conn:
        row = _row(conn, user_id)
        present = tables.present(conn)
        found = effects(conn, present, user_id)
        if found["blockers"]:
            return _blocked(db, conn, row, found["blockers"])
        # A workspace someone joined in the meantime is no longer this account's to delete.
        announce = _promote(db, conn, user_id, found)
        gone = [w["id"] for w in found["delete_workspaces"] if w["id"] in doomed]
        email = conn.execute("SELECT email FROM users WHERE id = ?", (user_id,)).fetchone()[0]
        scope = {"uid": user_id, "email": email}
        settle_invites(db, conn, user_id, email, current)
        for name in tables.children_first(present):
            if name in tables.KEPT:
                continue
            table = tables.quote(name)
            clause = tables.account_clause(name, present, credentials_only=True)
            if clause is not None:
                conn.execute(f"DELETE FROM {table} WHERE {clause}", scope)
            clause = tables.workspace_clause(name, present)
            if clause is not None:
                for workspace_id in gone:
                    conn.execute(f"DELETE FROM {table} WHERE {clause}", {"wid": workspace_id})
        for workspace_id in gone:
            for sql in (
                "DELETE FROM connections WHERE workspace_id = ?",
                "DELETE FROM memberships WHERE workspace_id = ?",
                "DELETE FROM workspaces WHERE id = ?",
            ):
                conn.execute(sql, (workspace_id,))
        conn.execute(
            "UPDATE connections SET revoked_at = COALESCE(revoked_at, ?), token_hash = NULL, "
            "token_hint = '' WHERE user_id = ?",
            (current, user_id),
        )
        conn.execute("UPDATE memberships SET role = 'member' WHERE user_id = ?", (user_id,))
        conn.execute(
            "UPDATE users SET email = ?, name = '', is_admin = 0, "
            "disabled_at = COALESCE(disabled_at, ?) WHERE id = ?",
            (f"deleted-{user_id}@account.invalid", current, user_id),
        )
        conn.execute(
            "UPDATE account_deletions SET state = 'purged', purged_at = ?, blocked = NULL, "
            "workspaces = ? WHERE user_id = ?",
            (current, json.dumps(gone), user_id),
        )
        db.audit(action="account.purge", decision="ok", user_id=user_id, target_ids=gone)
    _announce(gateway, announce)
    # A request that was already running may have recreated an empty directory.
    for workspace_id in gone:
        remove_workspace(gateway, workspace_id)
    return {"state": "purged", "deleted_workspaces": gone}


def remove_workspace(gateway, workspace_id: str) -> None:
    """Stop a workspace's worker and remove its directory and store generations."""
    root = workspace_root(gateway.supervisor.root.parent, workspace_id)
    generation = re.compile(re.escape(root.name) + r"\.generation-[0-9a-f]{24}")
    gateway.supervisor.stop(workspace_id)
    siblings = (
        [p for p in root.parent.glob(root.name + ".generation-*")] if root.parent.is_dir() else []
    )
    for path in [root, *sorted(p for p in siblings if generation.fullmatch(p.name))]:
        with GATE.creating(path):
            if path.is_symlink():
                path.unlink()  # never follow a link out of the data directory
            elif path.is_dir():
                shutil.rmtree(path)


def unfinished(db) -> bool:
    with db._lock:
        return (
            db._conn.execute(
                "SELECT 1 FROM account_deletions WHERE state = 'purging' LIMIT 1"
            ).fetchone()
            is not None
        )


class Sweeper:
    """Runs :func:`purge_due` on a timer. With deletion switched off it only
    finishes purges that had already started."""

    def __init__(self, gateway, interval_s: float = SWEEP_SECONDS):
        self.gateway = gateway
        self.interval_s = interval_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="account-purge", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.interval_s):
            try:
                purge_due(self.gateway, resume_only=not enabled())
            except Exception as exc:
                log_failure(LOG, "Account purge sweep failed", exc)

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5)
