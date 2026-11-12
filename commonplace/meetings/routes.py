"""``/api/meetings``: the owner's switches and connections, and each person's own opt-in.

Signed-in people only; an agent token or machine credential is 404, as is any route for a person
who may not use it. While the owner's master switch is off every route except the owner's own
settings answers 404 to a member and 409 ``meetings_disabled`` to the owner, and nothing is
created: reading a workspace that never used meetings opens no file.

    GET   /api/meetings/settings       owner: all switches; member: ``enabled`` and limits only
    PUT   /api/meetings/settings       owner: {enabled?, transcript_ok?, external_model_ok?,
                                       max_concurrent?, max_minutes?, daily_meetings?}
    GET   /api/meetings/overview       owner: counts per state, no text
    GET   /api/meetings/connections    owner: rows without credentials
    POST  /api/meetings/connections    owner: {provider, label, secrets:{name: text}} -> 201
    PATCH /api/meetings/connections/<id>       owner: {enabled?, label?, secrets?}
    DELETE /api/meetings/connections/<id>      owner
    POST  /api/meetings/connections/<id>/test  owner: runs the adapter's test -> {ok, reason, connection}
    GET   /api/meetings/me             own opt-in and defaults
    PATCH /api/meetings/me             {enabled?, keep_transcript?, language?, notes_wing?}
    GET   /api/meetings                own meetings (no text)
    POST  /api/meetings                invite {link, title, attested: true, request_id, connection_id?,
                                       keep_transcript?, board?, disclosure?, start_at?} -> 201, or
                                       200 with the first meeting for a repeated request_id
    GET   /api/meetings/<id>           own meeting (state, times, end reason; never the link)
    POST  /api/meetings/<id>/leave     the inviter's leave: the bot goes, what was captured is kept
    POST  /api/meetings/<id>/stop      owner: the bot goes and everything captured is discarded

An invite stores the meeting and nothing else: the worker joins, announces and listens
(``engine.py``) only after the announcement is delivered. Credentials and the meeting link are
write-only: no route returns them. Every change by the owner or a person is one
audit row: ids and a fixed code, never text.
"""

from __future__ import annotations

import logging
import re
import time

from commonplace import http_util
from commonplace.meetings import adapter, engine, seal, store, worker

PREFIX = "/api/meetings"
logger = logging.getLogger("commonplace.meetings")
AUDIT = "meetings"
MEETING_ID = re.compile(r"[0-9a-f]{32}\Z")


def _send(h, status, body):
    http_util.send_json(h, status, body)


def _error(h, status, code, message):
    _send(h, status, {"error": message, "code": code})


def _audit(gateway, principal, action, decision, reason="", target=()):
    gateway.db.audit(
        action=f"{AUDIT}.{action}",
        decision=decision,
        workspace_id=principal.workspace_id,
        user_id=principal.user_id,
        reason=reason,
        target_ids=target,
    )


def _body(h, allowed):
    """The JSON object, or None after a 400 for a malformed one."""
    try:
        body = http_util.read_json(h)
    except ValueError:
        return None
    if set(body) - allowed:
        raise store.Invalid("invalid_field", "Unknown field")
    return body


def _is_owner(principal) -> bool:
    return principal.role == "owner"


def _off(h, principal):
    """The refusal for a disabled workspace: absent for a member, explained for the owner."""
    if _is_owner(principal):
        _error(h, 409, "meetings_disabled", "Meetings are switched off for this workspace")
    else:
        _error(h, 404, "not_found", "Not found")


def _settings_view(principal, settings) -> dict:
    flags = {"enabled": bool(settings["enabled"])}
    limits = {name: settings[name] for name in store.LIMITS}
    if _is_owner(principal):
        flags.update(
            transcript_ok=bool(settings["transcript_ok"]),
            external_model_ok=bool(settings["external_model_ok"]),
        )
    return {"settings": {**flags, **limits}}


