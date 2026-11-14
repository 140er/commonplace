"""The delivery outbox: every post the bridge makes has a row, and a row is posted at most once.

A reply is a reference to the stored bot message (or a fixed sentence), never a copy of text.
One worker path posts it: a row goes ``queued -> sending`` by a compare-and-set before the
adapter is called and ``sent`` after, so a crash in between leaves ``sending``. The sweep turns
that into ``unknown`` and never retries blindly: an adapter that can find its own post by key
settles it, otherwise one fixed "may not have arrived" line goes out, once. A definite platform
failure (the adapter returns ``retry``: rate limit, server error) retries with backoff, bounded by ``MAX_ATTEMPTS``, then the
row is ``failed``; the stored reply is untouched and still shows in the web thread.
"""

import logging
import threading
from datetime import datetime, timedelta, timezone

from commonplace.bots import store as bots_store, turn as bots_turn
from commonplace.chat_bridge import adapter, confirm, links, service, store
from commonplace.http_util import log_failure

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
BACKOFF_S = (5, 15, 60, 300)  # after the n-th failed attempt; a platform's retry hint wins
STUCK_S = 60  # a row still ``sending`` this long was cut off by a crash
NOTICE_PART = -1  # the one "may not have arrived" line of an event

# One fixed sentence per code; the platform and the model never author these.
FIXED = {
    "unlinked": "I don't know you here yet. Open the Commonplace web app, go to the chat bridge "
    "page, get a link code and send it to me in a direct message.",
    "channel_denied": "I can't answer that in this channel. Message me directly instead.",
    "busy": "I'm still working on your last message. Ask again in a moment.",
    "cap": "This chat has used up its daily limit for me. Try again tomorrow.",
    "unsure": "My previous reply may not have arrived. Ask again if you did not see it.",
    "empty": "There is nothing to add.",
    "interrupted": bots_turn.SENTENCES["interrupted"],
    "too_long": bots_turn.SENTENCES["too_long"],
    "web_confirm": "That needs your confirmation in the Commonplace web app. It is waiting in "
    "your thread there.",
    "tap_denied": "That confirmation is not available to you.",
    "tap_off": "Confirming from chat is switched off for your account. Confirm it in the "
    "Commonplace web app, or switch it on there.",
    "tap_web_only": "This kind of action has to be confirmed in the Commonplace web app.",
    "tap_expired": "That confirmation has expired. Ask again.",
    "tap_done": "That was already handled.",
    "tap_failed": "That did not work. Try again in the Commonplace web app.",
    **{f"link_{code}": text for code, text in links.REDEEM_TEXT.items()},
}


_LOCKS: dict = {}  # one delivery at a time per install, so the parts of a reply keep their order


def _now(now):
    return now or datetime.now(timezone.utc)


def split_text(text, limit) -> list:
    """``text`` in parts of at most ``limit`` characters, cut at a line or word where it can."""
    text = text.strip() or FIXED["empty"]
    parts = []
    while len(text) > limit:
        cut = max(text.rfind("\n", 0, limit), text.rfind(" ", 0, limit))
        cut = cut if cut > limit // 2 else limit
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    return [*parts, text] if text else parts


def _platform(bridge, install_id):
    install = bridge.get(install_id)
    return install, adapter.get(install["platform"])


def enqueue_text(gateway, workspace_id, install_id, ext_event, code, part=0) -> None:
    """Queue one fixed sentence as the reply of an event."""
    bridge = store.open_bridge(gateway, workspace_id)
    state = "queued" if bridge.get(install_id)["enabled"] else "cancelled"
    bridge.enqueue(install_id, ext_event, part, "thread", f"text:{code}", state)


def enqueue_reply(gateway, workspace_id, install_id, ext_event, reply) -> None:
    """Queue the stored bot message ``reply`` as the (possibly split) reply of an event. A
    disabled install stores the reply and posts nothing."""
    bridge = store.open_bridge(gateway, workspace_id)
    install, platform = _platform(bridge, install_id)
    limit = platform.caps.max_text if platform is not None else 4000
    state = "queued" if install["enabled"] else "cancelled"
    for part, _text in enumerate(split_text(reply["text"], limit)):
        bridge.enqueue(install_id, ext_event, part, "thread", f"reply:{reply['id']}", state)


def _text_of(gateway, workspace_id, platform, row, event) -> str | None:
    kind, _, value = row["ref"].partition(":")
    if kind == "text":
        return FIXED.get(value)
    if kind == "answer":  # a fixed sentence for the tapper: ``<code>:<platform user>``
        return FIXED.get(value.partition(":")[0])
    if kind == "note":  # the stored outcome of a confirmed action: ``<id>:<platform user>``
        value = value.partition(":")[0]
    if kind not in ("reply", "note") or not event or not value.isdigit():
        return None
    bots = bots_store.open_bots(gateway, workspace_id)
    role = "bot" if kind == "reply" else "event"
    for message in bots.messages(event["bot_id"], event["user_id"], after=int(value) - 1, limit=1):
        if message["id"] == int(value) and message["role"] == role:
            parts = split_text(message["text"], platform.caps.max_text)
            return parts[row["part"]] if 0 <= row["part"] < len(parts) else None
    return None


