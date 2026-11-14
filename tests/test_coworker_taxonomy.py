"""Live directory contracts, with real person sessions and private-thread canaries."""

import json
import sqlite3
import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from commonplace import sessions
from commonplace.account import deletion, tables
from commonplace.bots import limits, sharing_setting, store as bot_store
from commonplace.bots.store import open_bots
from commonplace.chat.coworkers import taxonomy
from commonplace.chat.coworkers.store import Store, StoreError, open_store
from tests.test_coworker_api import api as api
from tests.test_coworker_owns import owned as owned


@pytest.fixture
def team_store(api):
    # open_bots is a workspace cache. Its owner, not individual GETs, closes it.
    store = open_bots(api.gateway, api.workspace)
    api.gateway.db.add_pre_close_callback(store.close)
    return store


def directory(api, *, user=None):
    status, body = api.request("GET", "/bots", user=user)
    assert status == 200, body
    return body


def conversations(api, *, user=None):
    status, body = api.request("GET", "/conversations", user=user)
    assert status == 200, body
    return {row["id"]: row for row in body["conversations"]}


def reply(api, cid, *, user=None, request="one"):
    store = open_store(api.gateway, api.workspace)
    store.post_message(user or api.owner, cid, "Exact words", request)
    job = store.claim_job()
    assert job["conversation_id"] == cid
    return store.add_bot_reply(job["id"], "Original reply")


def test_additive_fields_and_default_section_preserve_persona_fields(api):
    bot = api.bot()
    row = directory(api)["bots"][0]
    assert {key: row[key] for key in bot} == bot
    assert {key: row[key] for key in ("section", "private", "unread", "state", "summary")} == {
        "section": None,
        "private": False,
        "unread": 0,
        "state": "idle",
        "summary": "0 coworkers",
    }
    assert row["coworkers"] == []
    assert directory(api)["team_bots"] == []


def test_conversation_list_keeps_owned_paths_with_taxonomy_and_unread(owned):
    api = owned.api
    cid = owned.conversation()
    owned.card(["src/api/**"])
    assert (
        api.request("POST", f"/bots/{owned.bot['id']}/section", {"section": "Research"})[0] == 200
    )
    reply(api, cid)
    view = conversations(api)[cid]
    assert (view["section"], view["unread"], view["state"]) == ("Research", 1, "live")
    assert view["owns"] == {
        "availability": "available",
        "paths": [
            {"path": "src/api/**", "locked": False, "shared_lock": False, "lock_owner": None}
        ],
        "truncated": False,
    }
    assert conversations(api, user=api.member) == {}


def test_section_owner_only_sanitized_clearable_and_audited_without_name(api):
    bot = api.bot()
    path = f"/bots/{bot['id']}/section"
    assert api.request("POST", path, {"section": "Core Ops"}, user=api.member)[0] == 403
    assert api.request("POST", path, {"section": "Core Ops"}, headers={"Origin": None})[0] == 403
    for value in ("", "../escape", "x/child", "<script>", "x" * 61, 3, True):
        assert api.request("POST", path, {"section": value})[0] == 400
    assert api.request("POST", path, {"section": "  Core Ops  "}) == (
        200,
        {"bot_id": bot["id"], "section": "Core Ops"},
    )
    dm = api.dm(bot=bot)
    assert directory(api)["bots"][0]["section"] == "Core Ops"
    assert conversations(api)[dm["id"]]["section"] == "Core Ops"
    audit = [r for r in api.gateway.db.audit_entries() if r["action"] == "coworkers.section"]
    assert len(audit) == 1 and json.loads(audit[0]["target_ids"]) == [bot["id"]]
    assert "Core Ops" not in json.dumps(dict(audit[0]))
    assert api.request("POST", path, {"section": None})[0] == 200
    assert directory(api)["bots"][0]["section"] is None
    assert api.request("POST", "/bots/missing/section", {"section": "Research"})[0] == 404


