"""Scoped output and step observations; synthetic stores, no servers or providers."""

import json
from dataclasses import replace
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

from commonplace import ratelimit, sessions
from commonplace.chat.coworkers import livelog, steps
from commonplace.policy import Policy
from commonplace.runner import jobs as runner_jobs
from tests.test_coworker_api import API
from tests.test_coworker_dispatch import dispatch as dispatch
from tests.test_coworker_progress import progress as progress
from tests.test_coworker_runtime import env as env


@pytest.fixture
def live(env, dispatch, progress):
    api = API(env.gateway, env.wid, env.uid, env.other, "outsider")
    api.sessions[env.uid] = env.session
    api.sessions[env.other] = progress.other_session
    store = runner_jobs.open_store(env.gateway, env.wid)

    def output(item, text, *, state="started", ledger_state=None):
        with store.transaction() as conn:
            row = conn.execute(
                "SELECT id FROM runner_jobs WHERE task_number=?", (item.number,)
            ).fetchone()
            conn.execute(
                "UPDATE runner_jobs SET launch_output=?,state=? WHERE id=?",
                (text, state, row["id"]),
            )
            if ledger_state:
                conn.execute(
                    "UPDATE run_ledger SET status=?,ended_at='2026-11-14T00:00:00Z' WHERE job_id=?",
                    (ledger_state, row["id"]),
                )

    def get(item, suffix="log", *, user=None, params=None, headers=None):
        query = "?" + urlencode(params) if params else ""
        return api.request("GET", f"/{item.cid}/{suffix}{query}", user=user, headers=headers)

    return SimpleNamespace(api=api, output=output, get=get, store=store)


def test_detail_has_live_tree_and_both_log_paths_use_exact_job_output(env, progress, live):
    first, second = progress.start(), progress.start()
    live.output(first, "FIRST_OUTPUT\nReading the change")
    live.output(second, "OTHER_OUTPUT")
    for route in ("log", "log/stream"):
        status, body = live.get(first, route)
        assert status == 200
        assert [line["text"] for line in body["lines"]] == ["FIRST_OUTPUT", "Reading the change"]
        assert "OTHER_OUTPUT" not in json.dumps(body)
        assert len(body["working_on"]) == 2
        parent, child = body["working_on"]
        assert parent["parent_id"] is None and child["parent_id"] == parent["id"]
        assert child["display_name"] and child["state"] == "working"
        assert child["action_summary"] == "Working on the task"
        assert set(child) == {"id", "parent_id", "display_name", "action_summary", "state", "at"}
        assert live.get(first, route, params={"cursor": body["cursor"]})[1]["lines"] == []
    status, detail = live.api.request("GET", f"/conversations/{first.cid}")
    assert status == 200 and detail["working_on"] == body["working_on"]
    assert live.api.request("GET", f"/conversations/{first.cid}/log")[1] == live.get(first)[1]


SECRETS = [
    "ghp_" + "A" * 36,
    "sk-" + "B" * 24,
    "cmmn_" + "C" * 43,
    "AKIA" + "D" * 16,
    "Bearer " + "E" * 24,
    "cmmn_session=" + "F" * 24,
    "password=" + "G" * 24,
]
SPLITS = [
    "<b></b>",
    "&#0;",
    "\x1b[31m\x1b[0m",
    "\x1b]0;hidden\x07",
    "\x1bPhidden\x1b\\",
    "\x9b31m",
    "\r",
    r"\ud800",
    r"\u001b[31m",
    r"\x1b[31m",
    "\u200b",
    "&lt;b&gt;&lt;/b&gt;",
]


@pytest.mark.parametrize("route", ["log", "log/stream"])
@pytest.mark.parametrize("split", SPLITS)
def test_redaction_sentinel_matrix_across_tail_and_stream(progress, live, route, split):
    item = progress.start()
    for secret in SECRETS:
        # A recognizable prefix and credential must join before redaction.
        broken = secret[:2] + split + secret[2:]
        live.output(item, "before " + broken + " after")
        status, payload = live.get(item, route)
        assert status == 200
        text = "\n".join(row["text"] for row in payload["lines"])
        assert "[redacted:" in text
        assert secret not in text and secret.split("=")[-1][-16:] not in text
        assert "\x1b" not in text and "<b>" not in text and len(text) <= 500


