"""Rule lifecycle, visibility and HTTP authorization through the real gateway."""

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from commonplace import events, extensions, sessions, webhooks
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
    assert mcp(hub, env.token, "rules_changed", since=0) == (True, "seq 1\none proposed own")
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
        {"key": current["key"], "status": "retired", "target": "all" if owner_source else "own"}
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
    assert listing["suggestions"] == [suggestion] and listing["seq"] == current["seq"]
    assert listing["rules"][0]["text"] == current["text"]
    status, accepted = api(
        hub,
        env.cookie,
        "POST",
        f"/api/rules/suggestions/{suggestion['id']}",
        {"action": "accept", "version": current["version"]},
    )
    assert status == 200 and accepted["text"] == suggestion["text"]
    assert accepted["source"] == "owner" and accepted["status"] == "edited"
    assert accepted["agent_text"] == current["agent_text"]
    assert env.owner.history(current["id"])[-1]["source"] == "suggestion"
    suggestion = env.owner.suggest(current["id"], "Dismiss me", "helper")
    result = env.owner.decide_suggestion(suggestion["id"], "dismiss")
    assert result["text"] == accepted["text"] and result["seq"] == accepted["seq"]
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
    assert [r["version"] for r in history] == [1, 2]
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
    status, listing = api(hub, member_cookie, "GET", "/api/rules")
    assert status == 200 and listing["can_edit"] is False
    assert listing["agents"] == []
    assert api(hub, env.cookie, "GET", "/api/rules")[1]["can_edit"] is True
    assert api(hub, member_cookie, "GET", "/api/rules/count") == (200, {"waiting": 1, "seq": 1})
    assert api(hub, env.token, "GET", "/api/rules/count")[0] == 403
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
    skipped = scoped.rules_publish([rule(text="Attempt"), rule(text="Still applies", key="next")])
    assert [r["key"] for r in skipped["rules"]] == ["next"]
    assert (
        next(r for r in env.owner.list_rules()["rules"] if r["id"] == own_edited["id"])["text"]
        == own_edited["text"]
    )
    scoped.rules_publish([], replace=True)
    assert (
        next(r for r in env.owner.list_rules()["rules"] if r["id"] == own_edited["id"])["status"]
        == "edited"
    )
    assert len(env.owner.list_rules()["rules"]) == 6


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
        "rules": [{"key": current["key"], "status": "proposed", "target": "own"}],
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
        f"seq 1\n{current['key']} accepted all",
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


def test_republish_dismissed_wording_is_a_full_noop(hub, env):
    original = publish(env)
    edited = env.owner.edit(original["id"], original["version"], text="Owner wording")
    # The original wording and even different metadata cannot reopen the proposal.
    assert publish(env, kind="style") == edited
    proposed = publish(env, text="Proposal X")
    assert proposed["pending"] == 1
    kept = env.owner.action(proposed["id"], proposed["version"], "keep_edit")
    history = env.owner.history(kept["id"])
    assert publish(env, text="Proposal X") == kept
    assert env.owner.store.seq == kept["seq"]
    assert env.owner.history(kept["id"]) == history
    agreed = publish(env, text="Owner wording")
    assert agreed["pending"] == 0 and agreed["agent_text"] == agreed["text"]
    assert agreed["version"] == kept["version"] + 1
    assert publish(env, text="Owner wording") == agreed


def test_agent_retirement_revives_and_replace_protects_owner_edits(hub, env):
    original = publish(env)
    retired = env.worker.rules_publish([], replace=True)["rules"][0]
    assert retired["retired_by"] == "agent" and retired["status"] == "retired"
    revived = publish(env)
    assert revived["text"] == original["text"]
    assert revived["status"] == "proposed" and revived["source"] == "agent"
    assert revived["retired_by"] is None and revived["version"] == retired["version"] + 1
    edited = env.owner.edit(revived["id"], revived["version"], text="Owner edit")
    assert env.worker.rules_publish([], replace=True)["rules"] == []
    assert env.owner.list_rules()["rules"] == [edited]


