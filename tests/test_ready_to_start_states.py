"""Prerequisites remain independent and expose states rather than credentials."""

from types import SimpleNamespace

import pytest

from commonplace.projects import readiness
from tests._bots_helpers import bots_hub_fixture  # noqa: F401
from tests._projects_helpers import projects_hub_fixture  # noqa: F401


@pytest.fixture
def state_case(monkeypatch):
    config = SimpleNamespace(
        enabled=True,
        provider="openai_compatible",
        model="local-model",
        applies=True,
        key_ref=None,
        base_url="http://localhost:11434",
    )
    machine = {
        "enabled": True,
        "revoked_at": None,
        "machine_connection_id": "machine",
        "binding_state": "owner_attested",
        "requesters": '["agent"]',
    }
    gateway = SimpleNamespace(db=object())
    principal = SimpleNamespace(workspace_id="fixture")
    main = {"connected": True, "seen": True}
    machines = [machine]
    monkeypatch.setattr(readiness.settings, "front", lambda *a: config)
    monkeypatch.setattr(readiness.registry, "machines", lambda *a: machines)
    monkeypatch.setattr(
        readiness.seats, "active_connection", lambda *a: {"policy": '{"trust":"trusted"}'}
    )
    monkeypatch.setattr(
        readiness.seats, "ensure_main", lambda *a: SimpleNamespace(connection_id="agent")
    )
    monkeypatch.setattr(readiness.orchestration, "enabled", lambda *a: True)
    monkeypatch.setattr(readiness.settings, "key_allowed", lambda *a: True)
    monkeypatch.setattr(
        readiness.settings, "resolve_key", lambda *a: "synthetic-key-never-exported"
    )
    return SimpleNamespace(
        config=config,
        machine=machine,
        machines=machines,
        main=main,
        read=lambda board="product": readiness.snapshot(gateway, principal, board, main=main),
    )


def test_independent_prerequisites_and_local_key_not_needed(state_case):
    case = state_case
    assert {k: v["state"] for k, v in case.read().items()} == {
        "model": "done",
        "key": "not_needed",
        "machine": "done",
        "connection": "done",
        "trust": "done",
        "notifications": "not_needed",
    }
    case.config.provider = "none"
    result = case.read()
    assert result["model"]["state"] == result["key"]["state"] == "needs_you"
    assert result["machine"]["state"] == "done"
    case.config.provider = "anthropic"
    case.config.base_url = None
    case.config.key_ref = "env:COMMONPLACE_CHAT_KEY_FIXTURE"
    case.config.applies = False
    assert case.read()["model"] == {"state": "needs_you", "code": "needs_turn_on"}


@pytest.mark.parametrize(
    "allowed,available,code",
    [(False, True, "key_not_allowed"), (True, False, "key_missing"), (True, True, "key_missing")],
)
def test_hosted_key_present_and_allowed_are_both_required(
    state_case, monkeypatch, allowed, available, code
):
    case = state_case
    case.config.provider = "anthropic"
    case.config.base_url = None
    case.config.key_ref = "env:COMMONPLACE_CHAT_KEY_FIXTURE"
    monkeypatch.setattr(readiness.settings, "key_allowed", lambda *a: allowed)
    monkeypatch.setattr(
        readiness.settings, "resolve_key", lambda *a: "synthetic-key" if available else None
    )
    result = case.read()
    assert result["key"] == {
        "state": "done" if allowed and available else "needs_you",
        "code": code,
    }
    assert "synthetic-key" not in str(result) and "key_ref" not in str(result)


def test_missing_revoked_disabled_machine_and_untrusted_binding(state_case, monkeypatch):
    case = state_case
    for field, value in (("enabled", False), ("revoked_at", "revoked")):
        old = case.machine[field]
        case.machine[field] = value
        assert case.read()["machine"]["state"] == "needs_you"
        case.machine[field] = old
    case.machine["binding_state"] = "unbound"
    assert case.read()["trust"] == {"state": "needs_you", "code": "machine_untrusted"}
    case.machine["binding_state"] = "owner_attested"
    case.machine["requesters"] = "[]"
    assert case.read()["trust"] == {"state": "needs_you", "code": "trust_missing"}
    monkeypatch.setattr(readiness.seats, "active_connection", lambda *a: None)
    assert case.read()["machine"]["state"] == "needs_you"
    case.machines.clear()
    assert case.read()["machine"]["state"] == "needs_you"


def test_unused_connection_is_not_connected_and_personal_chat_has_no_machine_requirement(
    state_case,
):
    case = state_case
    case.main["seen"] = False
    assert case.read()["connection"] == {"state": "needs_you", "code": "connection_unused"}
    case.main["connected"] = False
    assert case.read()["connection"]["code"] == "connection_missing"
    personal = case.read(None)
    assert all(personal[k]["state"] == "not_needed" for k in ("machine", "connection", "trust"))


def test_real_project_read_exposes_same_secret_free_states_to_member(projects_hub):
    h = projects_hub
    owner_code, owner = h.project(h.owner, "work")
    member_code, member = h.project(h.member, "work")
    assert owner_code == member_code == 200
    assert owner["readiness"] == member["readiness"]
    assert owner["can_configure"] and not member["can_configure"]
    assert owner["readiness"]["model"]["state"] == "needs_you"
    assert owner["readiness"]["machine"]["state"] == "needs_you"
    assert set(owner["readiness"]) == {
        "model",
        "key",
        "machine",
        "connection",
        "trust",
        "notifications",
    }
    assert "key_ref" not in str(owner["readiness"]) and "token" not in str(owner["readiness"])


def test_real_bot_listing_carries_personal_and_project_states(bots_hub):
    h = bots_hub
    for auth in (h.owner, h.member):
        body = h.bots(auth)
        assert body["readiness"]["machine"]["state"] == "not_needed"
        for bot in body["bots"]:
            assert bot["readiness"]["machine"]["state"] == (
                "needs_you" if bot["board"] else "not_needed"
            )
