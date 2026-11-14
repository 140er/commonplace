"""Live path contracts through person-session reads; no worker or browser needed."""

import json
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest

from commonplace import events, sessions
from commonplace.boards.service import BoardService
from commonplace.chat.coworkers.store import open_store
from commonplace.extensions import ToolContext
from commonplace.policy import Policy
from tests.test_coworker_api import api as api


@pytest.fixture
def owned(api):
    events.ensure_schema(api.gateway.db)
    principal = sessions.person_principal(api.gateway.db, api.owner, api.workspace)
    boards = BoardService(ToolContext(principal=principal, gateway=api.gateway))
    boards.board_add("work", wing="project_work")
    connection, token = api.gateway.db.create_token(
        workspace_ref=api.workspace,
        user_ref=api.owner,
        name="Path-keeper",
        client="tests",
        policy=sessions.person_policy(),
        actor=api.owner,
    )
    bot = api.bot(name="Path-keeper", connection_id=connection)

    def conversation(*, group=False, board="work"):
        body = {"kind": "group" if group else "dm", "bot_ids": [bot["id"]], "board": board}
        if group:
            body["member_ids"] = [api.member]
        status, data = api.request("POST", "/conversations", body)
        assert status == 201, data
        return data["conversation"]["id"]

    def card(paths, *, actor=None, status="todo", board="work"):
        task = boards.task_add("Change files", board=board)
        with boards.store.transaction() as conn:
            conn.execute(
                "UPDATE tasks SET paths=?,assignee=?,status=? WHERE id=?",
                (json.dumps(paths), actor or "conn:" + connection, status, task["number"]),
            )
        return task["number"]

    def detail(cid, *, user=None):
        status, body = api.request("GET", "/conversations/" + cid, user=user)
        assert status == 200, body
        return body["conversation"]["owns"]

    return SimpleNamespace(
        api=api,
        boards=boards,
        connection=connection,
        token=token,
        bot=bot,
        conversation=conversation,
        card=card,
        detail=detail,
    )


def test_owns_is_live_on_detail_and_both_list_routes(owned):
    cid = owned.conversation()
    number = owned.card(["src/api/**", "docs/readme.md"])
    expected = {
        "availability": "available",
        "truncated": False,
        "paths": [
            {"path": "docs/readme.md", "locked": False, "shared_lock": False, "lock_owner": None},
            {"path": "src/api/**", "locked": False, "shared_lock": False, "lock_owner": None},
        ],
    }
    assert owned.detail(cid) == expected
    for route in ("", "/conversations"):
        status, data = owned.api.request("GET", route)
        assert status == 200
        assert next(c for c in data["conversations"] if c["id"] == cid)["owns"] == expected
    with owned.boards.store.transaction() as conn:
        conn.execute("UPDATE tasks SET paths=? WHERE id=?", ('["new/file.py"]', number))
    assert [p["path"] for p in owned.detail(cid)["paths"]] == ["new/file.py"]


def test_shared_holder_uses_display_name_and_release_is_live(owned):
    cid = owned.conversation(group=True)
    owned.card(["src/**"])
    other, _ = owned.api.gateway.db.create_token(
        workspace_ref=owned.api.workspace,
        user_ref=owned.api.owner,
        name="Parser-worker",
        client="tests",
        policy=sessions.person_policy(),
        actor=owned.api.owner,
    )
    number = owned.card(["src/parser.py"], actor="conn:" + other, status="doing")
    expected = {
        "path": "src/**",
        "locked": True,
        "shared_lock": True,
        "lock_owner": "Parser-worker",
    }
    assert owned.detail(cid)["paths"] == [expected]
    assert owned.detail(cid, user=owned.api.member)["paths"] == [expected]
    with owned.boards.store.transaction() as conn:
        conn.execute("UPDATE tasks SET status='done' WHERE id=?", (number,))
    assert owned.detail(cid)["paths"][0]["locked"] is False


