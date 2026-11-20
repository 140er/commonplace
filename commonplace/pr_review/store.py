"""Review comments in the workspace file ``pr_review.sqlite3``: intake, statuses and history.

One row of ``pr_review_comments`` is one comment of one pull request, keyed by
``(repo, pr, kind, GitHub id)``. Intake is idempotent: the same comment at the same edit time is
a no-op, an edit with a changed body makes the next revision and puts the comment back to
``new``, an edit with the same body changes nothing. Status moves only through the methods
below, each one conditional update, and every move appends a row to ``pr_review_events``
(append-only; nothing is edited or deleted by the API). The file sits next to the other
workspace stores, so it is encrypted with the workspace and holds one workspace only; a read
never creates it. Callers authorize first; this module validates, stores and reads. Every
statement is explicit literal SQL.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path

from commonplace.admission import GATE
from commonplace.pipeline.compute import stamp
from mempalace.sqlite_crypto import connect, row_factory

from . import status as st
from .intake import REPLY_MARKER, is_verdict

FILENAME = "pr_review.sqlite3"
ROUNDS_DEFAULT = 2
ROUNDS_MAX = 3
RUN_COMMENTS = 25
STALE_AFTER = timedelta(minutes=30)
COMMIT_GRACE = timedelta(minutes=10)
MAX_ATTEMPTS = 3
PENDING = ("pending", "files_pending")
WAITING_PRS_LIMIT = 20
MAX_PR_COMMENTS = 2000
MAX_WORKSPACE_COMMENTS = 200_000
MAX_REPLY = 4000
MAX_NOTE = 500
TRUSTED_ASSOCIATIONS = ("OWNER", "MEMBER", "COLLABORATOR")
LIMITS = {
    "request_comments": 100,
    "request_bytes": 1024 * 1024,
    "pr_comments": MAX_PR_COMMENTS,
    "workspace_comments": MAX_WORKSPACE_COMMENTS,
    "run_comments": RUN_COMMENTS,
    "rounds": ROUNDS_DEFAULT,
}
# Paths a fix may not change without a person looking: the checks and rules that judge a PR.
PROTECTED = (
    ".github/",
    ".claude/",
    "REVIEW.md",
    "CLAUDE.md",
    "AGENTS.md",
    "pyproject.toml",
    "tools/REVIEW_GATE.md",
    "commonplace/guards/",
)
_SCHEMA = """
CREATE TABLE IF NOT EXISTS pr_review_prs (
    repo TEXT NOT NULL, pr INTEGER NOT NULL,
    head TEXT NOT NULL DEFAULT '', pr_state TEXT NOT NULL DEFAULT '',
    branch TEXT NOT NULL DEFAULT '', head_repo TEXT NOT NULL DEFAULT '',
    fetched_at TEXT NOT NULL DEFAULT '', changed_files TEXT NOT NULL DEFAULT '[]',
    changed_head TEXT NOT NULL DEFAULT '',
    rounds_used INTEGER NOT NULL DEFAULT 0, rounds_allowed INTEGER NOT NULL DEFAULT 2,
    last_round_at TEXT NOT NULL DEFAULT '', loop_stopped INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY (repo, pr)
);
CREATE TABLE IF NOT EXISTS pr_review_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo TEXT NOT NULL, pr INTEGER NOT NULL, kind TEXT NOT NULL, gh_id TEXT NOT NULL,
    thread TEXT, in_reply_to TEXT, path TEXT, line INTEGER, commit_sha TEXT,
    author TEXT NOT NULL, author_type TEXT NOT NULL, association TEXT, review_state TEXT,
    body TEXT NOT NULL, body_digest TEXT NOT NULL,
    created_at TEXT NOT NULL, edited_at TEXT NOT NULL, rev INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL, reason TEXT, note TEXT NOT NULL DEFAULT '',
    holder TEXT NOT NULL DEFAULT '', round INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0, touched_at TEXT NOT NULL DEFAULT '',
    fix_commit TEXT NOT NULL DEFAULT '', fix_pending INTEGER NOT NULL DEFAULT 0,
    reply TEXT NOT NULL DEFAULT '', received_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE (repo, pr, kind, gh_id)
);
CREATE INDEX IF NOT EXISTS pr_review_comments_pr ON pr_review_comments(repo, pr, status);
CREATE TABLE IF NOT EXISTS pr_review_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    repo TEXT NOT NULL, pr INTEGER NOT NULL, comment_id INTEGER, at TEXT NOT NULL,
    kind TEXT NOT NULL, from_status TEXT, to_status TEXT, reason TEXT,
    actor TEXT NOT NULL DEFAULT '', round INTEGER NOT NULL DEFAULT 0, rev INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS pr_review_events_pr ON pr_review_events(repo, pr, seq);