@pytest.mark.parametrize("section", ["Core Ops", None])
def test_section_without_authority_refuses_set_and_clear(tmp_path, section):
    store = Store(tmp_path / "coworkers.sqlite3")
    bot = store.create_bot("fixture", "Fixture")
    with store._transaction() as conn:
        conn.execute("INSERT INTO coworker_sections VALUES (?,?)", (bot["id"], "Original"))
    with pytest.raises(StoreError, match="owner_required") as error:
        taxonomy.set_section(store, "fixture", bot["id"], section)
    assert error.value.status == 403
    with store._transaction() as conn:
        assert conn.execute("SELECT section FROM coworker_sections").fetchone()[0] == "Original"


def test_visible_holds_control_lock_through_query_and_fetch(api, monkeypatch):
    connection, _ = api.gateway.db.create_token(
        workspace_ref=api.workspace,
        user_ref=api.owner,
        name="fixture-bound",
        client="tests",
        policy=sessions.person_policy(),
        actor=api.owner,
    )
    bot = api.bot(connection_id=connection)
    store = open_store(api.gateway, api.workspace)
    db = api.gateway.db
    real_lock, real_conn = db._lock, db._conn

    class Lock:
        depth = 0

        def __enter__(self):
            real_lock.__enter__()
            self.depth += 1
            return self

        def __exit__(self, *args):
            self.depth -= 1
            return real_lock.__exit__(*args)

    lock = Lock()
    observations = []

    class Cursor:
        def __init__(self, cursor):
            self.cursor = cursor

        def fetchall(self):
            assert lock.depth > 0, "control cursor fetched outside its lock"
            observations.append("fetch")
            return self.cursor.fetchall()

    class Connection:
        def execute(self, *args):
            assert lock.depth > 0, "control query executed outside its lock"
            observations.append("query")
            return Cursor(real_conn.execute(*args))

    with monkeypatch.context() as patch:
        patch.setattr(db, "_lock", lock)
        patch.setattr(db, "_conn", Connection())
        assert taxonomy._visible(store, [bot]) == [bot]
    assert observations == ["query", "fetch"]


def test_unread_is_person_scoped_and_only_marks_delivered_pages(api):
    bot = api.bot()
    status, body = api.request(
        "POST",
        "/conversations",
        {"kind": "group", "bot_ids": [bot["id"]], "member_ids": [api.member]},
    )
    assert status == 201
    cid = body["conversation"]["id"]
    first = reply(api, cid)
    second = reply(api, cid, request="two")
    assert conversations(api)[cid]["unread"] == 2
    # The other person also sees the owner's two original messages.
    assert conversations(api, user=api.member)[cid]["unread"] == 4
    assert directory(api)["bots"][0]["unread"] == 2  # listing does not mark read
    status, page = api.request("GET", f"/conversations/{cid}/messages?after=0&limit=2")
    assert status == 200 and page["messages"][-1]["id"] == first["id"]
    assert conversations(api)[cid]["unread"] == 1
    assert conversations(api, user=api.member)[cid]["unread"] == 4
    api.request("GET", f"/conversations/{cid}/messages?after={second['id']}")
    assert conversations(api)[cid]["unread"] == 1  # empty page never jumps ahead
    api.request("GET", f"/conversations/{cid}")
    assert conversations(api)[cid]["unread"] == 0
    api.request("GET", f"/conversations/{cid}/messages?limit=1")
    assert conversations(api)[cid]["unread"] == 0  # older pages never regress
    api.request("GET", f"/conversations/{cid}", user=api.member)
    assert conversations(api, user=api.member)[cid]["unread"] == 0


def test_other_peoples_private_threads_never_affect_counts_or_state(api):
    bot = api.bot()
    own = api.dm(bot=bot)["id"]
    foreign = api.dm(bot=bot, user=api.member)["id"]
    reply(api, foreign, user=api.member)
    mine = directory(api)["bots"][0]
    assert (mine["unread"], mine["state"], mine["summary"]) == (0, "idle", "0 coworkers")
    assert {row["conversation_id"] for row in mine["coworkers"]} == set()
    assert set(conversations(api)) == {own}
    assert api.request("GET", f"/conversations/{foreign}")[0] == 404
    theirs = directory(api, user=api.member)["bots"][0]
    assert (theirs["unread"], theirs["state"], theirs["summary"]) == (1, "live", "1 coworker")
    assert conversations(api)[own]["private"] is True


