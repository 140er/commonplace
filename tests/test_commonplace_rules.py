"""Rule lifecycle, visibility and HTTP authorization through the real gateway."""

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from commonplace import extensions, sessions
from commonplace.boards import BoardService
from commonplace.extensions import ToolContext, ToolError
from commonplace.rules import RuleService, open_store
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_commonplace_boards import api, mcp


def service(hub, principal):
    return RuleService(ToolContext(principal=principal, gateway=hub.gateway))


def rule(text="  Exact words\nsecond line", key="one", kind="instruction", **kwargs):
    return dict(key=key, text=text, kind=kind, **kwargs)


@pytest.fixture
def env(hub):
    token = hub.owner_token("agent")
    agent = hub.gateway.authenticate(f"Bearer {token}")
    person = sessions.person_principal(hub.db, hub.admin, hub.workspace)
    session = sessions.redeem_login_link(
        hub.db, sessions.create_login_link(hub.db, person.user_email, hub.workspace)
    )
    cookie = {"Cookie": f"{sessions.COOKIE}={session}"}
    return SimpleNamespace(
        token=token,
        agent=agent,
        person=person,
        cookie=cookie,
        worker=service(hub, agent),
        owner=service(hub, person),
    )


def publish(env, **kwargs):
    return env.worker.rules_publish([rule(**kwargs)])["rules"][0]


def action(hub, env, current, name):
    status, result = api(
        hub,
        env.cookie,
        "POST",
        f"/api/rules/{current['id']}/action",
        {"version": current["version"], "action": name},
    )
    assert status == 200, result
    return result


def test_publish_get_changed_and_profiles(hub, env):
    assert env.worker.rules_changed(0) == {"rules": [], "seq": 0}
    for profile in ("slim", "standard", "full"):
        token = hub.token(
            profile, profile=profile, scopes=["memory:read", "memory:write"], write_wing="inbox"
        )
        assert {"rules_publish", "rules_get", "rules_changed"} <= hub.tool_names(token)
    ok, output = mcp(hub, env.token, "rules_publish", rules=[rule()])
    assert ok and output == "one proposed 1\nseq 1"
    current = env.worker.rules_get()["rules"][0]
    assert current["agent"] == env.agent.actor
    assert current["text"] == current["agent_text"] == rule()["text"]
    assert current["updated_by"] == env.agent.actor
    assert current["source"] == "agent" and current["version"] == 1
    ok, output = mcp(hub, env.token, "rules_get")
    assert (
        ok
        and output
        == "one [instruction global proposed agent v1] " + json.dumps(rule()["text"]) + "\nseq 1"
    )
    assert mcp(hub, env.token, "rules_changed", since=0) == (True, "seq 1\none proposed")
    assert env.worker.rules_changed(1) == {"rules": [], "seq": 1}
    assert publish(env) == current
    assert env.worker.store.seq == 1
    changed = publish(env, text="Changed")
    assert changed["version"] == 2 and changed["status"] == "proposed"
    history = env.owner.history(current["id"])
    assert [r["version"] for r in history] == [1, 2]
    assert all(
        r["stamp_user"] == env.agent.user_id
        and r["stamp_connection"] == env.agent.connection
        and r["stamp_trust"] == env.agent.policy.trust
        for r in history
    )
    status, listing = api(hub, env.cookie, "GET", "/api/rules")
    assert status == 200 and listing == env.owner.list_rules()
    assert set(listing["agents"]) == {env.agent.connection, "slim", "standard", "full"}
    assert listing["suggestions"] == []
    assert Path(env.worker.store.path).parent == Path(env.worker.ctx.workspace_dir())