@pytest.mark.parametrize(
    "state", ["queued", "leased", "starting", "uncertain", "started", "failed", "cancelled"]
)
def test_runner_hold_states_match_admission_and_name_the_subagent(owned, state):
    from commonplace.runner import jobs, ledger

    cid = owned.conversation()
    number = owned.card(["src/api.py"])
    runner = jobs.open_store(owned.api.gateway, owned.api.workspace)
    with runner.transaction() as conn:
        conn.execute(
            "INSERT INTO runner_jobs(id,task_number,board_slug,machine_id,project,topic,"
            "grant_version,runtime_mode,requested_by,state,created_at,updated_at) "
            "VALUES ('run',?,'work','fixture','demo','worker',1,'fixture',? ,?,?,?)",
            (
                number,
                "user:" + owned.api.owner,
                state,
                "2000-01-01T00:00:00Z",
                "2000-01-01T00:00:00Z",
            ),
        )
        row = conn.execute("SELECT * FROM runner_jobs WHERE id='run'").fetchone()
        ledger.sync(conn, row, agent=owned.connection)
    path = owned.detail(cid)["paths"][0]
    held = state not in ("failed", "cancelled")
    assert path == {
        "path": "src/api.py",
        "locked": held,
        "shared_lock": False,
        "lock_owner": "Path-keeper" if held else None,
    }


def test_canonical_chat_origin_links_unassigned_paths_without_cross_dm_leaks(owned):
    first, second = owned.conversation(), owned.conversation()
    posted = owned.api.post(first)[1]["job"]
    task = owned.boards.ensure_chat_task(
        f"coworker:{first}:{posted['id']}",
        {"board": "work", "title": "Read the source", "body": "Exact body"},
    )
    with owned.boards.store.transaction() as conn:
        conn.execute("UPDATE tasks SET paths=? WHERE id=?", ('["private-task.py"]', task["number"]))
    assert [p["path"] for p in owned.detail(first)["paths"]] == ["private-task.py"]
    assert owned.detail(second)["paths"] == []


def test_unreadable_project_and_archived_project_never_disclose_paths_or_locks(owned, monkeypatch):
    cid = owned.conversation(group=True)
    owned.card(["hidden/structure.py"], status="doing")
    authority = open_store(owned.api.gateway, owned.api.workspace)._authority
    original = authority.user

    def restricted(user_id, **kwargs):
        principal = original(user_id, **kwargs)
        return replace(
            principal,
            policy=Policy.build(
                profile="full", scopes=["memory:read"], read_wings=["elsewhere"], trust="trusted"
            ),
        )

    monkeypatch.setattr(authority, "user", restricted)
    for user in (owned.api.owner, owned.api.member):
        assert owned.detail(cid, user=user) == {
            "availability": "unavailable",
            "paths": [],
            "truncated": False,
        }
        assert "hidden/" not in json.dumps(owned.api.request("GET", user=user)[1])
    monkeypatch.setattr(authority, "user", original)
    with owned.boards.store.transaction() as conn:
        conn.execute("UPDATE boards SET archived=1 WHERE slug='work'")
    assert owned.detail(cid)["paths"] == []


def test_foreign_workspace_private_dm_and_agent_machine_tokens_are_refused(owned):
    from commonplace.runner import registry

    cid = owned.conversation()
    owned.card(["only-this-project.py"], status="doing")
    assert owned.api.request("GET", "/conversations/" + cid, user=owned.api.member)[0] == 404
    principal = sessions.person_principal(
        owned.api.gateway.db, owned.api.owner, owned.api.workspace
    )
    registry.ensure(owned.api.gateway.db)
    enrolled = registry.enroll(
        owned.api.gateway.db,
        principal,
        {"label": "fixture", "projects": ["demo"], "topics": ["worker"], "wings": ["shared"]},
    )
    for token in (owned.token, enrolled["machine_token"], enrolled["worker_token"]):
        for route in ("", "/conversations", "/conversations/" + cid):
            assert owned.api.request(
                "GET", route, headers={"Authorization": "Bearer " + token}
            ) == (403, {"error": "person_session_required"})
    other = owned.api.gateway.db.create_workspace("second workspace", owned.api.owner)
    owned.api.sessions[owned.api.owner] = sessions.start_session(
        owned.api.gateway.db, owned.api.owner, other
    )
    assert owned.api.request("GET", "/conversations/" + cid) == (404, {"error": "not_found"})
    assert owned.api.request("GET")[1]["conversations"] == []


