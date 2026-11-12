"""The policy check of one tool call, made once at the gateway's dispatch point.

``check_call`` runs after the connection's own policy (profile, scopes, trust, owner gates) has
let the tool through, so the workspace policy can only narrow: a tool the connection may not
see stays an unknown tool whatever the owner set here.

Ask first never runs the call it gates. It files one question to the workspace owner through
the ordinary ask path (a review card with Approve and Deny, shown in Needs me) and records a
request bound to the connection, the action and the digest of the arguments. The same call
runs once after a signed-in owner picked Approve on that card:

* another connection, another action or other arguments have another binding and file their
  own request;
* the request closes when the call runs, so a second identical call asks again;
* the owner has ``REQUEST_TTL_S`` to answer and the approval is good for ``APPROVAL_TTL_S``;
* anything but Approve is a denial, and an answer or a change on the card by anyone who is
  not a signed-in owner voids the request.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from commonplace.control_db import ControlDB
from commonplace.extensions import ToolContext, ToolError
from commonplace.rules import checks

from . import store

REQUEST_TTL_S = 24 * 3600
APPROVAL_TTL_S = 3600
MAX_PENDING = 20  # open requests per connection
APPROVE, DENY = "Approve", "Deny"
PERSON_REASON = "The workspace owner set this action to Ask first."
# 12 lines of at most 24 + 2 + 60 characters stay inside one decision-block preview.
_PREVIEW_LINES, _PREVIEW_KEY, _PREVIEW_VALUE, _PREVIEW_CHARS = 12, 24, 60, 1100

# One filing at a time: two identical calls must not file two requests.
_filing = threading.Lock()


def _clock(gateway) -> float:
    return getattr(gateway, "wall_clock", time.time)()


def _refusal(req_id, text: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {"content": [{"type": "text", "text": text}], "isError": True},
    }


def _printable(text: str) -> str:
    return "".join(c if c.isprintable() else f"\\u{ord(c):04x}" for c in text)


def preview(arguments: dict) -> str:
    """A bounded, printable picture of the arguments: one line per argument."""
    lines = []
    for key in sorted(arguments)[:_PREVIEW_LINES]:
        value = _printable(json.dumps(arguments[key], ensure_ascii=False, default=str))
        if len(value) > _PREVIEW_VALUE:
            value = value[: _PREVIEW_VALUE - 1] + "…"
        lines.append(f"{_printable(str(key))[:_PREVIEW_KEY]}: {value}")
    if len(arguments) > _PREVIEW_LINES:
        lines.append(f"… and {len(arguments) - _PREVIEW_LINES} more")
    text = "\n".join(lines) or "(no arguments)"
    return text if len(text) <= _PREVIEW_CHARS else text[: _PREVIEW_CHARS - 1] + "…"


def _minutes(seconds: float) -> str:
    minutes = max(1, round(seconds / 60))
    return f"{minutes // 60} h" if minutes % 60 == 0 else f"{minutes} min"


def _file(gateway, principal, name: str, arguments: dict, digest: str) -> dict:
    """Ask the owner on the caller's first visible board; the card and where its log stood."""
    from commonplace.boards.service import BoardService

    service = BoardService(ToolContext(principal=principal, gateway=gateway))
    boards = service.board_list()
    card = service.ask(
        f"Allow {principal.connection} to run the action {name} once?",
        options=[
            {
                "label": APPROVE,
                "detail": "Runs this exact call once, for this connection only.",
                "preview": preview(arguments),
            },
            {"label": DENY, "detail": "Nothing runs."},
        ],
        board=boards[0]["slug"] if boards else None,
        context=(
            f"The connection {principal.connection} called {name}. Nothing was run: the "
            f"workspace tool policy holds this action until you answer. Approve covers only "
            f"this connection, this action and these exact arguments (sha256 {digest}), once, "
            f"within {_minutes(APPROVAL_TTL_S)} of your answer."
        ),
        person_reason=PERSON_REASON,
    )
    if card["status"] != "review" or not str(card["ask_to"] or "").startswith("user:"):
        raise ToolError("no: the request did not reach the workspace owner")
    with service.store.connection() as conn:
        row = conn.execute(
            "SELECT version, created_at FROM tasks WHERE id=?", (card["number"],)
        ).fetchone()
        last = conn.execute(
            "SELECT MAX(id) FROM task_events WHERE task_id=?", (card["number"],)
        ).fetchone()[0]
    # A change that slipped in before the log position was read would go unseen later.
    if row is None or row["version"] != card["version"] or last is None:
        raise ToolError("no: the request changed while it was filed")
    return {"number": card["number"], "created_at": row["created_at"], "after_event": last}