def test_derived_key_and_md_quoted_grouping(hub, env):
    rules = [
        {"text": f'  {kind}\n## data "only"', "kind": kind}
        for kind in ("style", "instruction", "limit", "permission")
    ]
    published = env.worker.rules_publish(rules)
    for original, current in zip(rules, published["rules"]):
        expected = "r-" + hashlib.sha1(" ".join(original["text"].split()).encode()).hexdigest()[:10]
        assert current["key"] == expected
    ok, text = mcp(hub, env.token, "rules_get", format="md", include_retired=True)
    assert ok
    positions = [
        text.index(f"## {heading}")
        for heading in ("Hard limits", "Permissions", "Standing instructions", "Style")
    ]
    assert positions == sorted(positions)
    assert all("- " + json.dumps(r["text"]) in text for r in rules)
    assert text.endswith("seq 4")
    action(hub, env, published["rules"][0], "retire")
    assert len(env.worker.rules_get(format="md", include_retired=True)["rules"]) == 3
    assert len(env.worker.rules_get(include_retired=True)["rules"]) == 4


def test_bulk_replace_retires_only_own_previously_published_keys(hub, env):
    first, second = env.worker.rules_publish([rule(), rule(key="two")])["rules"]
    shared = env.owner.create("*", "All agents", "limit")
    owner_only = env.owner.create(env.agent.actor, "Owner original", "instruction")
    other = service(hub, hub.gateway.authenticate(f"Bearer {hub.owner_token('other')}"))
    other.rules_publish([rule(key="two", text="Other rule")])
    result = env.worker.rules_publish([rule()], replace=True)
    assert [(r["key"], r["status"]) for r in result["rules"]] == [
        ("one", "proposed"),
        ("two", "retired"),
    ]
    assert env.owner.history(second["id"])[-1]["note"] == "dropped by agent"
    listed = env.owner.list_rules()["rules"]
    assert next(r for r in listed if r["id"] == owner_only["id"])["status"] == "accepted"
    assert next(r for r in listed if r["id"] == shared["id"])["status"] == "accepted"
    assert other.rules_get()["rules"][-1]["text"] == "Other rule"
    assert first["id"] in {r["id"] for r in env.worker.rules_get()["rules"]}
    empty = env.worker.rules_publish([], replace=True)
    assert [r["key"] for r in empty["rules"]] == ["one"]
    assert env.worker.rules_publish([], replace=True)["seq"] == empty["seq"]


def test_owner_edit_wins_republish_pending_accept_agent_keep_edit(hub, env):
    original = publish(env)
    accepted = action(hub, env, original, "accept")
    assert accepted["status"] == "accepted"
    current = publish(env, text="Agent update")
    assert current["status"] == "proposed"
    status, edited = api(
        hub,
        env.cookie,
        "PATCH",
        f"/api/rules/{original['id']}",
        {
            "version": current["version"],
            "text": "Owner wording",
            "kind": "limit",
            "scope": "repo",
            "scope_ref": "https://EXAMPLE.TEST/org/project.git",
            "note": "Reason\nverbatim",
        },
    )
    assert status == 200 and edited["status"] == "edited" and edited["source"] == "owner"
    assert (
        edited["agent_text"] == "Agent update" and edited["scope_ref"] == "example.test/org/project"
    )
    current = publish(env, text="New agent proposal", kind="style")
    assert current["text"] == "Owner wording" and current["pending"] == 1
    assert current["kind"] == "limit" and current["scope"] == "repo"
    assert (
        current["agent_text"] == "New agent proposal"
        and current["version"] == edited["version"] + 1
    )
    kept = action(hub, env, current, "keep_edit")
    assert kept["pending"] == 0 and kept["text"] == "Owner wording"
    current = publish(env, text="Another proposal")
    accepted = action(hub, env, current, "accept_agent")
    assert accepted["text"] == "Another proposal" and accepted["source"] == "agent"
    assert accepted["status"] == "accepted" and accepted["pending"] == 0
    assert env.owner.history(original["id"])[3]["note"] == "Reason\nverbatim"
    current = env.owner.edit(accepted["id"], accepted["version"], text="Edited again")
    current = publish(env, text="Edited again")
    assert current["pending"] == 0 and current["source"] == "owner"


