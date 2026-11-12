"""Cost events and read-only sums: what runs cost per workspace, agent, project, board and card."""

import pytest

from commonplace.runner import costs, ledger
from mempalace.sqlite_crypto import connect, row_factory
from tests.runner_harness import (
    attest,
    card,
    cli_token,
    enroll,
    http,
    owners,  # noqa: F401
    signup_hub,  # noqa: F401
)


def _wing(hub, owner, wing):
    status, body = http(
        hub.port,
        "POST",
        "/api/memory",
        cookie=owner["cookie"],
        body={"text": "Synthetic workspace example.", "theme": wing, "topic": "demo"},
    )
    assert status == 201, (status, body)


def _machine(hub, owner, label="example-machine", wings=("shared",), projects=("demo",)):
    for wing in wings:
        _wing(hub, owner, wing)
    machine = enroll(hub, owner, label=label, wings=list(wings), projects=list(projects))
    attest(hub, owner, machine)
    return machine


def _call(hub, machine, action, body=None):
    return http(
        hub.port,
        "POST",
        "/api/runner/" + action,
        token=machine["machine_token"],
        body={} if body is None else body,
    )


def _started(hub, owner, machine, *, board="shared", project="demo"):
    """Start one card on ``board`` the way a machine does; return its task and job id."""
    task = card(hub, owner, board=board)["number"]
    status, body = http(
        hub.port,
        "POST",
        "/api/card-starts",
        cookie=owner["cookie"],
        body=dict(task=task, machine=machine["machine_id"], project=project, topic="worker"),
    )
    assert status == 201, (status, body)
    status, lease = _call(hub, machine, "lease")
    assert status == 200 and lease["job_id"] == body["job_id"], (status, lease)
    token = {"lease_token": lease["lease_token"]}
    assert _call(hub, machine, "starting", token)[0] == 200
    status, done = _call(
        hub, machine, "result", {**token, "outcome": "started", "thread_id": "thread-" + str(task)}
    )
    assert status == 200, (status, done)
    return task, body["job_id"]


def _usage(hub, machine, job_id, **fields):
    return _call(hub, machine, "usage", {"job_id": job_id, **fields})


def _sums(hub, path="/api/costs", **auth):
    status, body = http(hub.port, "GET", path, **auth)
    assert status == 200, (status, body)
    return body


def _db(hub, owner):
    conn = connect(
        str(hub.gateway.workspace_dir(owner["workspace"])) + "/runner.sqlite3",
        isolation_level=None,
    )
    conn.row_factory = row_factory
    return conn


def _events(hub, owner, job_id):
    conn = _db(hub, owner)
    try:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM cost_events WHERE job_id=? ORDER BY id", (job_id,)
            )
        ]
    finally:
        conn.close()


def _entry(rows, **key):
    matches = [row for row in rows if all(row.get(k) == v for k, v in key.items())]
    assert len(matches) == 1, (key, rows)
    return matches[0]