@pytest.mark.parametrize("decision", ["accept_agent", "keep_edit", "restore"])
@pytest.mark.parametrize("owner_source", [False, True])
def test_owner_retirement_survives_publication(hub, env, decision, owner_source):
    current = publish(env)
    if owner_source:
        current = env.owner.edit(current["id"], current["version"], text="Owner wording")
    retired = env.owner.action(current["id"], current["version"], "retire")
    assert retired["retired_by"] == "owner"
    assert publish(env) == retired
    changed = publish(env, text="New proposal")
    assert changed["status"] == "retired" and changed["retired_by"] == "owner"
    assert changed["text"] == current["text"] and changed["pending"] == 1
    assert env.worker.rules_get()["rules"] == []
    assert env.worker.rules_publish([], replace=True)["rules"] == []
    assert publish(env, text="New proposal") == changed
    decided = env.owner.action(changed["id"], changed["version"], decision)
    if decision == "keep_edit":
        assert decided["status"] == "retired" and decided["retired_by"] == "owner"
        assert publish(env, text="New proposal") == decided
    else:
        assert decided["status"] == (
            "edited" if decision == "restore" and owner_source else "accepted"
        )
        assert decided["retired_by"] is None
    if decision == "accept_agent":
        assert decided["text"] == "New proposal" and decided["source"] == "agent"


def test_suggestions_do_not_change_rules_and_acceptance_is_versioned(hub, env):
    current = publish(env)
    history = env.owner.history(current["id"])
    suggestion = env.owner.suggest(current["id"], "Suggested", "helper")
    assert suggestion["base_version"] == current["version"]
    assert env.owner.list_rules()["rules"] == [current]
    assert env.owner.store.seq == current["seq"]
    assert env.owner.history(current["id"]) == history
    path = f"/api/rules/suggestions/{suggestion['id']}"
    assert api(hub, env.cookie, "POST", path, {"action": "accept"})[0] == 400
    edited = env.owner.edit(current["id"], current["version"], text="New owner edit")
    for version in (current["version"], edited["version"]):
        assert api(hub, env.cookie, "POST", path, {"action": "accept", "version": version}) == (
            409,
            {"error": "rule changed", "current": edited},
        )
    assert env.owner.list_rules()["suggestions"] == [suggestion]
    history = env.owner.history(current["id"])
    assert env.owner.decide_suggestion(suggestion["id"], "dismiss") == edited
    assert env.owner.store.seq == edited["seq"]
    assert env.owner.history(current["id"]) == history
    retired = env.owner.action(edited["id"], edited["version"], "retire")
    suggestion = env.owner.suggest(retired["id"], "Restore silently", "helper")
    assert api(
        hub,
        env.cookie,
        "POST",
        f"/api/rules/suggestions/{suggestion['id']}",
        {
            "action": "accept",
            "version": retired["version"],
        },
    ) == (400, {"error": "cannot accept a suggestion on a retired rule"})
    assert env.owner.list_rules()["rules"] == [retired]


def test_rule_events_follow_commit_and_noops_emit_nothing(hub, env, monkeypatch):
    emitted = []
    real_emit = events.emit

    def emit(db, workspace, kind, **kwargs):
        # This would block if the service still held its SQLite write transaction.
        with env.owner.store.transaction() as conn:
            assert conn.execute("SELECT value FROM meta WHERE key='seq'").fetchone()[0] >= 1
        emitted.append((kind, kwargs["data"]))
        return real_emit(db, workspace, kind, **kwargs)

    monkeypatch.setattr(events, "emit", emit)
    current = publish(env)
    expected = {"agent": env.agent.actor, "ids": [current["id"]], "keys": [current["key"]]}
    assert emitted == [("rule.changed", expected)]
    emitted.clear()
    assert publish(env) == current
    assert emitted == []
    accepted = env.owner.action(current["id"], current["version"], "accept")
    edited = env.owner.edit(current["id"], accepted["version"], text="Owner edit")
    assert emitted == [("rule.changed", expected)] * 2
    emitted.clear()
    suggestion = env.owner.suggest(current["id"], "Suggestion", "helper")
    assert env.owner.store.seq == edited["seq"]
    env.owner.decide_suggestion(suggestion["id"], "dismiss")
    assert emitted == [
        (
            "rule.suggestion",
            {"rule_id": current["id"], "suggestion_id": suggestion["id"], "action": "stored"},
        ),
        (
            "rule.suggestion",
            {"rule_id": current["id"], "suggestion_id": suggestion["id"], "action": "dismissed"},
        ),
    ]
    emitted.clear()
    suggestion = env.owner.suggest(current["id"], "Suggestion", "helper")
    emitted.clear()
    env.owner.decide_suggestion(suggestion["id"], "accept", version=edited["version"])
    assert emitted == [("rule.changed", expected)]
    emitted.clear()
    env.owner.create("*", "Shared owner rule", "limit")
    assert emitted[0][0] == "rule.changed" and emitted[0][1]["agent"] == "*"


