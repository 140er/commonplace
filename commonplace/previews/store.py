"""Preview rows in the control database and revision files in the workspace directory.

``previews`` holds one row per (card, name); ``preview_revisions`` one row per published
revision (immutable: the files of ``r<n>`` are written once and never changed);
``preview_events`` is the append-only record of who did what, and ``preview_rules`` the
workspace's stop rule. Files live under ``<workspace dir>/previews/<id>/r<n>/``.

Nothing here deletes preview files by itself: stopping a preview keeps them, and only an owner's
explicit delete (``service.delete_files``) removes them. Only refused attempts that never
published are pruned (``prune_failed``). All SQL is literal.
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
MAX_ROWS = 200  # previews a workspace keeps; refused attempts that never published do not count
MAX_FAILED = 50  # refused attempts that never published anything are kept this many, newest first
BUILDING_STALE = timedelta(minutes=15)
MAX_NAME = 80

STATES = ("building", "ready", "stopped", "failed")

# code -> (what the caller reads, what they can do next). These are said to whoever made the
# request: a refused publish is told to the Coworker that sent it. A stored preview is shown to
# people on the card with ``PERSON_WORDS`` where the two differ.
REASONS = {
    "validating": ("The files are being checked.", "Wait a moment."),
    "storing": ("The files are being saved.", "Wait a moment."),
    "published": (
        "Published and open to people who can open this card. The address a person opens "
        "works for 30 minutes.",
        "",
    ),
    "stopped_by_person": (
        "Stopped by a person. The files are kept.",
        "Use Open again to serve it.",
    ),
    "files_deleted": (
        "A workspace owner deleted the files of this preview.",
        "Ask the bot to publish it again if it is still wanted.",
    ),
    "stopped_by_coworker": (
        "Stopped by the bot that published it. The files are kept.",
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
        "This workspace already serves 25 previews, the most it allows at once.",
        "Stop a preview that is no longer needed, then publish again.",
    ),
    "workspace_space_full": (
        "This workspace has used its 500 MiB of preview space.",
        "Stopping a preview does not free space. The workspace owner can use Delete files "
        "on its card to free space.",
    ),
    "storage_unavailable": (
        "The files could not be saved on the server.",
        "Try again; ask the workspace owner if it keeps failing.",
    ),
    "publish_interrupted": (
        "Publishing did not finish.",
        "Publish again.",
    ),
    "too_many_previews": (
        "This workspace keeps at most 200 previews, and it holds that many.",
        "The workspace owner can use Delete files on its card to free a place.",
    ),
    "changed_meanwhile": (
        "A person stopped this preview while it was being published, so nothing was changed.",
        "Look at the preview's current state, then publish again if it is still wanted.",
    ),
    "person_stopped": (
        "A person stopped this preview, so it is not published again by a bot.",
        "Ask on the card whether it should be served again; a person can use Open again.",
    ),
    "files_unreadable": (
        "The files of this preview cannot be read on the server right now.",
        "Try again later; if it keeps happening, tell the workspace owner.",
    ),
    "rate_limited": (
        "Too many publishes for this card in the last hour (limit 10).",
        "Wait before publishing again.",
    ),
}

_ASK = "Ask the bot that published it to publish again"
_REPLY = "Reply on this card."
# What a person reads about a stored preview on the card, where it differs from ``REASONS``.
PERSON_WORDS = {
    "validating": (
        "The files are being checked.",
        "This takes a few seconds; the card updates by itself.",
    ),
    "storing": (
        "The files are being saved.",
        "This takes a few seconds; the card updates by itself.",
    ),
    "stopped_by_person": (
        "Stopped by a person. The files are kept.",
        "Use Open again on this card to serve it.",
    ),
    "files_deleted": (
        "A workspace owner deleted the files of this preview.",
        f"{_ASK} if it is still wanted. {_REPLY}",
    ),
    "stopped_by_coworker": (
        "Stopped by the bot that published it. The files are kept.",
        "Use Open again on this card to serve it.",
    ),
    "pr_merged": (
        "Stopped because the card records its pull request as merged. The files are kept.",
        "Use Open again on this card to serve it.",
    ),
    "pr_closed": (
        "Stopped because the card records its pull request as closed. The files are kept.",
        "Use Open again on this card to serve it.",
    ),
    "expired": (
        "Stopped by the workspace's stop rule. The files are kept.",
        "Use Open again on this card to serve it.",
    ),
    "no_index_html": (
        "Nothing was published: the files had no index.html at the top.",
        f"{_ASK} with an index.html at the top of the files. {_REPLY}",
    ),
    "too_large": (
        "Nothing was published: the files were larger than 5 MiB.",
        f"{_ASK} with fewer or smaller files. {_REPLY}",
    ),
    "too_many_files": (
        "Nothing was published: there were more than 500 files.",
        f"{_ASK} with fewer files. {_REPLY}",
    ),
    "bad_path": (
        "Nothing was published: a file name was not allowed.",
        f"{_ASK} with that file renamed (relative names, no hidden or empty parts). {_REPLY}",
    ),
    "contains_secret_like_text": (
        "Nothing was published: a file looked like it holds a credential.",
        f"{_ASK} without the credential in that file. {_REPLY}",
    ),
    "workspace_quota_full": (
        "Nothing was published: the workspace already serves 25 previews, the most it allows.",
        "Stop a preview that is no longer needed, then ask the bot to publish again.",
    ),
    "workspace_space_full": (
        "Nothing was published: the workspace has used its 500 MiB of preview space.",
        "Stopping a preview does not free space. The workspace owner can use Delete files "
        "on its card to free space.",
    ),
    "too_many_previews": (
        "Nothing was published: the workspace keeps at most 200 previews.",
        "The workspace owner can use Delete files on its card to free a place.",
    ),
    "storage_unavailable": (
        "Nothing was published: the server could not save the files.",
        f"{_ASK} later. If it keeps failing, tell the workspace owner.",
    ),
    "publish_interrupted": ("Publishing did not finish.", f"{_ASK}. {_REPLY}"),
}


def person_words(reason) -> tuple:
    return PERSON_WORDS.get(reason) or REASONS[reason]


LIMITS = {
    "max_bundle_bytes": bundle.MAX_BUNDLE_BYTES,
    "max_files": bundle.MAX_FILES,
    "max_ready": MAX_READY,
    "max_workspace_bytes": MAX_WORKSPACE_BYTES,
    "max_previews": MAX_ROWS,
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
                failure_file TEXT NOT NULL DEFAULT '', failure_kind TEXT NOT NULL DEFAULT '',
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
        # A workspace that ran an earlier slice has these columns missing.
        columns = [row[1] for row in db._conn.execute("PRAGMA table_info(previews)")]
        if "failure_file" not in columns:
            db._conn.execute(
                "ALTER TABLE previews ADD COLUMN failure_file TEXT NOT NULL DEFAULT ''"
            )
        if "failure_kind" not in columns:
            db._conn.execute(
                "ALTER TABLE previews ADD COLUMN failure_kind TEXT NOT NULL DEFAULT ''"
            )
        columns = [row[1] for row in db._conn.execute("PRAGMA table_info(preview_revisions)")]
        if "deleted_at" not in columns:
            db._conn.execute("ALTER TABLE preview_revisions ADD COLUMN deleted_at TEXT")


def root(gateway, workspace_id) -> Path:
    return Path(gateway.workspace_dir(workspace_id)) / "previews"


def revision_dir(gateway, workspace_id, preview_id, revision) -> Path:
    return root(gateway, workspace_id) / preview_id / f"r{int(revision)}"


def label(actor: str) -> str:
    """What the append-only record keeps of an actor: a connection's name, or the words "a
    person". The record outlives an account (a purge keeps it), so it never holds a user id."""
    return "a person" if actor.startswith("user:") else actor


