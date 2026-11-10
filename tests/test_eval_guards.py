"""Base guards that eval slice 1 relies on (class A, plus the C4 control).

Every test here passes on the code eval was planned against and fails under a
named mutation of that base code. None imports ``commonplace.evals``:
principals are built inline with the eval field list, so the file also runs on
revisions without eval.
"""

from __future__ import annotations

import ast
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from commonplace import capture, continuity as core, continuity_schema as schema
from commonplace.control_db import ControlDB, Principal
from commonplace.handoff.models import ThreadMessage
from commonplace.handoff.recall import Recall, ThreadNotFound
from commonplace.handoff.thread_store import ThreadConflict, ThreadStore
from commonplace.handoff.thread_store_migrate import read_store
from commonplace.import_grant import grant_id, reader_eligible
from commonplace.import_sources import parse_import
from commonplace.policy import Policy

ROOT = Path(__file__).resolve().parents[1]
EVAL_SCOPES = ["threads:read", "memory:read"]


def principal(user, ws, *, auth="local", scopes=EVAL_SCOPES, role="owner"):
    """The eval principal's field list; ``auth``/``scopes`` vary only where named."""
    return Principal(
        user_id=user,
        user_email="owner@example.test",
        workspace_id=ws,
        connection_id="",
        connection="cli",
        client="commonplace",
        auth=auth,
        role=role,
        policy=Policy.build(
            profile="full", scopes=list(scopes), read_wings=["*"], trust="trusted"
        ).for_role(role),
    )


def native(sid="S", word="first"):
    record = dict(type="user", sessionId=sid, message=dict(role="user", content=word))
    return (json.dumps(record) + "\n").encode()


def sha(raw):
    import hashlib

    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("COMMONPLACE_PLANS", "off")
    db = ControlDB(str(tmp_path / "control.sqlite3"))
    user = db.create_user("owner@example.test")
    ws = db.create_workspace("Synthetic", user, personal=True)
    schema.ensure_schema(db)
    store = ThreadStore(str(tmp_path / "threads.sqlite3"))
    yield db, store, user, ws
    store.close()
    db.close()


def push(db, store, user, ws, raw, *, stream, before=b""):
    with db._lock, db._tx() as conn:
        return core.stream_push(
            conn,
            store,
            ws=ws,
            user_id=user,
            connection_id="",
            kind="capture",
            stream_id=stream,
            source="claude-code",
            native_source="claude-code",
            data=raw,
            offset=len(before),
            prefix=sha(before),
            actor="synthetic",
            maximum=None,
            gate=lambda: None,
        )


def referenced(db):
    conn = db._conn
    ids = [r[0] for r in conn.execute("SELECT own_stored_id FROM sync_streams")]
    ids += [r[0] for r in conn.execute("SELECT fork_stored_id FROM sync_streams") if r[0]]
    ids += [r[0] for r in conn.execute("SELECT holder_stored_id FROM sync_groups")]
    return ids


# ── A1: read-only opener ──
def test_a1_read_store_creates_nothing_and_keeps_main_bytes(tmp_path):
    missing = tmp_path / "missing" / "threads.sqlite3"
    missing.parent.mkdir()
    with pytest.raises(sqlite3.Error):
        with read_store(missing) as store:
            store.messages("thr_00")
    assert list(missing.parent.iterdir()) == [], "A1: read_store created a file"

    path = tmp_path / "threads.sqlite3"
    live = ThreadStore(str(path))
    stored = live.store(
        "ws_a", "t3:x", [ThreadMessage(0, "user", "message", "hi", "{}")], actor="a"
    )
    live.close()
    before = path.read_bytes()
    with read_store(path) as store:
        assert store.messages(stored.id)[0].text == "hi"
    assert path.read_bytes() == before, "A1: main file bytes changed"


# ── A2: adoption takes only private threads ──
@pytest.mark.parametrize("wing", ["threads", "notes"])
def test_a2_adoption_rejects_non_private_wings(env, wing):
    db, store, user, ws = env
    raw = native()
    rows = parse_import("claude-code", raw.decode())
    other = store.store(ws, grant_id(user, "claude-code", "x"), rows, actor="a", wing=wing)
    private = store.store(
        ws, grant_id(user, "claude-code", "y"), rows, actor="a", wing=capture.PRIVATE_WING
    )

    def read(thread):
        messages = store.messages(thread.id)
        segments = schema.archive_segments(messages)
        return thread, b"".join(s.raw for s in segments), "", messages

    prefix = f"import-private:{user}:"
    assert core._adoption_candidate(private, prefix, "import", "", read) is not None
    assert core._adoption_candidate(other, prefix, "import", "", read) is None, (
        "A2: a non-private thread became an adoption candidate"
    )
    core.backfill(db, store, ws, user)
    assert other.id not in referenced(db), "A2: backfill adopted a non-private thread"


