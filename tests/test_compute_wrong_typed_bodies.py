"""A wrong-typed value in a body of any Compute POST route is a refused request, never a server error.

The control journal turns only protocol refusals into a store error, so the non-workspace routes map the
other two exceptions a wrong-typed value can raise (``TypeError``, ``OverflowError``) to the fixed
``invalid_request`` themselves; the cloud workspace routes keep letting a bug show as one.
"""

import pytest

from tests.test_compute_api import API
from tests.test_compute_control import (  # noqa: F401 -- shared real-store fixture
    compute_env,
    gateway_payload,
    node_payload,
    scope_payload,
)

WRONG = [None, True, 0, -1, 1.5, 10**400, "x", "", [], [1], [[]], {}, {"a": 1}, [None], ["x" * 300]]


@pytest.fixture
def api(compute_env):  # noqa: F811
    return API(compute_env)


def _routes(api):
    """Every non-workspace POST route with a payload the store accepts (state it needs is created first)."""
    gateway, node = api.enrolled()
    status, scope = api.request("POST", "/scopes", scope_payload(api.env, gateway["id"]))
    assert status == 201, scope
    status, execution = api.propose(node)
    assert status == 201, execution
    execution = execution["execution"]
    return {
        "gateways": ("/gateways", gateway_payload(name="Another gateway")),
        "nodes": ("/nodes", node_payload(gateway["id"], name="Another node")),
        "scopes": ("/scopes", scope_payload(api.env, gateway["id"])),
        "executions": (
            "/executions",
            {
                "node_id": node["id"],
                "task_number": api.env.task,
                "operation": "system.info.v1",
                "arguments": {},
                "request_id": "typed",
            },
        ),
        "scripts": (
            "/scripts",
            {
                "node_id": node["id"],
                "task_number": api.env.task,
                "request_id": "typed-script",
                "source": "print(1)",
                "arguments": {},
                "output_paths": [],
            },
        ),
        "gateway credential": (
            f"/gateways/{gateway['id']}/credential",
            {"credential_id": "gateway-reference", "credential_version": 1},
        ),
        "node credential": (
            f"/nodes/{node['id']}/credential",
            {"credential_id": "node-reference", "credential_version": 1},
        ),
        "scope revoke": (f"/scopes/{scope['scope']['id']}/revoke", {}),
        "execution approve": (
            f"/executions/{execution['id']}/approve",
            {"expected_digest": execution["digest"], "expires_at": api.env.clock.now + 60},
        ),
        "execution cancel": (f"/executions/{execution['id']}/cancel", {}),
    }


def test_every_post_route_refuses_a_wrong_typed_value_with_a_client_error(api):
    routes = _routes(api)
    assert len(routes) == 10
    failures = []
    for label, (path, payload) in routes.items():
        for field in payload:
            for value in WRONG:
                try:
                    status, result = api.request("POST", path, {**payload, field: value})
                except (
                    Exception
                ) as exc:  # a server error shows as the exception the handler let through
                    failures.append((label, field, value, type(exc).__name__))
                    continue
                # "scripts_unavailable" is the fixed answer while the machine has no verified runtime.
                if status == 503 and result == {"error": "scripts_unavailable"}:
                    continue
                if not 200 <= status < 500 or (status >= 400 and not result.get("error")):
                    failures.append((label, field, value, status))
        for value in (value for value in WRONG if not isinstance(value, dict)):
            # A whole body of the wrong type is the fixed refusal too.
            status, result = api.request("POST", path, [value])
            if not (status == 400 and result == {"error": "invalid_request"}):
                failures.append((label, "<body>", value, status))
    assert not failures, "\n" + "\n".join(
        sorted(
            {
                repr((a, b, c if not isinstance(c, int) or abs(c) < 99 else "big", d))
                for a, b, c, d in failures
            }
        )
    )


@pytest.mark.parametrize("error", [TypeError("wrong type"), OverflowError("too big")])
def test_the_older_routes_map_a_leaked_type_error_to_the_fixed_refusal_and_workspace_routes_do_not(
    api, monkeypatch, error
):
    from commonplace.compute import control

    def broken(*args, **kwargs):
        raise error

    monkeypatch.setattr(control.Store, "create_gateway", broken)
    monkeypatch.setattr(control.Store, "list_gateways", broken)
    monkeypatch.setattr(control.Store, "create_workspace", broken)
    assert api.request("POST", "/gateways", gateway_payload()) == (
        400,
        {"error": "invalid_request"},
    )
    assert api.request(path="/gateways") == (400, {"error": "invalid_request"})
    with pytest.raises(type(error)):
        api.request(
            "POST",
            "/workspaces",
            {"name": "Box", "image_source": "custom", "image": "ghcr.io/acme/tools:1"},
        )


def test_the_scope_route_refuses_a_missing_task_number(api):
    gateway, _ = api.enrolled()
    payload = scope_payload(api.env, gateway["id"], task_number=None)
    assert api.request("POST", "/scopes", payload) == (400, {"error": "invalid_request"})