def _verdict(gateway, principal, row, now: float) -> tuple:
    """``(state, text)`` of a pending request: approved, waiting, denied, void or expired."""
    from commonplace.boards.service import BoardService

    number = row["card"]
    service = BoardService(ToolContext(principal=principal, gateway=gateway))
    with service.store.connection() as conn:
        card = conn.execute("SELECT created_at FROM tasks WHERE id=?", (number,)).fetchone()
        events = conn.execute(
            "SELECT kind, choice, stamp_user, stamp_connection, created_at FROM task_events "
            "WHERE task_id=? AND id>? ORDER BY id",
            (number, row["after_event"]),
        ).fetchall()
    if card is None or card["created_at"] != row["card_created_at"]:
        return "void", f"the approval request #{number} no longer exists"
    for event in events:
        if (
            event["stamp_connection"] is not None
            or gateway.db.role(principal.workspace_id, event["stamp_user"]) != "owner"
        ):
            return "void", (
                f"the approval request #{number} was changed or answered by someone other "
                "than the signed-in workspace owner, so it no longer counts"
            )
        if event["kind"] != "decide":
            continue
        if event["choice"] != APPROVE:
            return "denied", f"the workspace owner did not approve this call (#{number})"
        decided = (
            datetime.strptime(event["created_at"], "%Y-%m-%dT%H:%M:%SZ")
            .replace(tzinfo=timezone.utc)
            .timestamp()
        )
        if now > decided + APPROVAL_TTL_S:
            return "expired", f"the approval in #{number} expired before the call was repeated"
        return "approved", ""
    if now > row["expires_at"]:
        return "expired", f"the approval request #{number} expired without an answer"
    return "waiting", ""


def _audit(mediator, principal, name, reason):
    mediator._audit(principal, name, "deny", reason)