# ── A3: every stored ID a sync row references is private ──
def test_a3_continuity_writers_reference_only_private_rows(env):
    db, store, user, ws = env
    first, second, third = native(), native(word="second"), native(word="third")
    push(db, store, user, ws, first, stream=f"capture:{user}:claude-code:a")
    push(db, store, user, ws, first + second, stream=f"capture:{user}:claude-code:b")
    push(db, store, user, ws, first + third, stream=f"capture:{user}:claude-code:c")
    store.store(
        ws,
        grant_id(user, "claude-code", "legacy"),
        parse_import("claude-code", (first + second).decode()),
        actor="a",
        wing=capture.PRIVATE_WING,
    )
    core.backfill(db, store, ws, user)
    ids = referenced(db)
    assert ids
    markers = [i for i in ids if store.messages(i, 0, 0)[0].name == schema.MARKER]
    assert markers, "fixture must create at least one marker"
    assert all(store.get(i).wing == capture.PRIVATE_WING for i in ids), (
        "A3: a sync row references a non-private stored thread"
    )


def test_a3_threads_wing_collision_raises_and_adds_no_sync_row(env):
    db, store, user, ws = env
    # An existing group for the same native session is what would let a
    # marker-shaped row join as ``own``; without one the push refuses anyway.
    push(db, store, user, ws, native(), stream=f"capture:{user}:claude-code:first")
    identity = f"capture:{user}:claude-code:z"
    collision = store.store(
        ws,
        identity,
        [ThreadMessage(0, "system", "other", "", json.dumps({"v": 1}), name=schema.MARKER)],
        actor="a",
        wing="threads",
    )
    with pytest.raises(ThreadConflict):
        push(db, store, user, ws, native(), stream=identity)
    assert collision.id not in referenced(db), "A3: a threads-wing row became own_stored_id"


# ── A4: nothing rewrites wing/source_thread_id or deletes thread rows ──
_CONTENT_TABLES = r"(threads|messages|line|message|block|blob|dict)"
_SET_TARGET = re.compile(r"\b(wing|source_thread_id)\s*=", re.I)
_UPDATE_THREADS = re.compile(r"\bUPDATE\s+[\"\[`]?threads\b[\"\]`]?\s+SET\s+(.*)", re.I | re.S)
_UPSERT_THREADS = re.compile(
    r"\bINSERT\s+(OR\s+\w+\s+)?INTO\s+[\"\[`]?threads\b.*\bDO\s+UPDATE\s+SET\s+(.*)", re.I | re.S
)
_DESTRUCTIVE = re.compile(
    r"\b(DELETE\s+FROM|DROP\s+TABLE(\s+IF\s+EXISTS)?|REPLACE\s+INTO|INSERT\s+OR\s+REPLACE\s+INTO)"
    r"\s+[\"\[`]?" + _CONTENT_TABLES + r"\b",
    re.I,
)
# Clause (iii), case-insensitive. A statement keyword must open the string or
# follow ``;``, a newline, ``(`` or ``)`` (a CTE: ``WITH c AS (...) DELETE``).
# A placeholder table name (f-string or ``+`` part, ``%s``, ``{…}``) or the end
# of the string must follow, and then a SQL-shaped tail: ``UPDATE <t> SET``,
# ``DELETE FROM <t>`` before the end, ``;``, ``)``, ``WHERE`` or ``RETURNING``,
# and ``DROP TABLE <t>`` before the end, ``;`` or ``)``. Prose that opens with a
# keyword and a brace ("Delete from {path} failed", "Update {name} to retry")
# therefore stays out.
_ANCHOR = r"(?:^|[;\n()])\s*"
_NAME = r"(?:\x00|%s|\{[^{}]*\})"
_DYNAMIC = re.compile(
    _ANCHOR + r"DELETE\s+FROM\s*(?:$|" + _NAME + r"\s*(?=$|[;)]|WHERE\b|RETURNING\b))"
    r"|" + _ANCHOR + r"DROP\s+TABLE(?:\s+IF\s+EXISTS)?\s*(?:$|" + _NAME + r"\s*(?=$|[;)]))"
    r"|" + _ANCHOR + r"UPDATE\s+(?:OR\s+\w+\s+)?" + _NAME + r"\s+SET\b",
    re.I,
)
# Dynamic table names that are reviewed and touch no thread-store table.
ALLOWED_DYNAMIC = {
    ("mempalace/backends/pgvector.py", "DELETE FROM \x00 WHERE id = ANY(%s)"): "pgvector drawers",
    ("mempalace/backends/pgvector.py", "DELETE FROM \x00 WHERE \x00"): "pgvector drawers",
    ("mempalace/backends/pgvector.py", "DROP TABLE IF EXISTS \x00"): "pgvector drop_table",
    ("mempalace/fact_index.py", "DROP TABLE IF EXISTS \x00"): "three literal fact_search tables",
}