@pytest.mark.parametrize(
    "job_state,expected",
    [
        ("pending", "working"),
        ("running", "working"),
        ("needs_configuration", "waiting"),
        ("uncertain", "waiting"),
        ("done", "live"),
        ("failed", "idle"),
        ("cancelled", "idle"),
    ],
)
def test_closed_states_come_from_latest_real_job_without_model_or_ticket_copy(
    api, job_state, expected
):
    cid = api.dm()["id"]
    store = open_store(api.gateway, api.workspace)
    posted = store.post_message(api.owner, cid, "#123 model-tag canary", "one")
    with store._transaction() as conn:
        conn.execute("UPDATE jobs SET state=? WHERE id=?", (job_state, posted["job"]["id"]))
    row = directory(api)["bots"][0]
    assert row["state"] == conversations(api)[cid]["state"] == expected
    assert row["summary"] == ("Needs you" if expected == "waiting" else "1 coworker")
    assert "#123" not in row["summary"] and "model-tag" not in json.dumps(row["coworkers"])


def test_team_bots_reuse_roster_shares_and_existing_unread_without_copying(api, team_store):
    principal = sessions.person_principal(api.gateway.db, api.owner, api.workspace)
    sharing_setting.set_enabled(api.gateway.db, principal, True)
    bots = team_store
    own = bots.create(api.owner, "Private fixture")
    hidden = bots.create(api.member, "Hidden canary")
    shared = bots.create(api.member, "Shared fixture")
    bots.share(shared["id"], api.member, api.owner, True, False)
    thread = bots.thread_open(shared["id"], api.owner, "private")
    bots.add_event_message(shared["id"], api.owner, "For this reader", thread_id=thread["id"])
    bots.add_event_message(shared["id"], api.member, "Other private canary")
    bots.pending_create(shared["id"], api.member, "fixture", {}, "Private pending canary")
    bots.add_event_message(own["id"], api.owner, "For owner")
    roster = {row["id"]: row for row in bots.roster(api.owner)}
    rows = {row["id"]: row for row in directory(api)["team_bots"]}
    assert set(rows) == set(roster) == {own["id"], shared["id"]}
    assert hidden["id"] not in rows
    assert rows[own["id"]]["private"] is True
    assert rows[shared["id"]]["private"] is False
    assert all(row["section"] is None for row in rows.values())
    assert all(row["unread"] == roster[key]["unread"] == 1 for key, row in rows.items())
    bots.mark_read(shared["id"], api.owner, 2**62, thread_id=thread["id"])
    assert (
        next(row for row in directory(api)["team_bots"] if row["id"] == shared["id"])["unread"] == 0
    )
    with open_store(api.gateway, api.workspace)._transaction() as conn:
        assert conn.execute("SELECT count(*) FROM bots").fetchone()[0] == 0
    bots.revoke(shared["id"], api.member, api.owner)
    assert {row["id"] for row in directory(api)["team_bots"]} == {own["id"]}


def test_team_sharing_default_off_and_disabled_share_never_exposes_bot(api, team_store):
    bots = team_store
    hidden = bots.create(api.member, "Private fixture")
    assert directory(api)["team_bots"] == []
    principal = sessions.person_principal(api.gateway.db, api.owner, api.workspace)
    sharing_setting.set_enabled(api.gateway.db, principal, True)
    bots.share(hidden["id"], api.member, api.owner, False, True)
    assert {row["id"] for row in directory(api)["team_bots"]} == {hidden["id"]}
    # A read-only share without a private thread never inherits owner unread.
    bots.add_event_message(hidden["id"], api.member, "Owner-private canary")
    row = directory(api)["team_bots"][0]
    assert row["unread"] == 0 and row["state"] == "idle"
    sharing_setting.set_enabled(api.gateway.db, principal, False)
    assert directory(api)["team_bots"] == []


def test_team_projection_does_not_count_other_persons_running_turn(api, team_store):
    bots = team_store
    bot = bots.create(api.owner, "Fixture")
    turn = SimpleNamespace(
        workspace_id=api.workspace,
        bot_id=bot["id"],
        thread_id="foreign",
        user_id=api.member,
        request_id="canary",
    )
    limits.REGISTRY.admit(turn)
    try:
        row = directory(api)["team_bots"][0]
        assert row["state"] == "idle"
    finally:
        limits.REGISTRY.release(turn)


