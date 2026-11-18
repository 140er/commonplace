"""A gateway or target can be saved before it has a credential; the server refuses use until one is bound."""

import json

import pytest

from commonplace import sessions
from commonplace.compute.control import StoreError
from tests.test_compute_api import API
from tests.test_compute_control import (  # noqa: F401 -- shared real-store fixture
    compute_env,
    gateway_payload,
    node_payload,
)


@pytest.fixture
def api(compute_env):  # noqa: F811
    return API(compute_env)


def bare(payload):
    return {k: v for k, v in payload.items() if k not in ("credential_id", "credential_version")}


def unbound_pair(api):
    status, result = api.request("POST", "/gateways", bare(gateway_payload()))
    assert status == 201, result
    gateway = result["gateway"]
    status, result = api.request("POST", "/nodes", bare(node_payload(gateway["id"])))
    assert status == 201, result
    return gateway, result["node"]


def bind(api, kind, record, reference, version=1):
    return api.request(
        "POST",
        f"/{kind}/{record['id']}/credential",
        {"credential_id": reference, "credential_version": version},
    )


def test_records_are_created_without_a_credential_and_say_so(api):
    gateway, node = unbound_pair(api)
    for record in (gateway, node):
        assert record["credential_id"] is None and record["credential_version"] is None
        assert record["credential_bound"] is False
        assert record["state"] == "pending_verification"
    assert api.request(path="/gateways") == (200, {"gateways": [gateway]})
    assert api.request(path="/nodes") == (200, {"nodes": [node]})


def test_existing_manual_reference_entry_is_unchanged_and_bound(api):
    status, result = api.request("POST", "/gateways", gateway_payload())
    assert status == 201
    gateway = result["gateway"]
    assert gateway["credential_id"] == "gateway-reference" and gateway["credential_version"] == 1
    assert gateway["credential_bound"] is True


@pytest.mark.parametrize("changes", [{"credential_id": "only-id"}, {"credential_version": 1}])
def test_half_a_reference_is_still_refused(api, changes):
    body = {**bare(gateway_payload()), **changes}
    assert api.request("POST", "/gateways", body) == (400, {"error": "invalid_request"})
    assert api.request(path="/gateways") == (200, {"gateways": []})


def test_unbound_records_are_not_usable_and_the_refusal_names_the_reason(api):
    gateway, node = unbound_pair(api)
    refused = (409, {"error": "credential_not_bound"})
    assert api.propose(node) == refused
    assert api.request(path="/executions") == (200, {"executions": []})
    # A bound gateway does not make an unbound target usable.
    assert bind(api, "gateways", gateway, "gateway-reference")[0] == 200
    assert api.propose(node) == refused
    # Nor can a bound target be used through a gateway that has none.
    other_gateway, other_node = unbound_pair(api)
    assert bind(api, "nodes", other_node, "node-reference")[0] == 200
    assert api.propose(other_node) == refused


def test_binding_flips_the_state_and_the_record_becomes_usable(api):
    gateway, node = unbound_pair(api)
    status, result = bind(api, "gateways", gateway, "gateway-reference", 3)
    assert status == 200
    gateway = result["gateway"]
    assert (
        gateway["credential_id"],
        gateway["credential_version"],
        gateway["credential_bound"],
    ) == (
        "gateway-reference",
        3,
        True,
    )
    status, result = bind(api, "nodes", node, "node-reference")
    assert status == 200 and result["node"]["credential_bound"] is True
    assert api.request(path="/gateways") == (200, {"gateways": [gateway]})
    status, result = api.propose(result["node"])
    assert status == 201, result
    assert result["execution"]["state"] == "awaiting_approval"


def test_a_bound_record_cannot_be_rebound_or_swapped(api):
    gateway, _ = unbound_pair(api)
    assert bind(api, "gateways", gateway, "first")[0] == 200
    assert bind(api, "gateways", gateway, "second") == (409, {"error": "credential_already_bound"})
    full = api.request("POST", "/gateways", gateway_payload())[1]["gateway"]
    assert bind(api, "gateways", full, "other") == (409, {"error": "credential_already_bound"})
    assert api.request(path="/gateways")[1]["gateways"][0]["credential_id"] in (
        "first",
        "gateway-reference",
    )


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"credential_id": "ok"},
        {"credential_id": "ok", "credential_version": 0},
        {"credential_id": "bad id", "credential_version": 1},
        {"credential_id": "ok", "credential_version": 1, "state": "verified"},
        {"credential_id": "ok", "credential_version": 1, "private_key": "synthetic-canary"},
    ],
)
def test_bind_validates_the_reference_and_takes_no_other_field(api, body):
    gateway, _ = unbound_pair(api)
    status, result = api.request("POST", f"/gateways/{gateway['id']}/credential", body)
    assert (status, result) == (400, {"error": "invalid_request"})
    assert api.request(path="/gateways")[1]["gateways"][0]["credential_bound"] is False


def test_binding_keeps_the_permission_rules(api):
    gateway, _ = unbound_pair(api)
    member = sessions.start_session(api.env.db, api.env.member, api.env.workspace)
    body = {"credential_id": "ok", "credential_version": 1}
    assert api.request("POST", f"/gateways/{gateway['id']}/credential", body, cookie=member) == (
        403,
        {"error": "owner_required"},
    )
    assert api.request("POST", f"/gateways/{gateway['id']}/credential", body, cookie=False) == (
        401,
        {"error": "sign_in"},
    )
    assert api.request("POST", f"/gateways/{'0' * 32}/credential", body) == (
        404,
        {"error": "not_found"},
    )
    assert api.request("GET", f"/gateways/{gateway['id']}/credential")[0] == 404
    assert api.request(path="/gateways")[1]["gateways"][0]["credential_bound"] is False


def test_the_store_enforces_it_without_the_browser(compute_env):  # noqa: F811
    env = compute_env
    gateway = env.store.create_gateway(env.principal, **env.auth, **bare(gateway_payload()))
    node = env.store.create_node(env.principal, **env.auth, **bare(node_payload(gateway["id"])))
    with pytest.raises(StoreError) as refused:
        env.store.propose_execution(
            env.principal,
            **env.auth,
            node_id=node["id"],
            task_number=env.task,
            operation="system.info.v1",
            arguments={},
            request_id="one",
        )
    assert (refused.value.code, refused.value.status) == ("credential_not_bound", 409)
    assert "gateway-reference" not in json.dumps(gateway)


def test_an_agent_token_is_refused_on_the_bind_route_before_anything_changes(api):
    gateway, node = unbound_pair(api)
    env = api.env
    _, token = env.db.create_token(
        workspace_ref=env.workspace,
        user_ref=env.owner,
        name="owner-agent",
        client="tests",
        policy=sessions.person_policy(),
        actor=env.owner,
    )
    body = {"credential_id": "ok", "credential_version": 1}
    for kind, record in (("gateways", gateway), ("nodes", node)):
        status, result = api.request(
            "POST",
            f"/{kind}/{record['id']}/credential",
            body,
            headers={"Authorization": "Bearer " + token},
        )
        assert (status, result) == (403, {"error": "owner_required"})
    assert api.request(path="/gateways")[1]["gateways"][0]["credential_bound"] is False
    assert api.request(path="/nodes")[1]["nodes"][0]["credential_bound"] is False
