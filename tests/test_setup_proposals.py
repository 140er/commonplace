"""Setup proposals approved by the owner (card #675), slice 1: the proposal store, its sha256 over
canonical JSON, the propose tool and route, and the plain-words diff. Nothing applies here."""

import hashlib
import json

import pytest

from commonplace.boards.store import BoardStore
from commonplace.projects import setups
from tests._projects_helpers import projects_hub_fixture  # noqa: F401

ROUTE = "/api/setup-proposals"
GROK = {"provider": "grok", "model": "grok-4.7"}
OPUS = {"provider": "claude", "model": "claude-opus-5-5"}
SETUP = {
    "bots": [{"key": "lead", "name": "Work lead", "instructions": "Plan the week's cards."}],
    "coworkers": [{"key": "work-lead", "board": "work", "bot": "lead", "title": "Work"}],
    "models": {
        "workspace": {"main": GROK, "worker": GROK},
        "projects": {"work": {"reviewer": OPUS, "gate": None}},
    },
}


def _propose(h, auth, body=None):
    return h.request(auth, "POST", ROUTE, SETUP if body is None else body)[:2]


def _tool(h, auth, name, **arguments):
    status, said = h.mcp(auth, name, **arguments)
    assert status == 200, said
    return said


def _rows(h):
    with h.db._lock:
        return [
            dict(r)
            for r in h.db._conn.execute(
                "SELECT * FROM setup_proposals WHERE workspace_id=? ORDER BY id", (h.workspace,)
            )
        ]


def _model_rows(h):
    with h.db._lock:
        return h.db._conn.execute(
            "SELECT count(*) FROM chat_model_settings WHERE workspace_id=?", (h.workspace,)
        ).fetchone()[0]


def _member_token(h):
    return h.hub.token(
        "member-agent",
        user=h.member_user,
        profile="full",
        scopes=["memory:read", "memory:write"],
        read_wings=["work"],
        write_wings=["work"],
        trust="trusted",
    )


# ---------------------------------------------------------------- store and digest


