"""Confirmation on the chat surface: no write with a person's identity runs without their tap.

A direct message is the person's own Bots-layer turn with one change: when the person opted in
(``confirm_ok`` on their link) the writing tools are offered as confirm tools, so a write only
ever leaves a pending action behind (the Bots confirm machinery, unchanged: frozen arguments,
server-built label, ten minute expiry, item version, compare-and-set consumption, the "your own
words" check before any chip exists). With ``confirm_ok`` off (the default) the routine writes are
not offered at all and a confirm tool leaves its chip in the person's web thread, with one fixed
sentence saying so.

:func:`offer` posts the chips of a finished turn, each as a *private* message to the asker (never a
button in a channel) whose text is the server-built label, redacted, never anything the model
wrote. :func:`tap` handles the signed interaction that comes back. It runs the action only if the
tapper's platform id is the one that was shown the chip, in the channel it was shown in, belongs to
a live link of the person who owns the pending action, that link has ``confirm_ok``, the install is
on, the person is still a member and the tool may be confirmed here. Anything else is one fixed
sentence and nothing runs. A repeated tap meets the compare-and-set and says it is already done.
"""

import logging

from commonplace import redact
from commonplace.bots import store as bots_store, tools, turn as bots_turn
from commonplace.chat_bridge import adapter, links, store
from commonplace.http_util import log_failure

logger = logging.getLogger(__name__)

TAP = "tap:"  # journal key prefix of an interaction, so it can never meet a message event id
CHIP_PART, WEB_PART = 500, 1000  # outbox parts after the reply: the chips, then the web notice
CHIP_CHARS = 300


def allowed(install, tool_name) -> bool:
    """Whether a tap may confirm this tool here: the three consequential ones (start an agent,
    approve, create a project) only when the owner switched them on for the install."""
    if tool_name not in tools.CHAT_TAP:
        return False
    return tool_name not in tools.CHAT_CONSEQUENTIAL or bool(install["consequential_taps"])


def offer(gateway, workspace_id, install_id, ext_event, reply) -> None:
    """Queue the chips of a finished turn (a private post each) or, when none can be tapped, the
    one sentence that the action waits in the web app."""
    pendings = [
        b["pending"]
        for b in reply.get("blocks") or []
        if isinstance(b, dict) and b.get("type") == "confirm"
    ]
    bridge = store.open_bridge(gateway, workspace_id)
    event = bridge.event(install_id, ext_event) if pendings else None
    if event is None or not event["user_id"]:
        return
    install = bridge.get(install_id)
    state = "queued" if install["enabled"] else "cancelled"
    link = next(
        (
            item
            for item in bridge.links(user_id=event["user_id"])
            if item["install_id"] == install_id
        ),
        None,
    )
    bots = bots_store.open_bots(gateway, workspace_id)
    chips, web = 0, False
    for pending_id in pendings:
        pending = bots.pending_for(event["user_id"], pending_id)
        if pending is None:
            continue
        if link and link["confirm_ok"] and allowed(install, pending["tool"]):
            bridge.add_prompt(pending_id, install_id, link["ext_user"], event["ext_channel"] or "")
            bridge.enqueue(
                install_id, ext_event, CHIP_PART + chips, "private", f"prompt:{pending_id}", state
            )
            chips += 1
        else:
            web = True
    if web:
        bridge.enqueue(install_id, ext_event, WEB_PART, "thread", "text:web_confirm", state)


def chip(gateway, workspace_id, user_id, pending_id):
    """The chip of a still-open pending action: its server-built label (redacted again) and two
    buttons that carry only the pending id. None when it is no longer open."""
    pending = bots_store.open_bots(gateway, workspace_id).pending_for(user_id, pending_id)
    if pending is None or pending["state"] != "open" or pending["expires_at"] <= store.stamp():
        return None
    label = " ".join(redact.redact(pending["label"])[0].split())[:CHIP_CHARS]
    text = f"{label}\n\nConfirm this? It waits for you for 10 minutes."
    return adapter.Outbound(
        text, (("Confirm", f"confirm:{pending_id}"), ("Cancel", f"cancel:{pending_id}"))
    )


def tap(gateway, workspace_id, install, inter) -> None:
    """Handle one signed interaction. Recorded once per platform event id (a retry runs nothing)
    and answered privately to the tapper with one fixed sentence or the action's outcome."""
    install_id, key = install["id"], TAP + inter.ext_event
    bridge = store.open_bridge(gateway, workspace_id)
    if not bridge.journal(install_id, key, "received", inter.ext_channel, ""):
        return
    try:
        state, ref, fields = _handle(gateway, workspace_id, install, inter, bridge)
    except Exception as exc:  # noqa: BLE001 - what failed is for the log, not the platform
        log_failure(logger, "Chat tap failed", exc)
        state, ref, fields = _no("tap_failed", inter)
    bridge.set_event(install_id, key, ("received",), state, **fields)
    bridge.enqueue(install_id, key, 0, "private", ref)


def _no(code, inter):
    return "refused", f"answer:{code}:{inter.ext_user}", {}


def _handle(gateway, workspace_id, install, inter, bridge):
    action, _, pending_id = (inter.payload if isinstance(inter.payload, str) else "").partition(":")
    if action not in ("confirm", "cancel") or not pending_id.isalnum():
        return _no("tap_denied", inter)
    prompt = bridge.prompt(install["id"], pending_id)
    if (
        prompt is None
        or prompt["ext_user"] != inter.ext_user  # someone else's chip
        or prompt["ext_channel"] != inter.ext_channel  # not where it was shown
    ):
        return _no("tap_denied", inter)
    who = links.resolve(gateway, workspace_id, install["id"], inter.ext_user)
    if who.principal is None:
        return _no("tap_denied", inter)
    if not who.link["confirm_ok"]:
        return _no("tap_off", inter)
    bots = bots_store.open_bots(gateway, workspace_id)
    pending = bots.pending_for(who.principal.user_id, pending_id)
    if pending is None:
        return _no("tap_denied", inter)
    if not allowed(install, pending["tool"]):
        return _no("tap_web_only", inter)
    try:
        message = bots_turn.confirm(
            gateway,
            who.principal,
            pending["bot_id"],
            pending_id,
            action == "confirm",
            thread_id=pending["thread_id"],
        )
    except bots_turn.Refused as refused:
        return _no("tap_expired" if refused.code == "expired" else "tap_done", inter)
    except bots_store.Missing:
        return _no("tap_denied", inter)
    fields = {"user_id": who.principal.user_id, "bot_id": pending["bot_id"]}
    return "replied", f"note:{message['id']}:{inter.ext_user}", fields