@pytest.mark.parametrize("owner_source", [False, True])
def test_retire_restore_in_changed_and_get(hub, env, owner_source):
    current = env.owner.create("*", "Owner text", "limit") if owner_source else publish(env)
    before = current["seq"]
    retired = action(hub, env, current, "retire")
    assert env.worker.rules_get()["rules"] == []
    assert env.worker.rules_get(include_retired=True)["rules"] == [retired]
    assert env.worker.rules_changed(before)["rules"] == [
        {"key": current["key"], "status": "retired"}
    ]
    restored = action(hub, env, retired, "restore")
    assert restored["status"] == ("edited" if owner_source else "accepted")
    assert env.worker.rules_get()["rules"] == [restored]


def test_suggestions_accept_dismiss_and_history(hub, env):
    current = publish(env)
    path = f"/api/rules/{current['id']}/suggestions"
    status, suggestion = api(
        hub, env.cookie, "POST", path, {"text": "  Clearer\nrule", "from_agent": "helper"}
    )
    assert status == 201 and suggestion["status"] == "open"
    assert suggestion["stamp_user"] == env.person.user_id
    listing = env.owner.list_rules()
    assert listing["suggestions"] == [suggestion] and listing["seq"] == current["seq"] + 1
    assert listing["rules"][0]["text"] == current["text"]
    status, accepted = api(
        hub, env.cookie, "POST", f"/api/rules/suggestions/{suggestion['id']}", {"action": "accept"}
    )
    assert status == 200 and accepted["text"] == suggestion["text"]
    assert accepted["source"] == "owner" and accepted["status"] == "edited"
    assert accepted["agent_text"] == current["agent_text"]
    assert env.owner.history(current["id"])[-1]["source"] == "suggestion"
    suggestion = env.owner.suggest(current["id"], "Dismiss me", "helper")
    result = env.owner.decide_suggestion(suggestion["id"], "dismiss")
    assert result["text"] == accepted["text"] and result["seq"] == accepted["seq"] + 2
    assert env.owner.list_rules()["suggestions"] == []
    with env.owner.store.connection() as conn:
        stored = conn.execute(
            "SELECT * FROM rule_suggestions WHERE id=?", (suggestion["id"],)
        ).fetchone()
        assert stored["status"] == "dismissed" and stored["decided_at"] is not None
    with pytest.raises(ToolError, match="already decided"):
        env.owner.decide_suggestion(suggestion["id"], "accept")
    status, history = api(hub, env.cookie, "GET", f"/api/rules/{current['id']}/history")
    assert status == 200 and history == env.owner.history(current["id"])
    assert [r["version"] for r in history] == list(range(1, 6))
    assert all(
        r["stamp_connection"] is None and r["stamp_user"] == env.person.user_id for r in history[1:]
    )


@pytest.mark.parametrize(
    "suffix,method,extra",
    [("", "PATCH", {"text": "Stale"}), ("/action", "POST", {"action": "retire"})],
)
def test_stale_version_is_409_and_atomic(hub, env, suffix, method, extra):
    original = publish(env)
    current = env.owner.edit(original["id"], original["version"], text="Current")
    seq = env.owner.store.seq
    history = env.owner.history(original["id"])
    path = f"/api/rules/{original['id']}{suffix}"
    status, conflict = api(hub, env.cookie, method, path, dict(extra, version=original["version"]))
    assert status == 409 and conflict == {"error": "rule changed", "current": current}
    assert env.owner.store.seq == seq and env.owner.history(original["id"]) == history
    assert api(hub, env.cookie, method, path, extra)[0] == 400