def test_scoped_wildcard_webhook_cannot_receive_rule_changes(hub, env):
    token = hub.token(
        "scoped",
        profile="full",
        trust="trusted",
        scopes=["memory:read", "memory:write"],
        read_wings=["shared"],
        write_wings=["shared"],
    )
    principal = hub.gateway.authenticate(f"Bearer {token}")
    hook = webhooks.add(hub.db, principal, "https://example.test/hook", kinds=["*"])
    current = publish(env)
    event = next(e for e in events.after(hub.db, hub.workspace) if e["kind"] == "rule.changed")
    assert event["data"] == {
        "agent": env.agent.actor,
        "ids": [current["id"]],
        "keys": [current["key"]],
    }
    row = hub.db._conn.execute("SELECT * FROM webhooks WHERE id=?", (hook["id"],)).fetchone()
    assert webhooks._matches(row, event)
    assert not webhooks._can_receive(hub.db, hub.gateway, row, event)
    dispatcher = webhooks.Dispatcher(hub.db, gateway=hub.gateway)
    dispatcher.on_event(event)
    assert webhooks.deliveries(hub.db, principal, hook["id"]) == []


def test_waiting_count_matches_visible_review_rows(hub, env):
    first, second, third = env.worker.rules_publish([rule(key=str(i)) for i in range(3)])["rules"]
    env.owner.action(first["id"], first["version"], "accept")
    retired = env.owner.action(second["id"], second["version"], "retire")
    assert env.owner.count()["waiting"] == 1
    env.worker.rules_publish([rule(key=second["key"], text="Changed while retired")])
    assert env.owner.count()["waiting"] == 2
    # Only the third rule is eligible for agent retirement.
    env.worker.rules_publish([], replace=True)
    listing = env.owner.list_rules()
    assert listing["waiting"] == 1
    assert listing["waiting_by_agent"] == {env.agent.actor: 1}
    assert api(hub, env.cookie, "GET", "/api/rules/count") == (
        200,
        {"waiting": listing["waiting"], "seq": listing["seq"]},
    )
    current = next(r for r in listing["rules"] if r["id"] == retired["id"])
    env.owner.action(current["id"], current["version"], "keep_edit")
    assert env.owner.count()["waiting"] == 0
    assert third["key"] != current["key"]


def test_store_initializes_once_and_recovers_deleted_file(tmp_path, monkeypatch):
    from commonplace.rules.store import RuleStore

    reads = []
    read_text = Path.read_text

    def read(path, *args, **kwargs):
        if path.name == "schema.sql":
            reads.append(path)
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    store = RuleStore(tmp_path)
    assert RuleStore(tmp_path / ".").seq == 0
    assert len(reads) == 1
    Path(store.path).unlink()
    assert RuleStore(tmp_path).seq == 0
    assert len(reads) == 2


@pytest.mark.parametrize("key", ["has space", "a/b", "a:b", "é", "a\n", "x" * 101, None, 7])
def test_keys_use_a_bounded_ascii_identifier(hub, env, key):
    with pytest.raises(ToolError, match="no: key must match"):
        env.worker.rules_publish([rule(key=key)])
    assert env.owner.store.seq == 0


@pytest.mark.parametrize("key", ["a", "Ab9._-", "x" * 100])
def test_valid_keys(hub, env, key):
    assert publish(env, key=key)["key"] == key


@pytest.mark.parametrize("retired,total", [(False, 500), (True, 2000)])
def test_publish_caps_are_atomic_and_count_retired_rows(hub, env, retired, total):
    current = publish(env)
    with env.owner.store.transaction() as conn:
        # Populate the boundary without thousands of redundant public calls.
        conn.executemany(
            "INSERT INTO rules(agent,key,text,agent_text,kind,scope,status,source,seq,created_at,updated_at,updated_by,retired_by) "
            "VALUES (?,?,'text','text','instruction','global',?,'agent',1,'stamp','stamp',?,?)",
            [
                (
                    env.agent.actor,
                    f"limit-{i}",
                    "retired" if retired else "proposed",
                    env.agent.actor,
                    "agent" if retired else None,
                )
                for i in range(total - 1)
            ],
        )
    history = env.owner.history(current["id"])
    with pytest.raises(ToolError, match="no: agent rule limit exceeded"):
        env.worker.rules_publish([rule(text="Must roll back"), rule(key="extra")])
    assert env.owner.store.seq == current["seq"]
    assert env.owner.history(current["id"]) == history
    assert env.worker.rules_get()["rules"][0] == current
    assert len(env.owner.list_rules()["rules"]) == total
    if retired:
        # Reviving an existing key consumes no extra row.
        assert publish(env, key="limit-0")["status"] == "proposed"
    else:
        # Replacement is evaluated as a whole, after dropped keys retire.
        assert (
            env.worker.rules_publish([rule(key="replacement")], replace=True)["rules"][0]["status"]
            == "proposed"
        )


