"""Cookie-person coworker API, with live server authority and no model calls."""

from __future__ import annotations

from urllib.parse import parse_qsl, unquote, urlsplit

from commonplace import sessions
from commonplace.boards.service import BoardService
from commonplace.control_db import ControlError
from commonplace.extensions import ToolContext, ToolError
from commonplace.http_util import read_json, send_json

from .store import StoreError, _id, open_store


class Authority:
    """Reuse current workspace identity/board authority, never client claims."""

    def __init__(self, gateway, workspace_id):
        self.gateway = gateway
        self.workspace_id = workspace_id

    def guard(self):
        return self.gateway.db._lock

    def user(self, user_id, *, owner=False):
        try:
            principal = sessions.person_principal(self.gateway.db, user_id, self.workspace_id)
        except ControlError:
            principal = None
        if principal is None:
            raise StoreError("not_found", 404)
        if owner and principal.role != "owner":
            raise StoreError("owner_required", 403)
        return principal

    def bot(self, bot):
        if bot["connection_id"] is None:
            return
        principal = self.gateway.db.authenticate_connection(bot["connection_id"])
        if principal is None or principal.workspace_id != self.workspace_id:
            raise StoreError("not_found", 404)

    def job(self, job):
        if job["session_hash"] is None:
            return
        # The intake stores only the existing digest, never a bearer/session.
        # Check the exact originating session again at claim/publication time.
        with self.gateway.db._lock:
            row = self.gateway.db._conn.execute(
                "SELECT user_id,workspace_id,expires_at,revoked_at FROM sessions WHERE id_hash=?",
                (job["session_hash"],),
            ).fetchone()
        if (
            row is None
            or row["revoked_at"]
            or row["expires_at"] <= sessions._now().strftime(sessions._FMT)
            or row["user_id"] != job["user_id"]
            or row["workspace_id"] != self.workspace_id
        ):
            raise StoreError("not_found", 404)

    def board(self, user_id, board):
        service = BoardService(ToolContext(principal=self.user(user_id), gateway=self.gateway))
        try:
            service.space_read(board, last=1)
            with service.store.connection() as conn:
                row = service._board(conn, board)
                service._write(row)
                return row["slug"]
        except ToolError:
            raise StoreError("not_found", 404) from None

    def task(self, job, task, board):
        if board is None or isinstance(task, dict) and task["board"] != board:
            raise StoreError("not_found", 404)
        self.board(job["user_id"], board)
        service = BoardService(
            ToolContext(principal=self.user(job["user_id"]), gateway=self.gateway)
        )
        try:
            card = service.task_show(task["number"] if isinstance(task, dict) else task)
        except ToolError:
            raise StoreError("not_found", 404) from None
        if card.get("board") != board:
            raise StoreError("not_found", 404)

    def task_progress(self, job, task, user_id):
        from .task_progress import project

        return project(self, job, task, user_id)


def _person(gateway, h):
    # Do not accept PATs, an agent's cached principal or a request body identity.
    if h.headers.get_all("Authorization", []):
        raise StoreError("person_session_required", 403)
    cookies = h.headers.get_all("Cookie", [])
    if len(cookies) != 1:
        raise StoreError("sign_in", 401)
    session = sessions.cookie_value(cookies[0])
    principal = sessions.session_principal(gateway.db, session) if session else None
    if principal is None or not principal.person or principal.auth != "session":
        raise StoreError("sign_in", 401)
    return principal, session


def _write_boundary(gateway, h):
    from commonplace.gateway import _session_write_allowed

    # Exact, mandatory browser Origin plus non-simple JSON requests are the
    # gateway's CSRF boundary. Missing/null/duplicate/cross-origin headers fail
    # here, including for DELETE. There is no body-supplied authority or token.
    if len(h.headers.get_all("Origin", [])) != 1 or not _session_write_allowed(gateway, h):
        raise StoreError("same_origin_json_required", 403)
    lengths = h.headers.get_all("Content-Length", [])
    if h.headers.get_all("Transfer-Encoding", []) or len(lengths) != 1:
        raise StoreError()
    try:
        length = int(lengths[0])
    except ValueError:
        raise StoreError() from None
    if not 0 <= length <= 65_536:
        raise StoreError("request_too_large", 413)


def _fields(body, allowed, required=()):
    if set(body) - set(allowed) or set(required) - set(body):
        raise StoreError()
    return body


def _body(h):
    try:
        return read_json(h)
    except ValueError:
        # read_json has already replied and marked the connection for close.
        return None


def _numeric(value, *, zero=False):
    if (
        not isinstance(value, str)
        or len(value) > 19
        or not value.isascii()
        or not value.isdecimal()
    ):
        raise StoreError()
    number = int(value)
    if not (0 if zero else 1) <= number < 2**63:
        raise StoreError()
    return number


def _parameters(h, allowed=()):
    pairs = parse_qsl(urlsplit(h.path).query, keep_blank_values=True)
    if len(pairs) != len(dict(pairs)) or set(dict(pairs)) - set(allowed):
        raise StoreError()
    return dict(pairs)


