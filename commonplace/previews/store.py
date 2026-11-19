"""Preview rows in the control database and revision files in the workspace directory.

``previews`` holds one row per (card, name); ``preview_revisions`` one row per published
revision (immutable: the files of ``r<n>`` are written once and never changed);
``preview_events`` is the append-only record of who did what, and ``preview_rules`` the
workspace's stop rule. Files live under ``<workspace dir>/previews/<id>/r<n>/``.

Nothing here deletes by itself: stopping a preview keeps its files, and the only code that
removes files is ``delete_files``, which a person calls explicitly. All SQL is literal.
"""

from __future__ import annotations

import os
import secrets
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import bundle

MAX_READY = 25
MAX_WORKSPACE_BYTES = 500 * 1024 * 1024
MAX_PER_CARD_HOUR = 10
MAX_ROWS = 200  # a safety bound on rows (failed ones included), not a number users plan for
BUILDING_STALE = timedelta(minutes=15)
MAX_NAME = 80

STATES = ("building", "ready", "stopped", "failed")

# code -> (what the person reads, what they can do next)
REASONS = {
    "validating": ("The files are being checked.", "Wait a moment."),
    "storing": ("The files are being saved.", "Wait a moment."),
    "published": ("Published and open to people who can open this card.", ""),
    "stopped_by_person": (
        "Stopped by a person. The files are kept.",
        "Use Open again to serve it.",
    ),
    "stopped_by_coworker": (
        "Stopped by the Coworker that published it. The files are kept.",
        "Use Open again to serve it.",
    ),
    "pr_merged": (
        "Stopped because its pull request was merged. The files are kept.",
        "Use Open again to serve it.",
    ),
    "pr_closed": (
        "Stopped because its pull request was closed. The files are kept.",
        "Use Open again to serve it.",
    ),
    "expired": (
        "Stopped by the workspace's stop rule. The files are kept.",
        "Use Open again to serve it.",
    ),
    "files_deleted": (
        "The files were deleted by a person.",
        "Ask the Coworker to publish it again.",
    ),
    "no_index_html": (
        "The bundle has no index.html at its top level.",
        "Publish again with an index.html.",
    ),
    "too_large": (
        "The bundle is larger than 5 MiB.",
        "Publish fewer or smaller files.",
    ),
    "too_many_files": ("The bundle has more than 500 files.", "Publish fewer files."),
    "bad_path": (
        "A file path is not allowed: use relative paths with forward slashes, at most 10 folders "
        "deep and 255 bytes long, with no empty, dot or hidden parts.",
        "Rename the file and publish again.",
    ),
    "contains_secret_like_text": (
        "A file contains text shaped like a credential, so nothing was published.",
        "Remove the credential from that file and publish again.",
    ),
    "workspace_quota_full": (
        "This workspace is at its preview limit (25 open previews or 500 MiB).",
        "Stop or delete an older preview, then publish again.",
    ),
    "storage_unavailable": (
        "The files could not be saved on the server.",
        "Try again; ask the workspace owner if it keeps failing.",
    ),
    "publish_interrupted": (
        "Publishing did not finish.",
        "Publish again.",
    ),
    "rate_limited": (
        "Too many publishes for this card in the last hour (limit 10).",
        "Wait before publishing again.",
    ),
}

LIMITS = {
    "max_bundle_bytes": bundle.MAX_BUNDLE_BYTES,
    "max_files": bundle.MAX_FILES,
    "max_ready": MAX_READY,
    "max_workspace_bytes": MAX_WORKSPACE_BYTES,
    "max_publishes_per_card_hour": MAX_PER_CARD_HOUR,
}


class NotFound(LookupError):
    """No such preview, card or workspace for this caller; one answer for every cause."""


class Forbidden(PermissionError):
    """The caller can see the preview but may not do this to it."""


def clock() -> datetime:
    """The server clock; tests replace this one function."""
    return datetime.now(timezone.utc)


def stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_stamp(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def ensure_schema(db) -> None:
    with db._lock:
        db._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS previews (
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, card INTEGER NOT NULL,
                name TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'static' CHECK(kind IN ('static','app')),
                placement TEXT NOT NULL DEFAULT 'hub' CHECK(placement IN ('hub','workspace')),
                state TEXT NOT NULL CHECK(state IN ('building','ready','stopped','failed')),
                reason TEXT NOT NULL, current_revision INTEGER NOT NULL DEFAULT 0,
                pr_url TEXT NOT NULL DEFAULT '', pr_head TEXT NOT NULL DEFAULT '',
                actor TEXT NOT NULL, published_by TEXT NOT NULL, by_person INTEGER NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE (workspace_id, card, name)
            );
            CREATE INDEX IF NOT EXISTS previews_by_workspace ON previews(workspace_id, state);
            CREATE TABLE IF NOT EXISTS preview_revisions (
                preview_id TEXT NOT NULL REFERENCES previews(id), revision INTEGER NOT NULL,
                digest TEXT NOT NULL, size_bytes INTEGER NOT NULL, file_count INTEGER NOT NULL,
                published_by TEXT NOT NULL, by_person INTEGER NOT NULL,
                published_at TEXT NOT NULL, deleted_at TEXT,
                PRIMARY KEY (preview_id, revision)
            );
            CREATE TABLE IF NOT EXISTS preview_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, workspace_id TEXT NOT NULL,
                preview_id TEXT NOT NULL, card INTEGER NOT NULL, kind TEXT NOT NULL,
                actor TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS preview_events_by_card
                ON preview_events(workspace_id, card, at);
            CREATE TRIGGER IF NOT EXISTS preview_events_no_update BEFORE UPDATE ON preview_events
            BEGIN SELECT RAISE(ABORT, 'preview events are append-only'); END;
            CREATE TRIGGER IF NOT EXISTS preview_events_no_delete BEFORE DELETE ON preview_events
            BEGIN SELECT RAISE(ABORT, 'preview events are append-only'); END;
            CREATE TABLE IF NOT EXISTS preview_rules (
                workspace_id TEXT PRIMARY KEY, stop_days_after_pr INTEGER,
                max_age_days INTEGER, updated_by TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            """
        )


def root(gateway, workspace_id) -> Path:
    return Path(gateway.workspace_dir(workspace_id)) / "previews"


def revision_dir(gateway, workspace_id, preview_id, revision) -> Path:
    return root(gateway, workspace_id) / preview_id / f"r{int(revision)}"


def event(conn, workspace_id, preview_id, card, kind, actor, reason="") -> None:
    conn.execute(
        "INSERT INTO preview_events(workspace_id,preview_id,card,kind,actor,reason,at) "
        "VALUES (?,?,?,?,?,?,?)",
        (workspace_id, preview_id, card, kind, actor, reason, stamp(clock())),
    )


def usage(conn, workspace_id) -> dict:
    ready = conn.execute(
        "SELECT count(*) FROM previews WHERE workspace_id=? AND state='ready'", (workspace_id,)
    ).fetchone()[0]
    size = conn.execute(
        "SELECT coalesce(sum(r.size_bytes),0) FROM preview_revisions r "
        "JOIN previews p ON p.id=r.preview_id WHERE p.workspace_id=? AND r.deleted_at IS NULL",
        (workspace_id,),
    ).fetchone()[0]
    return {"ready": ready, "bytes": size}


def row_count(conn, workspace_id) -> int:
    return conn.execute(
        "SELECT count(*) FROM previews WHERE workspace_id=?", (workspace_id,)
    ).fetchone()[0]


def attempts_last_hour(conn, workspace_id, card) -> int:
    since = stamp(clock() - timedelta(hours=1))
    return conn.execute(
        "SELECT count(*) FROM preview_events WHERE workspace_id=? AND card=? AND at>=? "
        "AND kind IN ('published','publish_failed')",
        (workspace_id, card, since),
    ).fetchone()[0]


def check_room(conn, workspace_id, size, *, needs_ready_slot) -> None:
    """``Refused('workspace_quota_full')`` when one more ready preview or ``size`` more bytes
    would pass the workspace's limits. Nothing is ever evicted to make room."""
    held = usage(conn, workspace_id)
    if (needs_ready_slot and held["ready"] >= MAX_READY) or (
        held["bytes"] + size > MAX_WORKSPACE_BYTES
    ):
        raise bundle.Refused("workspace_quota_full")


def row_by_name(conn, workspace_id, card, name):
    return conn.execute(
        "SELECT * FROM previews WHERE workspace_id=? AND card=? AND name=?",
        (workspace_id, card, name),
    ).fetchone()


def row_by_id(conn, workspace_id, preview_id):
    return conn.execute(
        "SELECT * FROM previews WHERE id=? AND workspace_id=?", (str(preview_id), workspace_id)
    ).fetchone()


def new_id() -> str:
    return "pv_" + secrets.token_hex(8)


def effective(row) -> tuple[str, str]:
    """(state, reason) as a person should read them now: a publish that has been building for
    longer than ``BUILDING_STALE`` did not finish, so it reads as failed, never as stuck."""
    if row["state"] == "building" and clock() - parse_stamp(row["updated_at"]) > BUILDING_STALE:
        return "failed", "publish_interrupted"
    return row["state"], row["reason"]


def revision_rows(conn, preview_id) -> list:
    return conn.execute(
        "SELECT * FROM preview_revisions WHERE preview_id=? ORDER BY revision DESC",
        (preview_id,),
    ).fetchall()


def view(conn, row, *, revisions=False) -> dict:
    """The public shape of a preview. It names no path, host or environment value."""
    state, reason = effective(row)
    text, nxt = REASONS[reason]
    current = conn.execute(
        "SELECT * FROM preview_revisions WHERE preview_id=? AND revision=?",
        (row["id"], row["current_revision"]),
    ).fetchone()
    shown = {
        "id": row["id"],
        "card": row["card"],
        "name": row["name"],
        "kind": row["kind"],
        "placement": row["placement"],
        "revision": row["current_revision"],
        "state": state,
        "reason": reason,
        "reason_text": text,
        "next": nxt,
        "availability": "reachable" if state == "ready" else "not_served",
        "size_bytes": current["size_bytes"] if current else 0,
        "file_count": current["file_count"] if current else 0,
        "pr": {"url": row["pr_url"], "head": row["pr_head"]} if row["pr_url"] else None,
        "published_by": current["published_by"] if current else row["published_by"],
        "by": "person" if (current["by_person"] if current else row["by_person"]) else "coworker",
        "published_at": current["published_at"] if current else row["created_at"],
        "stops_at": None,
        "last_changed_at": row["updated_at"],
        "url": f"/p/{row['id']}/"
        if current is not None and current["deleted_at"] is None
        else None,
    }
    if revisions:
        shown["revisions"] = [
            {
                "revision": r["revision"],
                "size_bytes": r["size_bytes"],
                "file_count": r["file_count"],
                "published_by": r["published_by"],
                "published_at": r["published_at"],
                "files_deleted": r["deleted_at"] is not None,
            }
            for r in revision_rows(conn, row["id"])
        ]
    return shown


def write_files(gateway, workspace_id, preview_id, items) -> Path:
    """Write ``items`` to a fresh private directory beside the revisions; the caller renames
    it into place inside the database transaction. ``Refused('storage_unavailable')`` and
    nothing left behind when the disk refuses."""
    base = root(gateway, workspace_id) / preview_id
    tmp = base / (".tmp-" + secrets.token_hex(8))
    try:
        for path, data in items:
            target = tmp / path
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "xb") as handle:
                handle.write(data)
    except OSError:
        shutil.rmtree(tmp, ignore_errors=True)
        raise bundle.Refused("storage_unavailable") from None
    return tmp


def place(tmp: Path, final: Path) -> None:
    try:
        os.replace(tmp, final)
    except OSError:
        shutil.rmtree(tmp, ignore_errors=True)
        raise bundle.Refused("storage_unavailable") from None
