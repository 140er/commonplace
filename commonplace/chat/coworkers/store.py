"""Workspace-local, verbatim coworker conversations and durable response jobs.

Only the gateway adapter supplies identity authority. Standalone Store(path) is
useful for isolated storage tests; production must use open_store(gateway, id).
No content is stored in the control database or exposed to workspace event feeds.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path

from commonplace.admission import GATE
from mempalace.sqlite_crypto import connect, row_factory

MAX_TEXT = 8_000
MAX_REPLY = 2_000
MAX_INSTRUCTIONS = 4_000
MAX_MEMORY_TEXT = 4_000
MAX_MEMORIES = 100
MAX_BOTS = 200
MAX_CONVERSATIONS = 200
MAX_PENDING = 20
MAX_PROGRESS_TASKS = 20
MAX_CONTROL_TEXT = 4_000
MAX_CONTROLS = 100
CONTROL_HISTORY = 10
TERMINAL = frozenset(
    {
        "done",
        "failed",
        "cancelled",
        "interrupted",
        "uncertain",
        "needs_configuration",
        "unavailable",
        "skipped",
        "superseded",
    }
)
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS bots (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, instructions TEXT NOT NULL,
 connection_id TEXT, created_by TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('dm','group')),
 title TEXT NOT NULL, board TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS members (
 conversation_id TEXT NOT NULL REFERENCES conversations(id), user_id TEXT NOT NULL,
 PRIMARY KEY(conversation_id,user_id)
);
CREATE INDEX IF NOT EXISTS members_user ON members(user_id,conversation_id);
CREATE TABLE IF NOT EXISTS conversation_bots (
 conversation_id TEXT NOT NULL REFERENCES conversations(id), bot_id TEXT NOT NULL REFERENCES bots(id),
 PRIMARY KEY(conversation_id,bot_id)
);
CREATE TABLE IF NOT EXISTS messages (
 id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL REFERENCES conversations(id),
 actor TEXT NOT NULL, bot_id TEXT REFERENCES bots(id), text TEXT NOT NULL,
 created_at TEXT NOT NULL, request_id TEXT, reply_to INTEGER REFERENCES messages(id), model TEXT,
 UNIQUE(conversation_id,actor,request_id)
);
CREATE INDEX IF NOT EXISTS messages_conversation ON messages(conversation_id,id);
CREATE UNIQUE INDEX IF NOT EXISTS one_reply ON messages(reply_to,bot_id) WHERE reply_to IS NOT NULL;
CREATE TABLE IF NOT EXISTS jobs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL REFERENCES conversations(id),
 message_id INTEGER NOT NULL UNIQUE REFERENCES messages(id), bot_id TEXT NOT NULL REFERENCES bots(id),
 user_id TEXT NOT NULL, state TEXT NOT NULL, code TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, task TEXT, session_hash TEXT,
 requested_bot TEXT, visibility_version INTEGER, progress_binding TEXT
);
CREATE INDEX IF NOT EXISTS jobs_pending ON jobs(state,id);
CREATE INDEX IF NOT EXISTS jobs_conversation ON jobs(conversation_id,id);
CREATE UNIQUE INDEX IF NOT EXISTS one_running ON jobs(conversation_id,bot_id) WHERE state='running';
CREATE TABLE IF NOT EXISTS intake (
 message_id INTEGER PRIMARY KEY REFERENCES messages(id), requested_bot TEXT
);
CREATE TABLE IF NOT EXISTS memories (
 id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL REFERENCES conversations(id),
 text TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS memories_conversation ON memories(conversation_id,id);
CREATE TABLE IF NOT EXISTS memory_tombstones (
 memory_id INTEGER PRIMARY KEY REFERENCES memories(id), actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_controls (
 id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER NOT NULL REFERENCES jobs(id),
 actor TEXT NOT NULL, operation TEXT NOT NULL CHECK(operation IN ('follow_up','stop')),
 request_id TEXT NOT NULL, text TEXT NOT NULL, target TEXT NOT NULL,
 state TEXT NOT NULL, code TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(actor,operation,request_id)
);
CREATE INDEX IF NOT EXISTS task_controls_job ON task_controls(job_id,actor,id);
CREATE TRIGGER IF NOT EXISTS task_controls_immutable
BEFORE UPDATE OF job_id,actor,operation,request_id,text,target,created_at ON task_controls
BEGIN SELECT RAISE(ABORT, 'coworker control intent is immutable'); END;
CREATE TRIGGER IF NOT EXISTS task_controls_no_delete BEFORE DELETE ON task_controls
BEGIN SELECT RAISE(ABORT, 'coworker control history is append-only'); END;
"""
for _table in ("messages", "intake", "memories", "memory_tombstones"):
    for _operation in ("UPDATE", "DELETE"):
        _SCHEMA += (
            f"CREATE TRIGGER IF NOT EXISTS {_table}_no_{_operation.lower()} "
            f"BEFORE {_operation} ON {_table} BEGIN "
            "SELECT RAISE(ABORT, 'coworker history is append-only'); END;\n"
        )