CREATE TABLE IF NOT EXISTS pr_review_boards (
    board TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0,
    enabled_by TEXT NOT NULL DEFAULT '', rounds INTEGER NOT NULL DEFAULT 2,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pr_review_people (
    login TEXT PRIMARY KEY COLLATE NOCASE,
    role TEXT NOT NULL CHECK (role IN ('trusted', 'own')),
    added_by TEXT NOT NULL, added_at TEXT NOT NULL
);
"""


def is_protected(path: str) -> bool:
    """True when a changed path is one of the protected checks and rules. The path is
    normalised first (backslashes, ``./``, repeated slashes, case); a path that climbs with
    ``..`` is protected, because it cannot be placed."""
    parts = [p for p in str(path).replace("\\", "/").split("/") if p not in ("", ".")]
    if ".." in parts:
        return True
    clean = "/".join(parts).casefold()
    return any(
        clean == rule.casefold() or (rule.endswith("/") and clean.startswith(rule.casefold()))
        for rule in PROTECTED
    )


class Invalid(ValueError):
    """The request or the change is not acceptable (a plain 400); nothing was stored."""


class Limit(Exception):
    """A fixed limit is reached (a 429 with the reason ``intake_limit``); nothing was dropped."""


class Refused(Exception):
    """A change the status rules do not allow, with a fixed ``code`` (a 409)."""

    def __init__(self, code, message=""):
        super().__init__(message or code)
        self.code = code


def _redact(text: str) -> str:
    from commonplace.redact import redact

    return redact(text)[0]


class ReviewStore:
    """One workspace's review file. ``directory`` is a path or a callable returning it."""

    def __init__(self, directory):
        self._directory = directory if callable(directory) else lambda: directory
        self._lock = threading.RLock()
        self._conn = None
        self._path = None

    @property
    def path(self) -> Path:
        return Path(self._directory()).resolve() / FILENAME

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
            self._conn = self._path = None

    def _connection(self, create):
        path = self.path
        if path != self._path and self._conn is not None:
            self._conn.close()
            self._conn = None
        if self._conn is None:
            if not create and not path.exists():
                return None
            with GATE.creating(path):
                path.parent.mkdir(parents=True, exist_ok=True)
                conn = connect(str(path), timeout=5, check_same_thread=False, isolation_level=None)
            try:
                conn.row_factory = row_factory
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.executescript(_SCHEMA)
            except Exception:
                conn.close()
                raise
            self._conn, self._path = conn, path
        return self._conn

    # -- transactions ---------------------------------------------------------------------

    def _tx(self):
        return _Transaction(self)

    @staticmethod
    def _event(conn, row, kind, to_status, reason, actor, at, *, from_status=None):
        conn.execute(
            "INSERT INTO pr_review_events(repo, pr, comment_id, at, kind, from_status, to_status, "
            "reason, actor, round, rev) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                row["repo"],
                row["pr"],
                row["id"],
                at,
                kind,
                row["status"] if from_status is None else from_status,
                to_status,
                reason,
                actor,
                row["round"],
                row["rev"],
            ),
        )

    # -- intake ---------------------------------------------------------------------------

    def record(self, request: dict, now: datetime) -> dict:
        """Store a validated request. ``{accepted, duplicates, edited}``; one transaction."""
        repo, pr = request["repo"], request["pr"]
        at = stamp(now)
        accepted = duplicates = edited = 0
        with self._tx() as conn:
            known = conn.execute(
                "SELECT count(*) AS n FROM pr_review_comments WHERE repo=? AND pr=?", (repo, pr)
            ).fetchone()["n"]
            total = conn.execute("SELECT count(*) AS n FROM pr_review_comments").fetchone()["n"]
            people = {
                r["login"].lower(): r["role"]
                for r in conn.execute("SELECT login, role FROM pr_review_people")
            }
            for item in request["comments"]:
                row = conn.execute(
                    "SELECT * FROM pr_review_comments WHERE repo=? AND pr=? AND kind=? AND gh_id=?",
                    (repo, pr, item["kind"], item["id"]),
                ).fetchone()
                if row is None:
                    if known >= MAX_PR_COMMENTS or total >= MAX_WORKSPACE_COMMENTS:
                        raise Limit("intake_limit")
                    self._insert(conn, request, item, people, at)
                    known += 1
                    total += 1
                    accepted += 1
                elif self._update(conn, row, item, people, at):
                    edited += 1
                else:
                    duplicates += 1
            conn.execute(
                "INSERT INTO pr_review_prs(repo, pr, head, pr_state, branch, head_repo, fetched_at, "
                "changed_files, changed_head, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(repo, pr) DO UPDATE SET head=excluded.head, "
                "pr_state=CASE WHEN excluded.pr_state != '' THEN excluded.pr_state ELSE pr_state END, "
                "branch=CASE WHEN excluded.branch != '' THEN excluded.branch ELSE branch END, "
                "head_repo=CASE WHEN excluded.head_repo != '' THEN excluded.head_repo ELSE head_repo END, "
                "fetched_at=excluded.fetched_at, "
                "changed_files=CASE WHEN excluded.changed_head != '' THEN excluded.changed_files "
                "WHEN excluded.head != head THEN '[]' ELSE changed_files END, "
                "changed_head=CASE WHEN excluded.changed_head != '' THEN excluded.changed_head "
                "WHEN excluded.head != head THEN '' ELSE changed_head END, "
                "updated_at=excluded.updated_at",
                (
                    repo,
                    pr,
                    request["head"],
                    request["pr_state"],
                    request["branch"] or "",
                    request["head_repo"] or "",
                    request["fetched_at"],
                    json.dumps(request["changed_files"]),
                    request["head"] if request["changed_files"] else "",
                    at,
                    at,
                ),
            )
        return {"accepted": accepted, "duplicates": duplicates, "edited": edited}

    @staticmethod
    def _classify(item, people):
        """``(status, reason)`` a comment starts in."""
        if item["removed"]:
            return st.SKIPPED, "removed_by_reviewer"
        login = item["author"].lower()
        if people.get(login) == "own" or REPLY_MARKER in item["body"]:
            return st.SKIPPED, "own_reply"
        if item["kind"] == "review" and is_verdict(item["body"]):
            return st.SKIPPED, "verdict_line"
        if people.get(login) == "trusted" or item["association"] in TRUSTED_ASSOCIATIONS:
            return st.NEW, None
        return st.SKIPPED, "author_not_trusted"

    def _insert(self, conn, request, item, people, at):
        status, reason = self._classify(item, people)
        cursor = conn.execute(
            "INSERT INTO pr_review_comments(repo, pr, kind, gh_id, thread, in_reply_to, path, line, "
            "commit_sha, author, author_type, association, review_state, body, body_digest, "
            "created_at, edited_at, rev, status, reason, received_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?)",
            (
                request["repo"],
                request["pr"],
                item["kind"],
                item["id"],
                item["thread"],
                item["in_reply_to"],
                item["path"],
                item["line"],
                item["commit"],
                item["author"],
                item["author_type"],
                item["association"],
                item["review_state"],
                item["body"],
                item["digest"],
                item["created_at"],
                item["edited_at"],
                status,
                reason,
                at,
                at,
            ),
        )
        row = conn.execute(
            "SELECT * FROM pr_review_comments WHERE id=?", (cursor.lastrowid,)
        ).fetchone()
        self._event(conn, row, "arrived", status, reason, "feeder", at, from_status="")

    def _update(self, conn, row, item, people, at) -> bool:
        """Apply a repeated comment. True when it changed something (an edit or a removal)."""
        if item["removed"]:
            if row["status"] in (st.NEW, st.WORKING, st.NEEDS_YOU):
                self._move(
                    conn, row, st.SKIPPED, "removed_by_reviewer", "feeder", at, kind="removed"
                )
                return True
            return False
        if item["edited_at"] <= row["edited_at"]:
            return False
        if item["digest"] == row["body_digest"]:
            conn.execute(
                "UPDATE pr_review_comments SET edited_at=? WHERE id=?",
                (item["edited_at"], row["id"]),
            )
            return False
        status, reason = self._classify(item, people)
        conn.execute(
            "UPDATE pr_review_comments SET body=?, body_digest=?, edited_at=?, rev=rev+1, "
            "status=?, reason=?, note='', holder='', fix_commit='', fix_pending=0, updated_at=? "
            "WHERE id=?",
            (item["body"], item["digest"], item["edited_at"], status, reason, at, row["id"]),
        )
        self._event(
            conn,
            conn.execute("SELECT * FROM pr_review_comments WHERE id=?", (row["id"],)).fetchone(),
            "edited",
            status,
            reason,
            "feeder",
            at,
            from_status=row["status"],
        )
        return True

    def _move(self, conn, row, to_status, reason, actor, at, *, kind="moved", **fields):
        """One conditional update of a comment's status, with its history row."""
        done = conn.execute(
            "UPDATE pr_review_comments SET status=?, reason=?, updated_at=?, touched_at=?, "
            "holder=CASE WHEN ?='working' THEN holder ELSE '' END, "
            "note=COALESCE(?, note), reply=COALESCE(?, reply), "
            "fix_commit=COALESCE(?, fix_commit), fix_pending=COALESCE(?, fix_pending) "
            "WHERE id=? AND status=? AND rev=?",
            (
                to_status,
                reason,
                at,
                at,
                to_status,
                fields.get("note"),
                fields.get("reply"),
                fields.get("fix_commit"),
                fields.get("fix_pending"),
                row["id"],
                row["status"],
                row["rev"],
            ),
        )
        if done.rowcount != 1:
            raise Refused("comment_changed")
        self._event(conn, row, kind, to_status, reason, actor, at)

    # -- reads ----------------------------------------------------------------------------

    @staticmethod
    def shown(row) -> dict:
        """One comment as the API shows it: the redacted words, the status in words, the reason."""
        out = {
            "id": row["id"],
            "kind": row["kind"],
            "gh_id": row["gh_id"],
            "thread": row["thread"],
            "in_reply_to": row["in_reply_to"],
            "path": row["path"],
            "line": row["line"],
            "author": row["author"],
            "author_type": row["author_type"],
            "review_state": row["review_state"],
            "text": _redact(row["body"]),
            "text_redacted": _redact(row["body"]) != row["body"],
            "created_at": row["created_at"],
            "edited_at": row["edited_at"],
            "rev": row["rev"],
            "status": row["status"],
            "status_text": st.WORDS[row["status"]],
            "round": row["round"],
            "fix_commit": row["fix_commit"] or None,
            "fix_confirmed": bool(row["fix_commit"]) and not row["fix_pending"],
            "reply": row["reply"] or None,
            "note": row["note"] or None,
            "updated_at": row["updated_at"],
        }
        out.update(st.reason_fields(row["reason"]))
        return out

    def comments(self, repo: str, pr: int, limit: int = 200, offset: int = 0) -> list[dict]:
        """One page of a PR's comments (thread order); ``limit`` is at most 500."""
        with self._lock:
            conn = self._connection(create=False)
            if conn is None:
                return []
            rows = conn.execute(
                "SELECT * FROM pr_review_comments WHERE repo=? AND pr=? ORDER BY thread, id "
                "LIMIT ? OFFSET ?",
                (repo, pr, min(limit, 500), offset),
            ).fetchall()
        return [self.shown(r) for r in rows]

    def original(self, comment_id: int) -> dict | None:
        """The stored words of one comment, verbatim (for a person who can open its card)."""
        with self._lock:
            conn = self._connection(create=False)
            row = (
                None
                if conn is None
                else conn.execute(
                    "SELECT repo, pr, body FROM pr_review_comments WHERE id=?", (comment_id,)
                ).fetchone()
            )
        return dict(row) if row else None

    def pr(self, repo: str, pr: int) -> dict | None:
        with self._lock:
            conn = self._connection(create=False)
            if conn is None:
                return None
            row = conn.execute(
                "SELECT * FROM pr_review_prs WHERE repo=? AND pr=?", (repo, pr)
            ).fetchone()
        return self._pr_view(row) if row else None

    @staticmethod
    def _pr_view(row) -> dict:
        out = dict(row)
        out["changed_files"] = json.loads(out["changed_files"] or "[]")
        out["loop_stopped"] = bool(out["loop_stopped"])
        return out

    def prs(self, limit: int = 200) -> list[dict]:
        """Up to ``limit`` PRs, the most recently updated first."""
        with self._lock:
            conn = self._connection(create=False)
            if conn is None:
                return []
            rows = conn.execute(
                "SELECT * FROM pr_review_prs ORDER BY updated_at DESC, repo, pr LIMIT ?", (limit,)
            ).fetchall()
        return [self._pr_view(r) for r in rows]

    def counts(self, repo: str, pr: int) -> dict:
        with self._lock:
            conn = self._connection(create=False)
            rows = (
                []
                if conn is None
                else conn.execute(
                    "SELECT status, count(*) AS n FROM pr_review_comments WHERE repo=? AND pr=? "
                    "GROUP BY status",
                    (repo, pr),
                ).fetchall()
            )
        counts = {s: 0 for s in st.STATUSES}
        counts.update({r["status"]: r["n"] for r in rows})
        return counts

    def history(self, repo: str, pr: int, limit: int = 200) -> list[dict]:
        with self._lock:
            conn = self._connection(create=False)
            if conn is None:
                return []
            rows = conn.execute(
                "SELECT seq, comment_id, at, kind, from_status, to_status, reason, actor, round, rev "
                "FROM pr_review_events WHERE repo=? AND pr=? ORDER BY seq DESC LIMIT ?",
                (repo, pr, limit),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def waiting_prs(self, limit: int = WAITING_PRS_LIMIT) -> list[dict]:
        """Up to ``limit`` PRs with at least one comment ``new`` (what the trigger looks at),
        the longest-waiting first."""
        with self._lock:
            conn = self._connection(create=False)
            if conn is None:
                return []
            rows = conn.execute(
                "SELECT p.* FROM pr_review_prs p WHERE EXISTS (SELECT 1 FROM pr_review_comments c "
                "WHERE c.repo=p.repo AND c.pr=p.pr AND c.status='new') "
                "ORDER BY p.updated_at, p.repo, p.pr LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._pr_view(r) for r in rows]

    # -- board settings and people ---------------------------------------------------------

    def board(self, slug: str) -> dict:
        with self._lock:
            conn = self._connection(create=False)
            row = (
                None
                if conn is None
                else conn.execute(
                    "SELECT * FROM pr_review_boards WHERE board=?", (slug,)
                ).fetchone()
            )
        if row is None:
            return {"board": slug, "enabled": False, "enabled_by": "", "rounds": ROUNDS_DEFAULT}
        return {**dict(row), "enabled": bool(row["enabled"])}

    def set_board(self, slug: str, enabled: bool, by: str, rounds, now: datetime) -> dict:
        if rounds is None:
            rounds = ROUNDS_DEFAULT
        if isinstance(rounds, bool) or not isinstance(rounds, int) or not 1 <= rounds <= ROUNDS_MAX:
            raise Invalid(f"rounds must be a whole number from 1 to {ROUNDS_MAX}")
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO pr_review_boards(board, enabled, enabled_by, rounds, updated_at) "
                "VALUES (?,?,?,?,?) ON CONFLICT(board) DO UPDATE SET enabled=excluded.enabled, "
                "enabled_by=excluded.enabled_by, rounds=excluded.rounds, "
                "updated_at=excluded.updated_at",
                (slug, int(bool(enabled)), by if enabled else "", rounds, stamp(now)),
            )
        return self.board(slug)

    def enabled_boards(self) -> dict:
        with self._lock:
            conn = self._connection(create=False)
            if conn is None:
                return {}
            rows = conn.execute("SELECT * FROM pr_review_boards WHERE enabled=1").fetchall()
        return {r["board"]: dict(r) for r in rows}

    def people(self) -> list[dict]:
        with self._lock:
            conn = self._connection(create=False)
            if conn is None:
                return []
            rows = conn.execute(
                "SELECT login, role, added_by, added_at FROM pr_review_people ORDER BY login"
            ).fetchall()
        return [dict(r) for r in rows]

    def set_person(self, login: str, role: str | None, by: str, now: datetime) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.\[\]-]{1,100}", login or ""):
            raise Invalid("login is not valid")
        with self._tx() as conn:
            if role is None:
                conn.execute("DELETE FROM pr_review_people WHERE login=?", (login,))
                return
            if role not in ("trusted", "own"):
                raise Invalid("role must be trusted or own")
            conn.execute(
                "INSERT INTO pr_review_people(login, role, added_by, added_at) VALUES (?,?,?,?) "
                "ON CONFLICT(login) DO UPDATE SET role=excluded.role",
                (login, role, by, stamp(now)),
            )
            # A comment skipped only for its author, now from a trusted person, is work again.
            if role == "trusted":
                rows = conn.execute(
                    "SELECT * FROM pr_review_comments WHERE author=? COLLATE NOCASE AND status='skipped' "
                    "AND reason='author_not_trusted'",
                    (login,),
                ).fetchall()
                for row in rows:
                    self._move(conn, row, st.NEW, None, by, stamp(now), kind="trusted")

    # -- the round: take, report, settle -------------------------------------------------------

    def take_round(
        self, repo: str, pr: int, holder: str, now: datetime, *, rounds: int
    ) -> list[dict]:
        """Start a round for ``holder``: the ``new`` comments (up to 25, thread order) become
        ``working``. ``rounds_exhausted`` when the PR has used its rounds; ``[]`` when nothing waits.
        A holder that already has comments working gets them again (the run was resumed)."""
        at = stamp(now)
        with self._tx() as conn:
            pr_row = conn.execute(
                "SELECT * FROM pr_review_prs WHERE repo=? AND pr=?", (repo, pr)
            ).fetchone()
            if pr_row is None:
                return []
            mine = conn.execute(
                "SELECT * FROM pr_review_comments WHERE repo=? AND pr=? AND status='working' "
                "AND holder=? ORDER BY thread, id",
                (repo, pr, holder),
            ).fetchall()
            if mine:
                conn.execute(
                    "UPDATE pr_review_comments SET touched_at=? WHERE repo=? AND pr=? "
                    "AND status='working' AND holder=?",
                    (at, repo, pr, holder),
                )
                return [self.shown(r) for r in mine]
            if pr_row["rounds_used"] >= min(rounds, pr_row["rounds_allowed"]):
                raise Refused("rounds_exhausted")
            waiting = conn.execute(
                "SELECT * FROM pr_review_comments WHERE repo=? AND pr=? AND status='new' "
                "ORDER BY thread, id LIMIT ?",
                (repo, pr, RUN_COMMENTS),
            ).fetchall()
            if not waiting:
                return []
            number = pr_row["rounds_used"] + 1
            conn.execute(
                "UPDATE pr_review_prs SET rounds_used=?, last_round_at=?, updated_at=? "
                "WHERE repo=? AND pr=?",
                (number, at, at, repo, pr),
            )
            taken = []
            for row in waiting:
                conn.execute(
                    "UPDATE pr_review_comments SET status='working', holder=?, round=?, "
                    "attempts=attempts+1, touched_at=?, updated_at=?, reason=NULL "
                    "WHERE id=? AND status='new'",
                    (holder, number, at, at, row["id"]),
                )
                fresh = conn.execute(
                    "SELECT * FROM pr_review_comments WHERE id=?", (row["id"],)
                ).fetchone()
                self._event(conn, fresh, "taken", st.WORKING, None, holder, at, from_status=st.NEW)
                taken.append(self.shown(fresh))
            return taken

    def report(
        self,
        comment_id: int,
        holder: str,
        rev: int,
        outcome: str,
        now: datetime,
        *,
        commit=None,
        reply=None,
        note=None,
    ) -> dict:
        """One Coworker outcome for one comment it holds. Idempotent for the same outcome."""
        at = stamp(now)
        if outcome not in st.REPORTED:
            raise Invalid("outcome must be addressed, answered or needs_you")
        if commit is not None and not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", str(commit)):
            raise Invalid("commit must be a full lowercase commit SHA")
        if outcome == st.ADDRESSED and not commit:
            raise Invalid("a fixed comment needs the commit that fixes it")
        reply = _redact(reply.strip()) if isinstance(reply, str) and reply.strip() else None
        if reply is not None and len(reply) > MAX_REPLY:
            raise Invalid(f"reply is longer than {MAX_REPLY} characters")
        if outcome == st.ANSWERED and not reply:
            raise Invalid("an answered comment needs the reply that answers it")
        note = _redact(note.strip()) if isinstance(note, str) and note.strip() else None
        if note is not None and len(note) > MAX_NOTE:
            raise Invalid(f"note is longer than {MAX_NOTE} characters")
        if outcome == st.NEEDS_YOU and not note:
            raise Invalid("a comment that needs a person needs a note saying why")
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM pr_review_comments WHERE id=?", (comment_id,)
            ).fetchone()
            if row is None:
                raise Refused("not_found")
            if row["status"] == outcome and row["holder"] == "" and row["rev"] == rev:
                return self.shown(row)  # a repeated report: the stored state, no second move
            if row["rev"] != rev:
                raise Refused("comment_changed")
            if row["status"] != st.WORKING or row["holder"] != holder:
                raise Refused("not_working", "this comment is not held by you")
            if outcome == st.ADDRESSED:
                pr_row = conn.execute(
                    "SELECT * FROM pr_review_prs WHERE repo=? AND pr=?", (row["repo"], row["pr"])
                ).fetchone()
                code = self._commit_problem(pr_row, commit)
                pending = 1 if code in PENDING else 0
                if code not in (None, *PENDING):
                    self._move(
                        conn,
                        row,
                        st.NEEDS_YOU,
                        code,
                        holder,
                        at,
                        kind="reported",
                        fix_commit=commit,
                        fix_pending=0,
                        reply=reply,
                        note=note,
                    )
                    return self._shown_id(conn, comment_id)
                if pending:
                    # The feeder has not seen this commit yet: the comment stays working until
                    # it does (or the grace passes), so "addressed" always names a real commit.
                    conn.execute(
                        "UPDATE pr_review_comments SET fix_commit=?, fix_pending=1, reply=COALESCE(?, reply), "
                        "touched_at=?, updated_at=? WHERE id=?",
                        (commit, reply, at, at, comment_id),
                    )
                    return self._shown_id(conn, comment_id)
            self._move(
                conn,
                row,
                outcome,
                "coworker_asks" if outcome == st.NEEDS_YOU else None,
                holder,
                at,
                kind="reported",
                fix_commit=commit,
                fix_pending=0,
                reply=reply,
                note=note,
            )
            return self._shown_id(conn, comment_id)

    def _shown_id(self, conn, comment_id):
        return self.shown(
            conn.execute("SELECT * FROM pr_review_comments WHERE id=?", (comment_id,)).fetchone()
        )

    @staticmethod
    def _commit_problem(pr_row, commit):
        """``None`` when the commit is the PR head the feeder saw and the feeder reported that
        head's changed files, none of them protected. ``"pending"`` while the feeder has not seen
        the commit, ``"files_pending"`` while it has not reported that head's changed files (the
        list is unknown, never taken from an earlier head), else the reason code."""
        if pr_row is None or pr_row["head"] != commit:
            return "pending"
        if pr_row["changed_head"] != commit:
            return "files_pending"
        for path in json.loads(pr_row["changed_files"] or "[]"):
            if is_protected(path):
                return "touches_checks"
        return None

    def settle(self, now: datetime) -> dict:
        """Housekeeping the trigger runs: pending fixes meet the feeder's head, stale working
        comments return to ``new``. ``{confirmed, held, requeued, asked}`` counts."""
        at = stamp(now)
        out = {"confirmed": 0, "held": 0, "requeued": 0, "asked": 0}
        with self._lock:  # a workspace that never used this has no file, and a quiet one no write
            conn = self._connection(create=False)
            if (
                conn is None
                or not conn.execute(
                    "SELECT 1 FROM pr_review_comments WHERE status='working' LIMIT 1"
                ).fetchone()
            ):
                return out
        with self._tx() as conn:
            for row in conn.execute(
                "SELECT * FROM pr_review_comments WHERE status='working' AND fix_pending=1"
            ).fetchall():
                pr_row = conn.execute(
                    "SELECT * FROM pr_review_prs WHERE repo=? AND pr=?", (row["repo"], row["pr"])
                ).fetchone()
                code = self._commit_problem(pr_row, row["fix_commit"])
                if code is None:
                    self._move(
                        conn,
                        row,
                        st.ADDRESSED,
                        None,
                        row["holder"],
                        at,
                        kind="confirmed",
                        fix_pending=0,
                    )
                    out["confirmed"] += 1
                elif code not in PENDING:
                    self._move(
                        conn, row, st.NEEDS_YOU, code, row["holder"], at, kind="held", fix_pending=0
                    )
                    out["held"] += 1
                elif stamp(now - COMMIT_GRACE) > row["touched_at"] and (
                    pr_row is not None and pr_row["fetched_at"] > row["touched_at"]
                ):
                    self._move(
                        conn,
                        row,
                        st.NEEDS_YOU,
                        "commit_not_found" if code == "pending" else "files_unknown",
                        row["holder"],
                        at,
                        kind="held",
                        fix_pending=0,
                    )
                    out["held"] += 1
            old = stamp(now - STALE_AFTER)
            for row in conn.execute(
                "SELECT * FROM pr_review_comments WHERE status='working' AND fix_pending=0 "
                "AND touched_at != '' AND touched_at < ?",
                (old,),
            ).fetchall():
                if row["attempts"] >= MAX_ATTEMPTS - 1:
                    self._move(conn, row, st.NEEDS_YOU, "run_lost", "system", at, kind="lost")
                    out["asked"] += 1
                else:
                    self._move(conn, row, st.NEW, "run_failed", "system", at, kind="requeued")
                    out["requeued"] += 1
                self._give_back(conn, row)
        return out

    @staticmethod
    def _give_back(conn, row):
        """A round whose run reported nothing does not count against the PR's rounds."""
        reported = conn.execute(
            "SELECT count(*) AS n FROM pr_review_comments WHERE repo=? AND pr=? AND round=? "
            "AND status IN ('addressed', 'answered', 'working')",
            (row["repo"], row["pr"], row["round"]),
        ).fetchone()["n"]
        if not reported:
            conn.execute(
                "UPDATE pr_review_prs SET rounds_used=CASE WHEN rounds_used > 0 THEN rounds_used-1 "
                "ELSE 0 END WHERE repo=? AND pr=? AND rounds_used >= ?",
                (row["repo"], row["pr"], row["round"]),
            )

    def hold(self, repo: str, pr: int, reason: str, actor: str, now: datetime, *, note=None) -> int:
        """Move every waiting (``new``) comment of a PR to ``needs_you`` for ``reason``."""
        at = stamp(now)
        n = 0
        with self._tx() as conn:
            for row in conn.execute(
                "SELECT * FROM pr_review_comments WHERE repo=? AND pr=? AND status='new'",
                (repo, pr),
            ).fetchall():
                self._move(conn, row, st.NEEDS_YOU, reason, actor, at, kind="held", note=note)
                n += 1
        return n

    def decide(self, repo: str, pr: int, choice: str, by: str, now: datetime) -> dict:
        """A person's answer to the one Needs me item of a PR: ``again`` (two more rounds and the
        held comments return to ``new``), ``mine`` (the loop stops for this PR) or ``dismiss``
        (the comments that wait become ``skipped``)."""
        at = stamp(now)
        if choice not in ("again", "mine", "dismiss"):
            raise Invalid("choice must be again, mine or dismiss")
        with self._tx() as conn:
            pr_row = conn.execute(
                "SELECT * FROM pr_review_prs WHERE repo=? AND pr=?", (repo, pr)
            ).fetchone()
            if pr_row is None:
                raise Refused("not_found")
            held = conn.execute(
                "SELECT * FROM pr_review_comments WHERE repo=? AND pr=? AND status='needs_you'",
                (repo, pr),
            ).fetchall()
            if choice == "again":
                conn.execute(
                    "UPDATE pr_review_prs SET rounds_used=0, loop_stopped=0, updated_at=? "
                    "WHERE repo=? AND pr=?",
                    (at, repo, pr),
                )
                for row in held:
                    self._move(conn, row, st.NEW, None, by, at, kind="again")
            elif choice == "mine":
                conn.execute(
                    "UPDATE pr_review_prs SET loop_stopped=1, updated_at=? WHERE repo=? AND pr=?",
                    (at, repo, pr),
                )
                for row in conn.execute(
                    "SELECT * FROM pr_review_comments WHERE repo=? AND pr=? "
                    "AND status IN ('new', 'needs_you')",
                    (repo, pr),
                ).fetchall():
                    self._move(conn, row, st.SKIPPED, "handled_by_person", by, at, kind="mine")
            else:
                for row in conn.execute(
                    "SELECT * FROM pr_review_comments WHERE repo=? AND pr=? "
                    "AND status IN ('new', 'needs_you')",
                    (repo, pr),
                ).fetchall():
                    self._move(
                        conn, row, st.SKIPPED, "dismissed_by_person", by, at, kind="dismissed"
                    )
        return {"choice": choice, "counts": self.counts(repo, pr)}


class _Transaction:
    """One ``BEGIN IMMEDIATE`` on the store's connection, held under its lock."""

    def __init__(self, store):
        self.store = store

    def __enter__(self):
        self.store._lock.acquire()
        try:
            self.conn = self.store._connection(create=True)
            self.conn.execute("BEGIN IMMEDIATE")
        except BaseException:
            self.store._lock.release()
            raise
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        try:
            self.conn.execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            self.store._lock.release()
        return False


def open_review(gateway, workspace_id: str) -> ReviewStore:
    """The gateway's one store for a workspace; it follows the workspace's directory."""
    stores = gateway.__dict__.setdefault("_pr_review_stores", {})
    if workspace_id not in stores:
        supervisor = gateway.supervisor
        stores[workspace_id] = ReviewStore(lambda: supervisor.workspace_dir(workspace_id))
    return stores[workspace_id]
