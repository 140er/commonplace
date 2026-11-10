"""Owner-submitted grant scope reports: stored, shown, unverified and binding-resetting."""

# ruff: noqa: F811

import copy
import hashlib
import json
import sqlite3
from http.client import HTTPConnection

import pytest

from commonplace.runner import registry
from tests.runner_harness import (
    attest,
    enroll as harness_enroll,
    http,
    member,
    owners,  # noqa: F401
    signup_hub,  # noqa: F401
)
from tests.test_runner_agent import _queue

RECORD = {
    "version": 1,
    "provider_instance_id": "worker",
    "t3": {
        "max_runtime_mode": None,
        "workspace_strategies": None,
        "projects": None,
        "mcp": None,
        "mode_inheritance": "unknown",
    },
    "runner": {
        "workspace_strategy": "root",
        "projects": [
            {"label": "demo", "project_id": "demo", "runtime_ceiling": "approval-required"}
        ],
        "topic_models": None,
    },
}
# Frozen: canonical bytes and digest of RECORD, shared with the local check vector.
CANONICAL = (
    b'{"provider_instance_id":"worker","runner":{"projects":[{"label":"demo",'
    b'"project_id":"demo","runtime_ceiling":"approval-required"}],"topic_models":null,'
    b'"workspace_strategy":"root"},"t3":{"max_runtime_mode":null,"mcp":null,'
    b'"mode_inheritance":"unknown","projects":null,"workspace_strategies":null},"version":1}'
)
DIGEST = "ca053c5e57026aef8e63a46874f3db22c15ea5eb48f4a3a357fff39af50badc0"
SCOPE_COLUMNS = {
    "grant_scope",
    "grant_scope_sha256",
    "grant_scope_reported_at",
    "binding_scope_sha256",
}
BINDING_FIELDS = (
    "binding_topics",
    "binding_provider_instance_id",
    "binding_worker_connection_id",
    "binding_attested_by",
    "binding_attested_at",
    "binding_grant_version",
)


def edited(pointer, value, base=RECORD):
    """A deep copy of ``base`` with one JSON pointer replaced."""
    result = copy.deepcopy(base)
    target, parts = result, pointer.strip("/").split("/")
    for part in parts[:-1]:
        target = target[int(part) if isinstance(target, list) else part]
    target[int(parts[-1]) if isinstance(target, list) else parts[-1]] = value
    return result


def enroll(hub, owner, **grant):
    status, body = http(
        hub.port,
        "POST",
        "/api/memory",
        cookie=owner["cookie"],
        body={"text": "Synthetic workspace example.", "theme": "shared", "topic": "demo"},
    )
    assert status == 201, (status, body)
    return harness_enroll(hub, owner, **grant)


def attest_path(machine):
    return f"/api/runners/{machine['machine_id']}/attest"


def post(hub, owner, machine, body, **auth):
    return http(
        hub.port, "POST", attest_path(machine), body=body, **(auth or {"cookie": owner["cookie"]})
    )


def raw(hub, owner, machine, payload):
    """Post exact bytes; returns status, body and the Connection response header."""
    conn = HTTPConnection("127.0.0.1", hub.port, timeout=30)
    try:
        conn.request(
            "POST",
            attest_path(machine),
            payload,
            {
                "Content-Type": "application/json",
                "Cookie": owner["cookie"],
                "Origin": f"http://127.0.0.1:{hub.port}",
            },
        )
        response = conn.getresponse()
        return response.status, response.read(), response.getheader("Connection")
    finally:
        conn.close()


def view(hub, owner, machine):
    status, body = http(hub.port, "GET", "/api/runners", cookie=owner["cookie"])
    assert status == 200, (status, body)
    matches = [row for row in body["machines"] if row["id"] == machine["machine_id"]]
    assert len(matches) == 1, "machine missing from owner listing"
    return matches[0]


def stored(hub, machine):
    with hub.db._lock:
        row = hub.db._conn.execute(
            "SELECT * FROM runner_machines WHERE id=?", (machine["machine_id"],)
        ).fetchone()
    return dict(row)


