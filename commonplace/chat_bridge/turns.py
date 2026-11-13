"""Mapping a linked person's chat message to their Bots-layer turn.

The bridge adds intake, identity, a surface policy and delivery; the model work is the Bots
layer's own turn engine, with the person's own bot, tools, limits and budget. A direct message
goes to the person's General bot, a mapped channel's mention to their project bot for the mapped
project, with the surface set to ``dm`` or ``channel`` so the engine narrows what it may use.
The message is stored with a request id derived from the platform event, so the Bots store's
own ``(bot, request_id)`` key is a second idempotency layer behind the journal.
"""

import hashlib
import logging
import threading
from datetime import timedelta

from commonplace import sessions
from commonplace.boards.service import BoardService
from commonplace.bots import limits, store as bots_store, turn as bots_turn
from commonplace.chat_bridge import outbox, store
from commonplace.extensions import ToolContext
from commonplace.http_util import log_failure

logger = logging.getLogger(__name__)

RECEIVED_GRACE_S = 30  # an event that never started a turn, after its intake had time to


class NoBot(Exception):
    """The person has no bot for this chat: the project is not one they can read."""


class Busy(Exception):
    """The person's turn limit is full."""


def request_id(install_id, ext_event) -> str:
    """The Bots request id of an event (8 to 64 of ``A-Za-z0-9_-``, so not a colon form)."""
    digest = hashlib.sha256(f"commonplace-bridge\0{install_id}\0{ext_event}".encode())
    return "bridge-" + digest.hexdigest()[:32]


def bot_for(gateway, workspace_id, user_id, board):
    """The person's own bot for this chat: the project bot of ``board`` (a channel), else their
    General bot. A project they cannot read has no bot, and so no turn."""
    principal = sessions.person_principal(gateway.db, user_id, workspace_id)
    if principal is None:
        raise NoBot()
    boards = BoardService(ToolContext(principal=principal, gateway=gateway)).board_list()
    if board is not None and board not in {b["slug"] for b in boards}:
        raise NoBot()
    bots = bots_store.open_bots(gateway, workspace_id)
    bots.ensure_defaults(user_id, [(b["slug"], b["name"]) for b in boards])
    for bot in bots.roster(user_id):
        if bot["archived"]:
            continue
        if (bot["kind"], bot["board"]) == (("project", board) if board else ("general", None)):
            return principal, bots.viewable(bot["id"], user_id)
    raise NoBot()


def start(gateway, workspace_id, install_id, ext_event, user_id, text, board, disclosure):
    """Store the message and start the turn on its own thread. Returns the bot, the stored
    message and the run (None when the Bots store already held this request: nothing starts)."""
    principal, bot = bot_for(gateway, workspace_id, user_id, board)
    bots = bots_store.open_bots(gateway, workspace_id)
    rid = request_id(install_id, ext_event)
    seen = bots.message_for_request(bot["id"], user_id, rid)
    if seen is not None:
        return bot, seen, None
    surface = "channel" if board else "dm"
    run = bots_turn.TurnRun(gateway, principal, bot, rid, surface=surface, disclosure=disclosure)
    try:
        limits.REGISTRY.admit(run)
    except limits.Busy:
        raise Busy() from None
    try:
        bots.lease_start(bot["id"], user_id, run.key)
        message = bots.add_user_message(bot["id"], user_id, text, rid)
    except BaseException:
        bots.lease_end(bot["id"], run.key)
        limits.REGISTRY.release(run)
        raise
    run.start(message)
    return bot, message, run


def watch(gateway, workspace_id, install_id, ext_event, run) -> None:
    """After the turn, journal its outcome and queue its stored reply for delivery. Its own
    thread: the intake has long since answered the platform."""
    threading.Thread(
        target=_watch,
        args=(gateway, workspace_id, install_id, ext_event, run),
        name="bridge-turn",
        daemon=True,
    ).start()


def _watch(gateway, workspace_id, install_id, ext_event, run) -> None:
    try:
        run.finished.wait()
        if run.reply is not None:
            finish(gateway, workspace_id, install_id, ext_event, run.reply)
            outbox.deliver(gateway, workspace_id, install_id)
    except Exception as exc:  # noqa: BLE001 - the sweep resolves what this could not
        log_failure(logger, "Chat reply not queued", exc)


def finish(gateway, workspace_id, install_id, ext_event, reply) -> None:
    """Record the turn's outcome on its event and queue the reply, once."""
    state = reply["state"]
    state = (
        "replied" if state == "ok" else state if state.startswith("failed:") else f"failed:{state}"
    )
    bridge = store.open_bridge(gateway, workspace_id)
    if bridge.set_event(install_id, ext_event, ("running",), state):
        outbox.enqueue_reply(gateway, workspace_id, install_id, ext_event, reply)


def close_event(gateway, workspace_id, event) -> None:
    """A ``running`` event with no live turn: the Bots layer stores ``failed:interrupted`` for
    the request (nothing reruns) and that one fixed sentence is queued for the platform."""
    bots = bots_store.open_bots(gateway, workspace_id)
    user_id, bot_id = event["user_id"], event["bot_id"]
    rid = request_id(event["install_id"], event["ext_event"])
    message = bots.message_for_request(bot_id, user_id, rid) if bot_id and user_id else None
    reply = (
        bots_turn.interrupted(gateway, workspace_id, user_id, bot_id, message)
        if message is not None
        else None
    )
    if reply is None:
        if message is None:  # nothing stored: the turn never began
            bridge = store.open_bridge(gateway, workspace_id)
            if bridge.set_event(
                event["install_id"], event["ext_event"], ("running",), "failed:interrupted"
            ):
                outbox.enqueue_text(
                    gateway, workspace_id, event["install_id"], event["ext_event"], "interrupted"
                )
        return  # still running here or in another process: leave it
    finish(gateway, workspace_id, event["install_id"], event["ext_event"], reply)


def resolve_events(gateway, workspace_id, install_id, now) -> None:
    """Close what a restart left behind, without running anything again."""
    bridge = store.open_bridge(gateway, workspace_id)
    for event in bridge.open_events(
        install_id, "received", store.stamp(now - timedelta(seconds=RECEIVED_GRACE_S))
    ):
        if bridge.set_event(install_id, event["ext_event"], ("received",), "failed:interrupted"):
            outbox.enqueue_text(
                gateway, workspace_id, install_id, event["ext_event"], "interrupted"
            )
    for event in bridge.open_events(install_id, "running", store.stamp(now + timedelta(seconds=1))):
        close_event(gateway, workspace_id, event)