def test_reports_write_events_and_sums_read_back_per_scope(signup_hub, owners):  # noqa: F811
    hub, owner = signup_hub, owners["A"]
    machine = _machine(hub, owner, projects=("demo", "docs"))
    task_a, job_a = _started(hub, owner, machine)
    task_b, job_b = _started(hub, owner, machine, project="docs")
    task_c, job_c = _started(hub, owner, machine)
    for job, tokens, cost in ((job_a, (1000, 200), 400000), (job_b, (50, 5), 75000)):
        status, body = _usage(
            hub,
            machine,
            job,
            input_tokens=tokens[0],
            output_tokens=tokens[1],
            cost_micros=cost,
            currency="USD",
        )
        assert status == 200, (status, body)
    # A report writes one event, mirroring the run's totals so far.
    [event] = _events(hub, owner, job_a)
    assert event["source"] == "machine_report" and event["recorded_at"]
    assert (event["input_tokens"], event["output_tokens"]) == (1000, 200)
    assert (event["cost_micros"], event["currency"], event["cost_unknown"]) == (400000, "USD", 0)
    # A correction appends; the latest event is the run's cost, the first stays as history.
    assert (
        _usage(hub, machine, job_a, input_tokens=1500, cost_micros=450000, currency="USD")[0] == 200
    )
    assert [e["cost_micros"] for e in _events(hub, owner, job_a)] == [400000, 450000]

    sums = _sums(hub, cookie=owner["cookie"])
    assert sums["scope"] == "workspace"
    assert sums["window"]["custom"] is False and sums["window"]["window"] == "calendar_month"
    assert sums["window"]["start"].endswith("-01T00:00:00Z")
    total = sums["workspace"]
    assert total["runs"] == 3 and total["cost_known_runs"] == 2 and total["unknown_cost_runs"] == 1
    assert total["cost_micros"] == {"USD": 450000 + 75000}
    assert (total["input_tokens"], total["output_tokens"]) == (1500 + 50, 200 + 5)
    # The run nothing was reported for is counted as unknown, not as zero, and listed apart.
    assert total["token_unreported_runs"] == 1
    assert sums["unknown_cost"]["runs"] == 1
    [unknown] = sums["unknown_cost"]["listed"]
    assert unknown["job"] == job_c and unknown["task"] == task_c
    assert unknown["declared_unknown"] is False

    agent = machine["worker_connection_id"]
    [by_agent] = sums["by_agent"]
    assert by_agent["agent"] == agent and by_agent["runs"] == 3
    assert by_agent["cost_micros"] == {"USD": 525000}
    demo = _entry(sums["by_project"], project="demo")
    docs = _entry(sums["by_project"], project="docs")
    assert (demo["runs"], demo["cost_micros"]) == (2, {"USD": 450000})
    assert (docs["runs"], docs["cost_micros"]) == (1, {"USD": 75000})
    assert [b["board"] for b in sums["by_board"]] == ["shared"]
    assert _entry(sums["by_card"], task=task_a)["cost_micros"] == {"USD": 450000}
    assert _entry(sums["by_card"], task=task_b)["input_tokens"] == 50
    assert _entry(sums["by_card"], task=task_c)["unknown_cost_runs"] == 1


def test_unknown_is_explicit_and_never_zero(signup_hub, owners):  # noqa: F811
    hub, owner = signup_hub, owners["A"]
    machine = _machine(hub, owner)
    _, declared = _started(hub, owner, machine)
    _, ended = _started(hub, owner, machine)
    _, priced = _started(hub, owner, machine)
    # The machine says it cannot tell; later tokens do not turn that into a silent gap.
    assert _usage(hub, machine, declared, cost_unknown=True)[0] == 200
    assert _usage(hub, machine, declared, input_tokens=9)[0] == 200
    events = _events(hub, owner, declared)
    assert [(e["cost_unknown"], e["cost_micros"]) for e in events] == [(1, None), (1, None)]
    assert events[1]["input_tokens"] == 9
    # A run that ends without any cost is recorded as unknown at its end.
    assert _usage(hub, machine, ended, status="completed")[0] == 200
    [end_event] = _events(hub, owner, ended)
    assert (end_event["source"], end_event["cost_unknown"]) == ("run_end", 1)
    # A known cost replaces the unknown; unknown cannot overwrite a known cost.
    assert _usage(hub, machine, priced, cost_micros=0, currency="EUR")[0] == 200
    status, body = _usage(hub, machine, priced, cost_unknown=True)
    assert (status, body) == (409, {"error": "cost already reported"})
    assert _usage(hub, machine, declared, cost_micros=10, currency="USD")[0] == 200
    assert _events(hub, owner, declared)[-1]["cost_unknown"] == 0

    sums = _sums(hub, cookie=owner["cookie"])
    # Zero is a real cost; the two unknown runs are not in the money sums.
    assert sums["workspace"]["cost_micros"] == {"EUR": 0, "USD": 10}
    assert sums["workspace"]["unknown_cost_runs"] == 1
    [row] = sums["unknown_cost"]["listed"]
    assert row["job"] == ended and row["declared_unknown"] is True


