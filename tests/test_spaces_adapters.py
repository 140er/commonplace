"""Space cards and chat adapters and the space_* MCP names, through the real gateway."""

import json

from commonplace import events, sessions
from commonplace.boards import BoardService
from tests import test_commonplace_rules_enforce as rules_tests, test_spaces_contract as contract

env = rules_tests.env
http = contract.http
call = rules_tests.call
text = rules_tests.text
setup = rules_tests.setup
snapshot = contract.snapshot
VERBATIM = 'Every word.\n"Quoted" @reference — 그대로'


def failed(response):
    return bool(response.get("error") or response.get("result", {}).get("isError"))


def tool_names(env, principal=None):
    response = env.gateway.mediator.handle(
        principal or env.principal, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    )
    return {tool["name"] for tool in response["result"]["tools"]}


def test_cards_adapter_matches_the_board_route(env, http):
    setup(env, "off")
    canonical = http("GET", "/api/spaces/work/cards")
    assert canonical[0] == 200, canonical
    assert canonical[1] == http("GET", "/api/boards/work/cards")[1]
    assert [c["title"] for c in canonical[1]["cards"]] == ["Original"]
    status, card, _ = http(
        "POST", "/api/spaces/work/cards", {"title": "Via space", "space": "work"}
    )
    assert status == 201, card
    assert card["title"] == "Via space" and card["board"] == "work" and card["url"]
    titles = [c["title"] for c in http("GET", "/api/boards/work/cards")[1]["cards"]]
    assert titles == ["Original", "Via space"]


def test_cards_adapter_needs_the_exact_slug_and_one_reference(env, http):
    setup(env, "off")
    BoardService(env.owner.ctx).board_add(slug="bound", wing="shared", repo="github.com/acme/bound")
    assert http("GET", "/api/spaces/missing/cards")[:2] == (404, {"error": "space_not_found"})
    assert http("GET", "/api/spaces/github.com%2Facme%2Fbound/cards")[:2] == (
        404,
        {"error": "space_not_found"},
    )
    assert http("POST", "/api/spaces/missing/cards", {"title": "Nowhere"})[:2] == (
        404,
        {"error": "space_not_found"},
    )
    before = snapshot(env)
    for body in (
        {"title": "Conflict", "space": "bound"},
        {"title": "Conflict", "board": "bound"},
        {"title": "Stale", "rules_seq": 1},
        {"title": "Unknown", "bogus": 1},
    ):
        assert http("POST", "/api/spaces/work/cards", body)[:2] == (
            400,
            {"error": "invalid_arguments"},
        ), body
    assert http("POST", "/api/spaces/work/cards", raw="not json")[:2] == (
        400,
        {"error": "invalid_arguments"},
    )
    assert snapshot(env) == before


def test_chat_adapter_keeps_every_word_and_names_the_space(env, http):
    setup(env, "off")
    status, message, _ = http("POST", "/api/spaces/work/chat", {"text": VERBATIM})
    assert status == 201, message
    assert message["text"] == VERBATIM
    status, page, _ = http("GET", "/api/spaces/work/chat")
    assert status == 200, page
    assert page["space"] == "work" and "board" not in page
    assert [m["text"] for m in page["messages"]] == [VERBATIM]
    legacy = http("GET", "/api/boards/work/chat")[1]
    assert page["messages"] == legacy["messages"] and page["cursor"] == legacy["cursor"]
    assert http("GET", "/api/spaces/work/chat?after=abc")[:2] == (
        400,
        {"error": "invalid_arguments"},
    )
    assert http("POST", "/api/spaces/work/chat", {"text": "x", "space": "other"})[0] == 400
    assert http("GET", "/api/spaces/missing/chat")[:2] == (404, {"error": "space_not_found"})


def test_adapter_rejected_shapes(env, http):
    setup(env, "off")
    before = snapshot(env)
    for method, path in (
        ("PATCH", "/api/spaces/work/cards"),
        ("DELETE", "/api/spaces/work/chat"),
    ):
        status, body, headers = http(method, path, {})
        assert status == 405, (method, path)
        assert body == {"error": "method_not_allowed"} and headers["Allow"] == "GET, POST"
    for path in ("/api/spaces/work/settings", "/api/spaces/work/cards/1", "/api/spaces/work/x/y"):
        assert http("GET", path)[:2] == (404, {"error": "not_found"}), path
    assert http("GET", "/api/spaces/work/cards", headers={"Idempotency-Key": "k"})[1] == {
        "error": "unsupported_idempotency"
    }
    assert snapshot(env) == before


