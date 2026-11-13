"""Budgets over the cost ledger: warning at 80%, refusal of new starts at 100%, owner only."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from commonplace.runner import budgets, costs
from mempalace.sqlite_crypto import connect, row_factory
from tests.runner_harness import (
    card,
    cli_token,
    http,
    owners,  # noqa: F401
    signup_hub,  # noqa: F401
)
from tests.test_cost_ledger import _call, _db, _machine, _started, _usage

BUDGETS = "/api/settings/budgets"


def _put(hub, owner, kind, key, limit, **extra):
    scope = {"kind": kind} if key is None else {"kind": kind, "key": key}
    body = {"scope": scope, "limit_micros": limit, "currency": "USD", **extra}
    status, answer = http(hub.port, "PUT", BUDGETS, cookie=owner["cookie"], body=body)
    assert status == 200, (status, answer)
    return answer


def _needs_me(hub, owner):
    status, body = http(hub.port, "GET", "/api/needs-me", cookie=owner["cookie"])
    assert status == 200, (status, body)
    return body


def _start(hub, owner, machine, project="demo", board="shared"):
    task = card(hub, owner, board=board)["number"]
    return http(
        hub.port,
        "POST",
        "/api/card-starts",
        cookie=owner["cookie"],
        body=dict(task=task, machine=machine["machine_id"], project=project, topic="worker"),
    )


def _rows(hub, owner, sql, *args):
    conn = _db(hub, owner)
    try:
        return [dict(row) for row in conn.execute(sql, args)]
    finally:
        conn.close()


def _cost(hub, machine, job, micros, currency="USD"):
    assert _usage(hub, machine, job, cost_micros=micros, currency=currency)[0] == 200


@pytest.fixture
def two_projects(signup_hub, owners):  # noqa: F811
    hub, owner = signup_hub, owners["A"]
    return hub, owner, _machine(hub, owner, projects=("demo", "docs"))


def test_below_eighty_percent_nothing_happens(two_projects):
    hub, owner, machine = two_projects
    budget = _put(hub, owner, "project", "demo", 1000)
    assert budget["state"] == "ok" and budget["marks"] == []
    _, job = _started(hub, owner, machine)
    _cost(hub, machine, job, 790)
    [entry] = http(hub.port, "GET", BUDGETS, cookie=owner["cookie"])[1]["budgets"]
    assert (entry["spent_micros"], entry["percent"], entry["state"]) == (790, 79, "ok")
    assert entry["marks"] == []
    assert "budget_warnings" not in _needs_me(hub, owner)
    assert _rows(hub, owner, "SELECT * FROM cost_budget_marks") == []
    assert _start(hub, owner, machine)[0] == 201


def test_crossing_eighty_percent_warns_once_per_window_and_scope(two_projects):
    hub, owner, machine = two_projects
    _put(hub, owner, "project", "demo", 1000)
    _put(hub, owner, "project", "docs", 1000)
    _, job = _started(hub, owner, machine)
    _cost(hub, machine, job, 800)
    items = _needs_me(hub, owner)["budget_warnings"]
    assert len(items) == 1
    item = items[0]
    assert item["scope"] == {"kind": "project", "key": "demo"} and item["reasons"] == ["warning"]
    assert item["percent"] == 80 and "80%" in item["why"]
    # More reports in the same window and scope raise no second mark or item.
    _cost(hub, machine, job, 900)
    assert len(_needs_me(hub, owner)["budget_warnings"]) == 1
    marks = _rows(hub, owner, "SELECT level FROM cost_budget_marks")
    assert [m["level"] for m in marks] == ["warning"]
    # Another scope has its own warning.
    _, other = _started(hub, owner, machine, project="docs")
    _cost(hub, machine, other, 850)
    scopes = {i["scope"]["key"] for i in _needs_me(hub, owner)["budget_warnings"]}
    assert scopes == {"demo", "docs"}
    # The warning is for the owner only.
    _, agent = cli_token(owner, "agent", profile="standard")
    status, body = http(hub.port, "GET", "/api/needs-me", token=agent)
    assert "budget_warnings" not in (body if status == 200 else {})


def test_a_hundred_percent_refuses_new_starts_on_that_scope_only(two_projects):
    hub, owner, machine = two_projects
    budget = _put(hub, owner, "project", "demo", 1000)
    _, job = _started(hub, owner, machine)
    _cost(hub, machine, job, 1000)
    before = _rows(hub, owner, "SELECT COUNT(*) AS n FROM runner_jobs")[0]["n"]
    status, body = _start(hub, owner, machine)
    assert status != 201, (status, body)
    text = str(body)
    assert "budget_exceeded" in text and "project demo" in text and budget["id"] in text
    assert budget["window"]["start"] in text and budget["window"]["end"] in text
    assert "1000" not in text.replace(budget["window"]["start"], "")  # no amounts leak
    assert _rows(hub, owner, "SELECT COUNT(*) AS n FROM runner_jobs")[0]["n"] == before
    # A start on another project still works.
    assert _start(hub, owner, machine, project="docs")[0] == 201
    item = _needs_me(hub, owner)["budget_warnings"][0]
    assert item["reasons"] == ["limit"] and "refused" in item["why"]


@pytest.mark.parametrize(
    "kind,key",
    [
        ("workspace", None),
        ("agent", "AGENT"),
        ("card", "CARD"),
    ],
)
def test_every_scope_kind_stops_its_own_starts(two_projects, kind, key):
    hub, owner, machine = two_projects
    task, job = _started(hub, owner, machine)
    key = {"AGENT": machine["worker_connection_id"], "CARD": task}.get(key, key)
    _put(hub, owner, kind, key, 500)
    _cost(hub, machine, job, 500)
    if kind == "card":
        # The budget is the card's: the next start of that card is refused, other cards are not.
        status, body = http(
            hub.port,
            "POST",
            "/api/card-starts",
            cookie=owner["cookie"],
            body=dict(task=task, machine=machine["machine_id"], project="demo", topic="worker"),
        )
        assert status != 201 and "budget_exceeded" in str(body), (status, body)
        assert _start(hub, owner, machine)[0] == 201
    else:
        status, body = _start(hub, owner, machine)
        assert status != 201 and "budget_exceeded" in str(body), (status, body)


def test_running_work_is_flagged_and_never_killed(two_projects):
    hub, owner, machine = two_projects
    _put(hub, owner, "project", "demo", 1000)
    _, running = _started(hub, owner, machine)
    _, spent = _started(hub, owner, machine)
    _cost(hub, machine, spent, 1000)
    assert _start(hub, owner, machine)[0] != 201
    # Both runs are still started: the hub neither cancelled nor ended them.
    states = {r["id"]: r["state"] for r in _rows(hub, owner, "SELECT id, state FROM runner_jobs")}
    assert states[running] == states[spent] == "started"
    [entry] = http(hub.port, "GET", BUDGETS, cookie=owner["cookie"])[1]["budgets"]
    assert entry["state"] == "limit" and entry["running_runs"] == 2
    assert (
        "2 run(s) already running will finish" in _needs_me(hub, owner)["budget_warnings"][0]["why"]
    )
    # The running run keeps reporting and finishing normally.
    assert (
        _usage(hub, machine, running, status="completed", cost_micros=5, currency="USD")[0] == 200
    )


def test_queued_starts_are_cancelled_at_lease_once_the_budget_is_spent(two_projects):
    hub, owner, machine = two_projects
    _, spent = _started(hub, owner, machine)
    _cost(hub, machine, spent, 900)
    status, queued = _start(hub, owner, machine)
    assert status == 201
    _put(hub, owner, "project", "demo", 900)
    status, lease = _call(hub, machine, "lease")
    assert not (isinstance(lease, dict) and lease.get("job_id")), (status, lease)
    [row] = _rows(
        hub, owner, "SELECT state, outcome_code FROM runner_jobs WHERE id=?", queued["job_id"]
    )
    assert (row["state"], row["outcome_code"]) == ("cancelled", "budget_exceeded")


def test_unknown_cost_and_other_currency_runs_do_not_count(two_projects):
    hub, owner, machine = two_projects
    _put(hub, owner, "project", "demo", 1000, unknown_runs_warn=4)
    _, unreported = _started(hub, owner, machine)
    _, declared = _started(hub, owner, machine)
    _, euro = _started(hub, owner, machine)
    assert _usage(hub, machine, declared, cost_unknown=True)[0] == 200
    _cost(hub, machine, euro, 5000, "EUR")
    [entry] = http(hub.port, "GET", BUDGETS, cookie=owner["cookie"])[1]["budgets"]
    assert entry["spent_micros"] == 0 and entry["state"] == "ok"
    assert entry["unknown_cost_runs"] == 2 and entry["other_currency_runs"] == 1
    # Neither blocks a start; three unknown runs stay under the threshold of four.
    assert _started(hub, owner, machine)
    assert "budget_warnings" not in _needs_me(hub, owner)
    # A fourth run that started with no cost reported only raises the warning (marked when
    # the run starts); starts are still allowed.
    assert _started(hub, owner, machine)
    [item] = _needs_me(hub, owner)["budget_warnings"]
    assert item["reasons"] == ["unknown_runs"] and item["unknown_cost_runs"] == 4
    assert unreported
    assert _start(hub, owner, machine)[0] == 201


def test_the_window_rolling_over_resets_spend_and_warns_again(two_projects):
    hub, owner, machine = two_projects
    _put(hub, owner, "project", "demo", 1000)
    _, job = _started(hub, owner, machine)
    _cost(hub, machine, job, 1000)
    now = datetime.now(timezone.utc)
    later = (now.replace(day=1) + timedelta(days=32)).replace(day=1, hour=0, minute=0, second=1)
    later = later.strftime(costs.TS)
    conn = _db(hub, owner)
    try:
        old = {"agent": machine["worker_connection_id"], "project": "demo", "task_number": 1}
        assert budgets.stop_reason(conn, now.strftime(costs.TS), **{**old, "task_number": 99})
        # Next month the run belongs to the old window: nothing spent, starts allowed.
        assert budgets.stop_reason(conn, later, **{**old, "task_number": 99}) is None
        [entry] = budgets.status(conn, later)
        assert entry["spent_micros"] == 0 and entry["marks"] == []
        # A run of the new window crossing 80% is warned about again.
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO run_ledger(job_id,task_number,board_slug,project,topic,machine_id,agent,"
            "requested_by,status,created_at,updated_at) VALUES ('next',77,'shared','demo','worker',"
            "'m',?, 'user:u','started',?,?)",
            (machine["worker_connection_id"], later, later),
        )
        costs.record(conn, "next", now=later, cost_micros=850, currency="USD")
        budgets.after_run(conn, "next", later)
        conn.execute("COMMIT")
        [entry] = budgets.status(conn, later)
        assert entry["state"] == "warning" and entry["marks"] == ["warning"]
        periods = [
            r[0] for r in conn.execute("SELECT DISTINCT period FROM cost_budget_marks ORDER BY 1")
        ]
        assert len(periods) == 2 and periods[1] == entry["window"]["start"]
        assert len(budgets.attention(conn, later)) == 1
    finally:
        conn.close()


def test_two_concurrent_starts_at_the_limit_are_both_refused(two_projects):
    """Rule: a start is admitted only while the scope's reported spend in the window is below
    its limit, judged inside the one runner write transaction. Starts run one at a time there,
    so at or over the limit every concurrent start is refused and none is inserted."""
    hub, owner, machine = two_projects
    _put(hub, owner, "project", "demo", 1000)
    _, job = _started(hub, owner, machine)
    _cost(hub, machine, job, 1000)
    tasks = [card(hub, owner)["number"] for _ in range(4)]
    before = _rows(hub, owner, "SELECT COUNT(*) AS n FROM runner_jobs")[0]["n"]

    def go(task):
        return http(
            hub.port,
            "POST",
            "/api/card-starts",
            cookie=owner["cookie"],
            body=dict(task=task, machine=machine["machine_id"], project="demo", topic="worker"),
        )[0]

    with ThreadPoolExecutor(4) as pool:
        statuses = list(pool.map(go, tasks))
    assert 201 not in statuses, statuses
    assert _rows(hub, owner, "SELECT COUNT(*) AS n FROM runner_jobs")[0]["n"] == before


def test_a_start_and_a_report_racing_are_ordered_by_the_write_lock(two_projects):
    """Rule, other half: a start sees every report committed before it and none after, never
    a half state. Racing a limit-reaching report against starts, each start is either
    inserted (it ran first) or refused (it ran after), and the inserts all precede the report."""
    hub, owner, machine = two_projects
    _put(hub, owner, "project", "demo", 1000)
    _, job = _started(hub, owner, machine)
    tasks = [card(hub, owner)["number"] for _ in range(3)]

    def start(task):
        return http(
            hub.port,
            "POST",
            "/api/card-starts",
            cookie=owner["cookie"],
            body=dict(task=task, machine=machine["machine_id"], project="demo", topic="worker"),
        )[0]

    def report(_):
        return _usage(hub, machine, job, cost_micros=1000, currency="USD")[0]

    with ThreadPoolExecutor(4) as pool:
        futures = [pool.submit(start, t) for t in tasks] + [pool.submit(report, None)]
        results = [f.result() for f in futures]
    assert results[-1] == 200
    inserted = _rows(
        hub,
        owner,
        "SELECT created_at FROM runner_jobs WHERE task_number IN (%s)" % ",".join("?" * 3),
        *tasks,
    )
    assert len(inserted) == sum(1 for status in results[:-1] if status == 201)
    # After the report committed, nothing new gets in.
    assert _start(hub, owner, machine)[0] != 201


def test_only_a_signed_in_owner_sets_reads_or_clears_budgets(two_projects, owners):  # noqa: F811
    hub, owner, machine = two_projects
    other = owners["B"]
    _put(hub, owner, "project", "demo", 1000)
    _, agent = cli_token(owner, "agent", profile="standard")
    body = {"scope": {"kind": "workspace"}, "limit_micros": 5, "currency": "USD"}
    for auth in ({"token": agent}, {"token": machine["machine_token"]}):
        assert http(hub.port, "GET", BUDGETS, **auth)[0] == 404
        assert http(hub.port, "PUT", BUDGETS, body=body, **auth)[0] == 404
        assert http(hub.port, "DELETE", BUDGETS + "?kind=project&key=demo", **auth)[0] == 404
    assert [
        b["scope"]["key"]
        for b in http(hub.port, "GET", BUDGETS, cookie=owner["cookie"])[1]["budgets"]
    ] == ["demo"]
    # Another workspace never sees them, and its own list starts empty.
    assert http(hub.port, "GET", BUDGETS, cookie=other["cookie"])[1]["budgets"] == []
    assert (
        http(hub.port, "DELETE", BUDGETS + "?kind=project&key=demo", cookie=other["cookie"])[0]
        == 404
    )
    # Setting and clearing are audited.
    actions = [r[0] for r in hub.gateway.db._conn.execute("SELECT action FROM audit_log")]
    assert "costs.budget" in actions
    status, answer = http(
        hub.port, "DELETE", BUDGETS + "?kind=project&key=demo", cookie=owner["cookie"]
    )
    assert status == 200 and answer["removed"]
    assert "costs.budget_clear" in [
        r[0] for r in hub.gateway.db._conn.execute("SELECT action FROM audit_log")
    ]
    assert http(hub.port, "GET", BUDGETS, cookie=owner["cookie"])[1]["budgets"] == []
    assert _rows(hub, owner, "SELECT * FROM cost_budget_marks") == []


@pytest.mark.parametrize(
    "body",
    [
        {"limit_micros": 5, "currency": "USD"},
        {"scope": {"kind": "planet"}, "limit_micros": 5, "currency": "USD"},
        {"scope": {"kind": "workspace", "key": "x"}, "limit_micros": 5, "currency": "USD"},
        {"scope": {"kind": "card", "key": "abc"}, "limit_micros": 5, "currency": "USD"},
        {
            "scope": {"kind": "agent", "key": "no-such-connection"},
            "limit_micros": 5,
            "currency": "USD",
        },
        {"scope": {"kind": "workspace"}, "limit_micros": 0, "currency": "USD"},
        {"scope": {"kind": "workspace"}, "limit_micros": True, "currency": "USD"},
        {"scope": {"kind": "workspace"}, "limit_micros": 5, "currency": "usd"},
        {"scope": {"kind": "workspace"}, "limit_micros": 5, "currency": "USD", "x": 1},
        {
            "scope": {"kind": "workspace"},
            "limit_micros": 5,
            "currency": "USD",
            "unknown_runs_warn": 0,
        },
    ],
)
def test_invalid_budgets_are_refused(two_projects, body):
    hub, owner, _machine = two_projects
    assert http(hub.port, "PUT", BUDGETS, cookie=owner["cookie"], body=body)[0] == 400
    assert http(hub.port, "GET", BUDGETS, cookie=owner["cookie"])[1]["budgets"] == []


def test_raising_a_limit_restarts_the_warning_cycle(two_projects):
    hub, owner, machine = two_projects
    _put(hub, owner, "project", "demo", 1000)
    _, job = _started(hub, owner, machine)
    _cost(hub, machine, job, 1000)
    assert _needs_me(hub, owner)["budget_warnings"][0]["reasons"] == ["limit"]
    _put(hub, owner, "project", "demo", 5000)
    assert "budget_warnings" not in _needs_me(hub, owner)
    assert _start(hub, owner, machine)[0] == 201
    _put(hub, owner, "project", "demo", 1200)  # now at 83%
    assert _needs_me(hub, owner)["budget_warnings"][0]["reasons"] == ["warning"]


def test_a_clean_workspace_has_no_budget_rows_and_no_new_behaviour(two_projects):
    hub, owner, machine = two_projects
    _, job = _started(hub, owner, machine)
    _cost(hub, machine, job, 10**9)
    assert _start(hub, owner, machine)[0] == 201
    assert http(hub.port, "GET", BUDGETS, cookie=owner["cookie"])[1] == {
        "window": {"window": "calendar_month", "days": None},
        "budgets": [],
    }
    assert _rows(hub, owner, "SELECT * FROM cost_budgets") == []
    assert _rows(hub, owner, "SELECT * FROM cost_budget_marks") == []
    assert "budget_warnings" not in _needs_me(hub, owner)
    # Existing tokens get no budget tool.
    _, agent = cli_token(owner, "agent", profile="standard")
    status, body = http(
        hub.port,
        "POST",
        "/mcp",
        token=agent,
        body={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    assert status == 200
    assert not [t for t in body["result"]["tools"] if "budget" in t["name"]]


def test_a_workspace_that_never_ran_anything_is_not_given_a_store(signup_hub, owners):  # noqa: F811
    hub, owner = signup_hub, owners["A"]
    path = Path(hub.gateway.workspace_dir(owner["workspace"])) / "runner.sqlite3"
    assert not path.exists()
    assert "budget_warnings" not in _needs_me(hub, owner)
    assert not path.exists()


def test_budget_tables_are_plain_and_reopen_cleanly(two_projects):
    hub, owner, machine = two_projects
    _put(hub, owner, "workspace", None, 1000)
    conn = connect(
        str(hub.gateway.workspace_dir(owner["workspace"])) + "/runner.sqlite3",
        isolation_level=None,
    )
    conn.row_factory = row_factory
    try:
        budgets.ensure(conn)
        assert conn.execute("SELECT COUNT(*) FROM cost_budgets").fetchone()[0] == 1
    finally:
        conn.close()