@pytest.mark.parametrize("route", ["log", "log/stream"])
def test_private_key_multiline_paths_and_truncation_order(progress, live, route):
    item = progress.start()
    key = "-----BEGIN PRIVATE KEY-----\nPRIVATE_MATERIAL\n-----END PRIVATE KEY-----\n"
    paths = [
        "/private/person/file",
        r"C:\private\person\file",
        r"\\private\share\file",
        "~/private/file",
    ]
    live.output(item, key + "\n".join(paths) + "\n" + "word " * 98 + SECRETS[0])
    status, payload = live.get(item, route)
    assert status == 200
    text = json.dumps(payload)
    assert "PRIVATE_MATERIAL" not in text and SECRETS[0] not in text
    for path in paths:
        assert path not in "\n".join(row["text"] for row in payload["lines"])
    assert "[redacted:path]" in text
    assert all(len(row["text"]) <= 500 for row in payload["lines"])
    assert not payload["lines"][-1]["text"].endswith("[redacted:")


@pytest.mark.parametrize("route", ["log", "log/stream"])
def test_members_need_conversation_and_project_read_rights(env, dispatch, progress, live, route):
    private = progress.start()
    shared = progress.start(group=True)
    live.output(private, "PRIVATE_SENTINEL")
    live.output(shared, "SHARED_SENTINEL")
    assert live.get(private, route, user=env.other)[0] == 404
    status, body = live.get(shared, route, user=env.other)
    assert status == 200 and body["lines"][0]["text"] == "SHARED_SENTINEL"
    assert "PRIVATE_SENTINEL" not in json.dumps(body)
    # A still-valid conversation membership never overrides current project scope.
    original = sessions.person_principal

    def restrict(db, user, workspace):
        result = original(db, user, workspace)
        if result and user == env.other:
            result = replace(result, policy=Policy(tools="*", read_wings=[], write_wings=[]))
        return result

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(sessions, "person_principal", restrict)
        assert live.get(shared, route, user=env.other) == (404, {"error": "not_found"})
        assert live.api.request("GET", f"/conversations/{shared.cid}", user=env.other)[0] == 404
    with dispatch.boards.store.transaction() as conn:
        conn.execute("UPDATE boards SET archived=1 WHERE slug='work'")
    assert live.get(shared, route)[0] == 404


@pytest.mark.parametrize("route", ["log", "log/stream"])
def test_foreign_workspace_and_token_kinds_never_disclose(env, dispatch, progress, live, route):
    item = progress.start()
    live.output(item, "ISOLATED_SENTINEL")
    with env.db._lock:
        connections = env.db._conn.execute("SELECT id FROM connections").fetchall()
    # Every authorization header is refused, including a token alongside a cookie.
    for credential in ["agent-token", "machine-token", "worker-token", "", "Bearer token"]:
        assert live.get(item, route, headers={"Authorization": credential})[0] == 403
    assert connections  # Real enrolled runner identities also exist in this fixture.
    other = env.db.create_workspace("second", env.uid)
    live.api.sessions[env.uid] = sessions.start_session(env.db, env.uid, other)
    assert live.get(item, route) == (404, {"error": "not_found"})


@pytest.mark.parametrize("change", ["request_key", "requested_by", "board_slug", "task_number"])
def test_other_run_cannot_substitute_even_with_same_task(env, progress, live, change):
    item = progress.start()
    live.output(item, "WRONG_RUN_SENTINEL")
    value = 999999 if change == "task_number" else "different"
    with live.store.transaction() as conn:
        conn.execute(f"UPDATE runner_jobs SET {change}=?", (value,))
    status, body = live.get(item)
    assert status == 200 and body["lines"] == []
    assert all(node["parent_id"] is None for node in body["working_on"])
    assert "WRONG_RUN_SENTINEL" not in json.dumps(body)


@pytest.mark.parametrize("change", ["origin_session", "audience", "binding", "visibility"])
def test_stale_conversation_bindings_withhold_lines_and_run_steps(env, progress, live, change):
    item = progress.start(group=True)
    live.output(item, "STALE_SENTINEL")
    if change == "origin_session":
        sessions.end_session(env.db, env.session)
    else:
        with env.store._transaction() as conn:
            if change == "audience":
                conn.execute("DELETE FROM members WHERE user_id=?", (env.other,))
            elif change == "binding":
                conn.execute("UPDATE jobs SET progress_binding='old'")
            else:
                conn.execute("UPDATE jobs SET visibility_version=-1")
    status, body = live.get(item, user=env.other)
    assert status in (200, 404)
    assert "STALE_SENTINEL" not in json.dumps(body)
    if status == 200:
        assert body["lines"] == []
        assert all(node["parent_id"] is None for node in body["working_on"])


