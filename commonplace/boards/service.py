"""One authorization and move-rule implementation for MCP and HTTP."""

from __future__ import annotations

import json
import logging
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from commonplace import events, privacy
from commonplace.extensions import ToolError
from commonplace.http_util import log_failure

from .display import stale_before
from .store import (
    STATUSES,
    BoardStore,
    append_event,
    card,
    expire_claims,
    now,
    require_visible,
    task_row,
)

logger = logging.getLogger(__name__)


def open_store(workspace_dir) -> BoardStore:
    return BoardStore(workspace_dir)


class VersionClash(ToolError):
    def __init__(self, current):
        super().__init__("no: card changed")
        self.current = current


def validate_text(value, name, *, required=False, limit=100_000):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ToolError(f"no: {name} must be {'nonempty ' if required else ''}text")
    return value


def choices(value):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 6:
        raise ToolError("no: options must contain up to six choices")
    return [validate_text(v, "option", required=True, limit=120) for v in value]


def flags_for(*values):
    content = "\n".join(v for v in values if isinstance(v, str))
    patterns = {
        "ignore_previous": r"ignore\s+(?:all\s+)?previous",
        "tool_syntax": r"<tool_call|\btools/call\b|<function=",
        "curl_pipe": r"\b(?:curl|wget)\b[^\n]*\|\s*(?:ba)?sh\b",
        "long_base64": r"[A-Za-z0-9+/]{200,}={0,2}",
        "hidden_unicode": r"[\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff]",
        "image_query": r"https?://[^\s]+\.(?:png|jpg|jpeg|gif|svg|webp)\?",
    }
    return sorted(
        {name for name, pattern in patterns.items() if re.search(pattern, content, re.I)}
        | set(privacy.scan(content))
    )


def normalize_repo(value):
    value = validate_text(value, "repo", required=True, limit=1000).strip()
    if value.startswith("git@"):
        value = value[4:].replace(":", "/", 1)
    elif "://" in value:
        parsed = urlparse(value)
        value = (parsed.hostname or "") + parsed.path
    return value.rstrip("/").removesuffix(".git").lower()


def _restricted(principal):
    return principal.policy.trust == "untrusted" or principal.policy.write_wing in (
        "inbox",
        "wing_inbox",
    )


def _owner(principal):
    if principal.role != "owner":
        raise ToolError("no: only a workspace owner can do that")


def needs_me(store, principal) -> dict:
    """Owner's visible decision queue. One card can belong to several buckets."""
    _owner(principal)
    result = {
        key: [] for key in ("asks", "done_review", "backlog_from_agents", "ready_to_merge", "stale")
    }
    cutoff = stale_before()
    with store.connection() as conn:
        rows = conn.execute(
            "SELECT t.*,b.slug AS board,b.wing AS board_wing FROM tasks t "
            "JOIN boards b ON b.id=t.board_id ORDER BY t.created_at,t.id"
        ).fetchall()
        for row in rows:
            if not principal.policy.can_read_wing(row["board_wing"]):
                continue
            item = card(conn, row, principal)
            addressed = row["ask_to"] in (None, f"user:{principal.user_id}")
            if row["status"] == "review" and addressed:
                result["done_review" if row["ask"] == "Done?" else "asks"].append(item)
            if row["status"] == "backlog" and row["created_by"].startswith("conn:"):
                result["backlog_from_agents"].append(item)
            if row["pr_url"] and row["checks"] == "passing" and row["status"] != "done":
                result["ready_to_merge"].append(item)
            if row["status"] == "doing" and row["assignee"].startswith("conn:"):
                if (row["heartbeat_at"] or row["updated_at"]) < cutoff:
                    result["stale"].append(item)
    result["count"] = len({c["number"] for bucket in result.values() for c in bucket})
    return result


