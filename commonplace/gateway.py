"""The gateway: the only way into a workspace's palace.

``POST /mcp`` authenticates the bearer, resolves its connection and policy,
and then, for every JSON-RPC request:

* ``tools/list`` returns only the tools the policy lets this token call;
* ``tools/call`` refuses a hidden tool exactly as an unknown one, checks the
  call's wings against the policy, pins or forces wing arguments where the
  rule says so, and forwards to the workspace's worker with the worker's own
  token and the verified identity, which the worker stamps on every write;
* results that could carry another wing's drawers are checked once more on
  the way out, and anything outside the token's wings is dropped.

Every write and every refusal goes to the audit log (ids only, never text).
"""

from __future__ import annotations

import copy
import http.client
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional
from urllib.parse import urlparse

from mempalace import provenance
from mempalace.config import normalize_wing_name
from mempalace.tool_capabilities import classify_tool

from .auth import PersonalTokenAuthenticator, bearer_from_header
from .control_db import ControlDB, Principal
from .policy import TOOL_RULES, Policy
from .workers import Worker, WorkerSupervisor

logger = logging.getLogger("commonplace.gateway")

MAX_REQUEST_BYTES = 8 * 1024 * 1024
WORKER_TIMEOUT_S = 660.0
MAX_FANOUT_WINGS = 64
_FORWARDED_METHODS = ("initialize", "ping")
_DROP = object()


