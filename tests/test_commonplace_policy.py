"""Token policy: every path fails closed."""

import pytest

from commonplace import policy as P
from commonplace.policy import DENY_ALL, GATEWAY_NEVER, TOOL_RULES, Policy, PolicyError
from mempalace.tool_capabilities import TOOL_CAPABILITIES


def _p(**kw):
    kw.setdefault("trust", "standard")
    return Policy.build(**kw)


def test_every_classified_tool_has_a_rule():
    P.check_registry()
    assert set(TOOL_RULES) | GATEWAY_NEVER == set(TOOL_CAPABILITIES)


def test_unclassified_tool_is_refused_and_hidden(monkeypatch):
    owner = _p(
        profile="full", scopes=list(P.SCOPES), read_wings=["*"], write_wings=["*"], trust="trusted"
    )
    assert owner.refusal("mempalace_brand_new") == "unknown tool"
    # Classified upstream but no gateway rule yet: still refused.
    monkeypatch.setitem(TOOL_CAPABILITIES, "mempalace_brand_new", ("read", ""))
    assert owner.refusal("mempalace_brand_new") == "unknown tool"
    assert "mempalace_brand_new" not in owner.visible_tools()
    with pytest.raises(RuntimeError):
        P.check_registry()


@pytest.mark.parametrize("name", sorted(GATEWAY_NEVER))
def test_never_tools_are_refused_even_for_the_owner(name):
    owner = _p(
        profile="full", scopes=list(P.SCOPES), read_wings=["*"], write_wings=["*"], trust="trusted"
    )
    assert not owner.tool_visible(name)
    with pytest.raises(PolicyError):
        Policy.build(tools=[name], scopes=["memory:read"])


def test_profiles():
    assert len(P.PROFILES["slim"]) == 6
    assert P.PROFILES["slim"] < P.PROFILES["standard"] < P.PROFILES["full"]
    assert not P.PROFILES["full"] & GATEWAY_NEVER


def test_listed_equals_callable_for_every_tool_and_token():
    tokens = [
        DENY_ALL,
        _p(profile="slim", scopes=["memory:read"], read_wings=["shared"]),
        _p(
            profile="full",
            scopes=list(P.SCOPES),
            read_wings=["*"],
            write_wings=["*"],
            trust="trusted",
        ),
        _p(profile="standard", scopes=["memory:read", "kg:read"], read_wings=["*"]),
    ]
    for pol in tokens:
        for name in TOOL_CAPABILITIES:
            assert pol.tool_visible(name) == (pol.refusal(name) is None)
            assert (name in pol.visible_tools()) == pol.tool_visible(name)


def test_scope_needed_per_tool():
    pol = _p(profile="full", scopes=["memory:read"], read_wings=["shared"])
    assert pol.tool_visible("mempalace_search")
    assert pol.refusal("mempalace_add_drawer") == "needs scope memory:write"
    assert pol.refusal("mempalace_kg_query") == "needs scope kg:read"


def test_trust_caps_scopes():
    scopes = ["memory:read", "memory:write", "memory:delete", "private:write", "admin"]
    untrusted = Policy.build(
        profile="full", scopes=scopes, read_wings=["*"], write_wing="inbox", trust="untrusted"
    )
    standard = _p(profile="full", scopes=scopes, read_wings=["*"])
    trusted = _p(profile="full", scopes=scopes, read_wings=["*"], trust="trusted")
    assert not untrusted.has_scope("memory:delete") and not untrusted.has_scope("private:write")
    assert not standard.has_scope("memory:delete") and standard.has_scope("private:write")
    assert trusted.has_scope("memory:delete")
    assert not untrusted.tool_visible("mempalace_update_drawer")


def test_untrusted_writer_needs_a_write_wing_and_only_writes_there():
    with pytest.raises(PolicyError):
        Policy.build(profile="slim", scopes=["memory:write"], trust="untrusted")
    pol = Policy.build(
        profile="slim",
        scopes=["memory:write"],
        write_wings=["*"],
        write_wing="inbox",
        trust="untrusted",
    )
    assert pol.can_write_wing("inbox") and pol.can_write_wing("wing_inbox")
    assert not pol.can_write_wing("shared")


def test_wing_patterns_and_spellings():
    pol = _p(
        scopes=["memory:read", "memory:write"],
        read_wings=["shared", "project_*"],
        write_wings=["project_*"],
    )
    for wing in (
        "shared",
        "wing_shared",
        "Shared",
        "project_api",
        "project-api",
        "wing_project_api",
    ):
        assert pol.can_read_wing(wing), wing
    for wing in ("", None, "vault", "sharedx", "private", "projectx"):
        assert not pol.can_read_wing(wing), wing
    assert pol.can_write_wing("project_api") and not pol.can_write_wing("shared")


def test_private_wings_need_private_scopes():
    pol = _p(scopes=["memory:read", "memory:write"], read_wings=["*"], write_wings=["*"])
    for wing in ("private", "wing_private", "private_health", "Private-Health"):
        assert not pol.can_read_wing(wing) and not pol.can_write_wing(wing)
    assert not pol.sees_all_wings
    full = _p(
        scopes=["memory:read", "memory:write", "private:read", "private:write"],
        read_wings=["*"],
        write_wings=["*"],
    )
    assert full.can_read_wing("private") and full.sees_all_wings and full.writes_all_wings


def test_cross_wing_tools_need_every_wing():
    some = _p(profile="full", scopes=["memory:read"], read_wings=["shared"])
    assert some.refusal("mempalace_status") == "needs read access to every wing"
    reader = _p(
        profile="full",
        scopes=["memory:read", "memory:write", "private:read"],
        read_wings=["*"],
        write_wings=["shared"],
    )
    assert reader.tool_visible("mempalace_status")
    assert reader.refusal("mempalace_checkpoint") == "needs write access to every wing"


def test_bad_policies_are_refused_at_build():
    for kwargs in (
        dict(profile="huge"),
        dict(scopes=["memory:everything"]),
        dict(trust="root"),
        dict(tools=["mempalace_nope"]),
        dict(scopes=["memory:write"], write_wing="project_*", trust="standard"),
    ):
        with pytest.raises(PolicyError):
            Policy.build(**kwargs)


def test_round_trip_and_corrupt_policy_denies_all():
    pol = _p(tools=["mempalace_search"], scopes=["memory:read"], read_wings=["shared"])
    assert Policy.from_json(pol.to_json()) == pol
    assert (
        Policy.from_json(_p(profile="standard", scopes=["memory:read"]).to_json()).tools
        == "standard"
    )
    for raw in ("", "{", "[]", '{"scopes": ["nope"]}', '{"trust": "god"}'):
        assert Policy.from_json(raw) is DENY_ALL
    assert DENY_ALL.visible_tools() == []
