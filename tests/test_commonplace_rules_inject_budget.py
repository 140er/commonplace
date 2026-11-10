"""When the injection budget runs out the agent gets a fixed pointer, never silence or rule data."""

import pytest

from commonplace.gateway import _startup_pointer
from commonplace.rules import checks, service
from commonplace.rules.service import instructions_for
from tests.test_commonplace_rules_inject import (
    POINTER,
    assert_pointer_only,
    initialize,
    injected,
    local as local,
    quoted_rules,
    rules_surface_only as rules_surface_only,
)

SECRETS = ("alpha-private-wording", "beta-private-wording", "gamma-private-wording")


def burning(monkeypatch, step=0.05):
    """A starved host: every clock read costs more than the whole budget."""
    now = [0.0]

    def clock():
        now[0] += step
        return now[0]

    monkeypatch.setattr(checks, "monotonic", clock)


def jumping(monkeypatch):
    """The budget is already gone after the first read."""
    reads = []

    def clock():
        reads.append(1)
        return 0.0 if len(reads) == 1 else 60.0

    monkeypatch.setattr(checks, "monotonic", clock)


CLOCKS = pytest.mark.parametrize("starve", [burning, jumping])


@pytest.fixture
def secrets(local):
    for text in SECRETS:
        local.owner.create("*", text, "limit")


@CLOCKS
def test_exhausted_budget_gives_the_pointer_and_no_rule_content(
    local, secrets, monkeypatch, starve
):
    starve(monkeypatch)
    response = initialize(local)
    assert_pointer_only(response)
    text = response["result"]["instructions"]
    assert not any(secret in text for secret in SECRETS)
    assert "3" not in text and "rules_seq" not in text


@CLOCKS
def test_exhausted_budget_reaches_instructions_for(local, secrets, monkeypatch, starve):
    starve(monkeypatch)
    assert instructions_for(local.rules.ctx) == POINTER


def test_pointer_is_built_without_reading_any_store(local, secrets, monkeypatch, caplog):
    def expired(*_a, **_k):
        raise TimeoutError("rules deadline expired")

    monkeypatch.setattr(service, "effective_rules", expired)
    monkeypatch.setattr(checks, "connection", lambda *a, **k: pytest.fail("store opened"))
    monkeypatch.setattr(local.gateway, "workspace_dir", lambda _: pytest.fail("workspace opened"))
    assert_pointer_only(initialize(local))
    assert "rules instruction injection failed (TimeoutError)" in caplog.text


def test_gateway_fallback_when_the_deadline_is_gone_before_injection_starts(
    local, secrets, monkeypatch, caplog
):
    jumping(monkeypatch)
    assert_pointer_only(initialize(local))
    assert "startup context unavailable" in caplog.text


def test_pointer_joins_the_real_startup_pointer(local, secrets, monkeypatch):
    monkeypatch.setattr("commonplace.gateway._startup_pointer", _startup_pointer)
    burning(monkeypatch)
    principal = local.rules.principal
    assert injected(local) == POINTER + "\n\n" + _startup_pointer(principal)


def test_pointer_is_skipped_when_it_does_not_fit(local, secrets, monkeypatch):
    burning(monkeypatch)
    assert instructions_for(local.rules.ctx, budget=len(POINTER) - 1) == ""


@pytest.mark.parametrize("policy", [dict(tools=["memory_search"]), dict(scopes=["memory:write"])])
def test_no_pointer_without_rules_read_tool_or_for_a_person(local, monkeypatch, policy):
    worker = local.agent("restricted", **policy)
    burning(monkeypatch)
    monkeypatch.setattr(local.gateway, "workspace_dir", lambda _: pytest.fail("workspace opened"))
    assert "rules_get" not in injected(local, worker)
    assert instructions_for(local.owner.ctx) == ""


def test_kill_switch_stays_silent_when_the_budget_is_gone(local, secrets, monkeypatch):
    monkeypatch.setenv("COMMONPLACE_RULES_INJECT", "off")
    checks.configure(local.gateway)
    burning(monkeypatch)
    assert injected(local) == ""
    local.gateway.rules_inject_off = False


def test_unrelated_failure_still_stays_silent(local, secrets, monkeypatch):
    def broken(*_a, **_k):
        raise RuntimeError("unrelated")

    monkeypatch.setattr(service, "effective_rules", broken)
    assert injected(local) == ""


def test_normal_path_is_unchanged_when_injection_succeeds(local, secrets, monkeypatch):
    monkeypatch.setattr(checks, "monotonic", lambda: 0.0)
    text = injected(local)
    assert POINTER not in text
    assert sorted(quoted_rules(text)) == sorted(SECRETS)
