"""The Coworker tools: publish a preview for a card, list a card's previews, stop one."""

from __future__ import annotations

from commonplace.extensions import WORK_PROFILES, LocalTool, ToolError, register

from . import bundle, service, store
from .bundle import Refused
from .store import Forbidden, NotFound

_CARD = {
    "oneOf": [
        {"type": "integer", "minimum": 1},
        {"type": "string", "minLength": 1, "maxLength": 20},
    ],
    "description": "The card number.",
}


def _line(p) -> str:
    where = f" {p['url']}" if p["url"] and p["state"] == "ready" else ""
    return (
        f"{p['id']}  #{p['card']}  {p['name']}  revision {p['revision']}  {p['state']}"
        f" ({p['reason']}): {p['reason_text']}{where}"
    )


def _refused(exc: Refused) -> ToolError:
    text, nxt = store.REASONS[exc.reason]
    extra = " ".join(f"{k}={v}" for k, v in exc.detail.items() if k in ("file", "kind"))
    return ToolError(f"no: {text} [{exc.reason}]{' ' + extra if extra else ''} {nxt}".rstrip())


def _guard(call):
    try:
        return call()
    except NotFound:
        raise ToolError("no: preview or card not found") from None
    except Forbidden:
        raise ToolError("no: you cannot do that to this preview") from None
    except Refused as exc:
        raise _refused(exc) from None
    except ValueError as exc:
        raise ToolError(f"no: {exc}") from None


def preview_publish(ctx, args):
    unknown = set(args) - {"card", "name", "files"}
    if unknown or not {"card", "name", "files"} <= set(args):
        raise ToolError("no: send card, name and files")
    shown, created = _guard(
        lambda: service.publish(
            ctx.gateway, ctx.principal, args["card"], args["name"], args["files"]
        )
    )
    head = "Published" if created else "Already published, unchanged"
    return (
        f"{head}: {shown['name']} for #{shown['card']}, revision {shown['revision']}.\n"
        f"Open it at {shown['url']} (signed-in people who can open the card; never public).\n"
        f"{_line(shown)}"
    )


def preview_list(ctx, args):
    if set(args) - {"card", "pr"}:
        raise ToolError("no: send card or pr")
    if "card" in args and "pr" in args:
        raise ToolError("no: send card or pr, not both")
    found = _guard(
        lambda: service.listing(
            ctx.gateway, ctx.principal, card=args.get("card"), pr=args.get("pr")
        )
    )
    rows = [_line(p) for p in found["previews"]]
    held = found["usage"]
    tail = (
        f"{held['ready']} of {store.MAX_READY} previews open; "
        f"{held['bytes'] // (1024 * 1024)} of {store.MAX_WORKSPACE_BYTES // (1024 * 1024)} MiB used."
    )
    return "\n".join(rows + [tail]) if rows else "No previews.\n" + tail


def preview_stop(ctx, args):
    if set(args) != {"id"}:
        raise ToolError("no: send id")
    shown = _guard(lambda: service.stop(ctx.gateway, ctx.principal, args["id"]))
    return _line(shown)


register(
    LocalTool(
        name="preview_publish",
        description=(
            "Publish a built web bundle as a preview of a card, so people who can open the card "
            "reopen it from the card after your session ends. Send card, a name and files "
            "(each {path, text} or {path, base64}); index.html is required. Up to 5 MiB and 500 "
            "files. Publishing the same name again adds a revision. Only signed-in people who can "
            "open the card can open it; it is never public. Files holding credentials are refused."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "card": _CARD,
                "name": {"type": "string", "minLength": 1, "maxLength": store.MAX_NAME},
                "files": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": bundle.MAX_FILES,
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "minLength": 1, "maxLength": 255},
                            "text": {"type": "string"},
                            "base64": {"type": "string"},
                        },
                        "required": ["path"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["card", "name", "files"],
            "additionalProperties": False,
        },
        handler=preview_publish,
        scope="memory:write",
        kind="write",
        profiles=WORK_PROFILES,
        trusted_only=True,
    )
)
register(
    LocalTool(
        name="preview_list",
        description=(
            "List the previews of a card (or of a pull request URL) that you can see, with state "
            "and the reason for it, and the workspace's preview usage."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "card": _CARD,
                "pr": {"type": "string", "minLength": 1, "maxLength": 500},
            },
            "required": [],
            "additionalProperties": False,
        },
        handler=preview_list,
        scope="memory:read",
        kind="read",
        profiles=WORK_PROFILES,
    )
)
register(
    LocalTool(
        name="preview_stop",
        description=(
            "Stop serving a preview you published. Its files are kept; a person can open it "
            "again. You cannot delete files or change the stop rule."
        ),
        input_schema={
            "type": "object",
            "properties": {"id": {"type": "string", "minLength": 1, "maxLength": 40}},
            "required": ["id"],
            "additionalProperties": False,
        },
        handler=preview_stop,
        scope="memory:write",
        kind="write",
        profiles=WORK_PROFILES,
        trusted_only=True,
    )
)