def _put_settings(gateway, h, principal, meetings):
    body = _body(h, store.SETTINGS_KEYS)
    if body is None:
        return
    was = meetings.enabled()
    settings = meetings.put_settings(principal.user_id, body)
    _audit(gateway, principal, "settings", "ok", target=sorted(body))
    if was and not settings["enabled"]:
        _audit(gateway, principal, "disabled", "ok")
    _send(h, 200, _settings_view(principal, settings))


def _connection_create(gateway, h, principal, meetings):
    body = _body(h, {"provider", "label", "secrets"})
    if body is None:
        return
    if set(body) != {"provider", "label", "secrets"}:
        raise store.Invalid("invalid_field", "Provider, label and credentials are required")
    view = meetings.add_connection(
        principal.user_id, body["provider"], body["label"], body["secrets"]
    )
    _audit(gateway, principal, "connection.add", "ok", target=[view["id"]])
    _send(h, 201, {"connection": view})


def _connection_patch(gateway, h, principal, meetings, connection_id):
    body = _body(h, {"enabled", "label", "secrets"})
    if body is None:
        return
    view = meetings.update_connection(connection_id, body)
    action = "connection.rotate" if "secrets" in body else "connection.update"
    _audit(gateway, principal, action, "ok", target=[view["id"]])
    _send(h, 200, {"connection": view})


def _connection_test(gateway, h, principal, meetings, connection_id):
    connection = meetings.open_for_test(connection_id)
    provider = adapter.get(connection.provider)
    if provider is None:
        result = adapter.TestResult(False, "adapter_error")
    else:
        try:
            result = provider.test(connection)
        except Exception as exc:  # the provider's text may echo a credential: type only
            logger.warning("meetings adapter test failed type=%s", type(exc).__name__)
            result = adapter.TestResult(False, "adapter_error")
        if result.ok and not provider.caps.chat_send:
            result = adapter.TestResult(False, "no_chat_send")
    reason = "" if result.ok else (result.reason if result.reason in adapter.REASONS else "failed")
    view = meetings.record_test(connection_id, result.ok)
    _audit(
        gateway,
        principal,
        "connection.test",
        "ok" if result.ok else "refused",
        reason=reason,
        target=[connection_id],
    )
    _send(h, 200, {"ok": result.ok, "reason": reason, "connection": view})


def _owner_stop(gateway, h, principal, meetings, meeting_id):
    """The owner's stop: any meeting in the workspace, never its content (the answer is a state)."""
    if h.command != "POST" or not _is_owner(principal):
        return _error(h, 404, "not_found", "Not found")
    view = meetings.request_end(meeting_id, "stop_requested", time.time())
    _audit(gateway, principal, "stop", "ok", target=[meeting_id])
    return _send(h, 200, {"meeting": {"id": view["id"], "state": view["state"]}})


def _owner_route(gateway, h, principal, meetings, method, parts):
    if parts == ["overview"] and method == "GET":
        return _send(h, 200, {"overview": meetings.overview()})
    if parts == ["connections"] and method == "GET":
        return _send(
            h, 200, {"connections": meetings.connections(), "providers": adapter.providers()}
        )
    if parts == ["connections"] and method == "POST":
        return _connection_create(gateway, h, principal, meetings)
    if len(parts) >= 2 and parts[0] == "connections":
        connection_id = parts[1]
        if len(parts) == 2 and method == "PATCH":
            return _connection_patch(gateway, h, principal, meetings, connection_id)
        if len(parts) == 2 and method == "DELETE":
            meetings.delete_connection(connection_id)
            _audit(gateway, principal, "connection.delete", "ok", target=[connection_id])
            return _send(h, 200, {"ok": True})
        if parts[2:] == ["test"] and method == "POST":
            return _connection_test(gateway, h, principal, meetings, connection_id)
    return _error(h, 404, "not_found", "Not found")