def handle(gateway, h):
    try:
        principal, session = _person(gateway, h)
        write = h.command != "GET"
        if write:
            _write_boundary(gateway, h)
        path = urlsplit(h.path).path
        prefix = "/api/coworkers"
        tail = path[len(prefix) :] if path == prefix or path.startswith(prefix + "/") else None
        if tail is None or tail.endswith("/") or "//" in tail:
            raise StoreError("not_found", 404)
        parts = [unquote(p) for p in tail.split("/") if p]
        if any("/" in p or not p for p in parts):
            raise StoreError("not_found", 404)
        params = _parameters(
            h,
            ("after", "limit") if len(parts) == 3 and parts[2] == "messages" and not write else (),
        )
        store = open_store(gateway, principal.workspace_id)
        if not parts and h.command == "GET":
            return send_json(
                h,
                200,
                {
                    "bots": store.list_bots(),
                    "conversations": store.list_conversations(principal.user_id),
                    "user_id": principal.user_id,
                    "can_manage_bots": principal.role == "owner",
                },
            )
        if parts == ["bots"]:
            if h.command == "GET":
                return send_json(h, 200, {"bots": store.list_bots()})
            if h.command == "POST":
                if principal.role != "owner":
                    raise StoreError("owner_required", 403)
                body = _body(h)
                if body is None:
                    return None
                _fields(body, ("name", "instructions", "connection_id"), ("name",))
                return send_json(h, 201, {"bot": store.create_bot(principal.user_id, **body)})
        if parts == ["conversations"]:
            if h.command == "GET":
                return send_json(
                    h, 200, {"conversations": store.list_conversations(principal.user_id)}
                )
            if h.command == "POST":
                body = _body(h)
                if body is None:
                    return None
                _fields(body, ("kind", "title", "member_ids", "bot_ids", "board"), ("kind",))
                return send_json(
                    h, 201, {"conversation": store.create_conversation(principal.user_id, **body)}
                )
        if len(parts) >= 2 and parts[0] == "conversations":
            return _conversation_route(gateway, h, principal, session, store, parts, params)
        if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "cancel" and h.command == "POST":
            body = _body(h)
            if body is None:
                return None
            _fields(body, ())
            return send_json(
                h,
                200,
                {
                    "job": store.cancel_job(
                        principal.user_id, _numeric(parts[1]), session_hash=sessions._hash(session)
                    )
                },
            )
        raise StoreError("not_found", 404)
    except StoreError as exc:
        send_json(h, exc.status, {"error": exc.code})


def _conversation_route(gateway, h, principal, session, store, parts, params):
    conversation_id = _id(parts[1])
    # Authorize the object before parsing any subordinate body or IDs.
    conversation = store.get_conversation(principal.user_id, conversation_id)
    if len(parts) == 2 and h.command == "GET":
        return send_json(
            h,
            200,
            {
                "conversation": conversation,
                "messages": store.list_messages(principal.user_id, conversation_id),
                "jobs": store.list_jobs(
                    principal.user_id,
                    conversation_id,
                    progress=True,
                    session_hash=sessions._hash(session),
                ),
                "memories": store.list_memory(principal.user_id, conversation_id),
            },
        )
    if len(parts) == 3 and parts[2] == "messages":
        if h.command == "GET":
            after = _numeric(params.get("after", "0"), zero=True)
            limit = _numeric(params.get("limit", "100"))
            return send_json(
                h,
                200,
                {
                    "messages": store.list_messages(
                        principal.user_id, conversation_id, after=after, limit=limit
                    ),
                    "jobs": store.list_jobs(
                        principal.user_id,
                        conversation_id,
                        progress=True,
                        session_hash=sessions._hash(session),
                    ),
                },
            )
        if h.command == "POST":
            body = _body(h)
            if body is None:
                return None
            _fields(body, ("text", "request_id", "bot_id"), ("text", "request_id"))
            result = store.post_message(
                principal.user_id,
                conversation_id,
                **body,
                session_hash=sessions._hash(session),
            )
            worker = getattr(gateway, "coworker_worker", None)
            if result["job"] is not None and worker is not None:
                worker.wake()
            return send_json(h, 201, result)
    if len(parts) == 3 and parts[2] == "memory":
        if h.command == "GET":
            return send_json(
                h, 200, {"memories": store.list_memory(principal.user_id, conversation_id)}
            )
        if h.command == "POST":
            body = _body(h)
            if body is None:
                return None
            _fields(body, ("text",), ("text",))
            return send_json(
                h,
                201,
                {"memory": store.add_memory(principal.user_id, conversation_id, **body)},
            )
    if len(parts) == 4 and parts[2] == "memory" and h.command == "DELETE":
        if int(h.headers.get("Content-Length", "0")):
            body = _body(h)
            if body is None:
                return None
            _fields(body, ())
        return send_json(
            h,
            200,
            store.remove_memory(principal.user_id, conversation_id, _numeric(parts[3])),
        )
    raise StoreError("not_found", 404)


def install(gateway):
    for method in ("GET", "POST", "DELETE"):
        gateway.add_route(method, "/api/coworkers", lambda h: handle(gateway, h))
        gateway.add_prefix_route(method, "/api/coworkers/", lambda h: handle(gateway, h))
