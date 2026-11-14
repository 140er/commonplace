"""Owners see the raw vector-index reason through the gateway; everyone else stays private."""

import json
from types import SimpleNamespace

import pytest

import commonplace.gateway as gateway_module
from tests._commonplace_helpers import functional_worker_transport, hub_fixture  # noqa: F401
from tests.test_card253_s02 import seed, world  # noqa: F401
from tests.test_card253_s13 import FIXED, RAW, assert_private, diverged as _diverged

diverged = _diverged
SEARCH = {"query": "zznonmatchingpresencefixture", "include_facts": False, "wing": "wing_fixture"}


def _scoped_owner_token(w, name, **overrides):
    spec = {
        "user": w.h.db.authenticate_token(w.tokens["A"]).user_id,
        "tools": ["mempalace_search"],
        "scopes": ["memory:read", "private:read"],
        "read_wings": ["*"],
        "trust": "trusted",
    }
    spec.update(overrides)
    return w.h.token(name, **spec)


def test_card398_owner_sees_raw_reason(diverged):
    w = diverged
    assert w.owner.role == "owner"
    reply, _ = w.h.call(w.owner_token, "mempalace_search", **SEARCH)
    assert reply["vector_disabled"] is True
    assert reply["vector_disabled_reason"] == RAW


def test_card398_member_stays_private(diverged):
    w = diverged
    assert w.principal.role == "member"
    reply, _ = w.h.call(w.token, "mempalace_search", **SEARCH)
    assert reply["vector_disabled"] is True
    assert_private(reply)
    assert reply["vector_disabled_reason"] == FIXED


@pytest.mark.parametrize(
    "overrides",
    [
        {"read_wings": ["wing_fixture"]},
        {"scopes": ["memory:read"]},
    ],
    ids=["one-wing-token", "no-private-scope-token"],
)
def test_card398_scoped_owner_token_stays_private(diverged, overrides):
    w = diverged
    token = _scoped_owner_token(w, "scoped-owner", **overrides)
    principal = w.h.db.authenticate_token(token)
    assert principal.role == "owner" and not principal.policy.sees_all_wings
    reply, _ = w.h.call(token, "mempalace_search", **SEARCH)
    assert reply["vector_disabled"] is True
    assert_private(reply)
    assert reply["vector_disabled_reason"] == FIXED


def test_card398_mutation_member_would_leak(diverged, monkeypatch):
    # Reverting the owner check to "everyone" must surface the leak these tests catch.
    w = diverged
    monkeypatch.setattr(gateway_module, "sees_raw_vector_reason", lambda principal: True)
    reply, _ = w.h.call(w.token, "mempalace_search", **SEARCH)
    assert reply["vector_disabled_reason"] == RAW
    assert RAW in json.dumps(reply)


def test_card398_owner_check_and_normalizer_units():
    from commonplace.vector_diagnostics import (
        VECTOR_DISABLED_REASON,
        public_vector_diagnostics,
        sees_raw_vector_reason,
    )

    wide = SimpleNamespace(sees_all_wings=True)
    narrow = SimpleNamespace(sees_all_wings=False)
    assert sees_raw_vector_reason(SimpleNamespace(role="owner", policy=wide))
    assert not sees_raw_vector_reason(SimpleNamespace(role="member", policy=wide))
    assert not sees_raw_vector_reason(SimpleNamespace(role="owner", policy=narrow))
    assert not sees_raw_vector_reason(SimpleNamespace(role="owner"))
    assert not sees_raw_vector_reason(None)
    disabled = {"vector_disabled": True, "vector_disabled_reason": RAW}
    assert public_vector_diagnostics(disabled, True) is disabled
    assert public_vector_diagnostics(disabled)["vector_disabled_reason"] == VECTOR_DISABLED_REASON
    bare = {"vector_disabled": True}
    assert public_vector_diagnostics(bare, True)["vector_disabled_reason"] == VECTOR_DISABLED_REASON
    orphan = {"vector_disabled": False, "vector_disabled_reason": RAW}
    assert "vector_disabled_reason" not in public_vector_diagnostics(orphan, True)