@pytest.mark.parametrize(
    "action_name,status,pending",
    [
        ("accept", "accepted", 0),
        ("accept", "edited", 0),
        ("accept", "retired", 0),
        ("retire", "retired", 0),
        ("restore", "proposed", 0),
        ("restore", "accepted", 0),
        ("keep_edit", "edited", 0),
        ("accept_agent", "edited", 0),
    ],
)
def test_invalid_actions_are_400_without_writes(hub, env, action_name, status, pending):
    current = publish(env)
    with env.owner.store.transaction() as conn:
        conn.execute(
            "UPDATE rules SET status=?,pending=? WHERE id=?", (status, pending, current["id"])
        )
    history = env.owner.history(current["id"])
    assert api(
        hub,
        env.cookie,
        "POST",
        f"/api/rules/{current['id']}/action",
        {
            "version": current["version"],
            "action": action_name,
        },
    ) == (400, {"error": "invalid action"})
    assert env.owner.store.seq == current["seq"]
    assert env.owner.history(current["id"]) == history


def test_identical_owner_edits_are_400(hub, env):
    current = publish(env)
    for changes in (
        {},
        {"text": current["text"]},
        {"kind": current["kind"]},
        {"note": "Only a note"},
    ):
        assert api(
            hub,
            env.cookie,
            "PATCH",
            f"/api/rules/{current['id']}",
            {
                "version": current["version"],
                **changes,
            },
        ) == (400, {"error": "nothing to change"})
    assert env.owner.store.seq == current["seq"]
    assert len(env.owner.history(current["id"])) == 1


def test_unknown_arguments_and_internal_type_errors(hub, env, monkeypatch):
    from commonplace.rules.tools import run

    current = publish(env)
    for method, path, body in [
        ("POST", "/api/rules", {"agent": "*", "text": "Text", "kind": "limit"}),
        ("PATCH", f"/api/rules/{current['id']}", {"version": 1, "text": "Edit"}),
        ("POST", f"/api/rules/{current['id']}/action", {"version": 1, "action": "accept"}),
        (
            "POST",
            f"/api/rules/{current['id']}/suggestions",
            {"text": "Text", "from_agent": "helper"},
        ),
        ("POST", "/api/rules/suggestions/1", {"action": "dismiss"}),
    ]:
        assert api(hub, env.cookie, method, path, {**body, "unknown": True}) == (
            400,
            {"error": "unknown arguments"},
        )
    for name, arguments in [
        ("rules_get", {}),
        ("rules_publish", {"rules": []}),
        ("rules_changed", {"since": 0}),
    ]:
        with pytest.raises(ToolError, match="invalid arguments"):
            run(env.worker.ctx, name, {**arguments, "unknown": True})
    for name in ("rules_publish", "rules_changed"):
        with pytest.raises(ToolError, match="invalid arguments"):
            run(env.worker.ctx, name, {})

    def broken(*args, **kwargs):
        raise TypeError("internal bug")

    monkeypatch.setattr(RuleService, "rules_get", broken)
    with pytest.raises(TypeError, match="internal bug"):
        run(env.worker.ctx, "rules_get", {})
    monkeypatch.setattr(RuleService, "list_rules", broken)
    assert api(hub, env.cookie, "GET", "/api/rules") == (500, {"error": "internal error"})


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": "style"},
        {"scope": "repo", "repo": "example.test/org/project"},
        {"scope": "repo", "repo": "example.test/org/other"},
    ],
)
def test_agent_classification_changes_require_owner_review(hub, env, changes):
    initial = (
        {"scope": "repo", "repo": "example.test/org/project"}
        if changes.get("repo", "").endswith("other")
        else {}
    )
    current = publish(env, **initial)
    accepted = action(hub, env, current, "accept")
    changed = publish(env, **changes)
    assert changed["text"] == accepted["text"]
    assert changed["status"] == "proposed"
    assert changed["version"] == accepted["version"] + 1
    assert changed["seq"] == accepted["seq"] + 1
    assert publish(env, **changes) == changed