def _rpc_error(req_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _tool_response(req_id, result: dict, *, is_error: bool = False) -> dict:
    body = {"content": [{"type": "text", "text": json.dumps(result, indent=2, ensure_ascii=False)}]}
    if is_error:
        body["isError"] = True
    return {"jsonrpc": "2.0", "id": req_id, "result": body}


def _tool_result(response: dict):
    """The parsed JSON result inside a tools/call response, or ``None``."""
    try:
        text = response["result"]["content"][0]["text"]
        return json.loads(text)
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _replace_result(response: dict, result) -> dict:
    out = copy.copy(response)
    body = dict(out["result"])
    body["content"] = [{"type": "text", "text": json.dumps(result, indent=2, ensure_ascii=False)}]
    out["result"] = body
    return out


class Denied(Exception):
    """A call the policy refuses; the message goes back to the client."""


class Hidden(Exception):
    """A call on something the token can't see; answered as if it didn't exist."""

    def __init__(self, result: dict):
        super().__init__(result.get("error", ""))
        self.result = result


class WorkerUnavailable(Exception):
    pass


class WorkerClient:
    """Posts JSON-RPC to a worker as the gateway, on behalf of a principal."""

    def __init__(self, supervisor: WorkerSupervisor):
        self.supervisor = supervisor

    def post(self, principal: Principal, request: dict) -> Optional[dict]:
        try:
            worker = self.supervisor.get(principal.workspace_id)
        except Exception as exc:
            logger.error("worker start failed for %s: %s", principal.workspace_id, exc)
            raise WorkerUnavailable() from exc
        return self._post(worker, principal, request)

    def _post(self, worker: Worker, principal: Principal, request: dict) -> Optional[dict]:
        body = json.dumps(request).encode("utf-8")
        conn = http.client.HTTPConnection(worker.host, worker.port, timeout=WORKER_TIMEOUT_S)
        try:
            conn.request(
                "POST",
                "/mcp",
                body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {worker.token}",
                    "Host": worker.host_header,
                    provenance.IDENTITY_HEADER: provenance.encode_identity(principal.identity()),
                },
            )
            resp = conn.getresponse()
            raw = resp.read()
        except OSError as exc:
            raise WorkerUnavailable() from exc
        finally:
            conn.close()
        if resp.status == 202:
            return None
        if resp.status != 200:
            logger.error("worker answered HTTP %s for %s", resp.status, principal.workspace_id)
            raise WorkerUnavailable()
        return json.loads(raw.decode("utf-8"))

    def call_tool(self, principal: Principal, name: str, arguments: dict):
        """Run one tool internally and return its parsed result."""
        response = self.post(
            principal,
            {
                "jsonrpc": "2.0",
                "id": "commonplace-internal",
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
        )
        if not isinstance(response, dict) or "error" in response:
            raise WorkerUnavailable()
        return _tool_result(response)


class Mediator:
    """Applies a principal's policy to one JSON-RPC request."""

    def __init__(self, db: ControlDB, workers: WorkerClient):
        self.db = db
        self.workers = workers

    # ── entry point ──
    def handle(self, principal: Principal, request) -> Optional[dict]:
        if not isinstance(request, dict):
            return _rpc_error(None, -32600, "Batch requests are not supported")
        req_id = request.get("id")
        method = request.get("method")
        if not isinstance(method, str):
            return _rpc_error(req_id, -32600, "Invalid request")
        try:
            if method in _FORWARDED_METHODS or method.startswith("notifications/"):
                return self.workers.post(principal, request)
            if method == "tools/list":
                return self._tools_list(principal, request)
            if method == "tools/call":
                return self._tools_call(principal, request)
        except WorkerUnavailable:
            return _rpc_error(req_id, -32603, "This workspace's memory is unavailable right now")
        if req_id is None:
            return None
        return _rpc_error(req_id, -32601, f"Unknown method: {method}")

    # ── tools/list ──
    def _tools_list(self, principal: Principal, request: dict) -> dict:
        response = self.workers.post(principal, request)
        policy = principal.policy
        try:
            tools = response["result"]["tools"]
        except (KeyError, TypeError):
            return response
        response = copy.copy(response)
        response["result"] = dict(response["result"])
        response["result"]["tools"] = [t for t in tools if policy.tool_visible(t.get("name"))]
        return response

    # ── tools/call ──
    def _tools_call(self, principal: Principal, request: dict) -> dict:
        req_id = request.get("id")
        params = request.get("params")
        params = params if isinstance(params, dict) else {}
        name = params.get("name")
        args = params.get("arguments")
        args = dict(args) if isinstance(args, dict) else {}
        policy = principal.policy
        if not isinstance(name, str):
            return _rpc_error(req_id, -32602, "Invalid params: tool name must be a string")

        refusal = policy.refusal(name)
        if refusal is not None:
            self._audit(principal, name, "deny", refusal)
            # A hidden tool looks exactly like a tool that doesn't exist.
            return _rpc_error(req_id, -32601, f"Unknown tool: {name}")

        rule = TOOL_RULES[name][1]
        notes: list = []
        try:
            _check_argument_types(args)
            plan = getattr(self, f"_before_{rule}")(principal, name, args, notes)
        except Hidden as exc:
            self._audit(
                principal, name, "deny", "hidden drawer", target_ids=_target_ids(args, None)
            )
            return _tool_response(req_id, exc.result)
        except Denied as exc:
            self._audit(principal, name, "deny", str(exc), wing=_wing_of(args))
            return _tool_response(req_id, {"error": str(exc), "denied": True}, is_error=True)

        if isinstance(plan, dict) and "respond" in plan:
            response = _tool_response(req_id, plan["respond"])
        else:
            forwarded = dict(request)
            forwarded["params"] = {**params, "arguments": args}
            response = self.workers.post(principal, forwarded)
            response = self._after(principal, name, rule, args, response)

        if classify_tool(name) != "read":
            result = _tool_result(response) if isinstance(response, dict) else None
            ok = isinstance(result, dict) and not result.get("error") and "error" not in response
            self._audit(
                principal,
                name,
                "ok" if ok else "error",
                "; ".join(notes),
                wing=_wing_of(args),
                target_ids=_target_ids(args, result),
            )
        return response

    def _audit(self, principal, tool, decision, reason="", wing=None, target_ids=()):
        self.db.audit(
            action="tool.call",
            decision=decision,
            workspace_id=principal.workspace_id,
            user_id=principal.user_id,
            connection_id=principal.connection_id,
            auth=principal.auth,
            tool=tool,
            reason=reason,
            wing=wing,
            target_ids=target_ids,
        )

    # ── per-rule checks before the call ──
    def _before_none(self, principal, name, args, notes):
        return None

    def _before_all_wings(self, principal, name, args, notes):
        return None  # Policy.refusal already required every wing.

    def _before_wing_list(self, principal, name, args, notes):
        return None

    def _before_read_wing(self, principal, name, args, notes):
        policy = principal.policy
        if policy.sees_all_wings:
            return None
        wing = args.get("wing")
        if not wing:
            wing = _only_literal_wing(policy)
            if not wing:
                raise Denied(f"pass a wing: this connection reads {_wings_text(policy)}")
            args["wing"] = wing
        if not policy.can_read_wing(wing):
            raise Denied(f"this connection can't read wing {wing!r}")
        return None

    def _before_search(self, principal, name, args, notes):
        policy = principal.policy
        if policy.sees_all_wings:
            return None
        wing = args.get("wing")
        if wing:
            if not policy.can_read_wing(wing):
                raise Denied(f"this connection can't read wing {wing!r}")
            return None
        return {"respond": self._fan_out_search(principal, name, args)}

    def _before_drawer_read(self, principal, name, args, notes):
        return None  # Checked on the way out, so a hidden drawer reads as missing.

    def _before_drawer_write(self, principal, name, args, notes):
        policy = principal.policy
        ids = _drawer_ids(args)
        if ids is None:
            raise Denied("drawer_id or drawer_ids (1 to 500 ids) is required")
        wings = self._drawer_wings(principal, ids)
        for drawer_id in ids:
            wing = wings.get(drawer_id)
            if wing is None:
                continue  # Missing: the worker answers "not found".
            if not policy.can_read_wing(wing):
                raise Hidden({"success": False, "error": f"Drawer not found: {drawer_id}"})
            if not policy.can_write_wing(wing):
                raise Denied(f"this connection can't change memories in wing {wing!r}")
        new_wing = args.get("wing")
        if new_wing and not policy.can_write_wing(new_wing):
            raise Denied(f"this connection can't write to wing {new_wing!r}")
        return None

    def _before_add_drawer(self, principal, name, args, notes):
        self._pin_write_wing(principal.policy, args, args.get("wing"), notes)
        return None

    def _before_diary_write(self, principal, name, args, notes):
        wing = args.get("wing") or _agent_wing(args.get("agent_name"))
        self._pin_write_wing(principal.policy, args, wing, notes)
        return None

    def _before_diary_read(self, principal, name, args, notes):
        policy = principal.policy
        if policy.sees_all_wings:
            return None
        wing = args.get("wing") or _agent_wing(args.get("agent_name"))
        if not wing or not policy.can_read_wing(wing):
            raise Denied(f"this connection can't read wing {wing!r}")
        args["wing"] = wing
        return None

    def _before_tunnel_write(self, principal, name, args, notes):
        policy = principal.policy
        for key in ("source_wing", "target_wing"):
            wing = args.get(key)
            if not wing or not policy.can_write_wing(wing):
                raise Denied(f"this connection can't link wing {wing!r}")
        ids = [args[k] for k in ("source_drawer_id", "target_drawer_id") if args.get(k)]
        wings = self._drawer_wings(principal, ids) if ids else {}
        for drawer_id in ids:
            wing = wings.get(drawer_id)
            if wing is None or not policy.can_read_wing(wing):
                raise Hidden({"success": False, "error": f"Drawer not found: {drawer_id}"})
            if not policy.can_write_wing(wing):
                raise Denied(f"this connection can't link drawers in wing {wing!r}")
        return None

    def _pin_write_wing(self, policy: Policy, args: dict, wing, notes: list) -> None:
        if policy.trust == "untrusted":
            # Untrusted writes always land in the token's write wing (the inbox).
            if not policy.can_write_wing(policy.write_wing):
                raise Denied("this connection can't write")
            if wing and wing != policy.write_wing:
                notes.append(f"wing {wing!r} forced to {policy.write_wing!r}")
            args["wing"] = policy.write_wing
            return
        if not wing:
            wing = policy.write_wing
        if not wing or not policy.can_write_wing(wing):
            raise Denied(f"this connection can't write to wing {wing!r}")
        args["wing"] = wing

    # ── checks on the way out ──
    def _after(self, principal, name, rule, args, response):
        policy = principal.policy
        if not isinstance(response, dict) or "result" not in response or policy.sees_all_wings:
            return response
        if rule not in ("wing_list", "read_wing", "search", "drawer_read", "diary_read"):
            return response
        result = _tool_result(response)
        if result is None:
            return response
        if rule == "wing_list":
            cleaned = _filter_wing_keys(result, policy)
        elif name == "mempalace_get_drawers":
            cleaned = _filter_drawer_batch(result, policy)
        else:
            cleaned = _scrub(result, policy.can_read_wing)
            if cleaned is _DROP:
                missing = args.get("drawer_id") if rule == "drawer_read" else None
                cleaned = (
                    {"error": f"Drawer not found: {missing}"}
                    if missing
                    else {"error": "not found", "denied": True}
                )
        return _replace_result(response, cleaned)

    # ── helpers that ask the worker ──
    def _drawer_wings(self, principal, ids: list) -> dict:
        if not ids:
            return {}
        result = self.workers.call_tool(principal, "mempalace_get_drawers", {"drawer_ids": ids})
        items = result.get("results") if isinstance(result, dict) else None
        if not isinstance(items, list) or len(items) != len(ids):
            raise Denied("could not check which wings these drawers are in")
        # Results come back in input order. Match by position: a chunk id
        # resolves to a payload whose drawer_id is its group's.
        wings = {}
        for drawer_id, item in zip(ids, items):
            if isinstance(item, dict) and not item.get("error"):
                wings[drawer_id] = item.get("wing") or ""
        return wings

    def _fan_out_search(self, principal, name, args) -> dict:
        """One search per readable wing, merged by score.

        Each wing's own top ``limit`` is exact, so the merged top ``limit`` over
        the readable wings is exact too; nothing outside them is ever queried.
        """
        policy = principal.policy
        listing = self.workers.call_tool(principal, "mempalace_list_wings", {}) or {}
        wings = sorted(w for w in (listing.get("wings") or {}) if policy.can_read_wing(w))
        if len(wings) > MAX_FANOUT_WINGS:
            raise Denied(f"pass a wing: this connection reads {len(wings)} wings")
        limit = args.get("limit") if isinstance(args.get("limit"), int) else 5
        merged: list = []
        first: Optional[dict] = None
        seen = set()
        for wing in wings:
            result = self.workers.call_tool(principal, name, {**args, "wing": wing})
            if not isinstance(result, dict):
                continue
            if result.get("error"):
                return result
            if first is None:
                first = result
            for hit in result.get("results") or []:
                key = hit.get("drawer_id") or json.dumps(hit, sort_keys=True)
                if key not in seen:
                    seen.add(key)
                    merged.append(hit)
        merged.sort(key=_score_key)
        merged = [h for h in merged if _scrub(h, policy.can_read_wing) is not _DROP]
        out = dict(first or {"query": args.get("query", "")})
        out["results"] = merged[: max(1, limit)]
        if isinstance(out.get("filters"), dict):
            out["filters"] = {**out["filters"], "wing": None}
        out["wings_searched"] = wings
        return out


# ── pure helpers ──
def _wing_of(args: dict) -> Optional[str]:
    wing = args.get("wing")
    return wing if isinstance(wing, str) and wing else None


def _agent_wing(agent_name) -> str:
    if not isinstance(agent_name, str) or not agent_name.strip():
        return ""
    agent = agent_name.strip().lower()
    return f"wing_{normalize_wing_name(agent) or agent}"


def _only_literal_wing(policy: Policy) -> str:
    literal = [w for w in policy.read_wings if not any(c in w for c in "*?[")]
    return literal[0] if len(literal) == 1 and len(policy.read_wings) == 1 else ""


def _wings_text(policy: Policy) -> str:
    return ", ".join(policy.read_wings) or "no wings"


_WING_ARGS = ("wing", "source_wing", "target_wing", "wing_a", "wing_b")
_ID_ARGS = ("drawer_id", "source_drawer_id", "target_drawer_id")


def _check_argument_types(args: dict) -> None:
    """Wing and drawer arguments must already be what the policy checks.

    The worker coerces arguments (a JSON string becomes an array, a number a
    string), so a value the gateway can't read as-is must never reach it.
    ``drawer_ids`` given as a JSON string is parsed here, once, and the
    parsed list is what gets checked and forwarded.
    """
    for key in _WING_ARGS + _ID_ARGS:
        if key in args and args[key] is not None and not isinstance(args[key], str):
            raise Denied(f"'{key}' must be a string")
    if "drawer_ids" in args:
        ids = args["drawer_ids"]
        if isinstance(ids, str):
            try:
                ids = json.loads(ids)
            except (ValueError, RecursionError):
                ids = None
        if not isinstance(ids, list) or not all(isinstance(i, str) and i for i in ids):
            raise Denied("'drawer_ids' must be a list of drawer id strings")
        args["drawer_ids"] = ids


def _drawer_ids(args: dict) -> Optional[list]:
    if "drawer_ids" in args:
        ids = args["drawer_ids"]
        if isinstance(ids, list) and all(isinstance(i, str) for i in ids) and 0 < len(ids) <= 500:
            return ids
        return None
    drawer_id = args.get("drawer_id")
    return [drawer_id] if isinstance(drawer_id, str) and drawer_id else None


def _target_ids(args: dict, result) -> list:
    ids = []
    for key in ("drawer_id", "tunnel_id", "hallway_id", "event_id", "artifact_id"):
        if isinstance(args.get(key), str):
            ids.append(args[key])
    if isinstance(args.get("drawer_ids"), list):
        ids.extend(i for i in args["drawer_ids"] if isinstance(i, str))
    if isinstance(result, dict):
        for key in ("drawer_id", "entry_id", "triple_id", "tunnel_id", "event_id", "artifact_id"):
            value = result.get(key)
            if isinstance(value, str) and value not in ids:
                ids.append(value)
    return ids


def _score_key(hit) -> tuple:
    if not isinstance(hit, dict):
        return (1, 0.0)
    for key, sign in (("similarity", -1), ("score", -1), ("distance", 1)):
        value = hit.get(key)
        if isinstance(value, (int, float)):
            return (0, sign * float(value))
    return (1, 0.0)


def _scrub(node, can_read: Callable[[str], bool]):
    """``node`` without anything from an unreadable wing.

    A list drops the items that carry one. Any other object that carries one,
    itself or below it, is dropped whole, so the caller fails closed.
    """
    if isinstance(node, dict):
        wing = node.get("wing")
        if isinstance(wing, str) and wing and not can_read(wing):
            return _DROP
        out = {}
        for key, value in node.items():
            cleaned = _scrub(value, can_read)
            if cleaned is _DROP:
                return _DROP
            out[key] = cleaned
        return out
    if isinstance(node, list):
        return [c for c in (_scrub(v, can_read) for v in node) if c is not _DROP]
    return node


def _filter_wing_keys(result, policy: Policy):
    """list_wings / get_taxonomy keyed by wing: keep only the readable wings."""
    if not isinstance(result, dict):
        return {"error": "not found", "denied": True}
    for key in ("wings", "taxonomy"):
        if isinstance(result.get(key), dict):
            return {key: {w: v for w, v in result[key].items() if policy.can_read_wing(w)}}
    return {"error": "not found", "denied": True}


def _filter_drawer_batch(result, policy: Policy):
    if not isinstance(result, dict) or not isinstance(result.get("results"), list):
        return _scrub(result, policy.can_read_wing)
    items = []
    errors = 0
    for item in result["results"]:
        cleaned = _scrub(item, policy.can_read_wing)
        if cleaned is _DROP or (isinstance(cleaned, dict) and cleaned.get("error")):
            drawer_id = item.get("drawer_id") if isinstance(item, dict) else None
            errors += 1
            items.append({"drawer_id": drawer_id, "error": f"Drawer not found: {drawer_id}"})
        else:
            items.append(cleaned)
    return {"results": items, "count": len(items), "errors": errors}


# ── HTTP ──
RouteHandler = Callable[[BaseHTTPRequestHandler], None]


class Gateway:
    """The HTTP front. ``routes`` is the seam OAuth plugs its endpoints into."""

    def __init__(
        self,
        db: ControlDB,
        supervisor: WorkerSupervisor,
        authenticators: Optional[list] = None,
    ):
        self.db = db
        self.supervisor = supervisor
        self.authenticators: list = authenticators or [PersonalTokenAuthenticator(db)]
        self.mediator = Mediator(db, WorkerClient(supervisor))
        self.routes: dict = {}

    def add_route(self, method: str, path: str, handler: RouteHandler) -> None:
        self.routes[(method.upper(), path)] = handler

    def authenticate(self, header: Optional[str]) -> Optional[Principal]:
        bearer = bearer_from_header(header)
        if not bearer:
            return None
        for authenticator in self.authenticators:
            principal = authenticator.authenticate(bearer)
            if principal is not None:
                return principal
        return None

    def build_server(self, host: str, port: int) -> ThreadingHTTPServer:
        gateway = self

        class _Handler(BaseHTTPRequestHandler):
            server_version = "commonplace"
            sys_version = ""
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):
                logger.debug("%s - %s", self.address_string(), fmt % args)

            def _send_json(self, status: int, payload, extra_headers=None) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                for key, value in (extra_headers or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(body)

            def _route(self, method: str) -> bool:
                handler = gateway.routes.get((method, urlparse(self.path).path))
                if handler is None:
                    return False
                handler(self)
                return True

            def do_GET(self):
                path = urlparse(self.path).path
                if self._route("GET"):
                    return
                if path == "/health":
                    self._send_json(200, {"ok": True})
                    return
                if path == "/mcp":
                    self._send_json(405, {"error": "use POST"}, {"Allow": "POST"})
                    return
                self._send_json(404, {"error": "not found"})

            def do_POST(self):
                path = urlparse(self.path).path
                if path != "/mcp":
                    if not self._route("POST"):
                        self._send_json(404, {"error": "not found"})
                    return
                principal = gateway.authenticate(self.headers.get("Authorization"))
                if principal is None:
                    self._send_json(
                        401,
                        {"error": "unauthorized"},
                        {"WWW-Authenticate": 'Bearer realm="commonplace"'},
                    )
                    return
                media = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
                if media != "application/json":
                    self._send_json(415, {"error": "POST /mcp needs application/json"})
                    return
                try:
                    length = int(self.headers.get("Content-Length") or "0")
                except ValueError:
                    length = -1
                if length < 0 or length > MAX_REQUEST_BYTES:
                    self._send_json(413, _rpc_error(None, -32600, "Request too large"))
                    return
                try:
                    request = json.loads(self.rfile.read(length).decode("utf-8"))
                except Exception:
                    self._send_json(400, _rpc_error(None, -32700, "Parse error"))
                    return
                try:
                    response = gateway.mediator.handle(principal, request)
                except Exception:
                    logger.exception("gateway error")
                    req_id = request.get("id") if isinstance(request, dict) else None
                    self._send_json(500, _rpc_error(req_id, -32603, "Internal error"))
                    return
                if response is None:
                    self.send_response(202)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self._send_json(200, response)

        server = ThreadingHTTPServer((host, port), _Handler)
        server.daemon_threads = True
        return server

    def serve(self, host: str, port: int, idle_check_s: float = 60.0) -> None:
        server = self.build_server(host, port)
        stop = threading.Event()

        def reap():
            while not stop.wait(idle_check_s):
                self.supervisor.stop_idle()

        threading.Thread(target=reap, daemon=True).start()
        logger.info("commonplace gateway on %s:%s", host, server.server_address[1])
        try:
            server.serve_forever()
        finally:
            stop.set()
            server.server_close()
            self.supervisor.stop_all()