class StoreError(ValueError):
    def __init__(self, code="invalid_request", status=400):
        self.code, self.status = code, status
        super().__init__(code)


def _text(value, limit, *, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise StoreError()
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise StoreError() from None
    return value


def _id(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise StoreError()
    return value


def _number(value, *, zero=False):
    if type(value) is not int or not (0 if zero else 1) <= value < 2**63:
        raise StoreError()
    return value


def _ids(values, limit):
    if not isinstance(values, (list, tuple)) or len(values) > limit:
        raise StoreError()
    result = [_id(value) for value in values]
    if len(set(result)) != len(result):
        raise StoreError()
    return result


def _stamp():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path, *, authority=None):
        self._path = path
        self._authority = authority
        self._lock = threading.RLock()
        self._ready_path = None

    @contextmanager
    def _transaction(self):
        # Lock current control authority until publication commits. Never hold a
        # workspace writer lock while waiting for the control authority lock.
        guard = self._authority.guard() if self._authority else nullcontext()
        with guard, self._lock:
            path = Path(self._path() if callable(self._path) else self._path).resolve()
            existed = path.is_file()
            with GATE.creating(path):
                path.parent.mkdir(parents=True, exist_ok=True)
                conn = connect(str(path), timeout=0.25, isolation_level=None)
            try:
                conn.row_factory = row_factory
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("PRAGMA journal_mode=WAL")
                if self._ready_path != path or not existed:
                    conn.executescript(_SCHEMA)
                    self._ready_path = path
                conn.execute("BEGIN IMMEDIATE")
                try:
                    if "progress_binding" not in {
                        r[1] for r in conn.execute("PRAGMA table_info(jobs)")
                    }:
                        conn.execute("ALTER TABLE jobs ADD COLUMN progress_binding TEXT")
                    yield conn
                except BaseException:
                    conn.rollback()
                    raise
                else:
                    conn.commit()
            finally:
                conn.close()

    def _user(self, user_id, *, owner=False):
        _id(user_id)
        if self._authority:
            self._authority.user(user_id, owner=owner)

    def _conversation(self, conn, user_id, conversation_id):
        self._user(user_id)
        _id(conversation_id)
        row = conn.execute(
            "SELECT c.* FROM conversations c JOIN members m ON m.conversation_id=c.id "
            "WHERE c.id=? AND m.user_id=?",
            (conversation_id, user_id),
        ).fetchone()
        if row is None:
            raise StoreError("not_found", 404)
        return row

    @staticmethod
    def _bot(row):
        return {
            key: row[key] for key in ("id", "name", "instructions", "connection_id", "created_at")
        }

    def _bot_access(self, row):
        if self._authority:
            self._authority.bot(row)

    @staticmethod
    def _conversation_view(conn, row):
        return {
            **{key: row[key] for key in ("id", "kind", "title", "board", "created_at")},
            "member_ids": [
                r[0]
                for r in conn.execute(
                    "SELECT user_id FROM members WHERE conversation_id=? ORDER BY user_id",
                    (row["id"],),
                )
            ],
            "bot_ids": [
                r[0]
                for r in conn.execute(
                    "SELECT bot_id FROM conversation_bots WHERE conversation_id=? ORDER BY bot_id",
                    (row["id"],),
                )
            ],
        }

    @staticmethod
    def _message(row):
        result = {
            key: row[key]
            for key in (
                "id",
                "conversation_id",
                "actor",
                "bot_id",
                "text",
                "created_at",
                "request_id",
            )
        }
        if row["reply_to"] is not None:
            result["reply_to"] = row["reply_to"]
        if row["model"] is not None:
            result["model"] = json.loads(row["model"])
        return result

    @staticmethod
    def _job(row):
        if row is None:
            return None
        result = {
            key: row[key]
            for key in (
                "id",
                "conversation_id",
                "message_id",
                "bot_id",
                "user_id",
                "state",
                "code",
                "created_at",
                "updated_at",
            )
        }
        if row["task"] is not None:
            result["task"] = json.loads(row["task"])
        return result

    def list_bots(self):
        with self._transaction() as conn:
            return [
                self._bot(r)
                for r in conn.execute(
                    "SELECT * FROM bots ORDER BY created_at,id LIMIT ?", (MAX_BOTS,)
                )
            ]

    def create_bot(self, user_id, name, instructions="", connection_id=None):
        _text(name, 80)
        _text(instructions, MAX_INSTRUCTIONS, empty=True)
        if connection_id is not None:
            _id(connection_id)
        with self._transaction() as conn:
            self._user(user_id, owner=True)
            if connection_id is not None:
                self._bot_access({"connection_id": connection_id})
            if conn.execute("SELECT count(*) FROM bots").fetchone()[0] >= MAX_BOTS:
                raise StoreError("at_capacity", 409)
            bot_id = "bot_" + uuid.uuid4().hex
            conn.execute(
                "INSERT INTO bots VALUES (?,?,?,?,?,?)",
                (bot_id, name, instructions, connection_id, user_id, _stamp()),
            )
            return self._bot(conn.execute("SELECT * FROM bots WHERE id=?", (bot_id,)).fetchone())

    def list_conversations(self, user_id):
        with self._transaction() as conn:
            self._user(user_id)
            return [
                self._conversation_view(conn, r)
                for r in conn.execute(
                    "SELECT c.* FROM conversations c JOIN members m ON m.conversation_id=c.id WHERE m.user_id=? ORDER BY c.created_at DESC,c.id LIMIT ?",
                    (user_id, MAX_CONVERSATIONS),
                )
            ]

    def create_conversation(self, user_id, kind, title="", member_ids=(), bot_ids=(), board=None):
        if kind not in ("dm", "group"):
            raise StoreError()
        _text(title, 120, empty=True)
        members = sorted(set([_id(user_id), *_ids(member_ids, 31)]))
        bots = _ids(bot_ids, 16)
        if kind == "dm" and (len(members) != 1 or len(bots) != 1):
            raise StoreError()
        if kind == "group" and len(members) + len(bots) < 2:
            raise StoreError()
        if board is not None:
            _text(board, 100)
        with self._transaction() as conn:
            for member in members:
                self._user(member)
            for bot_id in bots:
                bot = conn.execute("SELECT * FROM bots WHERE id=?", (bot_id,)).fetchone()
                if bot is None:
                    raise StoreError("not_found", 404)
                self._bot_access(bot)
            if board is not None and self._authority:
                board = self._authority.board(user_id, board)
            if (
                conn.execute("SELECT count(*) FROM members WHERE user_id=?", (user_id,)).fetchone()[
                    0
                ]
                >= MAX_CONVERSATIONS
            ):
                raise StoreError("at_capacity", 409)
            conversation_id = "conv_" + uuid.uuid4().hex
            conn.execute(
                "INSERT INTO conversations VALUES (?,?,?,?,?)",
                (conversation_id, kind, title, board, _stamp()),
            )
            conn.executemany(
                "INSERT INTO members VALUES (?,?)", [(conversation_id, m) for m in members]
            )
            conn.executemany(
                "INSERT INTO conversation_bots VALUES (?,?)", [(conversation_id, b) for b in bots]
            )
            return self._conversation_view(
                conn,
                conn.execute(
                    "SELECT * FROM conversations WHERE id=?", (conversation_id,)
                ).fetchone(),
            )

    def get_conversation(self, user_id, conversation_id):
        with self._transaction() as conn:
            return self._conversation_view(conn, self._conversation(conn, user_id, conversation_id))

    def list_messages(self, user_id, conversation_id, after=0, limit=100):
        _number(after, zero=True)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise StoreError()
        with self._transaction() as conn:
            self._conversation(conn, user_id, conversation_id)
            return [
                self._message(r)
                for r in conn.execute(
                    "SELECT * FROM messages WHERE conversation_id=? AND id>? ORDER BY id LIMIT ?",
                    (conversation_id, after, limit),
                )
            ]

    def _target(self, conn, conversation_id, text, bot_id):
        bots = conn.execute(
            "SELECT b.* FROM bots b JOIN conversation_bots cb ON cb.bot_id=b.id WHERE cb.conversation_id=? ORDER BY b.id",
            (conversation_id,),
        ).fetchall()
        if bot_id is not None:
            _id(bot_id)
            selected = next((b for b in bots if b["id"] == bot_id), None)
            if selected is None:
                raise StoreError("not_found", 404)
        elif len(bots) == 1:
            selected = bots[0]
        else:
            # An exact name mention must identify one bot, including when names
            # collide. Ordinary group text creates no job and no bot loops.
            mentions = [
                b
                for b in bots
                if re.search(r"(?<![\w@])@" + re.escape(b["name"]) + r"(?!\w)", text, re.IGNORECASE)
            ]
            if len(mentions) > 1:
                raise StoreError("ambiguous_bot", 400)
            selected = mentions[0] if mentions else None
        if selected is not None:
            self._bot_access(selected)
        return selected["id"] if selected is not None else None

    def post_message(
        self, user_id, conversation_id, text, request_id, bot_id=None, *, session_hash=None
    ):
        _text(text, MAX_TEXT)
        _id(request_id)
        if bot_id is not None:
            _id(bot_id)
        if session_hash is not None and (
            not isinstance(session_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", session_hash)
        ):
            raise StoreError()
        with self._transaction() as conn:
            self._conversation(conn, user_id, conversation_id)
            if self._authority and session_hash is not None:
                self._authority.job({"user_id": user_id, "session_hash": session_hash})
            actor = "user:" + user_id
            existing = conn.execute(
                "SELECT m.*,i.requested_bot FROM messages m JOIN intake i ON i.message_id=m.id WHERE m.conversation_id=? AND m.actor=? AND m.request_id=?",
                (conversation_id, actor, request_id),
            ).fetchone()
            if existing is not None:
                if existing["text"] != text or existing["requested_bot"] != bot_id:
                    raise StoreError("request_conflict", 409)
                return {
                    "message": self._message(existing),
                    "job": self._progress_job(
                        conn,
                        conn.execute(
                            "SELECT * FROM jobs WHERE message_id=?", (existing["id"],)
                        ).fetchone(),
                        user_id,
                        session_hash=session_hash,
                    ),
                }
            target = self._target(conn, conversation_id, text, bot_id)
            if (
                target is not None
                and conn.execute(
                    "SELECT count(*) FROM jobs WHERE state IN ('pending','running')"
                ).fetchone()[0]
                >= MAX_PENDING
            ):
                raise StoreError("at_capacity", 409)
            stamp = _stamp()
            mid = conn.execute(
                "INSERT INTO messages(conversation_id,actor,bot_id,text,created_at,request_id) VALUES (?,?,?,?,?,?)",
                (conversation_id, actor, target, text, stamp, request_id),
            ).lastrowid
            conn.execute("INSERT INTO intake VALUES (?,?)", (mid, bot_id))
            if target is not None:
                conn.execute(
                    "INSERT INTO jobs(conversation_id,message_id,bot_id,user_id,state,created_at,updated_at,session_hash,requested_bot) VALUES (?,?,?,?,'pending',?,?,?,?)",
                    (conversation_id, mid, target, user_id, stamp, stamp, session_hash, bot_id),
                )
            return {
                "message": self._message(
                    conn.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
                ),
                "job": self._job(
                    conn.execute("SELECT * FROM jobs WHERE message_id=?", (mid,)).fetchone()
                ),
            }

    def list_jobs(self, user_id, conversation_id, *, progress=False, session_hash=None):
        with self._transaction() as conn:
            self._conversation(conn, user_id, conversation_id)
            rows = conn.execute(
                "SELECT * FROM jobs WHERE conversation_id=? ORDER BY id DESC LIMIT 100",
                (conversation_id,),
            ).fetchall()
            # Newest twenty task projections per response; older entries remain
            # receipts. No canonical content is copied into shared chat history.
            projected, remaining = [], MAX_PROGRESS_TASKS
            for row in rows:
                if progress and self._progress_candidate(conn, row):
                    projected.append(
                        self._progress_job(
                            conn, row, user_id, remaining > 0, session_hash=session_hash
                        )
                    )
                    remaining -= 1
                else:
                    projected.append(self._job(row))
            return projected[::-1]

    @staticmethod
    def _progress_candidate(conn, row):
        if row is None:
            return False
        if row["task"] is not None:
            return True
        if row["state"] not in ("done", "failed", "interrupted", "uncertain"):
            return False
        message = conn.execute(
            "SELECT text FROM messages WHERE id=?", (row["message_id"],)
        ).fetchone()
        if message is None:
            return False
        command = message["text"].partition("\n")[0]
        # Same explicit user intent as runtime._task, never model/tool text.
        return command.startswith("/task ") and 0 < len(command[len("/task") :].strip()) <= 200

    def _progress_job(self, conn, row, user_id, include=True, *, session_hash=None):
        result = self._job(row)
        if result is None or not self._progress_candidate(conn, row):
            return result
        # Even an idempotent POST acknowledgement must not revive an old task
        # reference after authority or conversation visibility changed.
        result.pop("task", None)
        result["task_progress"] = {"source": "canonical_task", "availability": "unavailable"}
        if not include:
            result["task_progress"]["availability"] = "omitted"
            return result
        try:
            if (
                self._authority is None
                or row["state"] not in ("done", "failed", "interrupted", "uncertain")
                or not row["session_hash"]
                or not session_hash
            ):
                return result
            viewer = {"user_id": user_id, "session_hash": session_hash}
            self._authority.job(viewer)
            self._conversation(conn, user_id, row["conversation_id"])
            self._runtime_access(conn, row)
            self._check_visibility(conn, row)
            if row["progress_binding"] != self._progress_binding(conn, row):
                return result
            task = (
                json.loads(row["task"])
                if row["task"] is not None
                else {
                    "board": self._conversation(conn, row["user_id"], row["conversation_id"])[
                        "board"
                    ],
                    "number": None,
                }
            )
            if not isinstance(task, dict) or set(task) != {"board", "number"}:
                return result
            projection = self._authority.task_progress(row, task, user_id)
            projection["controls"] = self._control_projection(conn, row, projection, user_id)
            # Recheck after source reads too; callbacks must not publish stale
            # data after a reentrant revocation/cancel/rebind or memory removal.
            current = conn.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone()
            self._runtime_access(conn, current)
            self._authority.job(viewer)
            self._conversation(conn, user_id, row["conversation_id"])
            self._check_visibility(conn, current)
            if (
                current["state"] != row["state"]
                or current["task"] != row["task"]
                or current["progress_binding"] != self._progress_binding(conn, current)
            ):
                return result
            self._authority.task_progress_current(current, projection, user_id)
        except StoreError:
            return result
        result["task"] = {"board": projection["board"], "number": projection["number"]}
        result["task_progress"] = projection
        return result

    @staticmethod
    def _control_receipt(row):
        # No canonical identifiers, session digests or internal target binding.
        return {
            key: row[key]
            for key in (
                "id",
                "job_id",
                "operation",
                "request_id",
                "text",
                "state",
                "code",
                "created_at",
                "updated_at",
            )
        }

    def _control_projection(self, conn, job, task, user_id):
        result = {"can_follow_up": False, "can_stop": False, "receipts": []}
        try:
            target = self._authority.task_control_target(job, task, user_id)
        except StoreError:
            return result
        result.update(can_follow_up=True, can_stop=target["runner_job"] is not None)
        rows = conn.execute(
            "SELECT * FROM task_controls WHERE job_id=? AND actor=? ORDER BY id DESC LIMIT ?",
            (job["id"], "user:" + user_id, CONTROL_HISTORY),
        ).fetchall()
        # A changed canonical origin/run never revives an earlier receipt.
        result["receipts"] = [
            self._control_receipt(r) for r in reversed(rows) if json.loads(r["target"]) == target
        ]
        return result

    def _control_context(self, conn, user_id, job_id, session_hash):
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if job is None or self._authority is None:
            raise StoreError("not_found", 404)
        self._conversation(conn, user_id, job["conversation_id"])
        view = self._progress_job(conn, job, user_id, session_hash=session_hash)
        if view.get("task_progress", {}).get("availability") != "available":
            raise StoreError("not_found", 404)
        target = self._authority.task_control_target(job, view["task"], user_id)
        current = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        self._runtime_access(conn, current)
        self._authority.job({"user_id": user_id, "session_hash": session_hash})
        self._conversation(conn, user_id, current["conversation_id"])
        self._check_visibility(conn, current)
        if (
            current["state"] != job["state"]
            or current["task"] != job["task"]
            or current["progress_binding"] != self._progress_binding(conn, current)
        ):
            raise StoreError("not_found", 404)
        return dict(current), target

    def control_task(self, user_id, job_id, operation, request_id, text="", *, session_hash=None):
        """Durable one-attempt outbox for an explicit person's targeted control.

        Commit the uncertain receipt before crossing the canonical store boundary.
        Exact retries observe it; they NEVER repeat an effect, even after restart
        or a lost acknowledgement. There is no worker launch or model call here.
        """
        from . import task_controls

        _number(job_id)
        _id(request_id)
        if operation not in ("follow_up", "stop"):
            raise StoreError()
        _text(text, MAX_CONTROL_TEXT, empty=operation == "stop")
        if operation == "stop" and text:
            raise StoreError()
        with self._transaction() as conn:
            job, target = self._control_context(conn, user_id, job_id, session_hash)
            if operation == "stop" and target["runner_job"] is None:
                raise StoreError("not_found", 404)
            actor = "user:" + user_id
            previous = conn.execute(
                "SELECT * FROM task_controls WHERE actor=? AND operation=? AND request_id=?",
                (actor, operation, request_id),
            ).fetchone()
            if previous is not None:
                if previous["job_id"] != job_id or previous["text"] != text:
                    raise StoreError("request_conflict", 409)
                if json.loads(previous["target"]) != target:
                    raise StoreError("not_found", 404)
                return {"receipt": self._control_receipt(previous)}
            if (
                conn.execute(
                    "SELECT count(*) FROM task_controls WHERE job_id=?", (job_id,)
                ).fetchone()[0]
                >= MAX_CONTROLS
            ):
                raise StoreError("at_capacity", 409)
            stamp = _stamp()
            receipt_id = conn.execute(
                "INSERT INTO task_controls(job_id,actor,operation,request_id,text,target,state,code,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,'uncertain','acknowledgement_pending',?,?)",
                (
                    job_id,
                    actor,
                    operation,
                    request_id,
                    text,
                    json.dumps(target, sort_keys=True),
                    stamp,
                    stamp,
                ),
            ).lastrowid

        def authorize():
            # No broad control/conversation lock spans the canonical mutation.
            # Fresh exact origin/viewer sessions and audience fences precede it.
            with self._transaction() as conn:
                current, binding = self._control_context(conn, user_id, job_id, session_hash)
                if binding != target:
                    raise StoreError("not_found", 404)
                return self._authority.user(user_id), current

        try:
            outcome = task_controls.apply(self._authority, target, operation, text, authorize)
        except Exception:
            # Includes commit-with-lost-ack. Do not infer failure and retry, or
            # expose exception text (which may contain private canonical data).
            outcome = None
        with self._transaction() as conn:
            _, current = self._control_context(conn, user_id, job_id, session_hash)
            if current != target:
                raise StoreError("not_found", 404)
            if outcome is not None:
                conn.execute(
                    "UPDATE task_controls SET state=?,code=?,updated_at=? WHERE id=?",
                    (*outcome, _stamp(), receipt_id),
                )
            receipt = conn.execute(
                "SELECT * FROM task_controls WHERE id=?", (receipt_id,)
            ).fetchone()
            return {"receipt": self._control_receipt(receipt)}

    def _progress_binding(self, conn, job):
        conversation = self._conversation(conn, job["user_id"], job["conversation_id"])
        bot = conn.execute("SELECT * FROM bots WHERE id=?", (job["bot_id"],)).fetchone()
        if bot is None:
            raise StoreError("not_found", 404)
        data = {"conversation": self._conversation_view(conn, conversation), "bot": self._bot(bot)}
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    def list_memory(self, user_id, conversation_id):
        with self._transaction() as conn:
            self._conversation(conn, user_id, conversation_id)
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT m.* FROM memories m WHERE m.conversation_id=? AND NOT EXISTS(SELECT 1 FROM memory_tombstones t WHERE t.memory_id=m.id) ORDER BY m.id DESC LIMIT ?",
                    (conversation_id, MAX_MEMORIES),
                )
            ][::-1]

    def add_memory(self, user_id, conversation_id, text):
        _text(text, MAX_MEMORY_TEXT)
        with self._transaction() as conn:
            self._conversation(conn, user_id, conversation_id)
            count = conn.execute(
                "SELECT count(*) FROM memories m WHERE conversation_id=? AND NOT EXISTS(SELECT 1 FROM memory_tombstones t WHERE t.memory_id=m.id)",
                (conversation_id,),
            ).fetchone()[0]
            if count >= MAX_MEMORIES:
                raise StoreError("at_capacity", 409)
            mid = conn.execute(
                "INSERT INTO memories(conversation_id,text,actor,created_at) VALUES (?,?,?,?)",
                (conversation_id, text, "user:" + user_id, _stamp()),
            ).lastrowid
            return dict(conn.execute("SELECT * FROM memories WHERE id=?", (mid,)).fetchone())

    def remove_memory(self, user_id, conversation_id, memory_id):
        _number(memory_id)
        with self._transaction() as conn:
            self._conversation(conn, user_id, conversation_id)
            if (
                conn.execute(
                    "SELECT 1 FROM memories WHERE id=? AND conversation_id=?",
                    (memory_id, conversation_id),
                ).fetchone()
                is None
            ):
                raise StoreError("not_found", 404)
            conn.execute(
                "INSERT OR IGNORE INTO memory_tombstones VALUES (?,?,?)",
                (memory_id, "user:" + user_id, _stamp()),
            )
            return {"ok": True}

    def cancel_job(self, user_id, job_id, *, session_hash=None):
        _number(job_id)
        with self._transaction() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise StoreError("not_found", 404)
            self._conversation(conn, user_id, row["conversation_id"])
            conn.execute(
                "UPDATE jobs SET state='cancelled',code='cancelled',updated_at=? WHERE id=? AND state IN ('pending','running')",
                (_stamp(), job_id),
            )
            return self._progress_job(
                conn,
                conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone(),
                user_id,
                session_hash=session_hash,
            )

    def _runtime_access(self, conn, job):
        self._conversation(conn, job["user_id"], job["conversation_id"])
        bot = conn.execute(
            "SELECT b.* FROM bots b JOIN conversation_bots cb ON cb.bot_id=b.id WHERE cb.conversation_id=? AND b.id=?",
            (job["conversation_id"], job["bot_id"]),
        ).fetchone()
        message = conn.execute(
            "SELECT * FROM messages WHERE id=? AND conversation_id=? AND actor=? AND bot_id=?",
            (job["message_id"], job["conversation_id"], "user:" + job["user_id"], job["bot_id"]),
        ).fetchone()
        if bot is None or message is None:
            raise StoreError("not_found", 404)
        self._bot_access(bot)
        if self._authority:
            self._authority.job(job)

    def claim_job(self):
        with self._transaction() as conn:
            rows = conn.execute(
                "SELECT j.* FROM jobs j WHERE state='pending' AND NOT EXISTS(SELECT 1 FROM jobs r WHERE r.state='running' AND r.conversation_id=j.conversation_id AND r.bot_id=j.bot_id) ORDER BY j.id LIMIT ?",
                (MAX_PENDING,),
            ).fetchall()
            for row in rows:
                try:
                    self._runtime_access(conn, row)
                except StoreError:
                    conn.execute(
                        "UPDATE jobs SET state='failed',code='no_access',updated_at=? WHERE id=?",
                        (_stamp(), row["id"]),
                    )
                    continue
                visibility = conn.execute(
                    "SELECT count(*) FROM memory_tombstones t JOIN memories m ON m.id=t.memory_id WHERE m.conversation_id=?",
                    (row["conversation_id"],),
                ).fetchone()[0]
                conn.execute(
                    "UPDATE jobs SET state='running',updated_at=?,visibility_version=?,progress_binding=? WHERE id=? AND state='pending'",
                    (_stamp(), visibility, self._progress_binding(conn, row), row["id"]),
                )
                return self._job(
                    conn.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone()
                )
            return None

    def runtime_job(self, job_id):
        """Internal fresh dispatch check, including the exact originating session.

        The session digest stays out of public job projections and REST replies.
        No authority lock is retained across provider or network work.
        """
        _number(job_id)
        with self._transaction() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise StoreError("not_found", 404)
            self._runtime_access(conn, row)
            if row["state"] != "running":
                raise StoreError("job_terminal", 409)
            self._check_visibility(conn, row)
            return {**self._job(row), "session_hash": row["session_hash"]}

    def finish_job(self, job_id, state, code="", task=None):
        _number(job_id)
        if not isinstance(state, str) or state not in TERMINAL:
            raise StoreError()
        _text(code, 80, empty=True)
        if task is not None:
            # BoardService remains canonical: only a typed card reference may
            # be attached, never arbitrary model output, URLs or credentials.
            if type(task) is int:
                _number(task)
            elif isinstance(task, dict) and set(task) == {"board", "number"}:
                _text(task["board"], 100)
                _number(task["number"])
            else:
                raise StoreError()
        with self._transaction() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise StoreError("not_found", 404)
            self._runtime_access(conn, row)
            if row["state"] != "running":
                raise StoreError("job_terminal", 409)
            if state == "done" or task is not None:
                self._check_visibility(conn, row)
            if task is not None and self._authority:
                board = conn.execute(
                    "SELECT board FROM conversations WHERE id=?", (row["conversation_id"],)
                ).fetchone()[0]
                self._authority.task(row, task, board)
            conn.execute(
                "UPDATE jobs SET state=?,code=?,task=?,updated_at=? WHERE id=? AND state='running'",
                (state, code, json.dumps(task) if task is not None else None, _stamp(), job_id),
            )
            return self._job(conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def add_bot_reply(self, job_id, text, model=None):
        _number(job_id)
        _text(text, MAX_REPLY)
        if isinstance(model, str):
            _text(model, 200)
        elif model is not None:
            if not isinstance(model, dict) or set(model) - {"provider", "model"}:
                raise StoreError()
            for value in model.values():
                _text(value, 200)
        with self._transaction() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise StoreError("not_found", 404)
            self._runtime_access(conn, row)
            existing = conn.execute(
                "SELECT * FROM messages WHERE reply_to=? AND bot_id=? AND actor=?",
                (row["message_id"], row["bot_id"], "bot:" + row["bot_id"]),
            ).fetchone()
            if existing is not None and row["state"] == "done":
                return self._message(existing)
            if row["state"] != "running":
                raise StoreError("job_terminal", 409)
            self._check_visibility(conn, row)
            stamp = _stamp()
            mid = conn.execute(
                "INSERT INTO messages(conversation_id,actor,bot_id,text,created_at,reply_to,model) VALUES (?,?,?,?,?,?,?)",
                (
                    row["conversation_id"],
                    "bot:" + row["bot_id"],
                    row["bot_id"],
                    text,
                    stamp,
                    row["message_id"],
                    json.dumps(model) if model is not None else None,
                ),
            ).lastrowid
            conn.execute(
                "UPDATE jobs SET state='done',code='ok',updated_at=? WHERE id=? AND state='running'",
                (stamp, job_id),
            )
            return self._message(
                conn.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
            )

    def recover_jobs(self):
        # A server restart cannot establish whether an external effect finished.
        # Keep pending intake; retire every in-flight lease without replaying it.
        with self._transaction() as conn:
            return conn.execute(
                "UPDATE jobs SET state='interrupted',code='interrupted',updated_at=? WHERE state='running'",
                (_stamp(),),
            ).rowcount

    @staticmethod
    def _check_visibility(conn, job):
        visibility = conn.execute(
            "SELECT count(*) FROM memory_tombstones t JOIN memories m ON m.id=t.memory_id WHERE m.conversation_id=?",
            (job["conversation_id"],),
        ).fetchone()[0]
        if job["visibility_version"] != visibility:
            raise StoreError("context_changed", 409)


def open_store(gateway, workspace_id):
    from commonplace.chat.coworkers.routes import Authority

    _id(workspace_id)
    with gateway.db._lock:
        stores = gateway.__dict__.setdefault("_coworker_stores", {})
        if workspace_id not in stores:
            stores[workspace_id] = Store(
                lambda: Path(gateway.supervisor.workspace_dir(workspace_id)) / "coworkers.sqlite3",
                authority=Authority(gateway, workspace_id),
            )
        return stores[workspace_id]
