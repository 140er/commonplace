"""Batched, person-filtered chat list metadata, only for consented phone clients."""

import json
import re
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser

from commonplace import redact
from commonplace.chat import store
from commonplace.console import authority

PREVIEW_CHARS = 240
ACTIVITY_STATES = ("unused", "working", "idle", "quiet")


class _Plain(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_entityref(self, name):
        self.parts.append(f"&{name};")

    def handle_charref(self, name):
        self.parts.append(f"&#{name};")


def preview(text):
    # Normalize markup and entities before the feed's redactor can match secrets.
    # Truncate only the redacted text, keeping a marker whole or omitting it.
    plain = _Plain()
    plain.feed(text)
    plain.close()
    clean = redact.redact(unescape("".join(plain.parts)))[0]
    stop = PREVIEW_CHARS
    for marker in re.finditer(r"\[(?:redacted|omitted):[a-z_]+\]", clean):
        if marker.start() < stop < marker.end():
            stop = marker.start()
            break
    return clean[:stop]


def _instant(at):
    parsed = datetime.fromisoformat(at.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp has no timezone")
    return parsed.astimezone(timezone.utc)


def _message(text, at, kind):
    try:
        stamp = _instant(at).isoformat().replace("+00:00", "Z")
    except (ValueError, TypeError):
        return None
    return {"text": preview(text), "at": stamp, "author_kind": kind}


def summaries(gateway, principal, service, boards):
    """One batched projection; query count is constant as project count grows.

    Readable boards are selected by BoardService before any message or activity
    lookup. Private front replies filter by this person's id before ranking.
    Private bot threads are never inputs to a project-chat preview.
    """
    slugs = [b["slug"] for b in boards]
    result = {slug: {"status": None, "last_message": None} for slug in slugs}
    if not slugs:
        return result
    agents = {"conn:" + a.name: a for a in authority.agents(gateway, principal)}
    now = datetime.now(timezone.utc)
    for board in boards:
        view = agents.get(board["coordinator"])
        if view is not None:
            try:
                result[board["slug"]]["status"] = authority.status(view.last_used_at, now)
            except ValueError:
                pass  # No honest activity classification for a malformed recorded timestamp.
    with service.store.connection() as conn:
        rows = conn.execute(
            "WITH ranked AS (SELECT b.slug,m.text,m.created_at AS at,m.stamp_connection, "
            "row_number() OVER (PARTITION BY b.id ORDER BY m.id DESC) AS n "
            "FROM space_messages m JOIN boards b ON b.id=m.board_id "
            "WHERE b.slug IN (SELECT value FROM json_each(?))) "
            "SELECT * FROM ranked WHERE n=1",
            (json.dumps(slugs),),
        ).fetchall()
    for row in rows:
        result[row["slug"]]["last_message"] = _message(
            row["text"], row["at"], "bot" if row["stamp_connection"] is not None else "person"
        )
    replies = store.open_replies(gateway, principal.workspace_id).latest_for_boards(
        slugs, principal.user_id
    )
    for row in replies:
        previous = result[row["board"]]["last_message"]
        candidate = _message(row["text"], row["created_at"], "bot")
        if candidate is not None and (
            previous is None or _instant(candidate["at"]) >= _instant(previous["at"])
        ):
            result[row["board"]]["last_message"] = candidate
    return result