def test_rest_owner_only_people_read_and_agents_cannot_write(hub, env):
    current = publish(env)
    member_id = hub.db.create_user("member@example.test")
    hub.db.add_member(hub.workspace, member_id, role="member", actor=hub.admin)
    member = sessions.person_principal(hub.db, member_id, hub.workspace)
    session = sessions.redeem_login_link(
        hub.db, sessions.create_login_link(hub.db, member.user_email, hub.workspace)
    )
    member_cookie = {"Cookie": f"{sessions.COOKIE}={session}"}
    requests = [
        ("POST", "/api/rules", {"agent": "*", "text": "Attempt", "kind": "limit"}),
        (
            "PATCH",
            f"/api/rules/{current['id']}",
            {"version": current["version"], "text": "Attempt"},
        ),
        (
            "POST",
            f"/api/rules/{current['id']}/action",
            {"version": current["version"], "action": "retire"},
        ),
        (
            "POST",
            f"/api/rules/{current['id']}/suggestions",
            {"text": "Attempt", "from_agent": "helper"},
        ),
        ("POST", "/api/rules/suggestions/1", {"action": "accept"}),
    ]
    for auth in (member_cookie, env.token):
        for method, path, body in requests:
            assert api(hub, auth, method, path, body)[0] == 403
    assert api(hub, member_cookie, "GET", "/api/rules")[0] == 200
    assert api(hub, member_cookie, "GET", f"/api/rules/{current['id']}/history")[0] == 200
    assert api(hub, env.token, "GET", "/api/rules")[0] == 403
    assert api(hub, env.token, "GET", f"/api/rules/{current['id']}/history")[0] == 403
    assert env.owner.store.seq == 1
    for principal in (env.agent, member):
        with pytest.raises(ToolError):
            service(hub, principal).create("*", "Attempt", "limit")


def test_board_visibility_repo_normalization_and_other_agents(hub, env):
    boards = BoardService(ToolContext(principal=env.person, gateway=hub.gateway))
    boards.board_add(slug="visible", wing="shared")
    boards.board_add(slug="hidden", wing="private.rules")
    shared = env.owner.create("*", "Shared limit", "limit", "board", "visible")
    secret = env.owner.create("*", "Hidden limit", "limit", "board", "hidden")
    repo = env.owner.create("*", "Repo limit", "limit", "repo", "git@EXAMPLE.TEST:org/project.git")
    token = hub.token(
        "scoped",
        profile="full",
        scopes=["memory:read", "memory:write"],
        read_wings=["shared"],
        write_wings=["shared"],
        trust="standard",
    )
    scoped = service(hub, hub.gateway.authenticate(f"Bearer {token}"))
    own = scoped.rules_publish([rule(scope="board", board="visible")])["rules"][0]
    assert scoped.rules_get()["rules"] == []
    assert scoped.rules_get(board="visible")["rules"] == [shared, own]
    assert scoped.rules_get(board="hidden")["rules"] == []
    assert scoped.rules_get(repo="https://EXAMPLE.TEST/org/project.git")["rules"] == [repo]
    assert secret["key"] not in {r["key"] for r in scoped.rules_changed(0)["rules"]}
    with pytest.raises(ToolError, match="board not found"):
        scoped.rules_publish([rule(scope="board", board="hidden")])
    assert not mcp(hub, token, "rules_publish", agent=env.agent.actor, rules=[rule()])[0]
    other = publish(env, text="Other connection", key="other")
    assert other["key"] not in {r["key"] for r in scoped.rules_changed(0)["rules"]}
    own_edited = env.owner.edit(own["id"], own["version"], scope="board", scope_ref="hidden")
    with pytest.raises(ToolError, match="rule not found"):
        scoped.rules_publish([rule(text="Attempt")])
    scoped.rules_publish([], replace=True)
    assert (
        next(r for r in env.owner.list_rules()["rules"] if r["id"] == own_edited["id"])["status"]
        == "edited"
    )
    assert len(env.owner.list_rules()["rules"]) == 5


def test_untrusted_publish_and_append_only_history(hub, env):
    token = hub.token(
        "untrusted",
        profile="slim",
        trust="untrusted",
        scopes=["memory:read", "memory:write"],
        write_wing="inbox",
    )
    ok, text = mcp(hub, token, "rules_publish", rules=[rule()])
    assert ok and "proposed 1" in text
    principal = hub.gateway.authenticate(f"Bearer {token}")
    current = service(hub, principal).rules_get()["rules"][0]
    assert env.owner.history(current["id"])[0]["stamp_trust"] == "untrusted"
    with env.owner.store.connection() as conn:
        for sql in ("UPDATE rule_versions SET text='changed'", "DELETE FROM rule_versions"):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                conn.execute(sql)
    assert env.owner.history(current["id"])[0]["text"] == rule()["text"]