def committed(hub, machine):
    """Read the row through a separate connection, proving it reached the database."""
    conn = sqlite3.connect(hub.db.path)
    conn.row_factory = sqlite3.Row
    try:
        return dict(
            conn.execute(
                "SELECT * FROM runner_machines WHERE id=?", (machine["machine_id"],)
            ).fetchone()
        )
    finally:
        conn.close()


def audit(hub, owner):
    with hub.db._lock:
        return [
            tuple(row)
            for row in hub.db._conn.execute(
                "SELECT * FROM audit_log WHERE workspace_id=?", (owner["workspace"],)
            )
        ]


def snapshot(hub, owner, machine):
    return stored(hub, machine), audit(hub, owner)


def report(hub, owner, machine, record=RECORD):
    status, body = post(hub, owner, machine, {"grant_scope": record})
    assert status == 200, (status, body)
    return body


def rejected(hub, owner, machine, body, expected=400):
    """A refused attest changes neither the machine row nor the audit log."""
    before = snapshot(hub, owner, machine)
    status, payload = post(hub, owner, machine, body)
    assert status == expected, (status, payload)
    assert snapshot(hub, owner, machine) == before
    return payload


def confirmation(hub, owner, machine, provider="worker", **overrides):
    row = view(hub, owner, machine)
    return {
        "topics": row["topics"],
        "provider_instance_id": provider,
        "worker_connection_id": row["worker_connection_id"],
        "grant_scope_sha256": row["scope"]["sha256"],
        "expected_scope_epoch": row["scope_epoch"],
        **overrides,
    }


def confirm(hub, owner, machine, provider="worker"):
    status, body = post(hub, owner, machine, confirmation(hub, owner, machine, provider))
    assert status == 200, (status, body)
    return body


def assert_unbound(row):
    assert row["binding_state"] == "unbound"
    assert row["binding_scope_sha256"] is None
    assert all(row[key] is None for key in BINDING_FIELDS)


def bound_machine(hub, owner, record=RECORD):
    machine = enroll(hub, owner)
    report(hub, owner, machine, record)
    confirm(hub, owner, machine)
    return machine


@pytest.mark.parametrize("state", ["unbound", "bound"])
def test_report_never_binds(signup_hub, owners, state):
    hub, owner = signup_hub, owners["A"]
    machine = enroll(hub, owner)
    if state == "bound":
        attest(hub, owner, machine)
    before = stored(hub, machine)["binding_epoch"]
    body = report(hub, owner, machine)
    assert body["verified"] is False
    row = committed(hub, machine)
    assert_unbound(row)
    assert row["binding_epoch"] == before + 1
    assert row["grant_scope_sha256"] == DIGEST
    assert row["grant_scope"].encode("utf-8") == CANONICAL
    assert hashlib.sha256(CANONICAL).hexdigest() == DIGEST
    registry.ensure(hub.db)
    assert committed(hub, machine)["grant_scope_sha256"] == DIGEST


@pytest.mark.parametrize("where", ["top", "nested"])
def test_hub_duplicate_keys(signup_hub, owners, where):
    hub, owner = signup_hub, owners["A"]
    machine = enroll(hub, owner)
    report(hub, owner, machine)
    text = json.dumps({"grant_scope": RECORD})
    if where == "top":
        payload = text[:-1] + ', "grant_scope": ' + json.dumps(RECORD) + "}"
    else:
        payload = text.replace('"label": "demo"', '"label": "demo", "label": "other"')
    assert payload != text
    before = snapshot(hub, owner, machine)
    assert raw(hub, owner, machine, payload.encode())[0] == 400
    assert snapshot(hub, owner, machine) == before


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_hub_nonfinite(signup_hub, owners, constant):
    hub, owner = signup_hub, owners["A"]
    machine = enroll(hub, owner)
    report(hub, owner, machine)
    payload = json.dumps({"grant_scope": RECORD}).replace('"version": 1', '"version": ' + constant)
    before = snapshot(hub, owner, machine)
    assert raw(hub, owner, machine, payload.encode())[0] == 400
    assert snapshot(hub, owner, machine) == before