def test_owner_classification_ignores_agent_only_kind_change(hub, env):
    current = publish(env)
    edited = env.owner.edit(current["id"], current["version"], kind="limit")
    history = env.owner.history(current["id"])
    assert publish(env, kind="style") == edited
    assert env.owner.store.seq == edited["seq"]
    assert env.owner.history(current["id"]) == history


def test_owner_restore_keeps_agent_dropped_rule(hub, env):
    publish(env)
    retired = env.worker.rules_publish([], replace=True)["rules"][0]
    restored = action(hub, env, retired, "restore")
    assert restored["status"] == "accepted" and restored["source"] == "owner"
    assert restored["retired_by"] is None
    assert env.worker.rules_publish([], replace=True)["rules"] == []
    assert env.worker.rules_get()["rules"] == [restored]
    proposal = publish(env, text="Different agent wording", kind="style")
    assert proposal["pending"] == 1 and proposal["agent_text"] == "Different agent wording"
    assert proposal["text"] == restored["text"] and proposal["kind"] == restored["kind"]
    assert proposal["source"] == "owner" and proposal["status"] == "accepted"


@pytest.mark.parametrize("retired_by", ["owner", "agent"])
@pytest.mark.parametrize("changes", [{}, {"text": "Attempted edit"}])
def test_patch_retired_rule_requires_restore(hub, env, retired_by, changes):
    current = publish(env)
    retired = (
        action(hub, env, current, "retire")
        if retired_by == "owner"
        else env.worker.rules_publish([], replace=True)["rules"][0]
    )
    history = env.owner.history(current["id"])
    assert api(
        hub,
        env.cookie,
        "PATCH",
        f"/api/rules/{current['id']}",
        {"version": retired["version"], **changes},
    ) == (400, {"error": "restore the rule first"})
    assert env.owner.store.seq == retired["seq"]
    assert env.owner.history(current["id"]) == history


def test_changed_reset_and_same_key_targets(hub, env):
    assert mcp(hub, env.token, "rules_changed", since=9) == (True, "reset\nseq 0")
    shared = env.owner.create("*", "Shared text", "limit")
    own = publish(env, key=shared["key"])
    assert env.worker.rules_changed(0) == {
        "rules": [
            {"key": shared["key"], "status": "accepted", "target": "all"},
            {"key": own["key"], "status": "proposed", "target": "own"},
        ],
        "seq": 2,
    }
    assert mcp(hub, env.token, "rules_changed", since=0) == (
        True,
        f"seq 2\n{shared['key']} accepted all\n{own['key']} proposed own",
    )
    assert mcp(hub, env.token, "rules_changed", since=3) == (True, "reset\nseq 2")
    assert mcp(hub, env.token, "rules_changed", since=2) == (True, "seq 2")


@pytest.mark.parametrize("operation", ["get", "changed", "replace"])
def test_board_visibility_cached_for_each_request(hub, env, monkeypatch, operation):
    boards = BoardService(ToolContext(principal=env.person, gateway=hub.gateway))
    boards.board_add(slug="visible", wing="shared")
    env.worker.rules_publish(
        [rule(key=f"rule-{i}", scope="board", board="visible") for i in range(3)]
    )
    original_connect = sqlite3.connect
    lookups = []

    def connect(path, *args, **kwargs):
        if "boards.sqlite3" in str(path):
            lookups.append(path)
        return original_connect(path, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
    worker = service(hub, env.agent)
    if operation == "get":
        assert len(worker.rules_get(board="visible")["rules"]) == 3
    elif operation == "changed":
        assert len(worker.rules_changed(0)["rules"]) == 3
    else:
        assert len(worker.rules_publish([], replace=True)["rules"]) == 3
    assert len(lookups) == 1
    assert worker._board_visible("missing") is False
    assert worker._board_visible("missing") is False
    assert len(lookups) == 2
    # A new request must recheck current visibility, including cached misses.
    boards.board_add(slug="missing", wing="shared")
    assert service(hub, env.agent)._board_visible("missing") is True
