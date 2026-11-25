"""Memory organisation views: graph facts, stored sources and links, read and maintain.

Every route here runs the same worker tool a connection would call, as the same caller,
through the gateway's full policy path, so a caller sees and changes only what the tools
already let them. Nothing is deleted and no stored text is rewritten by this module:

* a fact is ended (``mempalace_kg_invalidate``) or replaced (``mempalace_kg_supersede``);
  both keep the old fact and its sources as history;
* a link is created or removed (``mempalace_create_tunnel`` / ``mempalace_delete_tunnel``);
  the places it joins and every drawer in them stay as they are;
* drawers and diary entries are only read, word for word.

Each change is a two-step call to the same path. Without ``confirm`` it answers what would
change and an ``effect_id``; with ``confirm`` and that ``effect_id`` it applies exactly that
and nothing else. If the stored state moved since the preview it refuses and changes nothing.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone

from ._http import BadRequest, query, read_json, require_principal, send_json
from .extensions import RetryableToolError, ToolContext, ToolError
from .http_util import log_failure

logger = logging.getLogger(__name__)

FACT_PAGE = 100
SOURCE_PAGE = 20
LINK_PAGE = 100
MATCH_PAGES = 20
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}Z)?$")


class _Reply(Exception):
    def __init__(self, status, body):
        self.status, self.body = status, body


def _fail(status, message, **extra):
    raise _Reply(status, {"error": message, **extra})


def _text(data, key, *, required=True, limit=2000):
    value = data.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > limit:
        _fail(400, f"{key} is required" if required else f"{key} is not valid")
    try:
        value.encode("utf-8")
    except UnicodeError:
        _fail(400, f"{key} is not valid text")
    return value


def _page(params, name, default, maximum):
    raw = params.get(name)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except ValueError:
        _fail(400, f"{name} must be a whole number")
    if not 0 <= value <= maximum:
        _fail(400, f"{name} is out of range")
    return value


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _when(data, key):
    value = data.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not _DATE.match(value):
        _fail(400, f"{key} must be YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ")
    return value


def _tool(ctx, name, args):
    """One worker tool as this caller. Refusals and storage trouble get their own words."""
    refusal = ctx.principal.policy.refusal(name)
    if refusal:
        _fail(403, f"Your access does not include this: {refusal}.")
    try:
        return ctx.memory(name, args)
    except RetryableToolError as exc:
        _fail(
            503,
            "Memory is busy or paused just now, so nothing was read or changed. Try again shortly.",
            retry_after_s=max(1, int(exc.retry_after_s)),
        )
    except ToolError as exc:
        _fail(403, str(exc) or "Memory refused this request.")


def _effect_id(effect):
    return hashlib.sha256(json.dumps(effect, sort_keys=True).encode("utf-8")).hexdigest()


def _same_fact(row, subject, predicate, obj, wing):
    return (
        row.get("subject") == subject
        and row.get("predicate") == predicate
        and row.get("object") == obj
        and (wing is None or (row.get("wing") or "") == wing)
    )


def _public_fact(row):
    return {
        key: row.get(key)
        for key in (
            "id",
            "subject",
            "predicate",
            "object",
            "valid_from",
            "valid_to",
            "current",
            "status",
            "wing",
            "sensitivity",
            "sources",
            "confidence",
        )
    }


def _current_match(ctx, subject, predicate, obj, wing):
    """The one current fact this names, as this caller can see it, or a plain refusal."""
    found = []
    for page in range(MATCH_PAGES):
        reply = _tool(
            ctx,
            "mempalace_kg_timeline",
            {"entity": subject, "limit": FACT_PAGE, "offset": page * FACT_PAGE},
        )
        rows = reply.get("timeline") if isinstance(reply, dict) else None
        if not isinstance(rows, list):
            _fail(502, "Memory answered in a form this page does not understand. Nothing changed.")
        found += [
            r
            for r in rows
            if _same_fact(r, subject, predicate, obj, wing) and r.get("valid_to") is None
        ]
        if len(rows) < FACT_PAGE:
            break
    if not found:
        _fail(
            404,
            "No current fact matches this, or your access does not include it. Nothing was changed.",
        )
    if len(found) > 1:
        _fail(
            409,
            "More than one current fact matches. Name its wing so exactly one is changed. "
            "Nothing was changed.",
        )
    return found[0]


# ---- Reads --------------------------------------------------------------------------------------
def places(ctx, params):
    reply = _tool(ctx, "mempalace_get_taxonomy", {})
    taxonomy = reply.get("taxonomy") if isinstance(reply, dict) else None
    if not isinstance(taxonomy, dict):
        _fail(502, "Memory answered in a form this page does not understand.")
    return {
        "wings": [
            {"wing": wing, "rooms": [{"room": r, "count": n} for r, n in sorted(rooms.items())]}
            for wing, rooms in sorted(taxonomy.items())
            if isinstance(rooms, dict)
        ]
    }


def facts(ctx, params):
    entity = (params.get("entity") or "").strip() or None
    offset = _page(params, "offset", 0, 1_000_000)
    limit = _page(params, "limit", FACT_PAGE, FACT_PAGE) or FACT_PAGE
    args = {"limit": limit, "offset": offset}
    if entity:
        args["entity"] = entity
    reply = _tool(ctx, "mempalace_kg_timeline", args)
    rows = reply.get("timeline") if isinstance(reply, dict) else None
    if not isinstance(rows, list):
        _fail(502, "Memory answered in a form this page does not understand.")
    return {
        "entity": entity,
        "facts": [_public_fact(row) for row in rows if isinstance(row, dict)],
        "total": reply.get("total"),
        "offset": offset,
        "limit": limit,
    }


def sources(ctx, params):
    wing, room = (params.get("wing") or "").strip(), (params.get("room") or "").strip()
    offset = _page(params, "offset", 0, 1_000_000)
    args = {"limit": SOURCE_PAGE, "offset": offset}
    if wing:
        args["wing"] = wing
    if room:
        args["room"] = room
    reply = _tool(ctx, "mempalace_list_drawers", args)
    rows = reply.get("drawers") if isinstance(reply, dict) else None
    if not isinstance(rows, list):
        _fail(502, "Memory answered in a form this page does not understand.")
    return {
        "wing": wing,
        "room": room,
        "drawers": [
            {
                "id": row.get("drawer_id"),
                "wing": row.get("wing"),
                "room": row.get("room"),
                "preview": row.get("content_preview"),
                "source_file": (row.get("metadata") or {}).get("source_file"),
                "filed_at": (row.get("metadata") or {}).get("filed_at"),
                "added_by": (row.get("metadata") or {}).get("added_by"),
            }
            for row in rows
            if isinstance(row, dict) and row.get("drawer_id")
        ],
        "total": reply.get("total"),
        "offset": offset,
        "limit": SOURCE_PAGE,
    }


def source(ctx, params):
    identifier = (params.get("id") or "").strip()
    if not identifier:
        _fail(400, "id is required")
    try:
        reply = _tool(ctx, "mempalace_get_drawer", {"drawer_id": identifier})
    except _Reply as exc:
        if exc.status == 403 and "not found" in str(exc.body.get("error", "")).lower():
            _fail(404, "This stored text does not exist, or your access does not include it.")
        raise
    text = reply.get("content") if isinstance(reply, dict) else None
    if not isinstance(text, str):
        _fail(502, "Memory answered in a form this page does not understand.")
    return {
        "id": identifier,
        "wing": reply.get("wing"),
        "room": reply.get("room"),
        "text": text,
        "metadata": reply.get("metadata") or {},
    }


def links(ctx, params):
    wing = (params.get("wing") or "").strip()
    reply = _tool(ctx, "mempalace_list_tunnels", {"wing": wing} if wing else {})
    if not isinstance(reply, list):
        _fail(502, "Memory answered in a form this page does not understand.")
    # Hallways are the automatic links inside one wing; read only here.
    hallways, hallway_note = None, None
    try:
        page = _tool(
            ctx, "mempalace_list_hallways", {**({"wing": wing} if wing else {}), "limit": 25}
        )
        if isinstance(page, dict) and isinstance(page.get("hallways"), list):
            hallways = {"rows": page["hallways"][:25], "total": page.get("total")}
        else:
            hallway_note = "Memory answered in a form this page does not understand."
    except _Reply as exc:
        hallway_note = exc.body.get("error")
    return {
        "wing": wing,
        "tunnels": [t for t in reply[:LINK_PAGE] if isinstance(t, dict)],
        "total": len(reply),
        "shown": min(len(reply), LINK_PAGE),
        "hallways": hallways,
        "hallway_note": hallway_note,
    }


# ---- Changes: preview, then apply exactly the previewed effect -----------------------------------
def _fact_change(ctx, body, kind):
    subject = _text(body, "subject")
    predicate = _text(body, "predicate")
    old = _text(body, "object")
    wing = _text(body, "wing", required=False, limit=200)
    new = _text(body, "new_object") if kind == "supersede" else None
    when = _when(body, "at" if kind == "supersede" else "ended") or (
        _now() if kind == "supersede" else _now()[:10]
    )
    row = _current_match(ctx, subject, predicate, old, wing)
    if kind == "supersede" and new == old:
        _fail(400, "The new value is the same as the current one. Nothing would change.")
    fact = _public_fact(row)
    key = "at" if kind == "supersede" else "ended"
    if kind == "invalidate":
        lines = [
            f"Marks this fact as no longer true from {when}; it stops being returned as current.",
            "The fact, its start date, its wing and its sources stay stored and stay in its history.",
        ]
    else:
        lines = [
            f"Ends this fact on {when}.",
            "Starts the new value below on the same date, in the same wing and with the same "
            "sensitivity.",
            "The old fact, its start date and its sources stay stored and stay in its history.",
        ]
    lines.append("No stored memory text is deleted or changed.")
    effect = {
        "action": kind,
        "subject": subject,
        "predicate": predicate,
        "object": old,
        "wing": fact["wing"],
        "fact_id": fact["id"],
        key: when,
        **({"new_object": new} if new is not None else {}),
    }
    effect_id = _effect_id(effect)
    if not body.get("confirm"):
        return 200, {"preview": {**effect, "fact": fact, "lines": lines}, "effect_id": effect_id}
    if body.get("effect_id") != effect_id:
        _fail(
            409,
            "This change no longer matches what was shown. Review it again. Nothing was changed.",
        )
    if kind == "invalidate":
        args = {
            "subject": subject,
            "predicate": predicate,
            "object": old,
            "ended": when,
            "wing": fact["wing"],
        }
        result = _tool(ctx, "mempalace_kg_invalidate", args)
    else:
        args = {
            "subject": subject,
            "predicate": predicate,
            "old_object": old,
            "new_object": new,
            "at": when,
            "wing": fact["wing"],
            "sensitivity": fact["sensitivity"],
        }
        result = _tool(ctx, "mempalace_kg_supersede", args)
    if not isinstance(result, dict) or result.get("success") is not True:
        _fail(502, "Memory did not confirm the change. Reload to see what is stored.")
    return 200, {
        "done": True,
        "action": kind,
        "effect": effect,
        "result": {"fact": result.get("fact")},
    }


def invalidate(ctx, body):
    return _fact_change(ctx, body, "invalidate")


def supersede(ctx, body):
    return _fact_change(ctx, body, "supersede")


def _place(body, prefix):
    return _text(body, f"{prefix}_wing", limit=200), _text(body, f"{prefix}_room", limit=200)


def _ends(link):
    return {
        (e.get("wing"), e.get("room"), e.get("drawer_id") or None)
        for e in (link.get("source"), link.get("target"))
        if isinstance(e, dict)
    }


def link_add(ctx, body):
    sw, sr = _place(body, "source")
    tw, tr = _place(body, "target")
    label = _text(body, "label", required=False, limit=500) or ""
    if (sw, sr) == (tw, tr):
        _fail(400, "A link joins two different places. Pick another room for one end.")
    reply = _tool(ctx, "mempalace_get_taxonomy", {})
    taxonomy = reply.get("taxonomy") if isinstance(reply, dict) else None
    if not isinstance(taxonomy, dict):
        _fail(502, "Memory answered in a form this page does not understand. Nothing changed.")
    for wing, room in ((sw, sr), (tw, tr)):
        if room not in (taxonomy.get(wing) or {}):
            _fail(
                404,
                f"The room {room!r} in {wing!r} does not exist, or your access does not include "
                "it. Nothing was changed.",
            )
    existing = _tool(ctx, "mempalace_list_tunnels", {})
    if not isinstance(existing, list):
        _fail(502, "Memory answered in a form this page does not understand. Nothing changed.")
    wanted = {(sw, sr, None), (tw, tr, None)}
    if any(isinstance(t, dict) and _ends(t) == wanted for t in existing):
        _fail(
            409,
            "These two places are already linked. Creating it again would replace the existing "
            "label. Nothing was changed.",
        )
    effect = {
        "action": "link_add",
        "source": {"wing": sw, "room": sr},
        "target": {"wing": tw, "room": tr},
        "label": label,
    }
    effect_id = _effect_id(effect)
    lines = [
        f"Adds one link between {sw} / {sr} and {tw} / {tr}.",
        "Both places and every stored text in them stay exactly as they are.",
        "You can remove the link again later; that removes only the link.",
    ]
    if not body.get("confirm"):
        return 200, {"preview": {**effect, "lines": lines}, "effect_id": effect_id}
    if body.get("effect_id") != effect_id:
        _fail(
            409,
            "This change no longer matches what was shown. Review it again. Nothing was changed.",
        )
    result = _tool(
        ctx,
        "mempalace_create_tunnel",
        {
            "source_wing": sw,
            "source_room": sr,
            "target_wing": tw,
            "target_room": tr,
            **({"label": label} if label else {}),
        },
    )
    if not isinstance(result, dict) or not result.get("id"):
        _fail(502, "Memory did not confirm the link. Reload to see what is stored.")
    return 201, {"done": True, "action": "link_add", "effect": effect, "link": result}


def link_remove(ctx, body):
    identifier = _text(body, "id", limit=500)
    existing = _tool(ctx, "mempalace_list_tunnels", {})
    if not isinstance(existing, list):
        _fail(502, "Memory answered in a form this page does not understand. Nothing changed.")
    target = next((t for t in existing if isinstance(t, dict) and t.get("id") == identifier), None)
    if target is None:
        _fail(
            404,
            "This link does not exist, or your access does not include it. Nothing was changed.",
        )
    effect = {
        "action": "link_remove",
        "id": identifier,
        "source": target.get("source"),
        "target": target.get("target"),
        "label": target.get("label") or "",
    }
    effect_id = _effect_id(effect)
    # The use count moves whenever the link is crossed, so it is shown but never part of the
    # change a confirm has to match.
    crossings = target.get("access_count") or 0
    ends = [
        f"{e.get('wing')} / {e.get('room')}"
        for e in (target.get("source"), target.get("target"))
        if isinstance(e, dict)
    ]
    lines = [
        f"Removes the link record between {' and '.join(ends)}"
        + (f' (label "{effect["label"]}")' if effect["label"] else "")
        + ".",
        "Both places and every stored text in them stay exactly as they are.",
        f"The record's use count ({crossings}) goes with it; the link can be added "
        "again, starting from zero.",
    ]
    if not body.get("confirm"):
        return 200, {
            "preview": {**effect, "crossings": crossings, "lines": lines},
            "effect_id": effect_id,
        }
    if body.get("effect_id") != effect_id:
        _fail(
            409,
            "This change no longer matches what was shown. Review it again. Nothing was changed.",
        )
    _tool(ctx, "mempalace_delete_tunnel", {"tunnel_id": identifier})
    left = _tool(ctx, "mempalace_list_tunnels", {})
    if isinstance(left, list) and any(
        isinstance(t, dict) and t.get("id") == identifier for t in left
    ):
        _fail(502, "Memory did not confirm the removal. The link is still there.")
    return 200, {"done": True, "action": "link_remove", "effect": effect}


READS = {
    "/api/organise/places": places,
    "/api/organise/facts": facts,
    "/api/organise/sources": sources,
    "/api/organise/source": source,
    "/api/organise/links": links,
}
WRITES = {
    "/api/organise/facts/invalidate": invalidate,
    "/api/organise/facts/supersede": supersede,
    "/api/organise/links/add": link_add,
    "/api/organise/links/remove": link_remove,
}


def install(gateway):
    def serve(path, action, write):
        def handle(h):
            principal = require_principal(gateway, h)
            if principal is None:
                return
            try:
                ctx = ToolContext(principal=principal, gateway=gateway)
                if write:
                    status, body = action(ctx, read_json(h))
                else:
                    status, body = 200, action(ctx, query(h))
                send_json(h, status, body)
            except BadRequest:
                pass
            except _Reply as reply:
                send_json(h, reply.status, reply.body)
            except Exception as exc:
                log_failure(logger, "memory organise request failed", exc)
                send_json(h, 500, {"error": "The request failed, so nothing was changed."})

        gateway.add_route("POST" if write else "GET", path, handle)

    for path, action in READS.items():
        serve(path, action, False)
    for path, action in WRITES.items():
        serve(path, action, True)
