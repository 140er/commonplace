"""Public intake: one platform event becomes at most one turn of one linked person.

``POST /api/bridge/in/<install>/events`` runs these steps, in this order:

1. **Verify.** The adapter checks the platform's signature and freshness against the sealed
   secrets, and the team it names must be the install's team. A failure is 401: no row, no work.
2. **Filter.** Bots (this app included), non-mentions in channels, shared channels, channels the
   owner did not map, empty text: state ``ignored``, and still 2xx so the platform neither
   retries nor disables the app. A text that is only a link code is never ignored for its place
   (a code sent in a channel is burned).
3. **Journal.** ``bridge_events`` takes the event once; a platform retry meets the primary key
   and is answered 2xx at once, running nothing.
4. **Identity.** The sender's live link and the person's membership are re-checked. No link:
   one fixed sentence, the text is dropped (not stored, not sent to a model).
5. **Store and run.** The person's own bot gets the message and runs the Bots-layer turn on its
   own thread; the intake answers without waiting for it.

``POST /api/bridge/in/<install>/interact`` verifies the same way and hands a button tap to
:mod:`confirm`, which runs the confirmed action only for the person the chip was shown to.

Every reply is an outbox row posted by the worker. An install that is unknown or off answers a
plain 404 like a path that does not exist, and does nothing.
"""

import logging

from commonplace import ratelimit
from commonplace.bots import store as bots_store
from commonplace.chat_bridge import (
    adapter,
    confirm,
    index,
    links,
    outbox,
    service,
    store,
    turns,
    worker,
)
from commonplace.http_util import log_failure, send_json

logger = logging.getLogger(__name__)

BODY_MAX = 256 * 1024
INSTALL_LIMIT = ratelimit.Limit("bridge-install", 300, 60)  # events per install per minute


def _text(value) -> str:
    """The message text without control characters (newlines and tabs stay)."""
    value = value if isinstance(value, str) else ""
    return "".join(c for c in value if c in "\n\t" or (ord(c) >= 32 and ord(c) != 127)).strip()


def _reply(h, status, body):
    send_json(h, status, body)


def _read(h):
    """The raw body (the signature covers its exact bytes), or None after the refusal."""
    try:
        if h.headers.get("Transfer-Encoding"):
            raise ValueError
        length = int(h.headers.get("Content-Length", "0"))
        if length > BODY_MAX:
            h.close_connection = True
            _reply(h, 413, {"error": "request too large"})
            return None
        body = h.rfile.read(length) if length > 0 else b""
        if length <= 0 or len(body) != length:
            raise ValueError
        return body
    except ValueError:
        h.close_connection = True
        _reply(h, 400, {"error": "Invalid request"})
        return None


def _found(gateway, install_id):
    """``(workspace id, install)`` for an install that exists and is on, else None."""
    workspace_id = index.workspace_of(gateway.db, install_id) if install_id.isalnum() else None
    if workspace_id is None:
        return None
    bridge = store.open_bridge(gateway, workspace_id)
    if not bridge.exists():
        return None
    try:
        install = bridge.get(install_id)
    except store.Missing:
        return None
    return (workspace_id, install) if install["enabled"] else None


def handle(gateway, h, install_id, route) -> None:
    """Answer one intake request. ``route`` is ``events`` or ``interact``."""
    found = _found(gateway, install_id)
    platform = adapter.get(found[1]["platform"]) if found else None
    if found is None or platform is None:
        h.close_connection = True
        return _reply(h, 404, {"error": "Not found", "code": "not_found"})
    workspace_id, install = found
    if gateway.rate_limits and gateway.limiter.check([(INSTALL_LIMIT, install_id)]):
        return _reply(h, 429, {"error": "Busy, try again", "code": "busy"})
    body = _read(h)
    if body is None:
        return None
    ref = service.install_ref(gateway, workspace_id, install_id)
    if ref is None:
        return _reply(h, 404, {"error": "Not found", "code": "not_found"})
    headers = {k.lower(): v for k, v in h.headers.items()}
    try:
        verified = platform.verify(ref, headers, body)
    except Exception as exc:  # noqa: BLE001 - a platform library's text never reaches the caller
        log_failure(logger, "Chat intake verify failed", exc)
        verified = adapter.Reject("error")
    if isinstance(verified, adapter.Challenge):
        return _reply(h, verified.status, verified.body)
    if not isinstance(verified, adapter.Verified) or verified.team != install["ext_team"]:
        h.close_connection = True
        return _reply(h, 401, {"error": "Unauthorized", "code": "unauthorized"})
    worker.ensure(gateway)
    if route == "events":
        try:
            inbound = platform.normalize(ref, verified)
            if isinstance(inbound, adapter.Inbound):
                process(gateway, workspace_id, install, inbound)
        except Exception as exc:  # noqa: BLE001
            log_failure(logger, "Chat intake failed", exc)
            return _reply(h, 500, {"error": "Try again", "code": "failed"})
    else:
        try:
            tap = platform.normalize(ref, verified)
            if isinstance(tap, adapter.Interaction):
                confirm.tap(gateway, workspace_id, install, tap)
                worker.kick(gateway, workspace_id, install["id"])
        except Exception as exc:  # noqa: BLE001
            log_failure(logger, "Chat tap failed", exc)
            return _reply(h, 500, {"error": "Try again", "code": "failed"})
    _reply(h, 200, {"ok": True})


