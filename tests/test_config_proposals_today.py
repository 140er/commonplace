"""Pending Coworker config proposals in the owner's Waiting on you list (card #662).

A proposed config is a decision the owner makes in Today: the feed lists it for the owner only,
with the proposer by display name, a plain-words summary and the diff, and accepting or
rejecting goes through the existing orchestrator routes. Names and text are synthetic.
"""

from commonplace import sessions
from commonplace.orch import proposals, schema, store as orch_store
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.runner_harness import http
from tests.test_orch_surfaces import BOARD, FRESH, config_text, env as env, sha


def propose_limit(env, limit, *, principal=None, board=BOARD):
    text = config_text(board=board).replace("max_agents = 3", f"max_agents = {limit}")
    version = orch_store.open_store(env.hub.gateway, env.ws).propose(
        principal or env.a, board, text, connections=[]
    )
    return version.version, sha(text)


def feed(env, cookie=None):
    return http(env.hub.port, "GET", "/api/needs-me", cookie=cookie or env.cookie)


def member_cookie(env, email="queue-reader@example.test"):
    user = env.hub.db.create_user(email)
    env.hub.db.add_member(env.ws, user, "member", actor=env.hub.admin)
    link = sessions.create_login_link(env.hub.db, email, env.ws)
    return f"{sessions.COOKIE}={sessions.redeem_login_link(env.hub.db, link)}"


def test_owner_sees_a_proposed_config_with_name_summary_and_diff(env):
    status, before = feed(env)
    assert status == 200 and before["config_proposals"] == []
    version, digest = propose_limit(env, 5)
    status, body = feed(env)
    assert status == 200, body
    (item,) = body["config_proposals"]
    assert item["board"] == BOARD and item["version"] == version and item["ref"] == f"v{version}"
    assert item["sha256"] == digest and item["channel"] == "live"
    assert item["proposed_by"] == "conn:orch-a" and item["proposer_name"] == "orch-a"
    assert item["summary"] == "Agent limit: 3 to 5"
    assert "max_agents" in item["diff"] and "3" in item["diff"] and "5" in item["diff"]
    assert body["count"] == before["count"] + 1


def test_the_coworker_seat_is_named_after_its_project(env):
    _, seat = env.pat("Coworker:acme")
    propose_limit(env, 4, principal=seat)
    (item,) = feed(env)[1]["config_proposals"]
    assert item["proposed_by"] == "conn:Coworker:acme"
    assert item["proposer_name"] == "acme bot"
    assert "Coworker:" not in item["proposer_name"]


def test_a_member_never_sees_config_proposals(env):
    propose_limit(env, 5)
    status, body = feed(env, member_cookie(env))
    assert "config_proposals" not in (body if isinstance(body, dict) else {}), body
    assert status != 200 or not body.get("count")


def test_an_agent_token_does_not_get_the_proposals(env):
    propose_limit(env, 5)
    status, body = http(env.hub.port, "GET", "/api/needs-me", token=env.seat_token)
    assert "config_proposals" not in (body if isinstance(body, dict) else {}), (status, body)


def test_accepting_through_the_item_applies_the_existing_path(env):
    version, _ = propose_limit(env, 5)
    (item,) = feed(env)[1]["config_proposals"]
    status, result = env.person(
        "POST",
        f"boards/{item['board']}/configs/{item['version']}/accept",
        {"sha256": item["sha256"]},
    )
    assert status == 200 and result["accepted"] == f"v{version}", result
    assert feed(env)[1]["config_proposals"] == []
    status, configs = env.person("GET", f"boards/{BOARD}/configs")
    assert configs["accepted"]["version"] == version


def test_rejecting_through_the_item_leaves_the_current_config(env):
    version, _ = propose_limit(env, 5)
    (item,) = feed(env)[1]["config_proposals"]
    status, result = env.person(
        "POST", f"boards/{item['board']}/configs/{item['version']}/reject", {}
    )
    assert status == 200 and result["status"] == "rejected", result
    assert feed(env)[1]["config_proposals"] == []
    status, configs = env.person("GET", f"boards/{BOARD}/configs")
    assert configs["accepted"]["version"] != version
    assert {v["version"]: v["status"] for v in configs["versions"]}[version] == "rejected"


def test_a_board_the_owner_cannot_read_is_not_listed(env):
    text = config_text(board="closed")
    orch_store.open_store(env.hub.gateway, env.ws).propose(env.a, "closed", text, connections=[])
    boards = [i["board"] for i in feed(env)[1]["config_proposals"]]
    assert "closed" not in boards or env.owner.policy.can_read_wing("closed")


def test_decided_versions_are_not_listed(env):
    first, _ = propose_limit(env, 5)
    second, _ = propose_limit(env, 6)
    env.person("POST", f"boards/{BOARD}/configs/{first}/reject", {})
    versions = [i["version"] for i in feed(env)[1]["config_proposals"]]
    assert versions == [second]


def test_summary_words():
    def change(path, op, old=None, new=None):
        return schema.Change(path, op, old, new)

    assert proposals.summarize([change("limits.max_agents", "change", 2, 4)]) == (
        "Agent limit: 2 to 4"
    )
    assert proposals.summarize([change("limits.max_agents", "add", None, 4)]) == (
        "Agent limit set to 4"
    )
    assert proposals.summarize([change("limits.max_agents", "remove", 4, None)]) == (
        "Agent limit removed"
    )
    assert proposals.summarize([change("roles.main.prompt", "change", "a", "b")]) == (
        "Changes the main instructions"
    )
    many = [change(f"roles.r{i}.prompt", "change", "a", "b") for i in range(5)]
    words = proposals.summarize(many)
    assert words.endswith("and 2 more changes") and words.count(";") == 2
    assert proposals.summarize([]) == "No changes to the settings"


def test_first_config_has_no_diff_baseline_but_still_a_summary(env):
    # FRESH has no accepted config: the whole file is the change.
    text = config_text(board=FRESH).replace("max_agents = 3", "max_agents = 7")
    orch_store.open_store(env.hub.gateway, env.ws).propose(env.a, FRESH, text, connections=[])
    items = [i for i in feed(env)[1]["config_proposals"] if i["board"] == FRESH]
    (item,) = items
    assert item["summary"].startswith("First config")
    assert "Agent limit 7" in item["summary"]
    assert "max_agents" in item["diff"]