class BoardService:
    def __init__(self, ctx):
        self.ctx = ctx
        self.principal = ctx.principal
        self.store = open_store(ctx.workspace_dir())
        self.pending = []

    @contextmanager
    def transaction(self):
        self.pending = []
        conflict = None
        with self.store.transaction() as conn:
            try:
                yield conn
            except VersionClash as exc:
                # System expiries precede the version check; publish that current card.
                conflict = exc
        for kind, subject, data, actor in self.pending:
            try:
                events.emit(
                    self.ctx.db,
                    self.principal.workspace_id,
                    kind,
                    subject=subject,
                    actor=actor,
                    data=data,
                )
            except Exception as exc:
                log_failure(logger, "committed board event emission failed", exc)
        if conflict is not None:
            raise conflict

    def _emit(self, conn, number, kind, *, actor=None, **extra):
        row = task_row(conn, number)
        actor = actor or self.principal.actor
        data = {
            "board": row["board"],
            "number": number,
            "title": row["title"],
            "status": row["status"],
            "assignee": row["assignee"],
            "ask_to": row["ask_to"],
            "actor": actor,
            **extra,
        }
        self.pending.append((kind, f"#{number}", data, actor))

    def _log(self, conn, number, kind, **fields):
        append_event(
            conn,
            number,
            kind,
            self.principal.actor,
            trust=self.principal.policy.trust,
            user_id=self.principal.user_id,
            **fields,
        )

    def _write(self, board, *, submission=False):
        p = self.principal
        if not p.policy.has_scope("memory:write"):
            raise ToolError("no: this connection cannot change this board")
        if _restricted(p):
            if submission:
                return
            raise ToolError("no: this connection can only add cards and comments")
        if not p.policy.can_write_wing(board["wing"]):
            raise ToolError("no: this connection cannot change this board")

    def _visible_boards(self, conn):
        return [
            r
            for r in conn.execute("SELECT * FROM boards WHERE archived=0 ORDER BY id")
            if self.principal.policy.can_read_wing(r["wing"])
        ]

    def _board(self, conn, ref=None, *, create=False):
        if ref is None:
            visible = self._visible_boards(conn)
            if len(visible) == 1:
                return visible[0]
            if visible:
                raise ToolError("no: choose a board")
            ref = "commonplace"
        validate_text(ref, "board", required=True, limit=1000)
        row = conn.execute("SELECT * FROM boards WHERE slug=?", (ref,)).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT b.* FROM boards b JOIN board_repos r ON r.board_id=b.id WHERE r.repo=?",
                (normalize_repo(ref),),
            ).fetchone()
        if row is not None:
            if row["archived"] or not self.principal.policy.can_read_wing(row["wing"]):
                raise ToolError("no: board not found")
            return row
        if create and self.principal.policy.trust == "trusted" and not _restricted(self.principal):
            return self._add_board(conn, slug=ref)
        raise ToolError("no: board not found")

    def _add_board(self, conn, *, slug, name=None, wing=None, repo=None):
        p = self.principal
        if _restricted(p) or (not p.person and p.role != "owner" and p.policy.trust != "trusted"):
            raise ToolError("no: only an owner or trusted connection can create a board")
        slug = validate_text(slug, "slug", required=True, limit=100)
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", slug):
            raise ToolError("no: slug must use lowercase letters, numbers, underscores or hyphens")
        existing = conn.execute("SELECT * FROM boards WHERE slug=?", (slug,)).fetchone()
        if existing is not None and not p.policy.can_read_wing(existing["wing"]):
            raise ToolError("no: board not found")
        wing = validate_text(
            wing if wing is not None else f"project_{slug}", "theme", required=True, limit=200
        )
        if not p.policy.can_read_wing(wing) or not p.policy.can_write_wing(wing):
            raise ToolError("no: this connection cannot create this board")
        if existing is not None:
            raise ToolError("no: board already exists")
        name = validate_text(name if name is not None else slug, "name", required=True, limit=1000)
        normalized_repo = normalize_repo(repo) if repo is not None else None
        if (
            normalized_repo
            and conn.execute(
                "SELECT 1 FROM board_repos WHERE repo=?", (normalized_repo,)
            ).fetchone()
        ):
            raise ToolError("no: repo is already assigned")
        cur = conn.execute(
            "INSERT INTO boards(slug,name,wing,stamp_user,stamp_connection,stamp_trust,created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                slug,
                name,
                wing,
                p.user_id,
                None if p.person else p.connection,
                p.policy.trust,
                now(),
            ),
        )
        if normalized_repo:
            conn.execute(
                "INSERT INTO board_repos(repo,board_id) VALUES (?,?)",
                (normalized_repo, cur.lastrowid),
            )
        return conn.execute("SELECT * FROM boards WHERE id=?", (cur.lastrowid,)).fetchone()

    def _expire(self, conn, board):
        for row in expire_claims(conn, board["id"]):
            self._emit(
                conn,
                row["id"],
                "card.expired",
                actor="system",
                **{"from": "doing", "to": "todo", "target": row["assignee"]},
            )

    def _get(self, conn, ref):
        row = task_row(conn, ref)
        require_visible(row, self.principal)
        board = conn.execute("SELECT * FROM boards WHERE id=?", (row["board_id"],)).fetchone()
        self._expire(conn, board)
        return task_row(conn, row["id"]), board

    def _version(self, conn, row, version):
        if version is not None:
            if not isinstance(version, int) or isinstance(version, bool):
                raise ToolError("no: version must be an integer")
            if version != row["version"]:
                raise VersionClash(card(conn, row, self.principal))

    def _owner_actor(self):
        for member in self.ctx.db.members(self.principal.workspace_id):
            if member["role"] == "owner":
                return f"user:{member['id']}"
        raise ToolError("no: workspace owner not found")

    def _actor(self, value):
        value = validate_text(value, "recipient", required=True, limit=200)
        if value == "anyone":
            return None
        if value.casefold() == "owner":
            return self._owner_actor()
        members = self.ctx.db.members(self.principal.workspace_id)
        matches = [
            m
            for m in members
            if value.casefold()
            in (
                f"user:{m['id']}".casefold(),
                m["email"].casefold(),
                m["email"].split("@", 1)[0].casefold(),
                (self.ctx.db.user(m["id"])["name"] or "").casefold() or None,
            )
        ]
        if len(matches) == 1:
            return f"user:{matches[0]['id']}"
        if matches or value.startswith("user:") or "@" in value:
            raise ToolError("no: recipient is not a unique workspace member")
        if value.startswith("conn:"):
            value = value[5:]
        if not re.fullmatch(r"[A-Za-z0-9_.:@-]{1,120}", value):
            raise ToolError("no: invalid connection")
        return f"conn:{value}"

    def _until(self, board):
        return (datetime.now(timezone.utc) + timedelta(minutes=board["claim_minutes"])).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

    def _set(self, conn, row, updates):
        updates.update(
            version=row["version"] + 1, updated_at=now(), updated_by=self.principal.actor
        )
        if (
            row["status"] == "doing"
            and row["assignee"] == self.principal.actor
            and "claim_until" not in updates
            and not self.principal.person
        ):
            updates["claim_until"] = self._until(row)
        fields = ",".join(f"{key}=?" for key in updates)
        conn.execute(f"UPDATE tasks SET {fields} WHERE id=?", (*updates.values(), row["id"]))

    def _holder(self, row, *, editing=False):
        if self.principal.person:
            return
        if row["status"] == "done":
            raise ToolError("no: completed cards are frozen for connections")
        if (
            row["status"] == "doing"
            and not self.principal.person
            and row["assignee"] != self.principal.actor
        ):
            raise ToolError(
                f"no: #{row['id']} is held by {row['assignee']} until {row['claim_until']}"
            )
        if (
            row["status"] == "todo"
            and row["assignee"] not in (None, self.principal.actor)
            and not self.principal.person
        ):
            raise ToolError("no: card is reserved for another connection")
        if editing and not (
            (row["status"] == "doing" and row["assignee"] == self.principal.actor)
            or (
                row["status"] in ("todo", "backlog")
                and row["created_by"] == self.principal.actor
                and row["assignee"] is None
            )
        ):
            raise ToolError("no: take this card before editing it")

    def board_list(self):
        with self.transaction() as conn:
            result = []
            for board in self._visible_boards(conn):
                self._expire(conn, board)
                counts = dict.fromkeys(STATUSES, 0)
                counts.update(
                    {
                        r["status"]: r["n"]
                        for r in conn.execute(
                            "SELECT status,count(*) n FROM tasks WHERE board_id=? GROUP BY status",
                            (board["id"],),
                        )
                    }
                )
                result.append(
                    {
                        "slug": board["slug"],
                        "name": board["name"],
                        "wing": board["wing"],
                        "coordinator": board["coordinator"],
                        "can_configure": self.principal.role == "owner",
                        "counts": counts,
                    }
                )
            return result

    def board_add(self, **args):
        with self.transaction() as conn:
            board = self._add_board(conn, **args)
            return {
                "slug": board["slug"],
                "name": board["name"],
                "wing": board["wing"],
                "coordinator": board["coordinator"],
                "counts": dict.fromkeys(STATUSES, 0),
            }

    def board_set(self, board, coordinator):
        _owner(self.principal)
        with self.transaction() as conn:
            b = self._board(conn, board)
            self._write(b)
            actor = None
            if coordinator is not None:
                actor = self._actor(coordinator)
                candidates = [
                    self.ctx.db.authenticate_connection(c["id"])
                    for c in self.ctx.db.connections(self.principal.workspace_id)
                    if c["auth"] == "pat"
                ]
                if not any(
                    p is not None
                    and p.actor == actor
                    and not _restricted(p)
                    and p.policy.tool_visible("space_read")
                    and p.policy.tool_visible("space_post")
                    and p.policy.can_read_wing(b["wing"])
                    and p.policy.can_write_wing(b["wing"])
                    for p in candidates
                ):
                    raise ToolError(
                        "no: coordinator must be an active agent with access to this board"
                    )
            conn.execute("UPDATE boards SET coordinator=? WHERE id=?", (actor, b["id"]))
            self.pending.append(
                (
                    "space_configured",
                    f"space:{b['slug']}",
                    {"board": b["slug"]},
                    self.principal.actor,
                )
            )
            return {"board": b["slug"], "coordinator": actor}

    @staticmethod
    def _message(row):
        return {
            "id": row["id"],
            "text": row["text"],
            "actor": f"conn:{row['stamp_connection']}"
            if row["stamp_connection"] is not None
            else f"user:{row['stamp_user']}",
            "stamp_user": row["stamp_user"],
            "stamp_connection": row["stamp_connection"],
            "stamp_trust": row["stamp_trust"],
            "flags": json.loads(row["flags"]),
            "at": row["created_at"],
        }

    def space_read(self, board, after=0):
        if not self.principal.policy.has_scope("memory:read"):
            raise ToolError("no: this connection cannot read this board")
        if isinstance(after, bool) or not isinstance(after, int) or not 0 <= after < 2**63:
            raise ToolError("no: after must be a nonnegative message cursor")
        with self.store.connection() as conn:
            b = self._board(conn, board)
            messages = [
                self._message(row)
                for row in conn.execute(
                    "SELECT * FROM space_messages WHERE board_id=? AND id>? ORDER BY id LIMIT 100",
                    (b["id"], after),
                )
            ]
            return {
                "board": b["slug"],
                "coordinator": b["coordinator"],
                "messages": messages,
                "cursor": messages[-1]["id"] if messages else after,
            }

    def space_post(self, board, text):
        text = validate_text(text, "text", required=True)
        p = self.principal
        with self.transaction() as conn:
            b = self._board(conn, board)
            self._write(b)
            cur = conn.execute(
                "INSERT INTO space_messages(board_id,text,stamp_user,stamp_connection,"
                "stamp_trust,flags,created_at) VALUES (?,?,?,?,?,?,?)",
                (
                    b["id"],
                    text,
                    p.user_id,
                    None if p.person else p.connection,
                    p.policy.trust,
                    json.dumps(flags_for(text)),
                    now(),
                ),
            )
            row = conn.execute(
                "SELECT * FROM space_messages WHERE id=?", (cur.lastrowid,)
            ).fetchone()
            self.pending.append(
                (
                    "space_message" if p.person else "space_replied",
                    f"space:{b['slug']}",
                    {"board": b["slug"], "message_id": row["id"], "coordinator": b["coordinator"]},
                    p.actor,
                )
            )
            return self._message(row)

    def task_list(self, board=None, status=None, mine=False, *, all_statuses=False):
        with self.transaction() as conn:
            boards = [self._board(conn, board)] if board is not None else self._visible_boards(conn)
            if status is not None and status not in STATUSES:
                raise ToolError("no: invalid status")
            result = []
            for b in boards:
                self._expire(conn, b)
                for row in conn.execute(
                    "SELECT id FROM tasks WHERE board_id=? ORDER BY position,id", (b["id"],)
                ):
                    task = task_row(conn, row["id"])
                    if task["status"] not in (
                        (status,)
                        if status
                        else STATUSES
                        if all_statuses
                        else ("todo", "doing", "review")
                    ):
                        continue
                    if mine and self.principal.actor not in (
                        task["assignee"],
                        task["return_to"],
                        task["ask_to"],
                    ):
                        continue
                    result.append(card(conn, task, self.principal))
            return result

    def task_show(self, task):
        with self.transaction() as conn:
            row, _board = self._get(conn, task)
            result = card(conn, row, self.principal)
            hidden = self.store.hidden_link_targets(conn, row["id"], self.principal)
            result["events"] = [
                {
                    "kind": r["kind"],
                    "actor": f"conn:{r['stamp_connection']}"
                    if r["stamp_connection"]
                    else r["stamp_user"]
                    if r["stamp_user"].startswith(("user:", "system"))
                    else f"user:{r['stamp_user']}",
                    "text": r["text"],
                    "at": r["created_at"],
                    "choice": r["choice"],
                    "target": r["target"],
                    # Events that opened an ask carry the choices offered, verbatim.
                    **(
                        {"options": json.loads(r["options"] or "[]")}
                        if r["to_status"] == "review" and r["kind"] in ("ask", "handoff", "move")
                        else {}
                    ),
                }
                for r in conn.execute(
                    "SELECT * FROM task_events WHERE task_id=? ORDER BY id", (row["id"],)
                )
                if not (r["kind"] in ("link", "unlink") and r["target"] in hidden)
            ]
            result["linked"] = self.store.links(conn, row["id"], self.principal)
            return result

    def _new_card(
        self,
        conn,
        board,
        *,
        title,
        body="",
        status="todo",
        assignee=None,
        labels=None,
        ask=None,
        options=None,
        target=None,
        message_id=None,
    ):
        p = self.principal
        if message_id is not None:
            if (
                isinstance(message_id, bool)
                or not isinstance(message_id, int)
                or not 1 <= message_id < 2**63
                or not conn.execute(
                    "SELECT 1 FROM space_messages WHERE id=? AND board_id=?",
                    (message_id, board["id"]),
                ).fetchone()
            ):
                raise ToolError("no: message not found in this board")
        title = validate_text(title, "title", required=True, limit=5000)
        body = validate_text(body, "body")
        if labels is None:
            labels = []
        if not isinstance(labels, list) or len(labels) > 100:
            raise ToolError("no: labels must be a list")
        labels = [validate_text(label, "label", required=True, limit=200) for label in labels]
        if status not in ("todo", "backlog", "review") or (status == "review" and ask is None):
            raise ToolError("no: new cards must start in todo or backlog")
        if _restricted(p):
            status, assignee = "backlog", None
        elif assignee is not None:
            assignee = self._actor(assignee)
        position = conn.execute(
            "SELECT coalesce(max(position),0)+1 FROM tasks WHERE board_id=? AND status=?",
            (board["id"], status),
        ).fetchone()[0]
        repo = conn.execute(
            "SELECT repo FROM board_repos WHERE board_id=? ORDER BY repo LIMIT 1", (board["id"],)
        ).fetchone()
        at = now()
        cur = conn.execute(
            "INSERT INTO tasks(board_id,title,body,status,position,assignee,ask,ask_options,ask_to,return_to,flags,"
            "stamp_user,stamp_connection,stamp_trust,created_at,updated_at,updated_by,created_by,repo,labels,checks,message_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                board["id"],
                title,
                body,
                status,
                position,
                assignee,
                ask,
                json.dumps(options or []),
                target,
                p.actor if ask else None,
                json.dumps(flags_for(title, body, ask)),
                p.user_id,
                None if p.person else p.connection,
                p.policy.trust,
                at,
                at,
                p.actor,
                p.actor,
                repo[0] if repo else None,
                json.dumps(labels),
                "",
                message_id,
            ),
        )
        number = cur.lastrowid
        self._log(conn, number, "add", to_status=status)
        self._emit(conn, number, "card.created")
        if status == "review":
            self._log(
                conn,
                number,
                "ask",
                text=ask,
                target=target,
                to_status="review",
                options=options or [],
            )
            self._emit(conn, number, "card.review_needed", return_to=p.actor)
        return card(conn, task_row(conn, number), self.principal)

    def task_add(
        self, title, body="", board=None, status="todo", assignee=None, labels=None, message_id=None
    ):
        with self.transaction() as conn:
            b = self._board(conn, board, create=True)
            self._write(b, submission=True)
            self._expire(conn, b)
            return self._new_card(
                conn,
                b,
                title=title,
                body=body,
                status="backlog" if _restricted(self.principal) else status,
                assignee=assignee,
                labels=labels,
                message_id=message_id,
            )

    def task_comment(self, task, text, version=None):
        with self.transaction() as conn:
            row, board = self._get(conn, task)
            self._write(board, submission=True)
            self._version(conn, row, version)
            value = validate_text(text, "comment", required=True)
            flags = sorted(set(json.loads(row["flags"] or "[]")) | set(flags_for(value)))
            self._set(conn, row, {"flags": json.dumps(flags)})
            self._log(conn, row["id"], "comment", text=value, flags=flags_for(value))
            self._emit(conn, row["id"], "card.commented")
            return card(conn, task_row(conn, row["id"]), self.principal)

    def task_take_next(self, board=None, task=None):
        with self.transaction() as conn:
            if task is not None:
                row, b = self._get(conn, task)
                if board is not None and self._board(conn, board)["id"] != b["id"]:
                    raise ToolError("no: card not found")
            else:
                b = self._board(conn, board)
                self._expire(conn, b)
                row = None
            self._write(b)
            if task is None:
                found = conn.execute(
                    "SELECT id FROM tasks WHERE board_id=? AND status='todo' AND (assignee IS NULL OR assignee=?) "
                    "ORDER BY assignee IS NULL,position,id LIMIT 1",
                    (b["id"], self.principal.actor),
                ).fetchone()
                if found is None:
                    return None
                row = task_row(conn, found["id"])
            if row["status"] != "todo" or row["assignee"] not in (None, self.principal.actor):
                raise ToolError("no: card is not available to take")
            self._set(
                conn,
                row,
                {
                    "status": "doing",
                    "assignee": self.principal.actor,
                    "claim_until": None if self.principal.person else self._until(b),
                },
            )
            self._log(conn, row["id"], "claim", from_status="todo", to_status="doing")
            self._emit(conn, row["id"], "card.claimed", **{"from": "todo", "to": "doing"})
            result = card(conn, task_row(conn, row["id"]), self.principal)
            decision = conn.execute(
                "SELECT * FROM task_events WHERE task_id=? AND kind='decide' ORDER BY id DESC LIMIT 1",
                (row["id"],),
            ).fetchone()
            if row["return_to"] == self.principal.actor and decision:
                actor = (
                    f"conn:{decision['stamp_connection']}"
                    if decision["stamp_connection"]
                    else f"user:{decision['stamp_user']}"
                )
                result["decision"] = {
                    "actor": actor,
                    "choice": decision["choice"],
                    "reply": decision["text"],
                }
                # Clear the delivery marker in this same versioned mutation.
                conn.execute("UPDATE tasks SET return_to=NULL WHERE id=?", (row["id"],))
                result["return_to"] = None
            return result

    def _position(self, conn, row, updates, before=None, after=None):
        if before is None and after is None:
            return
        if before is not None and after is not None:
            raise ToolError("no: use before or after")
        neighbor, _board = self._get(conn, before if before is not None else after)
        lane = updates.get("status", row["status"])
        if (
            neighbor["board_id"] != row["board_id"]
            or neighbor["status"] != lane
            or neighbor["id"] == row["id"]
        ):
            raise ToolError("no: position target must be another card in this column")
        comparison, ordering = ("<", "DESC") if before is not None else (">", "ASC")
        other = conn.execute(
            f"SELECT position FROM tasks WHERE board_id=? AND status=? AND id!=? "
            f"AND position {comparison} ? ORDER BY position {ordering} LIMIT 1",
            (row["board_id"], lane, row["id"], neighbor["position"]),
        ).fetchone()
        boundary = other[0] if other else neighbor["position"] + (-2 if before is not None else 2)
        updates["position"] = (boundary + neighbor["position"]) / 2

    def _transition(self, row, status):
        if status not in STATUSES:
            raise ToolError("no: invalid status")
        old = row["status"]
        if status == old:
            return
        if old == "review":
            raise ToolError("no: use decide to move a card from review")
        if self.principal.person:
            allowed = {
                ("backlog", "todo"),
                ("todo", "doing"),
                ("doing", "todo"),
                ("doing", "review"),
                ("doing", "done"),
                ("todo", "review"),
                ("done", "todo"),
            }
            if status == "backlog" or (old, status) in allowed:
                return
        else:
            if old == "doing" and status in ("done", "backlog"):
                return
            if old == "todo" and status == "backlog" and row["assignee"] is None:
                return
        raise ToolError("no: this move requires a person or a handoff")

    def _memory_theme(self, memory_id):
        """The memory's theme and first line, read through the caller's own
        policy: a memory the caller can't read can't be linked."""
        from commonplace.policy import canonical_wing

        result = self.ctx.memory("mempalace_get_drawer", {"drawer_id": memory_id})
        metadata = (result.get("metadata") or {}) if isinstance(result, dict) else {}
        wing = (result.get("wing") if isinstance(result, dict) else None) or metadata.get("wing")
        if not wing:
            raise ToolError("no: memory not found")
        text = result.get("content") or ""
        return canonical_wing(wing), next(iter(str(text).splitlines()), "")

    def _link(self, conn, row, memory_id, *, unlink=False):
        memory_id = validate_text(memory_id, "memory id", required=True, limit=1000)
        if unlink:
            conn.execute(
                "DELETE FROM task_links WHERE task_id=? AND drawer_id=?", (row["id"], memory_id)
            )
        else:
            theme, title = self._memory_theme(memory_id)
            conn.execute(
                "INSERT OR IGNORE INTO task_links(task_id,drawer_id,stamp_user,stamp_connection,"
                "created_at,title,theme) VALUES (?,?,?,?,?,?,?)",
                (
                    row["id"],
                    memory_id,
                    self.principal.user_id,
                    None if self.principal.person else self.principal.connection,
                    now(),
                    title,
                    theme,
                ),
            )
        self._log(conn, row["id"], "unlink" if unlink else "link", target=memory_id)

    def _pr_updates(self, row, updates, value):
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            updates["pr_number"] = value
            repo = updates.get("repo", row["repo"])
            if repo:
                updates["pr_url"] = f"https://{repo}/pull/{value}"
        elif isinstance(value, str) and value.isdigit() and int(value) > 0:
            updates["pr_number"] = int(value)
            repo = updates.get("repo", row["repo"])
            if repo:
                updates["pr_url"] = f"https://{repo}/pull/{int(value)}"
        elif isinstance(value, str) and re.fullmatch(
            r"https?://[^\s]+/(?:pull|pulls|merge_requests)/[1-9][0-9]*/?", value
        ):
            updates["pr_url"] = value
            updates["pr_number"] = int(value.rstrip("/").rsplit("/", 1)[1])
        else:
            raise ToolError("no: pr must be a URL or a positive number")

    def task_update(self, task, *, version=None, **args):
        permitted = {
            "title",
            "body",
            "labels",
            "status",
            "before",
            "after",
            "link",
            "unlink",
            "lesson",
            "repo",
            "branch",
            "pr",
            "checks",
            "status_line",
        }
        with self.transaction() as conn:
            row, board = self._get(conn, task)
            self._write(board)
            self._version(conn, row, version)
            if set(args) - permitted:
                raise ToolError("no: unknown update field")
            self._holder(row, editing=bool(set(args) & {"title", "body", "labels"}))
            if row["status"] == "review" and not self.principal.person:
                raise ToolError("no: card is waiting on a person")
            if "lesson" in args and args.get("status") != "done":
                raise ToolError("no: a lesson requires status done")
            updates = {}
            if "labels" in args:
                labels = args["labels"]
                if not isinstance(labels, list) or len(labels) > 100:
                    raise ToolError("no: labels must be a list")
                updates["labels"] = json.dumps(
                    [validate_text(label, "label", required=True, limit=200) for label in labels]
                )
            for key in ("title", "body", "branch", "status_line"):
                if key in args:
                    updates[key] = validate_text(
                        args[key],
                        key,
                        required=key == "title",
                        limit=5000 if key == "title" else 100_000,
                    )
            if "repo" in args:
                updates["repo"] = normalize_repo(args["repo"])
            if "checks" in args:
                if args["checks"] not in ("", "pending", "passing", "failing"):
                    raise ToolError("no: checks must be pending, passing or failing")
                updates["checks"] = args["checks"]
            if "pr" in args:
                self._pr_updates(row, updates, args["pr"])
            if "status_line" in args:
                updates["heartbeat_at"] = now()
            status = args.get("status", row["status"])
            self._transition(row, status)
            if status != row["status"]:
                updates.update(
                    status=status, claim_until=None, done_at=now() if status == "done" else None
                )
                if status == "doing":
                    updates["assignee"] = self.principal.actor
                else:
                    updates["assignee"] = None
                if status == "review" or (
                    status == "done" and board["done_needs_review"] and not self.principal.person
                ):
                    done_review = status == "done"
                    updates.update(
                        status="review",
                        ask="Done?" if done_review else "Review?",
                        ask_options=json.dumps(["ship", "redo"] if done_review else []),
                        ask_to=self._owner_actor(),
                        return_to=self.principal.actor,
                        done_at=None,
                    )
            self._position(conn, row, updates, args.get("before"), args.get("after"))
            if "lesson" in args:
                lesson = validate_text(args["lesson"], "lesson", required=True)
                saved = self.ctx.memory(
                    "mempalace_add_drawer",
                    {
                        "content": lesson,
                        "wing": self.principal.policy.write_wing or board["wing"],
                        "room": "general",
                    },
                )
                memory_id = saved.get("drawer_id") if isinstance(saved, dict) else None
                if not memory_id:
                    raise ToolError("no: could not save lesson")
                self._link(conn, row, memory_id)
            for key in ("link", "unlink"):
                if key in args:
                    self._link(conn, row, args[key], unlink=key == "unlink")
            new_flags = flags_for(
                *(args.get(key) for key in ("title", "body", "lesson", "status_line"))
            )
            if new_flags:
                updates["flags"] = json.dumps(
                    sorted(set(json.loads(row["flags"] or "[]")) | set(new_flags))
                )
            if not updates and not any(key in args for key in ("link", "unlink", "lesson")):
                return card(conn, row, self.principal)
            self._set(conn, row, updates)
            moved = updates.get("status", row["status"]) != row["status"]
            ask = (
                {
                    "text": updates["ask"],
                    "target": updates["ask_to"],
                    "options": json.loads(updates["ask_options"]),
                }
                if moved and updates["status"] == "review"
                else {}
            )
            self._log(
                conn,
                row["id"],
                "move" if moved else "edit",
                from_status=row["status"],
                to_status=updates.get("status", row["status"]),
                **ask,
            )
            self._emit(
                conn,
                row["id"],
                "card.moved" if moved else "card.updated",
                **{"from": row["status"], "to": updates.get("status", row["status"])},
            )
            if updates.get("status") == "review":
                self._emit(conn, row["id"], "card.review_needed", return_to=self.principal.actor)
            return card(conn, task_row(conn, row["id"]), self.principal)

    def _handoff(self, conn, row, board, to, note, options=None):
        self._write(board)
        target = self._actor(to)
        person = target is not None and target.startswith("user:")
        if row["status"] not in ("todo", "doing"):
            raise ToolError("no: only todo or doing cards can be handed off")
        if row["status"] == "doing":
            self._holder(row)
        elif not person and not self.principal.person:
            raise ToolError("no: take this card before handing it off")
        note = validate_text(note, "note", required=True)
        options = choices(options)
        if options and not person:
            raise ToolError("no: options require a person")
        status = "review" if person else "todo"
        updates = {
            "status": status,
            "claim_until": None,
            "assignee": None if person else target,
            "ask": note if person else None,
            "ask_options": json.dumps(options),
            "ask_to": target if person else None,
            "return_to": self.principal.actor if person else None,
            "flags": json.dumps(
                sorted(set(json.loads(row["flags"] or "[]")) | set(flags_for(note)))
            ),
        }
        self._set(conn, row, updates)
        self._log(
            conn,
            row["id"],
            "handoff",
            text=note,
            target=target,
            from_status=row["status"],
            to_status=status,
            flags=flags_for(note),
            options=options if person else None,
        )
        self._emit(
            conn,
            row["id"],
            "card.handoff",
            **{
                "from": row["status"],
                "to": status,
                "return_to": updates["return_to"],
                "target": target,
            },
        )
        if person:
            self._emit(conn, row["id"], "card.review_needed", return_to=updates["return_to"])
        return card(conn, task_row(conn, row["id"]), self.principal)

    def task_handoff(self, task, to, note, options=None, version=None):
        with self.transaction() as conn:
            row, board = self._get(conn, task)
            self._version(conn, row, version)
            return self._handoff(conn, row, board, to, note, options)

    def ask(self, question, options=None, board=None, task=None):
        with self.transaction() as conn:
            if task is not None:
                row, b = self._get(conn, task)
                if board is not None and self._board(conn, board)["id"] != b["id"]:
                    raise ToolError("no: card not found")
                return self._handoff(conn, row, b, self._owner_actor(), question, options)
            b = self._board(conn, board, create=True)
            self._write(b)
            self._expire(conn, b)
            question = validate_text(question, "question", required=True, limit=5000)
            return self._new_card(
                conn,
                b,
                title=question,
                status="review",
                ask=question,
                options=choices(options),
                target=self._owner_actor(),
            )

    def decide(self, task, choice=None, reply=None, then="return", version=None):
        with self.transaction() as conn:
            row, board = self._get(conn, task)
            _owner(self.principal)
            # Jeffrey decides in the app, or through one of his own trusted
            # agents relaying his answer (Chief, voice). An agent never
            # answers a question it asked itself.
            if not self.principal.person and (
                self.principal.policy.trust != "trusted" or row["return_to"] == self.principal.actor
            ):
                raise ToolError("no: only Jeffrey or his trusted agent can answer this")
            self._write(board)
            self._version(conn, row, version)
            if row["status"] != "review":
                raise ToolError("no: card is not in review")
            if then not in ("return", "todo", "done", "backlog"):
                raise ToolError("no: invalid decision destination")
            if choice is not None:
                choice = validate_text(choice, "choice", required=True, limit=120)
                offered = json.loads(row["ask_options"] or "[]")
                if offered and choice not in offered:
                    raise ToolError("no: choose one of the offered options")
            if reply is not None:
                reply = validate_text(reply, "reply", required=True)
            if not choice and not reply:
                raise ToolError("no: choose an option or write a reply")
            status = "todo" if then == "return" else then
            assignee = row["return_to"] if then == "return" else None
            updates = {
                "status": status,
                "assignee": assignee,
                "claim_until": None,
                "ask": None,
                "ask_options": None,
                "ask_to": None,
                "return_to": row["return_to"] if then == "return" else None,
                "done_at": now() if status == "done" else None,
                "flags": json.dumps(
                    sorted(set(json.loads(row["flags"] or "[]")) | set(flags_for(reply)))
                ),
            }
            self._set(conn, row, updates)
            self._log(
                conn,
                row["id"],
                "decide",
                text=reply,
                choice=choice,
                target=assignee,
                from_status="review",
                to_status=status,
                flags=flags_for(reply),
            )
            self._emit(
                conn,
                row["id"],
                "card.decided",
                **{
                    "from": "review",
                    "to": status,
                    "return_to": row["return_to"],
                    "ask_to": row["ask_to"],
                },
            )
            return card(conn, task_row(conn, row["id"]), self.principal)

    def needs_me(self):
        return needs_me(self.store, self.principal)

    def seed_card(
        self,
        *,
        board,
        title,
        body="",
        status="todo",
        labels=None,
        repo=None,
        branch=None,
        pr_url=None,
    ):
        """Owner's explicit import API; repeat (board, title) imports are no-ops."""
        _owner(self.principal)
        if not self.principal.person:
            raise ToolError("no: only a signed-in person can import cards")
        with self.transaction() as conn:
            b = self._board(conn, board, create=True)
            self._write(b)
            existing = conn.execute(
                "SELECT id FROM tasks WHERE board_id=? AND title=? ORDER BY id LIMIT 1",
                (b["id"], title),
            ).fetchone()
            if existing:
                return card(conn, task_row(conn, existing["id"]), self.principal)
            if repo is not None:
                normalized = normalize_repo(repo)
                mapped = conn.execute(
                    "SELECT board_id FROM board_repos WHERE repo=?", (normalized,)
                ).fetchone()
                if mapped is not None and mapped[0] != b["id"]:
                    raise ToolError("no: repo is already assigned")
                conn.execute(
                    "INSERT OR IGNORE INTO board_repos(repo,board_id) VALUES (?,?)",
                    (normalized, b["id"]),
                )
            if status not in STATUSES:
                raise ToolError("no: invalid status")
            created = self._new_card(
                conn,
                b,
                title=title,
                body=body,
                labels=labels,
                status="backlog" if status == "backlog" else "todo",
            )
            updates = {"status": status}
            if status == "doing":
                updates["assignee"] = self.principal.actor
            if status == "review":
                updates.update(ask=body or title, ask_options="[]", ask_to=self._owner_actor())
            if status == "done":
                updates["done_at"] = now()
            if repo is not None:
                updates["repo"] = normalize_repo(repo)
            if branch is not None:
                updates["branch"] = validate_text(branch, "branch")
            if pr_url is not None:
                pr_url = validate_text(pr_url, "pr_url", required=True, limit=2000)
                if not re.fullmatch(
                    r"https?://[^\s]+/(?:pull|pulls|merge_requests)/[1-9][0-9]*/?", pr_url
                ):
                    raise ToolError("no: invalid PR URL")
                updates.update(pr_url=pr_url, pr_number=int(pr_url.rstrip("/").rsplit("/", 1)[1]))
            row = task_row(conn, created["number"])
            self._set(conn, row, updates)
            if status != row["status"]:
                ask = (
                    {"text": updates["ask"], "target": updates["ask_to"], "options": []}
                    if status == "review"
                    else {}
                )
                self._log(
                    conn, row["id"], "move", from_status=row["status"], to_status=status, **ask
                )
                self._emit(conn, row["id"], "card.moved", **{"from": row["status"], "to": status})
            if status == "review":
                self._emit(conn, row["id"], "card.review_needed")
            return card(conn, task_row(conn, row["id"]), self.principal)