@pytest.mark.parametrize(
    "item",
    [
        rule(text=""),
        rule(text=" \n "),
        rule(text="x" * 4001),
        rule(kind="invalid"),
        rule(scope="invalid"),
        rule(scope="board"),
        rule(scope="repo"),
        rule(scope="repo", repo="invalid"),
        rule(key=""),
        dict(rule(), agent="conn:other"),
    ],
)
def test_invalid_publish_is_atomic(hub, env, item):
    with pytest.raises(ToolError):
        env.worker.rules_publish([rule(key="valid"), item])
    assert env.owner.store.seq == 0 and env.owner.list_rules()["rules"] == []


def test_bulk_limits_duplicate_keys_and_scope_validation(hub, env):
    for items in ([rule()] * 201, [rule(), rule(text="duplicate")]):
        with pytest.raises(ToolError):
            env.worker.rules_publish(items)
    for value in (-1, True, "0"):
        with pytest.raises(ToolError):
            env.worker.rules_changed(value)
    assert env.worker.store.seq == 0
    assert len(env.worker.rules_publish([rule(key=f"key-{i}") for i in range(200)])["rules"]) == 200


def test_changed_uses_sequence_index_without_loading_text(hub, env):
    current = publish(env)
    with env.worker.store.connection() as conn:
        query = "SELECT agent,key,status,scope,scope_ref FROM rules WHERE seq>? ORDER BY seq"
        plan = conn.execute("EXPLAIN QUERY PLAN " + query, (0,)).fetchall()
        assert any("rules_by_seq" in r[3] for r in plan)
    assert env.worker.rules_changed(0) == {
        "rules": [{"key": current["key"], "status": "proposed"}],
        "seq": 1,
    }


def test_workspace_isolation_and_readonly_board_lookup(hub, env, tmp_path):
    current = publish(env)
    isolated = open_store(tmp_path / "other")
    assert isolated.seq == 0
    with isolated.connection() as conn:
        assert conn.execute("SELECT count(*) FROM rules").fetchone()[0] == 0
    board_path = Path(env.worker.ctx.workspace_dir()) / "boards.sqlite3"
    assert not board_path.exists()
    assert not env.worker._board_visible("missing")
    assert not board_path.exists()
    assert env.worker.rules_get()["rules"] == [current]


def test_concurrent_publications_have_unique_sequences(hub, env):
    def write(index):
        return service(hub, env.agent).rules_publish([rule(key=f"rule-{index}")])

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, range(8)))
    assert sorted(r["seq"] for r in results) == list(range(1, 9))
    assert len(env.owner.list_rules()["rules"]) == 8
    assert extensions.get("rules_publish").trusted_only is False


def test_owner_creates_shared_rule_over_rest(hub, env):
    text = "  Owner instruction\nverbatim"
    status, current = api(
        hub,
        env.cookie,
        "POST",
        "/api/rules",
        {"agent": "*", "text": text, "kind": "instruction", "scope": "global"},
    )
    assert status == 201
    assert current["text"] == text and current["agent_text"] is None
    assert current["source"] == "owner" and current["status"] == "accepted"
    assert current["updated_by"] == env.person.actor
    assert env.worker.rules_get()["rules"] == [current]
    assert mcp(hub, env.token, "rules_changed", since=0) == (
        True,
        f"seq 1\n{current['key']} accepted",
    )
    assert env.owner.history(current["id"])[0]["stamp_connection"] is None
    assert (
        api(
            hub,
            env.cookie,
            "POST",
            "/api/rules",
            {"agent": "*", "text": text, "kind": "instruction"},
        )[0]
        == 400
    )
    assert env.owner.store.seq == 1