@pytest.mark.parametrize(
    "fields",
    [
        {"cost_unknown": False},
        {"cost_unknown": 1},
        {"cost_unknown": "yes"},
        {"cost_unknown": True, "cost_micros": 5, "currency": "USD"},
    ],
)
def test_invalid_unknown_reports_are_refused(signup_hub, owners, fields):  # noqa: F811
    hub, owner = signup_hub, owners["A"]
    machine = _machine(hub, owner)
    _, job = _started(hub, owner, machine)
    assert _usage(hub, machine, job, **fields)[0] == 400
    assert _events(hub, owner, job) == []


def test_currencies_are_never_mixed(signup_hub, owners):  # noqa: F811
    hub, owner = signup_hub, owners["A"]
    machine = _machine(hub, owner)
    for cost, currency in ((100, "USD"), (200, "EUR"), (300, "USD")):
        _, job = _started(hub, owner, machine)
        assert _usage(hub, machine, job, cost_micros=cost, currency=currency)[0] == 200
    assert _sums(hub, cookie=owner["cookie"])["workspace"]["cost_micros"] == {
        "EUR": 200,
        "USD": 400,
    }


def test_a_member_reads_only_what_they_can_read_and_workspaces_do_not_leak(
    signup_hub,  # noqa: F811
    owners,  # noqa: F811
):
    hub, owner, other = signup_hub, owners["A"], owners["B"]
    machine = _machine(hub, owner, wings=("shared", "vault"))
    _, open_job = _started(hub, owner, machine)
    _, vault_job = _started(hub, owner, machine, board="vault")
    for job, cost in ((open_job, 100), (vault_job, 7000)):
        assert _usage(hub, machine, job, cost_micros=cost, currency="USD")[0] == 200
    # Another workspace has its own runs and sees neither these nor their agent.
    theirs = _machine(hub, other)
    _, their_job = _started(hub, other, theirs)
    assert _usage(hub, theirs, their_job, cost_micros=55, currency="USD")[0] == 200

    _, reader = cli_token(owner, "reader", profile="standard", read_wings="shared")
    seen = _sums(hub, token=reader)
    assert seen["scope"] == "readable"
    assert seen["workspace"]["runs"] == 1 and seen["workspace"]["cost_micros"] == {"USD": 100}
    assert [b["board"] for b in seen["by_board"]] == ["shared"]
    assert "vault" not in str(seen)
    assert _sums(hub, cookie=owner["cookie"])["workspace"]["cost_micros"] == {"USD": 7100}
    mine = _sums(hub, cookie=other["cookie"])
    assert mine["workspace"]["cost_micros"] == {"USD": 55}
    assert [a["agent"] for a in mine["by_agent"]] == [theirs["worker_connection_id"]]
    assert machine["worker_connection_id"] not in str(mine)
    # A connection that reads no wing of the workspace sees an empty, not a refused, sum.
    _, blind = cli_token(owner, "blind", profile="standard", read_wings="nowhere")
    assert _sums(hub, token=blind)["workspace"]["runs"] == 0
    # A machine credential reads nothing here, and the tool is not offered to it.
    assert http(hub.port, "GET", "/api/costs", token=machine["machine_token"])[0] != 200