def test_team_state_uses_own_live_reply_running_turn_and_unexpired_confirmation(api, team_store):
    bots = team_store
    bot = bots.create(api.owner, "Fixture")
    bots.add_event_message(bot["id"], api.owner, "Received")
    assert directory(api)["team_bots"][0]["state"] == "live"
    turn = SimpleNamespace(
        workspace_id=api.workspace,
        bot_id=bot["id"],
        thread_id=None,
        user_id=api.owner,
        request_id="fixture",
    )
    limits.REGISTRY.admit(turn)
    try:
        assert directory(api)["team_bots"][0]["state"] == "working"
        bots.pending_create(bot["id"], api.owner, "fixture", {}, "Expired", ttl_s=-1)
        assert directory(api)["team_bots"][0]["state"] == "working"
        bots.pending_create(bot["id"], api.owner, "fixture", {}, "Awaiting confirmation")
        row = directory(api)["team_bots"][0]
        assert (row["state"], row["summary"]) == ("waiting", "Needs you")
    finally:
        limits.REGISTRY.release(turn)


def test_workspace_and_agent_token_boundaries_apply_to_both_projections(api, team_store):
    bots = team_store
    bots.create(api.owner, "Workspace canary")
    api.bot()
    other = api.gateway.db.create_workspace("Other", api.owner)
    api.sessions[api.owner] = sessions.start_session(api.gateway.db, api.owner, other)
    assert directory(api) == {"bots": [], "team_bots": []}
    _, token = api.gateway.db.create_token(
        workspace_ref=api.workspace,
        user_ref=api.owner,
        name="fixture",
        client="tests",
        policy=sessions.person_policy(),
        actor=api.owner,
    )
    for path in ("/bots", "/conversations"):
        assert api.request("GET", path, headers={"Authorization": "Bearer " + token})[0] == 403


@pytest.mark.parametrize("bound", [False, True])
def test_directory_lookup_count_is_constant_for_fifty_bots(api, monkeypatch, bound):
    store = open_store(api.gateway, api.workspace)
    for number in range(50):
        connection = None
        if bound:
            connection, _ = api.gateway.db.create_token(
                workspace_ref=api.workspace,
                user_ref=api.owner,
                name=f"fixture-{number}",
                client="tests",
                policy=sessions.person_policy(),
                actor=api.owner,
            )
        bot = store.create_bot(api.owner, f"Fixture {number}", connection_id=connection)
        store.create_conversation(api.owner, "dm", bot_ids=[bot["id"]])
    queries = []
    original = store._transaction

    @contextmanager
    def traced():
        with original() as conn:
            conn.set_trace_callback(queries.append)
            yield conn

    monkeypatch.setattr(store, "_transaction", traced)
    for path, key in (("/bots", "bots"), ("/conversations", "conversations")):
        queries.clear()
        control_queries = []
        api.gateway.db._conn.set_trace_callback(control_queries.append)
        status, body = api.request("GET", path)
        api.gateway.db._conn.set_trace_callback(None)
        assert status == 200 and len(body[key]) == 50
        reads = [q for q in queries if q.lstrip().upper().startswith(("SELECT", "WITH"))]
        assert 1 <= len(reads) <= 4, reads
        assert (
            len([q for q in control_queries if q.lstrip().upper().startswith(("SELECT", "WITH"))])
            <= 10
        )


def test_team_lookup_count_is_constant_for_fifty_bots(api, team_store, monkeypatch):
    bots = team_store
    for number in range(50):
        bots.create(api.owner, f"Team fixture {number}")
    queries = []
    route_stores = []
    original_open = bot_store.open_bots

    def traced_open(gateway, workspace_id):
        store = original_open(gateway, workspace_id)
        route_stores.append(store)
        return store

    monkeypatch.setattr(bot_store, "open_bots", traced_open)
    with bots._tx(write=False) as conn:
        conn.set_trace_callback(queries.append)
        try:
            rows = directory(api)["team_bots"]
        finally:
            conn.set_trace_callback(None)
    assert len(rows) == 50
    assert route_stores == [bots]
    reads = [q for q in queries if q.lstrip().upper().startswith(("SELECT", "WITH"))]
    assert 1 <= len(reads) <= 4, reads


