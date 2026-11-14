"""Public contract for resource-aware starts at a project limit of 100."""

from commonplace.boards.why import summary_line
from tests.runner_harness import card, http, owners, signup_hub  # noqa: F401
from tests.test_orch_acceptance import BOARD, _configure, _lease, _machine, _person_start


def test_report_is_machine_only_and_caps_a_high_project_limit(signup_hub, owners):  # noqa: F811
    hub, owner = signup_hub, owners["A"]
    machine = _machine(hub, owner)
    task = card(hub, owner, board=BOARD)
    _configure(hub, owner, machine, max_agents=100)
    assert _person_start(hub, owner, machine, task)[0] == 201
    data = {"memory_available_mb": 4096, "memory_floor_mb": 256, "cpu_free": 0.25}
    assert (
        http(hub.port, "POST", "/api/runner/resources", cookie=owner["cookie"], body=data)[0] == 404
    )
    assert (
        http(hub.port, "POST", "/api/runner/resources", token=machine["machine_token"], body=data)[
            0
        ]
        == 200
    )
    assert _lease(hub, machine)[0] == 204
    data["cpu_free"] = 8
    assert (
        http(hub.port, "POST", "/api/runner/resources", token=machine["machine_token"], body=data)[
            0
        ]
        == 200
    )
    status, lease = _lease(hub, machine)
    assert status == 200 and lease["max_agents"] == 100
    assert _lease(hub, machine)[0] == 204  # granted max_in_flight remains one
    # Turn on shared admission to expose the board summary without changing config.
    assert (
        http(
            hub.port,
            "PUT",
            "/api/settings/orchestration",
            cookie=owner["cookie"],
            body={"enabled": True},
        )[0]
        == 200
    )
    status, body = http(hub.port, "GET", "/api/boards", cookie=owner["cookie"])
    assert status == 200
    board = next(b for b in body["boards"] if b["slug"] == BOARD)
    assert board["max_agents"] == 100
    assert board["runner_capacity"] == board["effective_start_limit"] == 1
    assert "limit 100, runner capacity 1" in board["waiting_summary"]


def test_summary_exposes_both_limits():
    assert (
        summary_line({}, used=0, cap=100, runner_capacity=4)
        == "0 agents busy (limit 100, runner capacity 4)."
    )


def test_normal_limit_lease_ignores_resource_shortage(signup_hub, owners):  # noqa: F811
    hub, owner = signup_hub, owners["A"]
    machine = _machine(hub, owner)
    task = card(hub, owner, board=BOARD)
    _configure(hub, owner, machine, max_agents=20)
    assert _person_start(hub, owner, machine, task)[0] == 201
    data = {"memory_available_mb": 0, "memory_floor_mb": 256, "cpu_free": 0}
    assert (
        http(hub.port, "POST", "/api/runner/resources", token=machine["machine_token"], body=data)[
            0
        ]
        == 200
    )
    status, lease = _lease(hub, machine)
    assert status == 200
    assert "max_agents" not in lease