@pytest.mark.parametrize(
    "bad",
    [
        "/outside/hidden.py",
        "../escape.py",
        "C:/outside/file.py",
        "C:relative.py",
        "\\\\host\\share",
        "~/private.py",
        "a\x00b",
    ],
)
def test_unsafe_legacy_paths_are_omitted_whole(owned, bad):
    cid = owned.conversation()
    owned.card([bad, "src/safe.py"])
    result = owned.detail(cid)
    assert [p["path"] for p in result["paths"]] == ["src/safe.py"]
    assert result["truncated"] is True


def test_owns_caps_deduplicates_and_marks_truncation(owned):
    cid = owned.conversation()
    for start in range(0, 210, 30):
        owned.card([f"src/file-{i:03}.py" for i in range(start, start + 30)])
    owned.card(["src/file-000.py"])
    result = owned.detail(cid)
    assert len(result["paths"]) == 200 and result["truncated"] is True
    assert len({p["path"] for p in result["paths"]}) == 200


def test_source_queries_are_batched_bounded_and_read_only(owned, monkeypatch):
    from commonplace.chat.coworkers import owns

    owned.card(["src/**"], status="doing")
    owned.conversation()
    reads, original = [], owns._read

    @contextmanager
    def observed(path):
        with original(path) as conn:
            conn.set_trace_callback(reads.append)
            yield conn

    monkeypatch.setattr(owns, "_read", observed)
    owned.api.request("GET", "/conversations")
    single = len(reads)
    for _ in range(8):
        owned.conversation()
    reads.clear()
    result = owned.api.request("GET", "/conversations")[1]["conversations"]
    assert all(c["owns"]["paths"] for c in result)
    assert len(reads) == single <= 12
    assert not any(
        q.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "ALTER", "CREATE"))
        for q in reads
    )


def test_other_projects_do_not_contribute_paths_or_shared_locks(owned):
    cid = owned.conversation()
    owned.card(["src/**"])
    owned.boards.board_add("other", wing="private_other")
    owned.card(["src/hidden.py"], board="other", status="doing")
    assert owned.detail(cid)["paths"] == [
        {"path": "src/**", "locked": False, "shared_lock": False, "lock_owner": None}
    ]


def test_revoked_binding_and_fresh_board_fence_withhold_paths(owned, monkeypatch):
    from commonplace.chat.coworkers import owns

    cid = owned.conversation()
    owned.card(["src/private.py"], status="doing")
    original = owns._read
    board_reads = 0

    @contextmanager
    def archive_before_fresh_read(path):
        nonlocal board_reads
        if path.name == "boards.sqlite3":
            board_reads += 1
            if board_reads == 2:
                with owned.boards.store.transaction() as conn:
                    conn.execute("UPDATE boards SET archived=1 WHERE slug='work'")
        with original(path) as conn:
            yield conn

    monkeypatch.setattr(owns, "_read", archive_before_fresh_read)
    assert owned.detail(cid)["paths"] == []
    monkeypatch.setattr(owns, "_read", original)
    with owned.boards.store.transaction() as conn:
        conn.execute("UPDATE boards SET archived=0 WHERE slug='work'")
    owned.api.gateway.db._conn.execute(
        "UPDATE connections SET revoked_at='2000-01-01T00:00:00Z' WHERE id=?",
        (owned.connection,),
    )
    assert owned.detail(cid) == {"availability": "unavailable", "paths": [], "truncated": False}


def test_unreadable_runner_store_preserves_paths_with_unknown_locks(owned):
    from pathlib import Path

    cid = owned.conversation()
    owned.card(["src/api.py"])
    root = Path(owned.api.gateway.workspace_dir(owned.api.workspace))
    (root / "runner.sqlite3").write_bytes(b"invalid database")
    assert owned.detail(cid)["paths"] == [
        {"path": "src/api.py", "locked": None, "shared_lock": None, "lock_owner": None}
    ]
