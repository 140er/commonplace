"""Blocks are stored as references and hydrated on read with the reader's own permissions."""

from commonplace import redact
from commonplace.boards import why
from commonplace.boards.service import BoardService
from commonplace.extensions import ToolContext, ToolError


class Reader:
    """The person a thread is read for; every lookup runs as them."""

    def __init__(self, gateway, principal):
        self.gateway, self.principal = gateway, principal
        self._boards = None

    @property
    def boards(self) -> BoardService:
        if self._boards is None:
            self._boards = BoardService(ToolContext(principal=self.principal, gateway=self.gateway))
        return self._boards


def _clean(value):
    return redact.redact(value)[0] if isinstance(value, str) else value


def _card(reader, number):
    """The card as this person sees it, or None when it is gone or hidden from them."""
    if type(number) is not int:
        return None
    try:
        return reader.boards.task_show(number)
    except ToolError:
        return None


def work_status(card) -> tuple[str, str]:
    """The only status words the UI knows, and the plain reason when it is blocked."""
    reason = card.get("waiting")
    note = why.text(reason) if reason else ""
    if card["status"] == "done":
        return "done", ""
    if card["status"] == "review":
        if card.get("review_kind") != "decision":
            return "ready", ""
        if card.get("need") == "you":
            return "needs_you", ""
        return "blocked", "Waiting for an answer"
    if reason:  # open blockers and every other waiting reason; only present when the card has them
        return "blocked", note
    return ("working" if card["status"] == "doing" else "queued"), note


def work(reader, stored) -> dict:
    card = _card(reader, stored.get("card"))
    if card is None:
        return {**stored, "gone": True}
    status, note = work_status(card)
    links = [{"label": "Pull request", "url": card["pr_url"]}] if card.get("pr_url") else []
    return {
        **stored,
        "title": _clean(card["title"]),
        "status": status,
        "note": _clean(note or card.get("status_line") or ""),
        "links": links,
        "version": card["version"],
    }


def decision(reader, stored) -> dict:
    card = _card(reader, stored.get("card"))
    if card is None:
        return {**stored, "gone": True}
    answers = [e for e in card["events"] if e["kind"] == "decide"]
    answer = answers[-1] if answers else None
    return {
        **stored,
        "question": _clean(card["ask"] or card["title"]),
        "context": _clean(card.get("review_context") or ""),
        "options": [_clean(option) for option in card["ask_options"]],
        "version": card["version"],
        "open": card["status"] == "review" and card.get("review_kind") == "decision",
        "answer": _clean(answer["choice"] or answer["text"]) if answer else None,
    }


def action(reader, stored) -> dict:
    return {**stored, "label": _clean(stored.get("label", ""))}


def setup(reader, stored) -> dict:
    return {**stored, "can_configure": reader.principal.role == "owner"}


def confirm(reader, stored) -> dict:
    """The server-built label and the state; an open one past its expiry reads ``expired``."""
    from commonplace.bots import store

    pending = store.open_bots(reader.gateway, reader.principal.workspace_id).pending_for(
        reader.principal.user_id, stored.get("pending")
    )
    if pending is None:
        return {**stored, "gone": True}
    state = pending["state"]
    if state == "open" and pending["expires_at"] <= store.stamp():
        state = "expired"
    return {**stored, "label": _clean(pending["label"]), "state": state}


def _can_read(reader):
    """``can_read(slug)`` for the reader, from their projects, listed once on first use."""
    slugs = None

    def can_read(slug):
        nonlocal slugs
        if slugs is None:
            slugs = {board["slug"] for board in reader.boards.board_list()}
        return slug in slugs

    return can_read


def learned(reader, stored) -> dict:
    """The learned items a reply used, or a turn just learned, that this person still has;
    forgotten ones are omitted. ``how`` (used or learned) is only what the stored block said."""
    from commonplace.bots import learned as store

    ids = stored.get("ids")
    items = store.open_learned(reader.gateway, reader.principal.workspace_id).visible(
        reader.principal.user_id, ids if isinstance(ids, list) else [], _can_read(reader)
    )
    if not items:
        return {"type": "learned", "gone": True}
    return {
        "type": "learned",
        "how": "learned" if stored.get("how") == "learned" else "used",
        "items": [{**item, "at": item["said"], "text": _clean(item["text"])} for item in items],
    }


def learn_proposal(reader, stored) -> dict:
    """One item the bot proposes to keep (ask mode), as this person sees it now: ``open`` until
    they answer, ``accepted`` once it is active. A rejected or forgotten one is ``gone``."""
    from commonplace.bots import learned as store

    items = store.open_learned(reader.gateway, reader.principal.workspace_id)
    try:
        item = items.get(reader.principal.user_id, stored.get("id"))
    except store.Missing:
        return {"type": "learn_proposal", "gone": True}
    if (
        item["kind"] == "note"
        or item["scope"] != "person"
        or (item["board"] and not _can_read(reader)(item["board"]))
    ):
        return {"type": "learn_proposal", "gone": True}
    return {
        "type": "learn_proposal",
        "id": item["id"],
        "state": "open" if item["state"] == "proposed" else "accepted",
        "item": {
            "id": item["id"],
            "text": _clean(item["text"]),
            "scope": item["scope"],
            "kind": item["kind"],
            "at": item["source_at"][:10],
            "edited": item["text_by"] == "person_edit",
        },
    }


HYDRATORS = {
    "work": work,
    "decision": decision,
    "action": action,
    "setup": setup,
    "confirm": confirm,
    "learned": learned,
    "learn_proposal": learn_proposal,
}


def hydrate(reader, blocks) -> list[dict]:
    """Each stored block through its hydrator; an unknown type comes back as stored."""
    return [
        HYDRATORS.get(block.get("type"), lambda _reader, stored: stored)(reader, block)
        for block in blocks
    ]


def message_view(reader, message) -> dict:
    """A stored message as the API shows it: redacted text, hydrated blocks."""
    return {
        "id": message["id"],
        "role": message["role"],
        "text": redact.redact(message["text"])[0],
        "blocks": hydrate(reader, message["blocks"]),
        "at": message["at"],
        "state": message["state"],
        "reply_to": message["reply_to"],
    }