def _render(node):
    """Text of a string expression; non-string parts become ``\x00``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(_render(part) for part in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _render(node.left) + _render(node.right)
    return "\x00"


def _is_string(node):
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _is_string(node.left) or _is_string(node.right)
    return isinstance(node, ast.JoinedStr) or (
        isinstance(node, ast.Constant) and isinstance(node.value, str)
    )


def _statements(tree):
    """String constants, f-strings and ``+`` chains that contain a string.

    Only parts of an f-string or a ``+`` chain are nested; the literal of a
    ``%``-formatted string is scanned on its own (``"... %s" % value``).
    """
    nested = {
        id(child)
        for node in ast.walk(tree)
        if isinstance(node, ast.JoinedStr)
        or (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add))
        for child in ast.iter_child_nodes(node)
    }
    for node in ast.walk(tree):
        if id(node) not in nested and _is_string(node):
            yield node.lineno, _render(node)


def _findings(relative, source):
    found = []
    for line, text in _statements(ast.parse(source)):
        for match in (_UPDATE_THREADS.search(text), _UPSERT_THREADS.search(text)):
            if match and _SET_TARGET.search(match.group(match.lastindex)):
                found.append((relative, line, "rewrites wing or source_thread_id"))
        if _DESTRUCTIVE.search(text):
            found.append((relative, line, "destructive thread-store statement"))
        if _DYNAMIC.search(text) and (relative, text) not in ALLOWED_DYNAMIC:
            found.append((relative, line, "dynamic table name not on the allowlist"))
    return found


def _scan():
    found = []
    for top in ("commonplace", "mempalace"):
        for path in sorted((ROOT / top).rglob("*.py")):
            relative = path.relative_to(ROOT).as_posix()
            if "/tests/" in relative or path.name.startswith("test_"):
                continue
            found += _findings(relative, path.read_text(encoding="utf-8"))
    return found


# Every snippet the earlier upper-case-only scan caught must stay caught, so the
# scan can never fall below it; plus the case and concatenation forms it missed.
CAUGHT = [
    'conn.execute("update threads set wing=? where id=?", args)',
    'conn.execute("UPDATE threads " + "SET wing=? WHERE id=?", args)',
    'conn.execute("delete from " + name)',
    'conn.execute(f"drop table if exists {name}")',
    'conn.execute("delete from messages where thread_id=?", args)',
    'conn.execute(("DELETE FROM " + "messages") + " WHERE 1")',
    'conn.execute("DELETE FROM %s WHERE id=?" % name, args)',
    'conn.execute("DELETE FROM messages WHERE thread_id=%s" % tid)',
    'conn.execute("UPDATE threads SET wing=%s WHERE id=%s" % (w, i))',
    'conn.execute("WITH c AS (SELECT 1) DELETE FROM " + name)',
    'conn.execute("BEGIN; DELETE FROM " + name)',
    'conn.execute("delete from {}".format(name))',
    'conn.execute(f"UPDATE threads SET wing={w} WHERE id={i}")',
    'conn.execute("UPDATE %s SET x=1" % table)',
    'conn.execute("DROP TABLE IF EXISTS " + name)',
    'conn.execute(f"DELETE FROM {table} WHERE id=?", args)',
]


@pytest.mark.parametrize("snippet", CAUGHT)
def test_a4_scan_catches_lower_case_and_concatenated_sql(snippet):
    assert _findings("synthetic.py", snippet), f"A4 scan missed: {snippet}"


@pytest.mark.parametrize(
    "snippet",
    [
        'note = f"please update {name} when you delete from the list"',
        'msg = f"Delete from {path} failed"',
        'msg = "Update {name} to retry"',
        'log.info("Drop table {name} skipped")',
        'conn.execute("UPDATE boards SET wing=? WHERE id=?", args)',
        'conn.execute("DELETE FROM drawers WHERE id=?", args)',
    ],
)
def test_a4_scan_ignores_prose_and_unrelated_tables(snippet):
    assert _findings("synthetic.py", snippet) == [], snippet


def test_a4_no_statement_rewrites_identity_or_deletes_thread_rows():
    assert _scan() == [], "A4: thread identity or content can be rewritten or deleted"


def test_a4_scan_sees_the_known_count_updates():
    texts = [
        text
        for path in (
            "commonplace/handoff/thread_store.py",
            "commonplace/handoff/thread_store_v2.py",
        )
        for _, text in _statements(ast.parse((ROOT / path).read_text(encoding="utf-8")))
        if _UPDATE_THREADS.search(text)
    ]
    assert len(texts) == 2 and all("sha256" in text for text in texts)


# ── A5–A6b and C4: the import branch is out of eval's reach ──
def _recall(tmp_path, db, *, eligible=lambda principal: True, view_calls=None):
    from commonplace.continuity_read import PrivateReader

    store = ThreadStore(str(tmp_path / "recall.sqlite3"))

    def store_for(_ws):
        return store

    private = PrivateReader(db, store_for)
    if view_calls is not None:

        def import_view(principal, current):
            view_calls.append(current.id)
            raise ThreadNotFound("spied")

        private.import_view = import_view
    return store, Recall(store_for, private=private, import_eligible=eligible)


def _grant_row(store, user, ws):
    stored = store.store(
        ws,
        f"import-private:{user}:src:t",
        [ThreadMessage(0, "user", "message", "imported", "{}")],
        actor="a",
        wing="private_chats",
    )
    assert re.fullmatch(r"thr_[0-9a-f]+", stored.id)
    return stored


def test_a5_scope_routing_never_enters_import_view(env, tmp_path, monkeypatch):
    db, _, user, ws = env
    store, recall = _recall(tmp_path, db)
    stored = _grant_row(store, user, ws)
    entered = []
    original = Recall._import_view

    def spy(self, principal, row):
        entered.append(row.id)
        return original(self, principal, row)

    monkeypatch.setattr(Recall, "_import_view", spy)
    who = principal(user, ws)
    assert recall.can_read(who, stored.id) is False
    with pytest.raises(ThreadNotFound):
        recall.thread(who, stored.id)
    assert entered == [], "A5: the eval principal was routed into the import view"
    store.close()


@pytest.mark.parametrize("granted", [False, True], ids=["A5b", "C4"])
def test_a5b_c4_scope_gates_the_import_view(env, tmp_path, granted):
    db, _, user, ws = env
    calls = []
    store, recall = _recall(tmp_path, db, view_calls=calls)
    stored = _grant_row(store, user, ws)
    scopes = EVAL_SCOPES + (["memory:import:read"] if granted else [])
    who = principal(user, ws, auth="pat", scopes=scopes)
    assert who.person is False
    recall.can_read(who, stored.id)
    if granted:
        assert calls == [stored.id], "C4: the fixture must reach import_view with the scope"
    else:
        assert calls == [], "A5b: import_view ran without the import read scope"
    store.close()


def test_a6_local_auth_never_reaches_the_eligibility_hook(env, tmp_path):
    db, _, user, ws = env
    hooks = []
    store, recall = _recall(tmp_path, db, eligible=lambda p: hooks.append(p) or True)
    who = principal(user, ws, scopes=EVAL_SCOPES + ["memory:import:read"])
    assert recall.import_reader(who) is False
    assert hooks == [], "A6: local auth reached the import eligibility hook"
    store.close()


def test_a6b_grant_eligibility_refuses_local_auth_before_lookup(tmp_path, monkeypatch):
    with closing(ControlDB(str(tmp_path / "grant.sqlite3"))) as db:
        user = db.create_user("owner@example.test")
        ws = db.create_workspace("Synthetic", user, personal=True)
        calls = []
        original = db.authenticate_connection
        monkeypatch.setattr(
            db, "authenticate_connection", lambda *a: calls.append(a) or original(*a)
        )
        who = principal(user, ws, scopes=EVAL_SCOPES + ["memory:import:read"])
        assert reader_eligible(db, who) is False
        assert calls == [], "A6b: local auth reached the live connection lookup"