def _content(gateway, workspace_id, platform, bridge, row, event):
    """``(Outbound, platform user, channel)`` of a row; the user and channel are set for a private
    post only. None when there is nothing (left) to post."""
    kind, _, value = row["ref"].partition(":")
    if row["target"] != "private":
        text = _text_of(gateway, workspace_id, platform, row, event)
        return None if text is None else (adapter.Outbound(text), None, None)
    if kind == "prompt":
        prompt = bridge.prompt(row["install_id"], value)
        out = confirm.chip(gateway, workspace_id, event["user_id"], value) if prompt else None
        return None if out is None else (out, prompt["ext_user"], prompt["ext_channel"])
    text = _text_of(gateway, workspace_id, platform, row, event)
    ext_user = value.partition(":")[2]
    if text is None or not ext_user:
        return None
    return adapter.Outbound(text[: platform.caps.max_text]), ext_user, event["ext_channel"] or ""


def deliver(gateway, workspace_id, install_id, now=None) -> int:
    """Post every queued row that is due. Returns how many were sent."""
    with _LOCKS.setdefault((workspace_id, install_id), threading.Lock()):
        return _deliver(gateway, workspace_id, install_id, _now(now))


def _deliver(gateway, workspace_id, install_id, now) -> int:
    bridge = store.open_bridge(gateway, workspace_id)
    install, platform = _platform(bridge, install_id)
    if not install["enabled"] or platform is None:
        return 0
    ref = service.install_ref(gateway, workspace_id, install_id)
    if ref is None:
        return 0
    sent = 0
    for row in bridge.due(install_id, now):
        claimed = bridge.claim(row["id"], now)
        if claimed is None:  # another caller has it, or the install was switched off
            continue
        event = bridge.event(install_id, claimed["ext_event"])
        content = (
            None
            if event is None
            else _content(gateway, workspace_id, platform, bridge, claimed, event)
        )
        if content is None:
            bridge.settle(claimed["id"], "failed")
            continue
        outbound, ext_user, ext_channel = content
        try:
            if claimed["target"] == "private":  # a chip or an answer to a tap: only to that person
                result = platform.post_private(ref, ext_user, ext_channel, outbound)
            else:
                target = adapter.Target(event["ext_channel"] or "", event["ext_thread"] or "")
                result = platform.post(ref, target, outbound, claimed["id"])
        except Exception as exc:  # noqa: BLE001 - a platform's error text never travels on
            # An error that is not a definite "nothing was posted" may have reached the
            # platform: the row stays ``sending`` and the sweep settles it, never a blind retry.
            log_failure(logger, "Chat post failed", exc)
            continue
        if not isinstance(result, adapter.PostResult):
            result = adapter.PostResult(False)
        if result.ok:
            bridge.settle(claimed["id"], "sent", ext_post=result.ext_post or None)
            sent += 1
        elif result.retry and claimed["attempts"] < MAX_ATTEMPTS:
            wait = result.retry_after or BACKOFF_S[min(claimed["attempts"], len(BACKOFF_S)) - 1]
            later = store.stamp(now + timedelta(seconds=wait))
            bridge.settle(claimed["id"], "queued", next_at=later)
        else:
            bridge.settle(claimed["id"], "failed")
    return sent


def settle_stuck(gateway, workspace_id, install_id, now) -> None:
    bridge = store.open_bridge(gateway, workspace_id)
    install, platform = _platform(bridge, install_id)
    if not install["enabled"] or platform is None:
        return
    ref = service.install_ref(gateway, workspace_id, install_id)
    for row in bridge.stuck(install_id, store.stamp(now - timedelta(seconds=STUCK_S))):
        if not bridge.settle(row["id"], "unknown"):
            continue
        event = bridge.event(install_id, row["ext_event"])
        found = None
        if ref is not None and event is not None and platform.caps.find_posted:
            try:
                found = platform.find_posted(ref, event["ext_channel"] or "", row["id"])
            except Exception as exc:  # noqa: BLE001
                log_failure(logger, "Chat post not looked up", exc)
        if found:
            bridge.settle(row["id"], "sent", ext_post=found, expect="unknown")
        elif platform.caps.find_posted:
            bridge.settle(row["id"], "queued", expect="unknown")  # it never went out: post it
        elif row["part"] != NOTICE_PART and row["target"] == "thread":
            bridge.enqueue(install_id, row["ext_event"], NOTICE_PART, "thread", "text:unsure")
