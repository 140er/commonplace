"""Setup proposals approved by the owner (card #675), slice 2: only the signed-in owner applies a
proposal, bound to its exact sha256, as the owner through the existing services; reject and a
stale digest change nothing; apply grants no scope, consent, account link or credential; the
proposer and the owner read back every created id."""

import json

from commonplace.boards.store import BoardStore
from tests._projects_helpers import projects_hub_fixture  # noqa: F401

ROUTE = "/api/setup-proposals"
GROK = {"provider": "grok", "model": "grok-4.7"}
OPUS = {"provider": "claude", "model": "claude-opus-5-5"}
SETUP = {
    "bots": [
        {"key": "lead", "name": "Work lead", "instructions": "Plan the week's cards."},
        {"key": "check", "name": "Checker"},
    ],
    "coworkers": [{"key": "work-lead", "board": "work", "bot": "lead", "title": "Work"}],
    "models": {
        "workspace": {"main": GROK, "worker": GROK},
        "projects": {"work": {"reviewer": OPUS}},
    },
}


def _propose(h, body=None):
    said = h.mcp(h.a1, "setup_propose", **(SETUP if body is None else body))[1]
    assert said["ok"], said
    return json.loads(said["text"])


def _decide(h, auth, number, action, sha):
    return h.request(auth, "POST", f"{ROUTE}/{number}/{action}", {"sha256": sha})[:2]


def _coworkers(h):
    status, body = h.request(h.owner, "GET", "/api/coworkers")[:2]
    assert status == 200, body
    return body


def _model_rows(h):
    with h.db._lock:
        return [
            tuple(r)
            for r in h.db._conn.execute(
                "SELECT scope, role, provider, model FROM chat_model_settings "
                "WHERE workspace_id=? ORDER BY scope, role",
                (h.workspace,),
            )
        ]


def _grants(h):
    """Everything that would widen what a connection or account may do."""
    with h.db._lock:
        conn = h.db._conn
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        snapshot = {
            "connections": [
                tuple(r)
                for r in conn.execute(
                    "SELECT id, policy, revoked_at FROM connections ORDER BY id"
                ).fetchall()
            ]
        }
        for table in ("oauth_person_grants", "model_connections", "chat_bridge_links"):
            if table in tables:
                snapshot[table] = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    return snapshot


def _audit(h, action):
    return [row for row in h.db.audit_entries(limit=500) if row["action"] == action]


def test_the_owner_applies_the_exact_version_and_both_sides_read_back_every_created_id(
    projects_hub,
):
    h = projects_hub
    view = _propose(h)
    before = _grants(h)
    status, applied = _decide(h, h.owner, view["id"], "apply", view["sha256"])
    assert status == 200, applied
    assert applied["status"] == "applied" and applied["decided_by"].startswith("user:")
    results = applied["results"]
    bots = [item for item in results if item["kind"] == "bot"]
    assert [(b["key"], b["ok"], b["name"]) for b in bots] == [
        ("lead", True, "Work lead"),
        ("check", True, "Checker"),
    ]
    assert all(b["id"].startswith("bot_") for b in bots)
    (coworker,) = [item for item in results if item["kind"] == "coworker"]
    assert coworker["ok"] and coworker["id"].startswith("conv_")
    assert (coworker["board"], coworker["bot_id"], coworker["title"]) == (
        "work",
        bots[0]["id"],
        "Work",
    )
    workspace, project = [item for item in results if item["kind"] == "models"]
    assert workspace["ok"] and workspace["board"] is None
    assert workspace["roles"]["main"] == {**GROK, "source": "workspace"}
    assert project["ok"] and project["board"] == "work"
    assert project["roles"] == {"reviewer": {**OPUS, "source": "project"}}

    # The owner reads every item back through the existing routes.
    listed = _coworkers(h)
    by_id = {bot["id"]: bot for bot in listed["bots"]}
    assert set(by_id) == {b["id"] for b in bots}
    assert by_id[bots[0]["id"]]["instructions"] == "Plan the week's cards."
    (conversation,) = [c for c in listed["conversations"] if c["id"] == coworker["id"]]
    assert conversation["board"] == "work" and conversation["bot_ids"] == [bots[0]["id"]]
    settings = h.request(h.owner, "GET", "/api/settings/models")[1]
    assert settings["models"]["main"] == GROK and settings["models"]["worker"] == GROK
    work = h.project(h.owner, "work")[1]
    assert work["models"]["reviewer"] == OPUS and OPUS in work["model_catalog"]
    # The proposer reads the same results and the model per role through model_mode.
    said = json.loads(h.mcp(h.a1, "setup_show", id=view["id"])[1]["text"])
    assert said["status"] == "applied" and said["results"] == results
    roles = json.loads(h.mcp(h.a1, "model_mode", board="work")[1]["text"])["project"]["roles"]
    assert (roles["reviewer"]["model"], roles["reviewer"]["source"]) == (
        "claude-opus-5-5",
        "project",
    )
    assert (roles["main"]["model"], roles["main"]["source"]) == ("grok-4.7", "workspace")

    # The audit log records who proposed and who approved, with the digest.
    (proposed,) = _audit(h, "setup.propose")
    (approved,) = _audit(h, "setup.apply")
    assert proposed["connection_id"] is not None and approved["connection_id"] is None
    assert applied["decided_by"] == f"user:{approved['user_id']}"
    assert view["sha256"] in json.loads(approved["target_ids"])
    assert coworker["id"] in json.loads(approved["target_ids"])

    # No scope, consent, account link or credential came with it.
    assert _grants(h) == before
    assert all(bot["connection_id"] is None for bot in listed["bots"])


