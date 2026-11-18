"""Canonical public URLs and workspace-scoped, persistent short links."""

from __future__ import annotations

import os
import re
import secrets
import sqlite3
import string
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit

APP_URL = (
    os.environ.get("COMMONPLACE_APP_URL")
    or os.environ.get("COMMONPLACE_BASE_URL")
    or "http://127.0.0.1:8765"
).rstrip("/")
AGENT_URL = (os.environ.get("COMMONPLACE_AGENT_URL") or APP_URL).rstrip("/")
SITE_URL = (os.environ.get("COMMONPLACE_SITE_URL") or APP_URL).rstrip("/")

_BASE62 = string.ascii_letters + string.digits
_CODE = re.compile(r"[A-Za-z0-9]{7}")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS short_links (
    code TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(workspace_id, path)
);
"""


def _path(path: str) -> str:
    if not isinstance(path, str) or len(path) > 16384:
        raise ValueError("invalid app path")
    decoded = unquote(path)
    if (
        not path.startswith("/")
        or decoded.startswith("//")
        or "\\" in decoded
        or any(ord(c) < 32 or ord(c) == 127 for c in decoded)
        or urlsplit(path).netloc
    ):
        raise ValueError("invalid app path")
    return path


def app_link(path: str) -> str:
    return APP_URL + _path(path)


def mcp_url() -> str:
    return AGENT_URL + "/mcp"


def ensure_schema(db) -> None:
    with db._lock:
        if not getattr(db, "_short_links_schema", False):
            db._conn.executescript(_SCHEMA)
            db._short_links_schema = True


def share_link(db, workspace_id: str, path: str) -> str:
    return share_links(db, workspace_id, [path])[path]


def share_links(db, workspace_id: str, paths) -> dict[str, str]:
    """Create a page's stable URLs in one transaction instead of one per card."""
    paths = list(dict.fromkeys(_path(path) for path in paths))
    if not paths:
        return {}
    ensure_schema(db)
    codes = {}
    with db._lock, db._tx() as conn:
        for start in range(0, len(paths), 400):
            batch = paths[start : start + 400]
            placeholders = ",".join("?" for _ in batch)
            codes.update(
                (row["path"], row["code"])
                for row in conn.execute(
                    f"SELECT path,code FROM short_links WHERE workspace_id=? AND path IN ({placeholders})",
                    [workspace_id, *batch],
                )
            )
        for path in paths:
            if path in codes:
                continue
            while True:
                code = "".join(secrets.choice(_BASE62) for _ in range(7))
                try:
                    conn.execute(
                        "INSERT INTO short_links(code, workspace_id, path, created_at) VALUES (?,?,?,?)",
                        (code, workspace_id, path, datetime.now(timezone.utc).isoformat()),
                    )
                except sqlite3.IntegrityError:
                    # A collision is retryable; invalid workspace ids still fail.
                    if conn.execute("SELECT 1 FROM short_links WHERE code=?", (code,)).fetchone():
                        continue
                    raise
                codes[path] = code
                break
    return {path: AGENT_URL + "/s/" + codes[path] for path in paths}


def resolve_link(db, code: str) -> str | None:
    if not _CODE.fullmatch(code):
        return None
    ensure_schema(db)
    with db._lock:
        row = db._conn.execute("SELECT path FROM short_links WHERE code = ?", (code,)).fetchone()
    return app_link(row["path"]) if row else None