def test_an_owner_agent_proposes_a_setup_stored_as_a_versioned_record_with_its_sha256(
    projects_hub,
):
    h = projects_hub
    said = _tool(h, h.a1, "setup_propose", **SETUP)
    assert said["ok"], said
    view = json.loads(said["text"])
    assert (view["id"], view["ref"], view["status"]) == (1, "v1", "proposed")
    assert view["proposed_by"] == "conn:a1" and view["proposer_name"] == "a1"
    # The digest is over the stored canonical bytes, which anyone can recompute from a read.
    assert view["sha256"] == hashlib.sha256(view["body"].encode("utf-8")).hexdigest()
    assert view["body"] == json.dumps(
        view["setup"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    assert view["setup"]["bots"][0] == SETUP["bots"][0]
    assert view["setup"]["models"]["projects"]["work"] == {"reviewer": OPUS, "gate": None}
    (row,) = _rows(h)
    assert (row["digest"], row["status"], row["body"]) == (view["sha256"], "proposed", view["body"])
    # Proposing changed nothing else: no model setting, no Coworker bot.
    assert _model_rows(h) == 0
    status, coworkers = h.request(h.owner, "GET", "/api/coworkers")[:2]
    assert status == 200 and coworkers["bots"] == []


def test_the_same_body_still_waiting_is_answered_again_and_a_changed_one_is_a_new_version(
    projects_hub,
):
    h = projects_hub
    status, first = _propose(h, h.owner)
    assert status == 201, first
    reordered = {"models": SETUP["models"], "coworkers": SETUP["coworkers"], "bots": SETUP["bots"]}
    status, again = _propose(h, h.a1, reordered)
    assert status == 200 and (again["id"], again["sha256"]) == (first["id"], first["sha256"])
    changed = {**SETUP, "bots": [{**SETUP["bots"][0], "name": "Work lead 2"}]}
    status, second = _propose(h, h.a1, changed)
    assert status == 201 and second["id"] == 2 and second["sha256"] != first["sha256"]
    listed = h.request(h.owner, "GET", ROUTE)[1]["proposals"]
    assert [item["ref"] for item in listed] == ["v2", "v1"]
    assert h.request(h.owner, "GET", ROUTE + "?status=proposed")[1]["proposals"][0]["id"] == 2


def test_at_most_twenty_proposals_wait_at_once(projects_hub):
    h = projects_hub
    for n in range(setups.MAX_PENDING):
        body = {"bots": [{"key": "b", "name": f"Bot {n}"}]}
        assert _propose(h, h.a1, body)[0] == 201
    status, body = _propose(h, h.a1, {"bots": [{"key": "b", "name": "One more"}]})
    assert (status, body["code"]) == (409, "too_many_pending")
    assert len(_rows(h)) == setups.MAX_PENDING


# ---------------------------------------------------------------- the readable diff


def test_every_read_carries_plain_words_changes_against_the_workspace_now(projects_hub):
    h = projects_hub
    # The workspace already runs main on Grok: that line reads "stays".
    assert h.request(h.owner, "PUT", "/api/settings/models", {"main": GROK})[0] == 200
    status, view = _propose(h, h.a1)
    assert status == 201, view
    by_kind = {}
    for line in view["changes"]:
        by_kind.setdefault(line["kind"], []).append(line)
    assert by_kind["bot"] == [
        {"kind": "bot", "key": "lead", "action": "create", "text": 'Creates the bot "Work lead"'}
    ]
    (coworker,) = by_kind["coworker"]
    assert coworker["action"] == "create" and coworker["board"] == "work"
    assert coworker["text"] == 'Creates a Coworker on work with the bot "Work lead": "Work"'
    models = {(line["board"], line["role"]): line for line in by_kind["model"]}
    assert models[(None, "main")]["action"] == "unchanged"
    assert models[(None, "main")]["text"] == "Workspace default Bot: stays grok/grok-4.7"
    assert models[(None, "worker")]["action"] == "set"
    assert models[("work", "reviewer")]["action"] == "set"
    # A model not on the project's list yet is added to it first on apply, and the line says so.
    assert models[("work", "reviewer")]["adds_to_list"] is True
    assert "added to the model list" in models[("work", "reviewer")]["text"]
    assert models[("work", "gate")]["action"] == "unchanged"  # nothing to clear
    assert view["problems"] == 0
    assert view["summary"].startswith('Creates the bot "Work lead"; Creates a Coworker on work')
    one = h.request(h.owner, "GET", f"{ROUTE}/{view['id']}")[1]
    assert one["changes"] == view["changes"] and "results" not in one


def test_a_project_that_disappears_after_proposing_is_marked_as_a_problem(projects_hub):
    h = projects_hub
    status, view = _propose(h, h.owner)
    assert status == 201
    with BoardStore(h.hub.gateway.workspace_dir(h.workspace)).connection() as conn:
        conn.execute("UPDATE boards SET archived=1 WHERE slug='work'")
        conn.commit()
    again = h.request(h.owner, "GET", f"{ROUTE}/{view['id']}")[1]
    flagged = {line["kind"] for line in again["changes"] if line.get("problem")}
    assert flagged == {"coworker", "model"} and again["problems"] == 3


# ---------------------------------------------------------------- validation


BAD = [
    ({"bots": [], "coworkers": []}, "empty", ""),
    ({"bots": [{"key": "a", "name": "A", "job": "x"}]}, "unknown_field", "bots[0]"),
    (
        {"bots": [{"key": "a", "name": "A"}, {"key": "a", "name": "B"}]},
        "duplicate_key",
        "bots[1].key",
    ),
    (
        {"coworkers": [{"key": "c", "board": "work", "bot": "none"}]},
        "unknown_bot",
        "coworkers[0].bot",
    ),
    ({"bots": [{"key": "Bad Key", "name": "A"}]}, "invalid", "bots[0].key"),
    ({"bots": [{"key": "a", "name": " "}]}, "invalid", "bots[0].name"),
    (
        {"bots": [{"key": "a", "name": "A", "instructions": "use sk-ant-" + "x" * 40}]},
        "looks_secret",
        "bots[0].instructions",
    ),
    ({"models": {"workspace": {"front": GROK}}}, "unknown_field", "models.workspace"),
    ({"models": {"workspace": {"main": {"provider": "grok"}}}}, "invalid", "models.workspace.main"),
    ({"setup": {}}, "unknown_field", ""),
]


@pytest.mark.parametrize("body, code, path", BAD)
def test_validate_refuses_a_bad_setup_with_its_code_and_field(body, code, path):
    with pytest.raises(setups.SetupError) as refused:
        setups.validate(body)
    assert (refused.value.code, refused.value.path) == (code, path)


def test_canonical_bytes_ignore_key_order_but_keep_list_order():
    body = setups.validate(SETUP)
    raw = setups.canonical(body)
    assert raw == setups.canonical(json.loads(json.dumps(body, sort_keys=False)))
    assert setups.digest(raw) == hashlib.sha256(raw).hexdigest()
    swapped = {**SETUP, "bots": SETUP["bots"] + [{"key": "b", "name": "Ünïcode"}]}
    other = {**SETUP, "bots": [{"key": "b", "name": "Ünïcode"}] + SETUP["bots"]}
    assert setups.canonical(setups.validate(swapped)) != setups.canonical(setups.validate(other))
    assert "Ünïcode".encode() in setups.canonical(setups.validate(swapped))


def test_a_bad_setup_is_refused_on_every_surface_and_nothing_is_stored(projects_hub):
    h = projects_hub
    unknown_project = [
        ({"models": {"projects": {"nowhere": {"main": GROK}}}}, "unknown_project"),
        (
            {
                "bots": [{"key": "a", "name": "A"}],
                "coworkers": [{"key": "c", "board": "nowhere", "bot": "a"}],
            },
            "unknown_project",
        ),
    ]
    for body, code in [(body, code) for body, code, _ in BAD] + unknown_project:
        status, answer = _propose(h, h.a1, body)
        assert (status, answer["code"]) == (400, code), answer
        if "setup" not in body:
            said = _tool(h, h.a1, "setup_propose", **body)
            assert not said["ok"] and f"({code})" in said["text"], said
    assert _rows(h) == []


# ---------------------------------------------------------------- who is answered


def test_members_their_agents_and_other_workspaces_get_the_404_of_an_unknown_route(projects_hub):
    h = projects_hub
    status, view = _propose(h, h.owner)
    assert status == 201
    member_agent = _member_token(h)
    for who in (h.member, member_agent):
        assert h.request(who, "GET", ROUTE)[:2] == (404, {"error": "not found"})
        assert h.request(who, "GET", f"{ROUTE}/{view['id']}")[:2] == (404, {"error": "not found"})
        assert _propose(h, who)[0] == 404
    # The owner of another workspace reads only their own (empty) list, never this one.
    assert h.request(h.other, "GET", ROUTE)[1] == {"proposals": []}
    assert h.request(h.other, "GET", f"{ROUTE}/{view['id']}")[:2] == (404, {"error": "not found"})
    # The tools are not even listed for a member's agent.
    status, listed, _ = h.request(
        member_agent, "POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    )
    names = {tool["name"] for tool in listed["result"]["tools"]}
    assert status == 200 and not {"setup_propose", "setup_show"} & names
    assert not h.mcp(member_agent, "setup_show", id=view["id"])[1]["ok"]
    # An unknown id and a malformed path read exactly like an unknown route.
    assert h.request(h.owner, "GET", f"{ROUTE}/999")[:2] == (404, {"error": "not found"})
    assert h.request(h.owner, "GET", f"{ROUTE}/v1")[:2] == (404, {"error": "not found"})
    assert _rows(h)[0]["status"] == "proposed"


def test_an_owner_connection_reads_only_proposals_whose_projects_it_can_read(projects_hub):
    h = projects_hub
    h.create_board(h.owner, "alpha")
    status, view = _propose(
        h, h.owner, {**SETUP, "models": {"projects": {"alpha": {"main": GROK}}}}
    )
    assert status == 201
    narrow = h.hub.token(
        "narrow",
        profile="full",
        scopes=["memory:read", "memory:write"],
        read_wings=["work"],
        write_wings=["work"],
        trust="trusted",
    )
    assert h.request(narrow, "GET", f"{ROUTE}/{view['id']}")[0] == 404
    assert h.request(narrow, "GET", ROUTE)[1]["proposals"] == []
    status, refused = _propose(h, narrow, {"models": {"projects": {"alpha": {"main": GROK}}}})
    assert (status, refused["code"]) == (400, "unknown_project")
    said = json.loads(_tool(h, h.a1, "setup_show", id=view["id"])["text"])
    assert said["id"] == view["id"] and said["problems"] == 0


def test_no_read_holds_a_token_or_credential(projects_hub):
    h = projects_hub
    _propose(h, h.a1)
    raw = h.request(h.owner, "GET", ROUTE)[2].decode()
    for marker in ("cmmn_", "Bearer", "token", "secret", "password", "key_ref"):
        assert marker not in raw


def test_the_schema_is_additive_and_keeps_every_row(projects_hub):
    h = projects_hub
    _propose(h, h.a1)
    before = _rows(h)
    setups.ensure_schema(h.db)
    setups.ensure_schema(h.db)
    assert _rows(h) == before