def padded(body, size):
    payload = json.dumps(body).encode()
    return payload + b" " * (size - len(payload))


def test_report_wire_cap(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    machine = enroll(hub, owner)
    status, _, connection = raw(hub, owner, machine, padded({"grant_scope": RECORD}, 20481))
    assert status == 413
    # The unread body must not be parsed as a next request on this connection.
    assert connection == "close"
    assert stored(hub, machine)["grant_scope"] is None
    assert raw(hub, owner, machine, padded({"grant_scope": RECORD}, 20480))[0] == 200
    assert stored(hub, machine)["grant_scope_sha256"] == DIGEST


def test_confirm_wire_cap(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    machine = enroll(hub, owner)
    row = view(hub, owner, machine)
    body = {
        "topics": row["topics"],
        "provider_instance_id": "worker",
        "worker_connection_id": row["worker_connection_id"],
    }
    before = snapshot(hub, owner, machine)
    assert raw(hub, owner, machine, padded(body, 4097))[0] == 413
    assert snapshot(hub, owner, machine) == before
    assert raw(hub, owner, machine, padded(body, 4096))[0] == 200
    assert stored(hub, machine)["binding_state"] == "owner_attested"


@pytest.mark.parametrize("extra", ["grant_scope_sha256", "extra", "combined_confirm"])
def test_report_extra_key(signup_hub, owners, extra):
    hub, owner = signup_hub, owners["A"]
    machine = enroll(hub, owner)
    report(hub, owner, machine)
    body = {"grant_scope": RECORD}
    if extra == "grant_scope_sha256":
        body["grant_scope_sha256"] = DIGEST
    elif extra == "extra":
        body["note"] = "x"
    else:
        body.update(confirmation(hub, owner, machine))
    rejected(hub, owner, machine, body)


def many_projects(count):
    return [
        {"label": f"p{i}", "project_id": f"project_{i}", "runtime_ceiling": "auto"}
        for i in range(count)
    ]


def servers(count, tools=None):
    return [{"server_id": f"srv{i}", "tools": tools} for i in range(count)]


def split_tools(total):
    return [
        {"server_id": "a", "tools": [f"a{i}" for i in range(total // 2)]},
        {"server_id": "b", "tools": [f"b{i}" for i in range(total - total // 2)]},
    ]


LONG_TOOLS = [{"server_id": "a", "tools": [f"{i:03d}" + "t" * 124 for i in range(128)]}]
MCP = "/t3/mcp"
PROJECTS = "/runner/projects"
INVALID = {
    "no_version": lambda r: {k: v for k, v in r.items() if k != "version"},
    "extra_top": lambda r: {**r, "other": 1},
    "extra_t3": lambda r: edited("/t3", {**r["t3"], "other": 1}),
    "missing_t3": lambda r: edited("/t3", {k: v for k, v in r["t3"].items() if k != "mcp"}),
    "extra_project": lambda r: edited(PROJECTS + "/0", {**r["runner"]["projects"][0], "x": 1}),
    "not_object": lambda r: [],
    "bool_version": lambda r: edited("/version", True),
    "version_two": lambda r: edited("/version", 2),
    "float_version": lambda r: edited("/version", 1.0),
    "provider_type": lambda r: edited("/provider_instance_id", 7),
    "mode_type": lambda r: edited("/t3/max_runtime_mode", "root"),
    "strategy": lambda r: edited("/t3/workspace_strategies", ["other"]),
    "strategy_duplicate": lambda r: edited("/t3/workspace_strategies", ["root", "root"]),
    "t3_projects_duplicate": lambda r: edited("/t3/projects", ["a", "a"]),
    "mcp_shape": lambda r: edited(MCP, [{"server_id": "a"}]),
    "mcp_duplicate": lambda r: edited(MCP, [{"server_id": "a", "tools": None}] * 2),
    "tool_duplicate": lambda r: edited(MCP, [{"server_id": "a", "tools": ["x", "x"]}]),
    "bad_tool": lambda r: edited(MCP, [{"server_id": "a", "tools": ["x y"]}]),
    "duplicate_label": lambda r: edited(
        PROJECTS,
        [r["runner"]["projects"][0], {**r["runner"]["projects"][0], "project_id": "other"}],
    ),
    "duplicate_project_id": lambda r: edited(
        PROJECTS, [r["runner"]["projects"][0], {**r["runner"]["projects"][0], "label": "other"}]
    ),
    "ceiling": lambda r: edited(PROJECTS + "/0/runtime_ceiling", "root"),
    "control_character": lambda r: edited("/provider_instance_id", "a\nb"),
    "nul_character": lambda r: edited("/t3/projects", ["a\x00b"]),
    "space_id": lambda r: edited("/provider_instance_id", "a b"),
    "slash_id": lambda r: edited("/provider_instance_id", "a/b"),
    "empty_id": lambda r: edited("/provider_instance_id", ""),
    "long_id": lambda r: edited("/provider_instance_id", "a" * 129),
    "label_upper": lambda r: edited(PROJECTS + "/0/label", "Demo"),
    "marker_bearer": lambda r: edited("/provider_instance_id", "Bearer abc"),
    "marker_sk": lambda r: edited("/provider_instance_id", "SK-abc"),
    "marker_ghp": lambda r: edited("/t3/projects", ["ghp_abc"]),
    "marker_github": lambda r: edited(PROJECTS + "/0/project_id", "github_pat_abc"),
    "marker_xox": lambda r: edited(MCP, [{"server_id": "xoxb-1", "tools": None}]),
    "marker_cp_pat": lambda r: edited(MCP, [{"server_id": "a", "tools": ["cp_pat_abc"]}]),
    "marker_cmmn": lambda r: edited("/provider_instance_id", "cmmn_abc"),
    "marker_label": lambda r: edited(PROJECTS + "/0/label", "sk-demo"),
    "33_projects": lambda r: edited(PROJECTS, many_projects(33)),
    "33_t3_projects": lambda r: edited("/t3/projects", [f"p{i}" for i in range(33)]),
    "33_servers": lambda r: edited(MCP, servers(33)),
    "129_tools": lambda r: edited(MCP, split_tools(129)),
    "canonical_cap": lambda r: edited(MCP, LONG_TOOLS),
}
VALID = {
    "underscore_id": lambda r: edited("/provider_instance_id", "a_b_c"),
    "uuid_id": lambda r: edited("/provider_instance_id", "123e4567-e89b-12d3-a456-426614174000"),
    "long_id": lambda r: edited("/provider_instance_id", "a" * 128),
    "interior_markers": lambda r: edited(PROJECTS + "/0/project_id", "task-sk-1"),
    "interior_label": lambda r: edited(PROJECTS + "/0/label", "project-xoxo"),
    "32_projects": lambda r: edited(PROJECTS, many_projects(32)),
    "32_t3_projects": lambda r: edited("/t3/projects", [f"p{i}" for i in range(32)]),
    "32_servers": lambda r: edited(MCP, servers(32)),
    "128_tools": lambda r: edited(MCP, split_tools(128)),
}


def test_invalid_record(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    machine = enroll(hub, owner)
    report(hub, owner, machine)
    for name, build in INVALID.items():
        before = snapshot(hub, owner, machine)
        status, payload = post(hub, owner, machine, {"grant_scope": build(RECORD)})
        assert status == 400, (name, status, payload)
        assert snapshot(hub, owner, machine) == before, name
    for name, build in VALID.items():
        assert post(hub, owner, machine, {"grant_scope": build(RECORD)})[0] == 200, name


FIELD_EDITS = [
    ("/provider_instance_id", "other-provider"),
    ("/provider_instance_id", None),
    ("/t3/max_runtime_mode", "auto"),
    ("/t3/workspace_strategies", ["root"]),
    ("/t3/workspace_strategies", []),
    ("/t3/projects", ["project_1"]),
    ("/t3/projects", []),
    ("/t3/mcp", [{"server_id": "srv", "tools": ["tool"]}]),
    ("/t3/mcp", [{"server_id": "srv", "tools": None}]),
    ("/t3/mcp", []),
    ("/runner/projects/0/label", "renamed"),
    ("/runner/projects/0/project_id", "other_project"),
    ("/runner/projects/0/runtime_ceiling", "auto"),
]
SINGLETONS = [
    ("/t3/mode_inheritance", "known"),
    ("/runner/workspace_strategy", "existing_worktree"),
    ("/runner/topic_models", {"worker": "model"}),
]


def test_scope_field_reset(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    machine = bound_machine(hub, owner)
    for pointer, value in FIELD_EDITS:
        report(hub, owner, machine)
        confirm(hub, owner, machine)
        before = stored(hub, machine)
        assert before["binding_state"] == "owner_attested", pointer
        assert before["binding_scope_sha256"] == DIGEST, pointer
        report(hub, owner, machine, edited(pointer, value))
        row = stored(hub, machine)
        assert_unbound(row)
        assert row["binding_epoch"] == before["binding_epoch"] + 1, pointer
        assert row["grant_scope_sha256"] != DIGEST, pointer
    report(hub, owner, machine)
    confirm(hub, owner, machine)
    for pointer, value in SINGLETONS:
        rejected(hub, owner, machine, {"grant_scope": edited(pointer, value)})
    assert stored(hub, machine)["binding_state"] == "owner_attested"


def test_scope_change_unknown_known_unknown_and_aba(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    machine = bound_machine(hub, owner)
    epoch = stored(hub, machine)["binding_epoch"]
    known = edited("/t3/projects", ["project_1"])
    for record in (known, RECORD, edited("/t3/projects", []), RECORD):
        report(hub, owner, machine, record)
        epoch += 1
        row = stored(hub, machine)
        assert row["binding_epoch"] == epoch
        assert_unbound(row)


def test_reorder_is_not_change(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    projects = many_projects(3)
    record = edited(
        "/t3",
        {
            "max_runtime_mode": "auto",
            "workspace_strategies": ["root", "existing_worktree"],
            "projects": ["b", "a"],
            "mcp": [
                {"server_id": "b", "tools": ["y", "x"]},
                {"server_id": "a", "tools": None},
            ],
            "mode_inheritance": "unknown",
        },
        edited(PROJECTS, projects),
    )
    machine = bound_machine(hub, owner, record)
    before = stored(hub, machine)
    reordered = edited(
        "/t3",
        {
            "mode_inheritance": "unknown",
            "mcp": list(reversed(record["t3"]["mcp"])),
            "projects": ["a", "b"],
            "workspace_strategies": ["existing_worktree", "root"],
            "max_runtime_mode": "auto",
        },
        edited(PROJECTS, list(reversed(projects))),
    )
    reordered["t3"]["mcp"][1]["tools"] = ["x", "y"]
    report(hub, owner, machine, reordered)
    row = stored(hub, machine)
    assert row["grant_scope"] == before["grant_scope"]
    assert row["grant_scope_sha256"] == before["grant_scope_sha256"]
    assert row["binding_state"] == "owner_attested"
    assert row["binding_epoch"] == before["binding_epoch"]
    assert row["binding_scope_sha256"] == before["grant_scope_sha256"]


def test_five_key_confirm_success(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    machine = enroll(hub, owner)
    report(hub, owner, machine)
    epoch = stored(hub, machine)["binding_epoch"]
    body = confirm(hub, owner, machine)
    assert body == {
        "binding_state": "owner_attested",
        "verified": False,
        "note": "attested by the owner; not verified by Commonplace",
        "launch_shape": "uncaptured",
    }
    row = committed(hub, machine)
    assert row["binding_state"] == "owner_attested"
    assert row["binding_scope_sha256"] == DIGEST
    assert row["binding_epoch"] == epoch
    confirm(hub, owner, machine)
    assert stored(hub, machine)["binding_epoch"] == epoch


def test_legacy_confirm_transition(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    machine = enroll(hub, owner)
    attest(hub, owner, machine)
    report(hub, owner, machine)
    full = confirmation(hub, owner, machine)
    legacy = {k: full[k] for k in ("topics", "provider_instance_id", "worker_connection_id")}
    rejected(hub, owner, machine, legacy)
    status, body = post(hub, owner, machine, full)
    assert status == 200, (status, body)


def test_confirm_needs_a_report(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    machine = enroll(hub, owner)
    row = view(hub, owner, machine)
    assert "scope" in row, "owner view must show the scope, even when none was reported"
    assert row["scope"] is None
    rejected(
        hub,
        owner,
        machine,
        {
            "topics": row["topics"],
            "provider_instance_id": "worker",
            "worker_connection_id": row["worker_connection_id"],
            "grant_scope_sha256": DIGEST,
            "expected_scope_epoch": row["scope_epoch"],
        },
    )


def test_stale_confirm_no_write(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    machine = enroll(hub, owner)
    report(hub, owner, machine)
    rejected(
        hub, owner, machine, confirmation(hub, owner, machine, grant_scope_sha256="0" * 64), 409
    )
    good = confirmation(hub, owner, machine)
    rejected(
        hub,
        owner,
        machine,
        {**good, "expected_scope_epoch": good["expected_scope_epoch"] + 1},
        409,
    )
    report(hub, owner, machine, edited("/t3/projects", []))
    report(hub, owner, machine)
    assert view(hub, owner, machine)["scope"]["sha256"] == good["grant_scope_sha256"]
    rejected(hub, owner, machine, good, 409)
    fresh = confirmation(hub, owner, machine)
    for bad in (
        {"topics": ["other"]},
        {"worker_connection_id": "conn_0000000000000000"},
        {"provider_instance_id": "not-the-reported-provider"},
        {"provider_instance_id": "has space"},
        {"grant_scope_sha256": "NOT-HEX"},
        {"expected_scope_epoch": True},
        {"expected_scope_epoch": "1"},
    ):
        rejected(hub, owner, machine, {**fresh, **bad})


def test_confirm_recomputes_stored_digest(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    machine = enroll(hub, owner)
    report(hub, owner, machine)
    body = confirmation(hub, owner, machine)
    swapped = json.dumps(edited("/t3/projects", []), separators=(",", ":"), sort_keys=True)
    for stored_text, expected in ((swapped, 409), ("{not json", 400)):
        with hub.db._lock:
            hub.db._conn.execute(
                "UPDATE runner_machines SET grant_scope=? WHERE id=?",
                (stored_text, machine["machine_id"]),
            )
        rejected(hub, owner, machine, body, expected)


def test_provider_change_refused_reported_null(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    machine = bound_machine(hub, owner, edited("/provider_instance_id", None))
    payload = rejected(hub, owner, machine, confirmation(hub, owner, machine, "P3"), 409)
    assert payload == {"error": "provider_change_requires_reset"}


def test_same_provider_null_record_no_bump(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    machine = bound_machine(hub, owner, edited("/provider_instance_id", None))
    before = stored(hub, machine)
    confirm(hub, owner, machine)
    row = stored(hub, machine)
    assert row["binding_epoch"] == before["binding_epoch"]
    assert row["binding_provider_instance_id"] == "worker"


def test_reset_then_confirm_other_provider(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    machine = bound_machine(hub, owner, edited("/provider_instance_id", None))
    epoch = stored(hub, machine)["binding_epoch"]
    status, body = http(
        hub.port,
        "PATCH",
        f"/api/runners/{machine['machine_id']}",
        cookie=owner["cookie"],
        body={"topics": ["worker", "reviewer"]},
    )
    assert status == 200, (status, body)
    assert stored(hub, machine)["binding_state"] == "unbound"
    assert stored(hub, machine)["binding_epoch"] == epoch + 1
    assert stored(hub, machine)["binding_scope_sha256"] is None
    confirm(hub, owner, machine, "P3")
    row = stored(hub, machine)
    assert row["binding_provider_instance_id"] == "P3"
    assert row["binding_epoch"] == epoch + 1


def test_scope_columns_upgrade(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    machine = enroll(hub, owner)
    attest(hub, owner, machine)
    with hub.db._lock:
        fresh = {r[1] for r in hub.db._conn.execute("PRAGMA table_info(runner_machines)")}
    assert SCOPE_COLUMNS <= fresh
    legacy = {k: v for k, v in stored(hub, machine).items() if k not in SCOPE_COLUMNS}
    with hub.db._lock:
        for column in sorted(SCOPE_COLUMNS):
            hub.db._conn.execute(f"ALTER TABLE runner_machines DROP COLUMN {column}")
    for _ in range(2):
        registry.ensure(hub.db)
        row = stored(hub, machine)
        assert SCOPE_COLUMNS <= set(row)
        assert all(row[column] is None for column in SCOPE_COLUMNS)
        assert {k: row[k] for k in legacy} == legacy
    with hub.db._lock:
        info = {r[1]: r[3] for r in hub.db._conn.execute("PRAGMA table_info(runner_machines)")}
    assert all(info[column] == 0 for column in SCOPE_COLUMNS)


def test_owner_projection_and_isolation(signup_hub, owners, caplog):
    hub, owner = signup_hub, owners["A"]
    machine = enroll(hub, owner)
    row = view(hub, owner, machine)
    assert "scope" in row
    assert row["scope"] is None
    assert row["scope_epoch"] == row["binding_epoch"]
    sentinel = "scope-sentinel-value"
    record = edited("/provider_instance_id", sentinel)
    with caplog.at_level("DEBUG"):
        report(hub, owner, machine, record)
        confirm(hub, owner, machine, sentinel)
        number, _ = _queue(hub, owner, machine)
        status, leased = http(
            hub.port, "POST", "/api/runner/lease", token=machine["machine_token"], body={}
        )
        assert status == 200, (status, leased)
        status, jobs = http(
            hub.port, "GET", f"/api/card-starts?task={number}", cookie=owner["cookie"]
        )
        assert status == 200, (status, jobs)
    row = view(hub, owner, machine)
    canonical = json.loads(stored(hub, machine)["grant_scope"])
    assert row["scope"] == {
        "record": canonical,
        "sha256": stored(hub, machine)["grant_scope_sha256"],
        "reported_at": stored(hub, machine)["grant_scope_reported_at"],
        "source": "runner-local-owner-submitted",
        "verified": False,
    }
    assert row["scope_epoch"] == row["binding_epoch"]
    assert row["verified"] is False
    assert not SCOPE_COLUMNS & set(row)
    status, patched = http(
        hub.port,
        "PATCH",
        f"/api/runners/{machine['machine_id']}",
        cookie=owner["cookie"],
        body={"projects": ["demo", "extra"]},
    )
    assert status == 200, (status, patched)
    assert not SCOPE_COLUMNS & set(patched)
    # Only the signed-in owner of this workspace reaches the report.
    other = post(hub, owners["B"], machine, {"grant_scope": RECORD}, cookie=owners["B"]["cookie"])
    assert other[0] == 404, other
    joined = member(hub, owner, owners["B"])
    assert post(hub, joined, machine, {"grant_scope": RECORD}, cookie=joined["cookie"])[0] == 403
    assert http(hub.port, "GET", "/api/runners", cookie=joined["cookie"])[0] == 403
    assert post(hub, owner, machine, {"grant_scope": RECORD}, token=machine["worker_token"])[0] in (
        401,
        403,
    )
    for surface in (leased, jobs, audit(hub, owner)):
        assert sentinel not in json.dumps(surface)
    assert sentinel not in caplog.text
    # The owner view, including its PATCH response, is the one place the record is shown.
    assert sentinel in json.dumps(patched)
    assert sentinel in json.dumps(view(hub, owner, machine)["scope"])