def test_reentrant_project_revocation_after_source_read_fails_closed(
    env, dispatch, progress, live, monkeypatch
):
    item = progress.start()
    live.output(item, "REVOKED_SENTINEL")
    read = livelog._run

    def revoke(*args):
        result = read(*args)
        with dispatch.boards.store.transaction() as conn:
            conn.execute("UPDATE boards SET archived=1 WHERE slug='work'")
        return result

    monkeypatch.setattr(livelog, "_run", revoke)
    assert live.get(item) == (404, {"error": "not_found"})


def test_tail_window_page_cursor_reset_and_cross_object_refusal(env, progress, live):
    item = progress.start()
    live.output(item, "\n".join(f"line {n}" for n in range(350)))
    status, first = live.get(item)
    assert status == 200 and len(first["lines"]) == 200
    assert first["lines"][0]["text"] == "line 150"
    assert first["lines"][-1]["text"] == "line 349"
    status, tail = live.get(item, params={"limit": 2})
    assert [row["text"] for row in tail["lines"]] == ["line 348", "line 349"]
    assert tail["cursor"] == first["cursor"]
    # A cursor at an earlier retained line pages forward, in chronological order.
    scope = __import__("hashlib").sha256(f"{env.wid}:{item.cid}".encode()).hexdigest()[:24]
    cursor = livelog._cursor(scope, first["lines"][0]["id"])
    _, page = live.get(item, params={"cursor": cursor, "limit": 2})
    assert [row["text"] for row in page["lines"]] == ["line 151", "line 152"] and page["more"]
    second = progress.start()
    assert live.get(second, params={"cursor": cursor})[0] == 400
    live.output(item, "Replacement snapshot")
    _, changed = live.get(item, params={"cursor": tail["cursor"]})
    assert changed["reset"] and changed["lines"][0]["text"] == "Replacement snapshot"


@pytest.mark.parametrize(
    "params",
    [
        {"limit": "0"},
        {"limit": "201"},
        {"limit": "-1"},
        {"limit": "1.0"},
        {"limit": "9" * 100},
        {"cursor": ""},
        {"cursor": "!"},
        {"cursor": "e30"},
        {"cursor": "x" * 161},
        {"workspace_id": "other"},
    ],
)
def test_invalid_log_pagination_is_rejected(progress, live, params):
    assert live.get(progress.start(), params=params)[0] == 400


def test_concurrent_read_cap_releases_on_errors_and_rate_limit_is_bounded(
    env, progress, live, monkeypatch
):
    item = progress.start()
    reads = livelog._reads(env.gateway)
    for _ in range(livelog.MAX_READERS):
        assert reads.slots.acquire(blocking=False)
    try:
        assert live.get(item)[0] == 429
    finally:
        for _ in range(livelog.MAX_READERS):
            reads.slots.release()
    snapshot = livelog.snapshot
    monkeypatch.setattr(livelog, "snapshot", lambda *args: (_ for _ in ()).throw(RuntimeError()))
    with pytest.raises(RuntimeError):
        live.get(item)
    monkeypatch.setattr(livelog, "snapshot", snapshot)
    assert live.get(item)[0] == 200
    reads.limiter = ratelimit.TokenBuckets(clock=lambda: 0, max_entries=1)
    for _ in range(livelog.READ_LIMIT.capacity):
        assert reads.limiter.check([(livelog.READ_LIMIT, env.wid + ":" + env.uid)]) == 0
    assert live.get(item) == (429, {"error": "rate_limited"})
    assert len(reads.limiter._buckets) == 1


