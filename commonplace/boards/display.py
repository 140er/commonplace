"""Card links and readable names for people, added on the way out.

Cards store actor refs (``user:<id>``, ``conn:<name>``). Replies show a
person by name (or email) and every card carries its short link.
"""

import re
from datetime import datetime, timedelta, timezone

_USER = re.compile(r"\buser:(usr_[0-9a-f]+)\b")

# What a card needs from the person reading it, strongest first. ``done`` is
# terminal and wins over everything; the rest apply in this order.
NEEDS = ("you", "plan", "merge", "checks", "working", "idle", "done")
STALE_MINUTES = 30


def stale_before(at=None) -> str:
    """ISO cutoff: a doing card last seen before this has a stale heartbeat."""
    at = at or datetime.now(timezone.utc)
    return (at - timedelta(minutes=STALE_MINUTES)).strftime("%Y-%m-%dT%H:%M:%SZ")


def need(card, *, me=None, owner=False, now=None) -> str:
    """One of ``NEEDS`` for a public card dict, seen by actor ``me``.

    An ask with no recipient goes to the workspace owner. ``now`` is a datetime
    (default: the server clock).
    """
    status = card["status"]
    if status == "done":
        return "done"
    ask_to = card.get("ask_to")
    if card.get("ask") and (ask_to == me if ask_to else owner):
        return "you"
    if status == "review" or "plan" in (card.get("labels") or ()):
        return "plan"
    if card.get("pr_url") and card.get("checks") == "passing":
        return "merge"
    if card.get("checks") == "pending":
        return "checks"
    if status == "doing" and (card.get("heartbeat_at") or card["updated_at"]) >= stale_before(now):
        return "working"
    return "idle"


def card_url(ctx, number) -> str:
    path = f"/app/#/card/{number}"
    try:
        from commonplace import urls
    except ModuleNotFoundError as exc:
        if exc.name != "commonplace.urls":
            raise
        import os

        base = os.environ.get("COMMONPLACE_APP_URL") or os.environ.get(
            "COMMONPLACE_BASE_URL", "http://127.0.0.1:8765"
        )
        return base.rstrip("/") + path
    return urls.share_link(ctx.db, ctx.principal.workspace_id, path)


def with_urls(ctx, value):
    """Every card dict in ``value`` (nested) gains ``url``."""
    if isinstance(value, list):
        return [with_urls(ctx, v) for v in value]
    if isinstance(value, dict):
        out = {k: with_urls(ctx, v) for k, v in value.items()}
        if isinstance(out.get("number"), int) and "status" in out and "board" in out:
            out.setdefault("url", card_url(ctx, out["number"]))
        return out
    return value


def people(ctx, text: str) -> str:
    """Replace ``user:<id>`` with that member's name or email."""
    if "user:" not in text:
        return text
    names = {}
    for member in ctx.db.members(ctx.principal.workspace_id):
        try:
            name = ctx.db.user(member["id"])["name"]
        except Exception:
            name = ""
        names[member["id"]] = name or member["email"]
    return _USER.sub(lambda m: names.get(m.group(1), m.group(0)), text)
