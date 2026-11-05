"""The owner can widen an existing agent connection's read wings without a new token."""

from tests._commonplace_helpers import (
    functional_worker_transport,  # noqa: F401
    hub_fixture,  # noqa: F401
)
from tests.test_commonplace_agent_connections import (
    create,
    fresh as fresh_fixture,
    http,
    seed_memory,
    tool,
)

fresh = fresh_fixture


def _grant(hub, fresh, connection_id, wings):
    return http(
        hub,
        "PATCH",
        f"/api/connections/{connection_id}",
        cookie=fresh.cookie,
        body={"read_wings": wings},
    )


def test_agent_scoped_to_one_wing_cannot_read_another_project_wing(hub, fresh):
    hidden = seed_memory(fresh, "GRANT_LATER_CANARY project decisions", "project_b")
    seed_memory(fresh, "GRANT_SCOPE_CANARY project decisions", "project_a")
    result = create(hub, fresh, read_wings=["project_a"])
    principal = hub.db.authenticate_token(result["token"])
    assert not principal.policy.can_read_wing("project_b")
    assert tool(hub, result["token"], "memory_get", id=hidden)[0]


def test_owner_grants_existing_agent_read_access_to_a_project_wing(hub, fresh):
    granted = seed_memory(fresh, "GRANTED_WING_CANARY project decisions", "project_b")
    seed_memory(fresh, "FIRST_WING_CANARY project decisions", "project_a")
    result = create(hub, fresh, read_wings=["project_a"])
    token, connection_id = result["token"], result["connection"]["id"]

    status, _, _ = _grant(hub, fresh, connection_id, ["project_a", "project_b"])

    assert status == 200
    principal = hub.db.authenticate_token(token)
    assert principal is not None and principal.connection_id == connection_id
    assert principal.policy.can_read_wing("project_a")
    assert principal.policy.can_read_wing("project_b")
    assert not principal.policy.can_read_wing("private.notes")
    assert not tool(hub, token, "memory_get", id=granted)[0]


def test_wing_grant_refuses_private_and_unknown_wings(hub, fresh):
    seed_memory(fresh, "REFUSE_CANARY project decisions", "project_a")
    result = create(hub, fresh, read_wings=["project_a"])
    connection_id = result["connection"]["id"]

    for wings in (["private.notes"], ["missing_wing"], ["project_*"], []):
        assert _grant(hub, fresh, connection_id, wings)[0] == 400

    principal = hub.db.authenticate_token(result["token"])
    assert principal.policy.read_wings == ("project_a",)