def _ask_first(mediator, principal, req_id, name: str, arguments: dict) -> Optional[dict]:
    gateway, db = mediator.gateway, mediator.db
    if gateway is None:
        _audit(mediator, principal, name, "tool policy: ask first unavailable")
        return _refusal(req_id, f"no: {name} needs the workspace owner's approval first")
    now = _clock(gateway)
    digest = store.digest(name, arguments)
    binding = (principal.workspace_id, principal.connection_id, name, digest)
    with _filing:
        row = store.pending(db, *binding)
        if row is not None:
            state, text = _verdict(gateway, principal, row, now)
            if state == "waiting":
                _audit(mediator, principal, name, f"tool policy: waiting on #{row['card']}")
                return _refusal(
                    req_id,
                    f"ask first: {name} still waits for the workspace owner's answer in "
                    f"#{row['card']}. Nothing was run. Repeat the same call after the approval.",
                )
            if state != "approved":
                store.close(db, row["id"], state, now)
                _audit(mediator, principal, name, f"tool policy: {state} #{row['card']}")
                return _refusal(
                    req_id,
                    f"no: {text}. Nothing was run. Call {name} again to ask anew.",
                )
            # Closed before the call runs: one approval, one call.
            if store.close(db, row["id"], "used", now):
                db.audit(
                    action="tool.approval",
                    decision="used",
                    workspace_id=principal.workspace_id,
                    user_id=principal.user_id,
                    connection_id=principal.connection_id,
                    auth=principal.auth,
                    tool=name,
                    reason=f"card=#{row['card']}",
                    target_ids=[row["id"]],
                )
                return None
        if store.pending_count(db, principal.workspace_id, principal.connection_id, now) >= (
            MAX_PENDING
        ):
            _audit(mediator, principal, name, "tool policy: too many open requests")
            return _refusal(
                req_id,
                f"no: {name} needs the workspace owner's approval, and this connection "
                f"already has {MAX_PENDING} approval requests waiting. Nothing was run.",
            )
        try:
            filed = _file(gateway, principal, name, arguments, digest)
        except ToolError as exc:
            _audit(mediator, principal, name, "tool policy: ask first could not be filed")
            return _refusal(
                req_id,
                f"no: {name} needs the workspace owner's approval first, and the request "
                f"could not be filed ({str(exc).removeprefix('no: ')}). Nothing was run.",
            )
        store.add(
            db,
            workspace_id=principal.workspace_id,
            connection_id=principal.connection_id,
            tool=name,
            digest_=digest,
            card=filed["number"],
            card_created_at=filed["created_at"],
            after_event=filed["after_event"],
            now=now,
            expires_at=now + REQUEST_TTL_S,
        )
    _audit(mediator, principal, name, f"tool policy: ask first #{filed['number']}")
    from commonplace.boards.display import card_url

    link = card_url(ToolContext(principal=principal, gateway=gateway), filed["number"])
    return _refusal(
        req_id,
        f"ask first: {name} needs the workspace owner's approval. Asked in "
        f"#{filed['number']} ({link}). Nothing was run. After the owner approves, repeat the "
        f"same call with the same arguments within {_minutes(APPROVAL_TTL_S)}; the approval "
        "covers this connection and that one call.",
    )


def check_call(mediator, principal, req_id, name: str, arguments: dict) -> Optional[dict]:
    """The response that refuses this call, or ``None`` to let it run.

    People in the app and the gateway's own nested calls are not agent tool calls.
    """
    # The policy lives in the control database; a bare worker helper has none to consult.
    if not isinstance(getattr(mediator, "db", None), ControlDB):
        return None
    if principal.person or checks.is_internal_call():
        return None
    mode = store.mode(mediator.db, principal.workspace_id, name)
    if mode == store.ALLOWED:
        return None
    if mode == store.ASK:
        return _ask_first(mediator, principal, req_id, name, arguments)
    _audit(mediator, principal, name, "tool policy: off")
    return _refusal(
        req_id,
        f"no: the workspace owner turned {name} off for agents (tool policy: Off). "
        "Nothing was run.",
    )


def _inventory_tool(method: str, path: str) -> Optional[str]:
    """The tool a REST write stands for, from the rules inventory of REST dispatch shapes."""
    from commonplace.rules.enforce import REST_ROUTES

    parts = path.strip("/").split("/")
    for route in REST_ROUTES:
        shape = route.path.strip("/").split("/")
        if (
            route.tool
            and route.method == method
            and len(shape) == len(parts)
            and all(s.startswith("<") or s == p for s, p in zip(shape, parts))
        ):
            return route.tool
    return None


def rest_refusal(gateway, principal, tool, method: str, path: str) -> Optional[str]:
    """Why a connection's REST form of a tool action is refused, or ``None``.

    ``tool`` is the gateway's REST alias; other REST writes are found in the rules
    inventory. An approval binds to an MCP call's arguments, so Ask first is not served
    over REST.
    """
    if principal.person:
        return None
    tool = tool or _inventory_tool(method, path)
    if not tool:
        return None
    mode = store.mode(gateway.db, principal.workspace_id, tool)
    if mode == store.ALLOWED:
        return None
    from commonplace.rules.enforce import audit

    audit(gateway.db, principal, tool, "deny", f"tool policy: {mode}", transport="rest")
    if mode == store.ASK:
        return (
            f"the workspace owner set {tool} to Ask first; call the MCP tool so the request "
            "can be approved"
        )
    return f"the workspace owner turned {tool} off for agents (tool policy: Off)"