@pytest.mark.parametrize("fails", [False, True])
def test_team_cached_handle_survives_request_and_owner_closes_it(
    api, team_store, monkeypatch, fails
):
    bots = team_store
    bots.create(api.owner, "Fixture")
    connection = bots._conn

    if fails:

        def unavailable(*args):
            raise RuntimeError("fixture unavailable")

        monkeypatch.setattr(bots, "roster", unavailable)
        with pytest.raises(RuntimeError, match="fixture unavailable"):
            api.request("GET", "/bots")
    else:
        assert directory(api)["team_bots"][0]["name"] == "Fixture"
    assert open_bots(api.gateway, api.workspace) is bots
    assert bots._conn is connection
    assert connection.execute("SELECT 1").fetchone()[0] == 1
    # Both outcomes leave the shared connection to its owner for cleanup.
    api.gateway.db.close()
    assert bots._conn is None
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")


@pytest.mark.parametrize("former_member", [False, True])
def test_read_mark_classification_and_account_purge_in_retained_workspace(api, former_member):
    cid = api.dm()["id"]
    store = open_store(api.gateway, api.workspace)
    group = store.create_conversation(api.owner, "group", member_ids=[api.member])["id"]
    store.post_message(api.owner, group, "Shared original", "one")
    api.request("GET", f"/conversations/{group}", user=api.member)
    reply(api, cid)
    api.request("GET", f"/conversations/{cid}")
    root = api.gateway.supervisor.root / api.workspace
    retired = root.with_name(root.name + ".generation-" + "a" * 24)
    retired.mkdir()
    with sqlite3.connect(root / "coworkers.sqlite3") as source:
        with sqlite3.connect(retired / "coworkers.sqlite3") as target:
            source.backup(target)
    assert tables.WORKSPACE_RULES["coworkers.sqlite3"]["coworker_reads"].user == ("user_id",)
    deletion.ensure_schema(api.gateway.db)
    api.gateway.wall_clock = time.time
    with api.gateway.db._lock:
        if former_member:
            api.gateway.db._conn.execute("DELETE FROM memberships WHERE user_id=?", (api.member,))
        api.gateway.db._conn.execute(
            "INSERT INTO account_deletions(user_id,state,requested_at,purge_after) VALUES (?,'pending',?,?)",
            (api.member, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
    assert deletion.purge(api.gateway, api.member)["state"] == "purged"
    with store._transaction() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM coworker_reads WHERE user_id=?", (api.member,)
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM coworker_reads WHERE user_id=?", (api.owner,)
            ).fetchone()[0]
            == 1
        )
        assert conn.execute("SELECT count(*) FROM messages").fetchone()[0] == 3
    with sqlite3.connect(retired / "coworkers.sqlite3") as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM coworker_reads WHERE user_id=?", (api.member,)
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM coworker_reads WHERE user_id=?", (api.owner,)
            ).fetchone()[0]
            == 1
        )
        assert conn.execute("SELECT count(*) FROM messages").fetchone()[0] == 3


def test_legacy_database_upgrades_additively_and_revoked_bot_is_not_projected(api):
    connection, _ = api.gateway.db.create_token(
        workspace_ref=api.workspace,
        user_ref=api.owner,
        name="bound",
        client="tests",
        policy=sessions.person_policy(),
        actor=api.owner,
    )
    bot = api.bot(connection_id=connection)
    dm = api.dm(bot=bot)
    store = open_store(api.gateway, api.workspace)
    with store._transaction() as conn:
        conn.execute("DROP TABLE IF EXISTS coworker_reads")
        conn.execute("DROP TABLE IF EXISTS coworker_sections")
    api.gateway._coworker_stores[api.workspace] = Store(store._path, authority=store._authority)
    assert directory(api)["bots"][0]["section"] is None
    api.gateway.db._conn.execute(
        "UPDATE connections SET revoked_at='2026-01-01T00:00:00Z' WHERE id=?", (connection,)
    )
    assert directory(api)["bots"] == []
    assert dm["id"] not in conversations(api)