def _filter(bridge, install_id, inbound, text):
    """``(state, mapping)``: ``ignored`` for what is not addressed to the app, else ``received``."""
    if inbound.from_bot:
        return "ignored", None
    if links.normalize(text) is not None:
        return "received", None  # a code is handled wherever it was said
    if not text or inbound.kind not in ("dm", "mention"):
        return "ignored", None
    if inbound.kind == "dm":
        return "received", None
    mapping = None if inbound.shared_channel else bridge.channel(install_id, inbound.ext_channel)
    if mapping is None or not mapping["enabled"]:
        return "ignored", None
    return "received", mapping


def _answer(gateway, workspace_id, install_id, ext_event, state, code) -> None:
    store.open_bridge(gateway, workspace_id).set_event(install_id, ext_event, ("received",), state)
    outbox.enqueue_text(gateway, workspace_id, install_id, ext_event, code)
    worker.kick(gateway, workspace_id, install_id)


def process(gateway, workspace_id, install, inbound) -> None:
    install_id, event = install["id"], inbound.ext_event
    bridge = store.open_bridge(gateway, workspace_id)
    text = _text(inbound.text)
    state, mapping = _filter(bridge, install_id, inbound, text)
    thread = inbound.ext_thread or (inbound.ext_message if inbound.kind != "dm" else "")
    if not bridge.journal(install_id, event, state, inbound.ext_channel, thread):
        return  # a platform retry: answered 2xx, nothing runs
    if state == "ignored":
        return
    if links.normalize(text) is not None and not inbound.from_bot:
        done = links.redeem(gateway, workspace_id, install_id, inbound.ext_user, inbound.kind, text)
        if done.code in ("off", "not_a_code"):
            bridge.set_event(install_id, event, ("received",), "ignored")
            return
        _answer(
            gateway,
            workspace_id,
            install_id,
            event,
            "replied" if done.code == "linked" else "refused",
            f"link_{done.code}",
        )
        return
    who = links.resolve(gateway, workspace_id, install_id, inbound.ext_user)
    if who.reason == "off":
        bridge.set_event(install_id, event, ("received",), "refused")
        return
    if who.principal is None:
        _answer(gateway, workspace_id, install_id, event, "refused", "unlinked")
        return
    if len(text) > bots_store.TEXT_MAX:
        _answer(gateway, workspace_id, install_id, event, "refused", "too_long")
        return
    if bridge.turns_today(install_id) >= install["daily_cap"]:
        _answer(gateway, workspace_id, install_id, event, "refused", "cap")
        return
    board = mapping["board"] if mapping else None
    try:
        bot, message, run = turns.start(
            gateway,
            workspace_id,
            install_id,
            event,
            who.principal.user_id,
            text,
            board,
            mapping["disclosure"] if mapping else "full",
            tap=bool(who.link["confirm_ok"]),
        )
    except turns.NoBot:
        code = "channel_denied" if board else "unlinked"
        _answer(gateway, workspace_id, install_id, event, "refused", code)
        return
    except turns.Busy:
        _answer(gateway, workspace_id, install_id, event, "refused", "busy")
        return
    if run is None:  # the Bots store already held this request: it ran before
        bridge.set_event(install_id, event, ("received",), "ignored")
        return
    bridge.set_event(
        install_id,
        event,
        ("received",),
        "running",
        user_id=who.principal.user_id,
        bot_id=bot["id"],
        message_id=message["id"],
    )
    turns.watch(gateway, workspace_id, install_id, event, run)