def test_the_tool_is_listed_and_answers_like_the_route(signup_hub, owners):  # noqa: F811
    hub, owner = signup_hub, owners["A"]
    machine = _machine(hub, owner)
    _, job = _started(hub, owner, machine)
    assert _usage(hub, machine, job, cost_micros=42, currency="USD")[0] == 200
    _, token = cli_token(owner, "agent-reader", profile="standard")

    def rpc(method, **params):
        status, body = http(
            hub.port,
            "POST",
            "/mcp",
            token=token,
            body={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        )
        assert status == 200, (status, body)
        return body["result"]

    assert "costs_summary" in [tool["name"] for tool in rpc("tools/list")["tools"]]
    answer = rpc("tools/call", name="costs_summary", arguments={})
    assert not answer.get("isError"), answer
    assert "42" in str(answer)
    bad = rpc("tools/call", name="costs_summary", arguments={"since": "last tuesday"})
    assert bad.get("isError")


def test_window_default_setting_and_owner_only(signup_hub, owners):  # noqa: F811
    hub, owner = signup_hub, owners["A"]
    machine = _machine(hub, owner)
    _, recent = _started(hub, owner, machine)
    _, old = _started(hub, owner, machine)
    for job, cost in ((recent, 10), (old, 20)):
        assert _usage(hub, machine, job, cost_micros=cost, currency="USD")[0] == 200
    conn = _db(hub, owner)
    try:
        conn.execute(
            "UPDATE run_ledger SET created_at='2020-03-15T12:00:00Z' WHERE job_id=?", (old,)
        )
    finally:
        conn.close()
    # Default: the UTC calendar month; the old run is outside it.
    assert _sums(hub, cookie=owner["cookie"])["workspace"]["cost_micros"] == {"USD": 10}
    settings = "/api/settings/costs"
    assert _sums(hub, settings, cookie=owner["cookie"]) == {
        "window": "calendar_month",
        "days": None,
    }
    # A read may name its own window; end is exclusive.
    past = _sums(hub, "/api/costs?since=2020-03-01&until=2020-04-01", cookie=owner["cookie"])
    assert past["window"]["custom"] is True and past["window"]["start"] == "2020-03-01T00:00:00Z"
    assert past["workspace"]["cost_micros"] == {"USD": 20}
    exact = "/api/costs?since=2020-03-01&until=2020-03-15T12:00:00Z"
    assert _sums(hub, exact, cookie=owner["cookie"])["workspace"]["runs"] == 0
    for query in ("since=2021-01-01&until=2020-01-01", "since=nonsense", "until=2020-13-45"):
        assert http(hub.port, "GET", "/api/costs?" + query, cookie=owner["cookie"])[0] == 400
    # Only a signed-in owner sets the workspace window; a token (even trusted) cannot.
    _, token = cli_token(owner, "trusted-writer", profile="standard")
    status, _ = http(hub.port, "PUT", settings, body={"window": "calendar_week"}, token=token)
    assert status == 403
    for body in (
        {"window": "fortnight"},
        {"window": "rolling_days"},
        {"window": "rolling_days", "days": 0},
        {"window": "rolling_days", "days": 367},
        {"window": "calendar_week", "days": 7},
        {"window": "calendar_week", "extra": 1},
    ):
        assert http(hub.port, "PUT", settings, cookie=owner["cookie"], body=body)[0] == 400
    assert _sums(hub, settings, cookie=owner["cookie"])["window"] == "calendar_month"
    status, body = http(
        hub.port,
        "PUT",
        settings,
        cookie=owner["cookie"],
        body={"window": "rolling_days", "days": 5},
    )
    assert (status, body) == (200, {"window": "rolling_days", "days": 5})
    assert _sums(hub, cookie=owner["cookie"])["window"]["window"] == "rolling_days"
    assert _sums(hub, cookie=owner["cookie"])["workspace"]["cost_micros"] == {"USD": 10}
    # The setting belongs to its workspace.
    assert _sums(hub, settings, cookie=owners["B"]["cookie"])["window"] == "calendar_month"


def test_runs_that_never_started_have_no_cost_to_report(signup_hub, owners):  # noqa: F811
    hub, owner = signup_hub, owners["A"]
    machine = _machine(hub, owner)
    task = card(hub, owner)["number"]
    status, job = http(
        hub.port,
        "POST",
        "/api/card-starts",
        cookie=owner["cookie"],
        body=dict(task=task, machine=machine["machine_id"], project="demo", topic="worker"),
    )
    assert status == 201, (status, job)
    assert _sums(hub, cookie=owner["cookie"])["workspace"]["runs"] == 0


def test_reports_from_before_the_event_ledger_get_an_event(signup_hub, owners):  # noqa: F811
    hub, owner = signup_hub, owners["A"]
    machine = _machine(hub, owner)
    _, job = _started(hub, owner, machine)
    assert _usage(hub, machine, job, input_tokens=3, cost_micros=9, currency="USD")[0] == 200
    conn = _db(hub, owner)
    try:
        conn.execute("DROP TABLE cost_events")
        costs.ensure(conn)
        [event] = [dict(r) for r in conn.execute("SELECT * FROM cost_events")]
    finally:
        conn.close()
    assert (event["job_id"], event["source"], event["cost_micros"]) == (job, "backfill", 9)
    assert _sums(hub, cookie=owner["cookie"])["workspace"]["cost_micros"] == {"USD": 9}


def test_record_refuses_a_silent_zero():
    conn = connect(":memory:", isolation_level=None)
    conn.row_factory = row_factory
    conn.execute(ledger.TABLE)
    costs.ensure(conn)
    conn.execute(
        "INSERT INTO run_ledger(job_id,task_number,board_slug,project,topic,machine_id,"
        "requested_by,status,created_at,updated_at) "
        "VALUES ('j1',1,'b','p','t','m','user:u','started','2030-01-02T03:04:05Z',"
        "'2030-01-02T03:04:05Z')"
    )
    now = "2030-01-02T03:04:06Z"
    for kwargs in (
        {},
        {"cost_micros": 5},
        {"cost_micros": 5, "currency": "USD", "unknown": True},
        {"input_tokens": -1},
        {"source": "guess", "unknown": True},
    ):
        with pytest.raises(ValueError):
            costs.record(conn, "j1", now=now, **kwargs)
    with pytest.raises(ValueError):
        costs.record(conn, "missing", now=now, unknown=True)
    assert conn.execute("SELECT COUNT(*) FROM cost_events").fetchone()[0] == 0
    costs.record(conn, "j1", now=now, unknown=True, model="m-1", model_provider="p-1")
    costs.record(conn, "j1", now=now, cost_micros=0, currency="USD")
    sums = costs.summarize(conn, "2030-01-01T00:00:00Z", "2030-02-01T00:00:00Z")
    assert sums["workspace"]["cost_micros"] == {"USD": 0}
    assert sums["unknown_cost"]["runs"] == 0


@pytest.mark.parametrize(
    "setting,now,expected",
    [
        (
            {"window": "calendar_month", "days": None},
            "2030-02-14T10:00:00Z",
            ("2030-02-01T00:00:00Z", "2030-03-01T00:00:00Z"),
        ),
        (
            {"window": "calendar_month", "days": None},
            "2030-12-31T23:59:59Z",
            ("2030-12-01T00:00:00Z", "2031-01-01T00:00:00Z"),
        ),
        (
            {"window": "calendar_week", "days": None},
            "2030-02-14T10:00:00Z",
            ("2030-02-11T00:00:00Z", "2030-02-18T00:00:00Z"),
        ),
        (
            {"window": "calendar_day", "days": None},
            "2030-02-14T10:00:00Z",
            ("2030-02-14T00:00:00Z", "2030-02-15T00:00:00Z"),
        ),
        (
            {"window": "rolling_days", "days": 2},
            "2030-02-14T10:00:00Z",
            ("2030-02-12T10:00:01Z", "2030-02-14T10:00:01Z"),
        ),
    ],
)
def test_window_boundaries_are_utc(setting, now, expected):
    assert costs.window(setting, now) == expected
