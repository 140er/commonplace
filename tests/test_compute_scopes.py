"""Discovery policy tests use address strings only; no network is contacted."""

from dataclasses import replace

import pytest

from commonplace.compute.scopes import (
    DiscoveryContext,
    DiscoveryLimits,
    NetworkScope,
    ScopeDenied,
)


@pytest.fixture
def scope():
    return NetworkScope(
        context=DiscoveryContext("workspace", "project", "gateway", "user", "agent", "task"),
        cidrs=("10.77.0.0/29", "fd42:289::/125"),
        interfaces=("eth0",),
        methods=frozenset({"passive"}),
        approved_by="owner",
        expires_at=100,
        limits=DiscoveryLimits(8, 2, 4, 30, 20),
    )


def authorize(scope, **changes):
    args = dict(
        context=scope.context,
        address="10.77.0.3",
        interface="eth0",
        method="passive",
        now=99,
    )
    return scope.authorize(**(args | changes))


def test_exact_approved_ipv4_and_ipv6(scope):
    assert authorize(scope) == "10.77.0.3"
    assert authorize(scope, address="fd42:289::2") == "fd42:289::2"


@pytest.mark.parametrize("field", ("workspace", "project", "gateway", "user", "agent", "task"))
def test_every_context_dimension_is_required(scope, field):
    with pytest.raises(ScopeDenied, match="context"):
        authorize(scope, context=replace(scope.context, **{field: "foreign"}))


@pytest.mark.parametrize(
    "changes",
    (
        {"now": 100},
        {"now": float("nan")},
        {"now": float("inf")},
        {"now": True},
        {"interface": "eth1"},
        {"method": "active"},
        {"address": "10.77.0.8"},
        {"address": "10.77.1.3"},
        {"address": "127.0.0.1"},
        {"address": "169.254.169.254"},
        {"address": "fd42:289::8"},
        {"address": "::ffff:10.77.0.3"},
        {"address": "fd42:289::2%eth0"},
        {"address": "pi.example"},
        {"address": 123},
    ),
)
def test_denied_requests_cannot_expand_scope(scope, changes):
    with pytest.raises(ScopeDenied):
        authorize(scope, **changes)


def test_revocation_denies_even_an_approved_address(scope):
    with pytest.raises(ScopeDenied, match="revoked"):
        authorize(replace(scope, revoked=True))


@pytest.mark.parametrize(
    "cidr",
    (
        "0.0.0.0/0",
        "8.8.8.0/24",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "::/0",
        "fe80::/64",
        "10.77.0.1/29",
    ),
)
def test_unapproved_network_classes_and_noncanonical_scopes(scope, cidr):
    with pytest.raises(ScopeDenied):
        replace(scope, cidrs=(cidr,))


@pytest.mark.parametrize("value", (0, -1, True, 1.5, float("inf"), 1000000))
def test_limits_must_be_finite_positive_bounded_integers(value):
    with pytest.raises(ScopeDenied):
        DiscoveryLimits(value, 1, 1, 1, 1)


def test_scope_copies_collections_before_caller_can_mutate_them(scope):
    cidrs, interfaces, methods = ["10.77.0.0/29"], ["eth0"], {"passive"}
    grant = replace(scope, cidrs=cidrs, interfaces=interfaces, methods=methods)
    cidrs.append("10.77.1.0/24")
    interfaces.append("eth1")
    methods.add("active")
    with pytest.raises(ScopeDenied):
        authorize(grant, address="10.77.1.3")
    with pytest.raises(ScopeDenied):
        authorize(grant, method="active")
    with pytest.raises(ScopeDenied):
        authorize(grant, interface="eth1")


@pytest.mark.parametrize(
    "changes",
    (
        {"methods": ["passive"]},
        {"methods": "passive"},
        {"cidrs": "10.77.0.0/29"},
        {"interfaces": None},
        {"expires_at": 10**500},
    ),
)
def test_malformed_scope_contracts_fail_closed(scope, changes):
    with pytest.raises(ScopeDenied):
        replace(scope, **changes)
