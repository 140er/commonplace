"""The gateway limits facts to the wings and sensitivities a token may read."""

from tests._commonplace_helpers import hub_fixture  # noqa: F401

SCOPED = dict(
    profile="full",
    scopes=["memory:read", "memory:write", "kg:read", "kg:write"],
    read_wings=["shared", "project_*"],
    write_wings=["shared", "project_*"],
    write_wing="shared",
    trust="standard",
)
GITHUB_TOKEN = "ghp_" + "a" * 36


def _add(hub, token, **args):
    result, text = hub.call(token, "mempalace_kg_add", **args)
    assert result is not None, text
    return result


def _seed(hub):
    owner = hub.owner_token()
    _add(hub, owner, subject="api", predicate="uses", object="sqlite", wing="shared")
    _add(hub, owner, subject="api", predicate="uses", object="postgres", wing="vault_x")
    _add(hub, owner, subject="api", predicate="uses", object="health-plan", wing="private")
    _add(hub, owner, subject="api", predicate="uses", object="legacy")  # no wing
    _add(
        hub,
        owner,
        subject="api",
        predicate="uses",
        object="secret-store",
        wing="shared",
        sensitivity="secret",
    )
    _add(hub, owner, subject="Alice Hidden", predicate="works_at", object="Acme", wing="vault_x")
    return owner


def _objects(result):
    return sorted(f["object"] for f in result["facts"])


def test_scoped_reads_see_only_their_wings(hub):
    owner = _seed(hub)
    token = hub.token("scoped", **SCOPED)
    facts, _ = hub.call(token, "mempalace_kg_query", entity="api")
    assert _objects(facts) == ["sqlite"]
    timeline, _ = hub.call(token, "mempalace_kg_timeline", entity="api")
    assert [f["object"] for f in timeline["timeline"]] == ["sqlite"]
    assert timeline["total"] == 1
    everything, _ = hub.call(owner, "mempalace_kg_query", entity="api")
    assert len(everything["facts"]) == 5


def test_hidden_entities_are_not_offered_as_candidates(hub):
    _seed(hub)
    token = hub.token("scoped", **SCOPED)
    result, text = hub.call(token, "mempalace_kg_query", entity="Alice")
    assert result["facts"] == [] and "candidates" not in result
    assert "Hidden" not in text


def test_a_token_cannot_widen_its_own_filter(hub):
    _seed(hub)
    token = hub.token("scoped", **SCOPED)
    denied, _ = hub.call(token, "mempalace_kg_query", entity="api", wings=["vault_x"])
    assert denied["denied"] and "vault_x" in denied["error"]
    narrowed, _ = hub.call(
        token, "mempalace_kg_query", entity="api", sensitivities=["secret", "internal"]
    )
    assert _objects(narrowed) == ["sqlite"]
    bad, _ = hub.call(token, "mempalace_kg_query", entity="api", wings="shared")
    assert bad["denied"]


def test_private_sensitivity_needs_private_read(hub):
    owner = hub.owner_token()
    _add(
        hub,
        owner,
        subject="api",
        predicate="notes",
        object="private-note",
        wing="shared",
        sensitivity="private",
    )
    plain = hub.token("plain", **SCOPED)
    assert hub.call(plain, "mempalace_kg_query", entity="api")[0]["facts"] == []
    with_private = hub.token(
        "with-private", **{**SCOPED, "scopes": SCOPED["scopes"] + ["private:read"]}
    )
    assert _objects(hub.call(with_private, "mempalace_kg_query", entity="api")[0]) == [
        "private-note"
    ]


def test_writes_land_in_a_writable_wing(hub):
    owner = hub.owner_token()
    token = hub.token("scoped", **SCOPED)
    result = _add(hub, token, subject="svc", predicate="uses", object="redis")
    assert result["wing"] == "shared"
    denied, _ = hub.call(
        token, "mempalace_kg_add", subject="svc", predicate="uses", object="x", wing="vault_x"
    )
    assert denied["denied"]
    (fact,) = hub.call(owner, "mempalace_kg_query", entity="svc")[0]["facts"]
    assert fact["wing"] == "shared"
    # Only a token that sees every wing files a fact with no wing.
    assert _add(hub, owner, subject="svc", predicate="runs", object="linux")["wing"] is None


def test_sensitivity_is_raised_from_the_wing_and_the_words(hub):
    owner = hub.owner_token()
    _add(
        hub,
        owner,
        subject="me",
        predicate="has",
        object="a plan",
        wing="private",
        sensitivity="public",
    )
    _add(hub, owner, subject="deploy", predicate="token", object=GITHUB_TOKEN, wing="shared")
    facts = {
        f["object"]: f["sensitivity"]
        for entity in ("me", "deploy")
        for f in hub.call(owner, "mempalace_kg_query", entity=entity)[0]["facts"]
    }
    assert facts == {"a plan": "private", GITHUB_TOKEN: "secret"}
    token = hub.token("scoped", **SCOPED)
    assert hub.call(token, "mempalace_kg_query", entity="deploy")[0]["facts"] == []
    bad, _ = hub.call(
        owner, "mempalace_kg_add", subject="a", predicate="b", object="c", sensitivity="top"
    )
    assert bad["denied"]


def test_changes_stay_in_the_callers_wing(hub):
    owner = hub.owner_token()
    _add(hub, owner, subject="api", predicate="uses", object="sqlite", wing="vault_x")
    token = hub.token("scoped", **SCOPED)
    _add(hub, token, subject="api", predicate="uses", object="sqlite", wing="project_api")
    result, text = hub.call(
        token,
        "mempalace_kg_supersede",
        subject="api",
        predicate="uses",
        old_object="sqlite",
        new_object="postgres",
        wing="project_api",
    )
    assert result and result["success"], text
    hub.call(
        token,
        "mempalace_kg_invalidate",
        subject="api",
        predicate="uses",
        object="sqlite",
        wing="vault_x",
    )
    facts = hub.call(owner, "mempalace_kg_query", entity="api")[0]["facts"]
    by_wing = {(f["wing"], f["object"]): f["current"] for f in facts}
    assert by_wing[("vault_x", "sqlite")] is True
    assert by_wing[("project_api", "sqlite")] is False
    assert by_wing[("project_api", "postgres")] is True


def test_stats_still_need_every_wing(hub):
    token = hub.token("scoped", **SCOPED)
    response = hub.rpc(token, "tools/call", {"name": "mempalace_kg_stats", "arguments": {}})
    assert "error" in response