def test_a_stale_digest_or_a_second_tap_applies_nothing(projects_hub):
    h = projects_hub
    first = _propose(h)
    second = _propose(h, {**SETUP, "bots": SETUP["bots"][:1]})
    # The digest of another version, or a malformed one, never applies this one.
    status, refused = _decide(h, h.owner, first["id"], "apply", second["sha256"])
    assert (status, refused["code"]) == (409, "sha256_mismatch")
    status, refused = _decide(h, h.owner, first["id"], "apply", "ABC")
    assert (status, refused["code"]) == (400, "sha256_invalid")
    assert _coworkers(h)["bots"] == [] and _model_rows(h) == []
    assert h.request(h.owner, "GET", f"{ROUTE}/{first['id']}")[1]["status"] == "proposed"
    # Applied once; the second tap is refused and creates nothing twice.
    assert _decide(h, h.owner, first["id"], "apply", first["sha256"])[0] == 200
    status, refused = _decide(h, h.owner, first["id"], "apply", first["sha256"])
    assert (status, refused["code"]) == (409, "not_proposed")
    assert len(_coworkers(h)["bots"]) == 2
    assert len(_audit(h, "setup.apply")) == 1


def test_rejecting_changes_nothing_and_a_rejected_setup_cannot_be_applied(projects_hub):
    h = projects_hub
    view = _propose(h)
    before = (_model_rows(h), _grants(h))
    status, rejected = _decide(h, h.owner, view["id"], "reject", view["sha256"])
    assert status == 200 and rejected["status"] == "rejected" and "results" not in rejected
    assert _coworkers(h)["bots"] == [] and (_model_rows(h), _grants(h)) == before
    status, refused = _decide(h, h.owner, view["id"], "apply", view["sha256"])
    assert (status, refused["code"]) == (409, "not_proposed")
    assert len(_audit(h, "setup.reject")) == 1 and _audit(h, "setup.apply") == []


def test_only_the_signed_in_owner_decides(projects_hub):
    h = projects_hub
    view = _propose(h)
    member_agent = h.hub.token(
        "member-agent",
        user=h.member_user,
        profile="full",
        scopes=["memory:read", "memory:write"],
        read_wings=["work"],
        write_wings=["work"],
        trust="trusted",
    )
    for who in (h.member, member_agent, h.other):
        for action in ("apply", "reject"):
            assert _decide(h, who, view["id"], action, view["sha256"]) == (
                404,
                {"error": "not found"},
            )
    # The owner's own agent reads the proposal but can never decide it.
    for action in ("apply", "reject"):
        status, refused = _decide(h, h.a1, view["id"], action, view["sha256"])
        assert (status, refused["code"]) == (403, "owner_only")
    assert h.request(h.owner, "GET", f"{ROUTE}/{view['id']}")[1]["status"] == "proposed"
    assert _coworkers(h)["bots"] == [] and _model_rows(h) == []
    # A body other than {sha256} is refused before anything happens.
    status, refused = h.request(
        h.owner, "POST", f"{ROUTE}/{view['id']}/apply", {"sha256": view["sha256"], "x": 1}
    )[:2]
    assert (status, refused["code"]) == (400, "invalid")
    assert _decide(h, h.owner, 999, "apply", view["sha256"]) == (404, {"error": "not found"})
    # Malformed JSON is answered 400 by the shared reader and decides nothing.
    status, _, raw = h.request(h.owner, "POST", f"{ROUTE}/{view['id']}/apply", None)
    assert status == 400 and b"Invalid JSON body" in raw
    assert h.request(h.owner, "GET", f"{ROUTE}/{view['id']}")[1]["status"] == "proposed"


def test_an_item_that_fails_is_reported_and_the_rest_still_apply(projects_hub):
    h = projects_hub
    h.create_board(h.owner, "alpha")
    view = _propose(
        h,
        {
            "bots": [{"key": "lead", "name": "Lead"}],
            "coworkers": [
                {"key": "a", "board": "alpha", "bot": "lead"},
                {"key": "w", "board": "work", "bot": "lead"},
            ],
            "models": {"projects": {"alpha": {"main": GROK}, "work": {"main": GROK}}},
        },
    )
    # The alpha project is archived between proposing and the owner's tap.
    with BoardStore(h.hub.gateway.workspace_dir(h.workspace)).connection() as conn:
        conn.execute("UPDATE boards SET archived=1 WHERE slug='alpha'")
        conn.commit()
    status, applied = _decide(h, h.owner, view["id"], "apply", view["sha256"])
    assert status == 200 and applied["status"] == "partial"
    outcome = [(i["kind"], i.get("board"), i["ok"], i.get("error")) for i in applied["results"]]
    assert outcome == [
        ("bot", None, True, None),
        ("coworker", "alpha", False, "not_found"),
        ("coworker", "work", True, None),
        ("models", "alpha", False, "unknown_project"),
        ("models", "work", True, None),
    ]
    assert _model_rows(h) == [("work", "main", "grok", "grok-4.7")]
    (audit,) = _audit(h, "setup.apply")
    assert audit["decision"] == "partial"


def test_the_list_answers_the_newest_twenty_however_many_were_decided(projects_hub):
    h = projects_hub
    views = [_propose(h, {"bots": [{"key": "b", "name": f"Bot {n}"}]}) for n in range(20)]
    for view in views[:2]:
        assert _decide(h, h.owner, view["id"], "reject", view["sha256"])[0] == 200
    newest = _propose(h, {"bots": [{"key": "b", "name": "Bot 20"}]})
    listed = h.request(h.owner, "GET", ROUTE)[1]["proposals"]
    assert [item["id"] for item in listed] == list(range(newest["id"], newest["id"] - 20, -1))
    rejected = h.request(h.owner, "GET", ROUTE + "?status=rejected")[1]["proposals"]
    assert [item["id"] for item in rejected] == [views[1]["id"], views[0]["id"]]