def _person_route(gateway, h, principal, meetings, method, parts):
    if parts == ["me"] and method == "GET":
        settings = meetings.settings()
        return _send(
            h,
            200,
            {
                "me": {
                    **{
                        k: v
                        for k, v in meetings.person(principal.user_id).items()
                        if k != "user_id"
                    },
                    "transcript_allowed": bool(settings["transcript_ok"]),
                }
            },
        )
    if parts == ["me"] and method == "PATCH":
        body = _body(h, store.PERSON_KEYS)
        if body is None:
            return None
        person = meetings.put_person(
            principal.user_id, body, transcript_ok=bool(meetings.settings()["transcript_ok"])
        )
        _audit(gateway, principal, "person", "ok", target=sorted(body))
        return _send(h, 200, {"me": {k: v for k, v in person.items() if k != "user_id"}})
    if parts == [] and method == "GET":
        return _send(h, 200, {"meetings": meetings.meetings_for(principal.user_id)})
    if parts == [] and method == "POST":
        return _invite(gateway, h, principal, meetings)
    if len(parts) in (1, 2) and MEETING_ID.fullmatch(parts[0]):
        if len(parts) == 1 and method == "GET":
            return _send(h, 200, {"meeting": meetings.meeting(parts[0], principal.user_id)})
        if parts[1:] == ["leave"] and method == "POST":
            view = meetings.request_end(parts[0], "inviter_left", time.time(), principal.user_id)
            _audit(gateway, principal, "leave", "ok", target=[parts[0]])
            return _send(h, 200, {"meeting": view})
    return _error(h, 404, "not_found", "Not found")


def _invite(gateway, h, principal, meetings):
    body = _body(h, store.INVITE_KEYS)
    if body is None:
        return None
    refusal = meetings.refusal_for_invite(principal.user_id)
    if refusal is not None:
        _audit(gateway, principal, "invite", "refused", reason=refusal.code)
        return _error(h, refusal.status, refusal.code, refusal.message)
    try:
        name = engine.bot_name(gateway.db.user(principal.user_id)["name"])
        meeting, created = meetings.invite(principal.user_id, body, bot_name=name, now=time.time())
    except (store.Invalid, store.Refused) as exc:
        _audit(gateway, principal, "invite", "refused", reason=exc.code)
        raise
    _audit(gateway, principal, "invite", "ok", target=[meeting["id"]])
    if created:
        worker.of(gateway).kick(principal.workspace_id)
    return _send(h, 201 if created else 200, {"meeting": meeting})


def handle(gateway, h):
    principal = http_util.require_principal(gateway, h)
    if principal is None:
        return None
    if not principal.person:
        return _error(h, 404, "not_found", "Not found")
    parts = http_util.path_parts(h, PREFIX + "/")
    meetings = store.open_meetings(gateway, principal.workspace_id)
    try:
        if parts == ["settings"] and h.command in ("GET", "PUT"):
            if h.command == "PUT":
                if not _is_owner(principal):
                    return _error(h, 404, "not_found", "Not found")
                return _put_settings(gateway, h, principal, meetings)
            settings = meetings.settings()
            if not settings["enabled"] and not _is_owner(principal):
                return _off(h, principal)
            return _send(h, 200, _settings_view(principal, settings))
        if not meetings.enabled():
            return _off(h, principal)
        if len(parts) == 2 and parts[1] == "stop" and MEETING_ID.fullmatch(parts[0]):
            return _owner_stop(gateway, h, principal, meetings, parts[0])
        owner_only = parts[:1] in (["connections"], ["overview"])
        if owner_only:
            if not _is_owner(principal):
                return _error(h, 404, "not_found", "Not found")
            return _owner_route(gateway, h, principal, meetings, h.command, parts)
        return _person_route(gateway, h, principal, meetings, h.command, parts)
    except store.Missing:
        _error(h, 404, "not_found", "Not found")
    except store.Invalid as exc:
        _error(h, 400, exc.code, exc.message)
    except store.Refused as exc:
        _error(h, exc.status, exc.code, exc.message)
    except seal.SealError as exc:
        _error(h, 503, exc.code, "The credential store is not available")
    except TimeoutError:
        _error(h, 429, "busy", "Busy, try again")


def install(gateway) -> None:
    def route(h):
        handle(gateway, h)

    for method in ("GET", "POST"):
        gateway.add_route(method, PREFIX, route)
    for method in ("GET", "POST", "PATCH", "PUT", "DELETE"):
        gateway.add_prefix_route(method, PREFIX + "/", route)
