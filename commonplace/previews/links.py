"""Short-lived preview links: how a sandboxed preview page loads its own files.

A preview page runs with an opaque origin (see ``serving``), so the requests it makes for its
own stylesheets, scripts and images carry no session cookie. When a signed-in person opens
``/p/<id>/...``, the server therefore sends them on to ``/p/<id>/~<token>/...``: the page's
relative links then name the token, and the token stands in for the session for that one
preview only.

A token is random, stored only as a digest, bound to one preview, one person and one workspace,
and lasts ``TTL``. Every request with it checks again that the person is still a member who can
open the card; a token that fails any check is treated as unknown. Expired tokens are removed
when the next one is made (they are credentials, not user data).
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from commonplace import sessions
from commonplace.control_db import ControlError

from . import store

TTL = timedelta(hours=8)
MARK = "~"


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def ensure_schema(db) -> None:
    with db._lock:
        db._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS preview_links (
                token_hash TEXT PRIMARY KEY, preview_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL, user_id TEXT NOT NULL, expires_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS preview_links_by_expiry ON preview_links(expires_at);
            """
        )


def mint(gateway, principal, preview_id) -> str:
    """A new token for a person who has just been allowed to open ``preview_id``."""
    token = secrets.token_urlsafe(24)
    now = store.clock()
    db = gateway.db
    with db._lock, db._tx() as conn:
        conn.execute("DELETE FROM preview_links WHERE expires_at<?", (store.stamp(now),))
        conn.execute(
            "INSERT INTO preview_links(token_hash,preview_id,workspace_id,user_id,expires_at) "
            "VALUES (?,?,?,?,?)",
            (
                _digest(token),
                preview_id,
                principal.workspace_id,
                principal.user_id,
                store.stamp(now + TTL),
            ),
        )
    return token


def principal(gateway, preview_id, token):
    """The person a token stands for, re-checked now, or ``None`` for any failed check."""
    if not isinstance(token, str) or not 20 <= len(token) <= 64:
        return None
    db = gateway.db
    with db._lock:
        row = db._conn.execute(
            "SELECT * FROM preview_links WHERE token_hash=?", (_digest(token),)
        ).fetchone()
    if row is None or row["preview_id"] != preview_id:
        return None
    if row["expires_at"] <= store.stamp(store.clock()):
        return None
    try:
        return sessions.person_principal(db, row["user_id"], row["workspace_id"])
    except ControlError:
        return None