def event(conn, workspace_id, preview_id, card, kind, actor, reason="") -> None:
    conn.execute(
        "INSERT INTO preview_events(workspace_id,preview_id,card,kind,actor,reason,at) "
        "VALUES (?,?,?,?,?,?,?)",
        (workspace_id, preview_id, card, kind, label(actor), reason, stamp(clock())),
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


def counts_live(row) -> bool:
    """Whether a preview counts toward ``MAX_ROWS``: not a refused attempt that never published
    and not one whose files a person deleted."""
    if row["state"] == "failed" and row["current_revision"] == 0:
        return False
    return not (row["state"] == "stopped" and row["reason"] == "files_deleted")


def live_rows(conn, workspace_id) -> int:
    return conn.execute(
        "SELECT count(*) FROM previews WHERE workspace_id=? "
        "AND NOT (state='failed' AND current_revision=0) "
        "AND NOT (state='stopped' AND reason='files_deleted')",
        (workspace_id,),
    ).fetchone()[0]


def prune_failed(conn, workspace_id) -> None:
    """Keep the newest ``MAX_FAILED`` refused attempts that never published. They hold no files
    and no revision; the append-only events keep the history."""
    conn.execute(
        "DELETE FROM previews WHERE workspace_id=? AND state='failed' AND current_revision=0 "
        "AND id NOT IN (SELECT id FROM previews WHERE workspace_id=? AND state='failed' "
        "AND current_revision=0 ORDER BY updated_at DESC, id DESC LIMIT ?)",
        (workspace_id, workspace_id, MAX_FAILED),
    )


def attempts_last_hour(conn, workspace_id, card) -> int:
    since = stamp(clock() - timedelta(hours=1))
    return conn.execute(
        "SELECT count(*) FROM preview_events WHERE workspace_id=? AND card=? AND at>=? "
        "AND kind='publish_started'",
        (workspace_id, card, since),
    ).fetchone()[0]


def check_room(conn, workspace_id, size, *, needs_ready_slot) -> None:
    """``Refused`` naming the limit that would be passed: ``workspace_space_full`` when ``size``
    more bytes would pass the space limit (stopping does not free space), else
    ``workspace_quota_full`` when one more ready preview would pass the count. Nothing is ever
    evicted to make room."""
    held = usage(conn, workspace_id)
    if held["bytes"] + size > MAX_WORKSPACE_BYTES:
        raise bundle.Refused("workspace_space_full")
    if needs_ready_slot and held["ready"] >= MAX_READY:
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


def snapshot(conn, row, *, revisions=False) -> tuple:
    """What ``shape`` needs for one row, read in one go: its current revision row and, when
    asked, all its revisions."""
    current = conn.execute(
        "SELECT * FROM preview_revisions WHERE preview_id=? AND revision=?",
        (row["id"], row["current_revision"]),
    ).fetchone()
    return current, (revision_rows(conn, row["id"]) if revisions else None)


def shape(row, current, revisions=None, stops_at=None) -> dict:
    """The public shape of a preview, from rows already read. It names no path, host or
    environment value."""
    state, reason = effective(row)
    text, nxt = person_words(reason)
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
        # Only a revision that was published has a publish time; an attempt has a start time.
        "published_at": current["published_at"] if current else None,
        "started_at": None if current else row["updated_at"],
        "stops_at": stops_at if state == "ready" else None,
        "last_changed_at": row["updated_at"],
        # Which file a refused publish was about (never its content), when the reason names one.
        "failure": (
            {"file": row["failure_file"], "kind": row["failure_kind"]}
            if state == "failed" and row["failure_file"]
            else None
        ),
        "url": f"/p/{row['id']}/"
        if current is not None and current["deleted_at"] is None
        else None,
    }
    if revisions is not None:
        shown["revisions"] = [
            {
                "revision": r["revision"],
                "files_deleted": r["deleted_at"] is not None,
                "size_bytes": r["size_bytes"],
                "file_count": r["file_count"],
                "published_by": r["published_by"],
                "published_at": r["published_at"],
            }
            for r in revisions
        ]
    return shown


def view(conn, row, *, revisions=False) -> dict:
    return shape(row, *snapshot(conn, row, revisions=revisions))


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


def place(tmp: Path, final: Path):
    """Move the written files into ``final`` with renames only, which is cheap enough to do
    while the control-database lock is held. The caller has just taken ``final``'s revision
    number from the database, so a directory already there is left by a publish that died after
    this step: no row names it. It is renamed aside and returned, and the caller removes it
    once the lock is released (nothing recursive ever runs under the lock). Returns the aside
    path or ``None``."""
    aside = None
    try:
        if final.exists():
            aside = final.with_name(".stale-" + secrets.token_hex(8))
            os.replace(final, aside)
        os.replace(tmp, final)
    except OSError:
        if aside is not None:
            try:
                os.replace(aside, final)  # put it back; nothing changed
            except OSError:
                pass
        raise bundle.Refused("storage_unavailable") from None
    return aside
