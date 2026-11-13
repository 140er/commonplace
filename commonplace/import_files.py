"""Session-only file previews and consent using the existing import authority."""

from __future__ import annotations

import hashlib
import logging

from . import import_grant, imports, sync_protocol
from .handoff.api import _RequestError, _text
from .http_util import log_failure
from .import_sources import SOURCES, parse_import

MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_BODY_BYTES = 6 * MAX_FILE_BYTES + 4096
FIELDS = {"filename", "source", "format", "transcript", "private", "preview", "consent"}


def prepare(principal, data, path):
    filename = _text(data, "filename", required=True)
    if (
        len(filename) > 255
        or any(character in filename for character in ("/", "\\", ":", "\x00"))
        or any(ord(character) < 32 for character in filename)
        or filename in (".", "..")
    ):
        raise _RequestError(400, "Choose a file name without a path")
    source = _text(data, "source", required=True)
    fmt = _text(data, "format", "auto")
    memory_file = source == sync_protocol.MEMORY_IMPORT_SOURCE
    if source not in (*SOURCES, sync_protocol.MEMORY_IMPORT_SOURCE):
        raise _RequestError(400, "Unsupported source")
    if memory_file and path != "grant":
        raise _RequestError(
            403, "Memory text files require your private imported history destination"
        )
    extensions = (".md", ".txt") if memory_file else (".json", ".jsonl", ".ndjson")
    if not filename.lower().endswith(extensions):
        raise _RequestError(415, "Choose a file matching the selected export format")
    transcript = _text(data, "transcript")
    raw = transcript.encode("utf-8")
    if len(raw) > MAX_FILE_BYTES:
        raise _RequestError(413, "Choose a file of 4 MiB or less")
    if not transcript.strip() or "\x00" in transcript:
        raise _RequestError(400, "Choose a nonempty UTF-8 text export")
    try:
        messages = parse_import(source, transcript, fmt)
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise _RequestError(400, "This file does not match the selected export format") from None
    # The name is display-only. Renaming a file cannot create another import.
    identity = hashlib.sha256(raw).hexdigest()
    if memory_file:
        identity = sync_protocol.memory_thread_id("file", "content", identity)
    item = {"thread_id": identity, "transcript": transcript, "format": fmt}
    scope = "private" if path == "grant" else "threads"
    digest = import_grant.request_digest(
        "file-preview",
        {
            "user": principal.user_id,
            "workspace": principal.workspace_id,
            "scope": scope,
            "source": source,
            "item": item,
        },
    )
    preview = {
        "source": source,
        "scope": scope,
        "total": 1,
        "messages": len(messages) - 1,
        "source_bytes": len(raw),
        "preview": digest,
    }
    return source, item, preview


def register(ctx):
    def handle(handler, *, confirm=False):
        handler._handoff_body_read = False
        try:
            principal = ctx.gateway.principal_for(handler)
            if principal is None:
                raise _RequestError(401, "Sign in")
            if not principal.person or principal.auth != "session":
                raise _RequestError(403, "Use your signed-in session to choose a file")
            data, path = imports._import_envelope(
                ctx, principal, handler, allowed=FIELDS, limit=MAX_BODY_BYTES
            )
            source, item, preview = prepare(principal, data, path)
            if confirm:
                if data.get("consent") is not True or data.get("preview") != preview["preview"]:
                    raise _RequestError(409, "Preview this file and confirm its destination first")
                if path == "grant":
                    wait = imports.grant_admit(
                        ctx,
                        principal,
                        preview["preview"],
                        ride=source
                        in (*sync_protocol.TRANSCRIPT_SOURCES, sync_protocol.MEMORY_IMPORT_SOURCE),
                    )
                    if wait > 0:
                        raise _RequestError(429, f"Import not due; retry in {wait} seconds")
                result = ctx.import_batch(principal, source, [item], path=path)
            else:
                # No staging files, import rows, cadence admission or memory writes.
                result = preview
            status = 200
        except _RequestError as error:
            status, result = error.status, {"error": error.message}
        except Exception as error:
            log_failure(logging.getLogger(__name__), "File import failed", error)
            status, result = 500, {"error": "File import unavailable; retry safely"}
        handler.close_connection = True
        handler._send_json(status, result, {"Connection": "close"})

    ctx.gateway.add_route("POST", "/api/memory/import/preview", handle)
    ctx.gateway.add_route("POST", "/api/memory/import/confirm", lambda h: handle(h, confirm=True))