def test_no_new_persistence_and_bounded_job_and_line_candidates(env, progress, live):
    item = progress.start()
    live.output(item, "\n" * 3999)
    root = env.path.parent
    before = {p.name for p in root.iterdir()}
    with env.store._transaction() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (item.job["id"],)).fetchone()
        # Twenty-one observations without launching twenty-one workers.
        for _ in range(21):
            cur = conn.execute(
                "INSERT INTO messages(conversation_id,actor,bot_id,text,created_at) VALUES(?,?,?,?,?)",
                (item.cid, "user:" + env.uid, env.bot["id"], "answer", job["created_at"]),
            )
            conn.execute(
                "INSERT INTO jobs(conversation_id,message_id,bot_id,user_id,state,created_at,updated_at) "
                "VALUES(?,?,?,?,'pending',?,?)",
                (
                    item.cid,
                    cur.lastrowid,
                    env.bot["id"],
                    env.uid,
                    job["created_at"],
                    job["updated_at"],
                ),
            )
    status, body = live.get(item)
    assert status == 200 and body["lines"] == []  # Old job is outside the bounded candidate window.
    assert len(body["working_on"]) <= len(env.store.list_bots()) + steps.MAX_JOBS
    # SQLite can initialize its existing stores' WAL/SHM auxiliaries on a
    # read-only open. These are not application stores or captured log files.
    assert {p.name.removesuffix("-wal").removesuffix("-shm") for p in root.iterdir()} == {
        name.removesuffix("-wal").removesuffix("-shm") for name in before
    }


@pytest.mark.parametrize(
    "value,expected",
    [
        ("running", "working"),
        ("leased", "working"),
        ("starting", "working"),
        ("started", "working"),
        ("doing", "working"),
        ("working", "working"),
        ("completed", "done"),
        ("done", "done"),
        ("uncertain", "waiting"),
        ("needs_me", "waiting"),
        ("review", "waiting"),
        ("queued", "idle"),
        ("pending", "idle"),
        ("cancelled", "idle"),
        ("errored", "idle"),
        ("unknown", "idle"),
    ],
)
def test_step_state_mapping(value, expected):
    assert steps.state(value) == expected


@pytest.mark.parametrize(
    "status,expected", [("completed", "done"), ("errored", "idle"), ("stopped", "idle")]
)
def test_run_ledger_end_status_drives_child_state(progress, live, status, expected):
    item = progress.start()
    live.output(item, "Finished output", ledger_state=status)
    _, body = live.get(item)
    assert body["working_on"][-1]["state"] == expected
    assert body["working_on"][-1]["at"]


def test_display_names_use_the_same_normalize_redact_cap_order():
    # Names are data, never bypass strings.
    result = steps.tree(
        [{"id": "bot", "name": SECRETS[0][:2] + "<b></b>" + SECRETS[0][2:], "created_at": "now"}],
        [],
    )
    assert SECRETS[0] not in json.dumps(result) and "[redacted:" in result[0]["display_name"]


def test_recorded_run_name_is_redacted_on_both_routes(env, progress, live):
    item = progress.start()
    with live.store.connection() as conn:
        agent = conn.execute("SELECT agent FROM run_ledger").fetchone()[0]
    assert agent
    secret = SECRETS[0]
    with env.db._lock:
        env.db._conn.execute(
            "UPDATE connections SET name=? WHERE id=?", (secret[:2] + "<b></b>" + secret[2:], agent)
        )
    for route in ("log", "log/stream"):
        status, body = live.get(item, route)
        assert status == 200
        assert body["working_on"][-1]["display_name"] == "[redacted:github_token]"
        assert secret not in json.dumps(body)


def test_embedded_nul_does_not_bypass_output_size_bounds(progress, live):
    item = progress.start()
    live.output(item, "small\0" + "word " * 4000 + SECRETS[0])
    status, body = live.get(item)
    assert status == 200
    assert [row["text"] for row in body["lines"]] == ["[omitted:too_long]"]


def test_waiting_task_state_is_preserved_with_running_child(dispatch, progress, live):
    item = progress.start()
    live.output(item, "Running")
    dispatch.boards.task_update(
        item.number,
        status="review",
        question="Which direction should the next run take?",
        options=["Continue", "Hold"],
        context="Choose whether the currently running task may continue.",
    )
    status, body = live.get(item)
    assert status == 200
    assert body["working_on"][0]["state"] == "waiting"
    assert body["working_on"][-1]["state"] == "working"


def test_full_source_is_redacted_before_line_cap():
    secret = SECRETS[0]
    result = livelog.clean("word " * 98 + secret)
    assert result == "word " * 98  # The whole redaction marker falls outside the cap.
    assert livelog.clean("x" * 16001) == "[omitted:too_long]"
