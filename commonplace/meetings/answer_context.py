"""What an in-meeting answer may read: this meeting, and at most one pinned project.

The asker in the meeting is not a person of the workspace; the answer runs with the *inviter's*
read right, narrowed to the meeting's own content and the one project the inviter pinned, at the
disclosure the inviter chose (``none`` by default: nothing of the project; ``titles``: names,
statuses, counts and whether a decision is open; ``full``: bodies and comments too). The inviter's
right is resolved fresh for every answer through the same board service the web app uses, so a
project they can no longer read is refused, never read. There is no personal memory, no other
project, no machine and no write in this module: it builds quoted data and nothing else.
"""

from __future__ import annotations

import logging
from collections import Counter

from commonplace import redact, sessions
from commonplace.boards.service import BoardService
from commonplace.bots import blocks
from commonplace.extensions import ToolContext

logger = logging.getLogger("commonplace.meetings")

TRANSCRIPT_SEGMENTS = 120
TRANSCRIPT_CHARS = 12_000
SEGMENT_CHARS = 600
NOTES_SHOWN = 20
NOTE_CHARS = 500
WORK_SHOWN = 30
TITLE_CHARS = 120
FULL_CARDS = 8
BODY_CHARS = 800
COMMENTS_PER_CARD = 3
COMMENT_CHARS = 300
STATUSES = ("queued", "working", "needs_you", "ready", "blocked", "done")


def _clip(text, limit) -> str:
    text = " ".join(redact.redact(str(text or ""))[0].split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def meeting_data(meetings, row) -> dict:
    """The meeting so far: the newest transcript segments (speakers as numbers, never names) and
    its draft notes. Everyone present already heard this."""
    aliases, segments, used = {}, [], 0
    for segment in reversed(meetings.recent_segments(row["id"], TRANSCRIPT_SEGMENTS)):
        text = _clip(segment["text"], SEGMENT_CHARS)
        if used + len(text) > TRANSCRIPT_CHARS:
            break
        used += len(text)
        segments.append((segment, text))
    for segment, _text in reversed(segments):
        ext = segment["ext_participant"]
        if ext and ext not in aliases:
            aliases[ext] = f"speaker {len(aliases) + 1}"
    return {
        "title": _clip(row["title"], TITLE_CHARS),
        "transcript": [
            {
                "speaker": aliases.get(s["ext_participant"]),
                "start_s": s["t_start_ms"] // 1000,
                "text": t,
            }
            for s, t in reversed(segments)
        ],
        "notes": [
            {"kind": n["kind"], "text": _clip(n["text"], NOTE_CHARS)}
            for n in meetings.open_notes(row["id"])[:NOTES_SHOWN]
        ],
    }


def pinned_project(gateway, workspace_id, user_id, board, level):
    """``(data, ok)``. ``ok`` is False when a project is pinned and the inviter cannot read it now
    (the caller refuses; nothing was read). ``data`` is None when nothing of the project is to be
    used: no pin, or disclosure ``none``."""
    if not board:
        return None, True
    try:
        principal = sessions.person_principal(gateway.db, user_id, workspace_id)
        if principal is None:
            return None, False
        service = BoardService(ToolContext(principal=principal, gateway=gateway))
        entry = next((b for b in service.board_list() if b["slug"] == board), None)
        if entry is None:
            return None, False
        if level not in ("titles", "full"):
            return None, True
        return _project(service, entry, board, full=level == "full"), True
    except Exception as exc:  # fail closed: a project that cannot be checked is not read
        logger.warning("meeting pinned project failed type=%s", type(exc).__name__)
        return None, False


def _project(service, entry, board, *, full) -> dict:
    cards = service.task_list(board=board, all_statuses=True, top_level=True)
    counts, work = Counter({status: 0 for status in STATUSES}), []
    for card in cards:
        status = blocks.work_status(card)[0]
        counts[status] += 1
        if len(work) < WORK_SHOWN and status != "done":
            work.append(
                {
                    "number": card["number"],
                    "title": _clip(card["title"], TITLE_CHARS),
                    "status": status,
                }
            )
    try:
        asks = service.needs_me()["asks"]
    except Exception:  # a member without the tool has none waiting
        asks = []
    data = {
        "name": _clip(entry["name"], TITLE_CHARS),
        "counts": dict(counts),
        "open_decisions": sum(1 for ask in asks if ask.get("board") == board),
        "work": [{k: v for k, v in item.items() if k != "number"} for item in work],
    }
    if full:
        for item in work[:FULL_CARDS]:
            shown = service.task_show(item["number"])
            comments = [
                e["text"]
                for e in shown.get("events", ())
                if e.get("kind") == "comment" and e.get("text")
            ]
            data["work"][work.index(item)].update(
                body=_clip(shown.get("body"), BODY_CHARS),
                comments=[_clip(c, COMMENT_CHARS) for c in comments[-COMMENTS_PER_CARD:]],
            )
    return data