def test_adapter_rules_header_is_required_and_audited(env, http):
    setup(env)
    seq = rules_tests.refresh(env)
    before = events.latest_seq(env.db, env.workspace)
    for path, body in (
        ("/api/spaces/work/cards", {"title": "Needs rules"}),
        ("/api/spaces/work/chat", {"text": "Needs rules"}),
    ):
        status, response, _ = http("POST", path, body)
        assert status == 428 and response["rules_refresh"] is True, path
    assert events.latest_seq(env.db, env.workspace) == before
    assert http("POST", "/api/spaces/work/cards", {"title": "Fresh"}, seq)[0] == 201
    assert http("POST", "/api/spaces/work/chat", {"text": "Fresh"}, seq)[0] == 201


def test_adapter_routes_follow_the_tool_gates(env, http):
    setup(env, "off")
    reader = env.agent("reader", tools=["task_list", "space_read"])[0]
    assert http("GET", "/api/spaces/work/cards", token=reader)[0] == 200
    assert http("GET", "/api/spaces/work/chat", token=reader)[0] == 200
    for method, path, body in (
        ("POST", "/api/spaces/work/cards", {"title": "No"}),
        ("POST", "/api/spaces/work/chat", {"text": "No"}),
    ):
        assert http(method, path, body, token=reader)[:2] == (
            403,
            {"error": "tool not allowed for this connection"},
        )
    blind = env.agent("blind", tools=["board_list"])[0]
    for path in ("/api/spaces/work/cards", "/api/spaces/work/chat"):
        assert http("GET", path, token=blind)[0] == 403, path
    assert http("GET", "/api/spaces/work/cards", principal=env.person)[0] == 200


def test_space_tools_run_the_board_operations(env):
    setup(env, "off")
    names = tool_names(env)
    assert {"space_list", "space_add", "space_set", "space_read", "space_post"} <= names
    listed = call(env, "space_list")
    assert not failed(listed), listed
    assert text(listed) == text(call(env, "board_list"))
    added = call(env, "space_add", slug="made", wing="shared")
    assert not failed(added), added
    assert text(added) == "added space made"
    assert text(call(env, "board_add", slug="legacy")) == "added board legacy"
    assert "made" in text(call(env, "board_list"))


def test_space_chat_tools_take_space_or_board_but_not_two(env):
    setup(env, "off")
    posted = call(env, "space_post", space="work", text=VERBATIM)
    assert not failed(posted), posted
    assert json.loads(text(posted))["text"] == VERBATIM
    by_space = call(env, "space_read", space="work")
    assert not failed(by_space), by_space
    assert text(by_space) == text(call(env, "space_read", board="work"))
    assert VERBATIM in json.loads(text(by_space))["messages"][0]["text"]
    for arguments in ({"space": "work", "board": "other"}, {}):
        refused = call(env, "space_read", **arguments)
        assert failed(refused), arguments
    assert "different spaces" in text(call(env, "space_post", space="a", board="b", text="x"))
    assert "space is required" in text(call(env, "space_post", text="x"))


def test_space_set_is_owner_only_like_board_set(env):
    setup(env, "off")
    coordinator = env.principal.actor
    changed = call(env, "space_set", principal=env.person, space="work", coordinator=coordinator)
    assert not failed(changed), changed
    assert json.loads(text(changed))["coordinator"] == coordinator
    cleared = call(env, "space_set", space="work", coordinator=None, principal=env.person)
    assert not failed(cleared), cleared
    assert BoardService(env.owner.ctx).board_list()[0]["coordinator"] is None
    user = env.db.create_user("member@example.test")
    env.db.add_member(env.workspace, user, "member", actor=env.user)
    member = sessions.person_principal(env.db, user, env.workspace)
    for tool, key in (("space_set", "space"), ("board_set", "board")):
        refused = call(env, tool, principal=member, **{key: "work", "coordinator": None})
        assert failed(refused), tool
    assert tool_names(env, member) >= {"space_list"}
    assert "space_set" not in tool_names(env, member)


def test_either_tool_name_grants_both(env):
    setup(env, "off")
    adder = env.agent("adder", tools=["board_add"])[1]
    assert {"board_add", "space_add"} <= tool_names(env, adder)
    assert "space_list" not in tool_names(env, adder)
    refused = call(env, "space_list", principal=adder)
    assert failed(refused)
    made = call(env, "space_add", principal=adder, slug="by-alias", wing="shared")
    assert not failed(made), made
    lister = env.agent("lister", tools=["space_list"])[1]
    assert {"board_list", "space_list"} <= tool_names(env, lister)
    assert not failed(call(env, "board_list", principal=lister))
    assert failed(call(env, "space_add", principal=lister, slug="denied"))
