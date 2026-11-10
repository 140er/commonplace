"""Real control/board/workspace stores; synthetic enrollment, no remote effects.

The integration maintainer runs these with the grouped suite. Fixtures never enroll live devices or
open custody; public SSH key bytes are deterministic non-secret test material.
"""

import base64
import hashlib
import json
import sqlite3
from dataclasses import replace
from types import SimpleNamespace

import pytest

from commonplace import events, sessions
from commonplace.boards.service import BoardService
from commonplace.compute import control
from commonplace.compute.control import Store, StoreError, open_store
from commonplace.compute.protocol import canonical
from commonplace.control_db import ControlDB
from commonplace.extensions import ToolContext
from commonplace.policy import Policy
from commonplace.projects import orchestration, reports


def public_pin(byte=1):
    algorithm = b"ssh-ed25519"
    raw = (
        len(algorithm).to_bytes(4, "big") + algorithm + (32).to_bytes(4, "big") + bytes([byte]) * 32
    )
    return {"algorithm": algorithm.decode(), "key": base64.b64encode(raw).decode()}


@pytest.fixture
def compute_env(tmp_path):
    db = ControlDB(str(tmp_path / "control.sqlite3"))
    sessions.ensure_schema(db)
    events.ensure_schema(db)
    orchestration.ensure_schema(db)
    reports.ensure_control(db)
    owner = db.create_user("owner@compute.test")
    member = db.create_user("member@compute.test")
    foreign = db.create_user("foreign@compute.test")
    workspace = db.create_workspace("local compute", owner)
    other_workspace = db.create_workspace("other compute", foreign)
    db.add_member(workspace, member, "member", actor=owner)
    root = tmp_path / "workspaces"
    gateway = SimpleNamespace(
        db=db,
        secure_cookies=False,
        supervisor=SimpleNamespace(root=root, workspace_dir=lambda wid: root / wid),
        workspace_dir=lambda wid: str(root / wid),
    )
    cookie = sessions.start_session(db, owner, workspace)
    principal = sessions.session_principal(db, cookie)
    service = BoardService(ToolContext(principal=principal, gateway=gateway))
    service.board_add("compute", wing="project_compute")
    task = service.task_add("Inspect synthetic host", board="compute")
    service.board_add("elsewhere", wing="project_compute")
    other_task = service.task_add("Another task", board="elsewhere")
    path = root / workspace / "compute.sqlite3"
    clock = SimpleNamespace(now=control.time.time())
    store = Store(path, gateway=gateway, workspace_id=workspace, clock=lambda: clock.now)
    env = SimpleNamespace(
        db=db,
        gateway=gateway,
        owner=owner,
        member=member,
        foreign=foreign,
        workspace=workspace,
        other_workspace=other_workspace,
        principal=principal,
        cookie=cookie,
        auth={"session_hash": sessions._hash(cookie)},
        store=store,
        path=path,
        service=service,
        task=task["number"],
        other_task=other_task["number"],
        clock=clock,
    )
    try:
        yield env
    finally:
        db.close()


def gateway_payload(**changes):
    return {
        "board": "compute",
        "name": "Synthetic gateway",
        "address": "192.0.2.10",
        "port": 22,
        "account": "compute",
        "host_key": public_pin(),
        "credential_id": "gateway-reference",
        "credential_version": 1,
        "pin_confirmed": True,
        **changes,
    }


def node_payload(gateway_id, **changes):
    return {
        "gateway_id": gateway_id,
        "name": "Synthetic node",
        "address": "10.20.0.2",
        "port": 22,
        "interface": "eth0",
        "account": "inspect",
        "host_key": public_pin(2),
        "credential_id": "node-reference",
        "credential_version": 1,
        "pin_confirmed": True,
        **changes,
    }


def enroll(env):
    gateway = env.store.create_gateway(env.principal, **env.auth, **gateway_payload())
    node = env.store.create_node(env.principal, **env.auth, **node_payload(gateway["id"]))
    return gateway, node


def scope_payload(env, gateway_id, **changes):
    return {
        "gateway_id": gateway_id,
        "task_number": env.task,
        "cidrs": ["10.20.0.0/24"],
        "interfaces": ["eth0"],
        "methods": ["passive"],
        "expires_at": env.clock.now + 60,
        "limits": {
            "hosts": 8,
            "concurrency": 2,
            "rate_per_second": 2,
            "duration_seconds": 10,
            "results": 8,
        },
        **changes,
    }


def propose(env, node, **changes):
    payload = {
        "node_id": node["id"],
        "task_number": env.task,
        "operation": "system.info.v1",
        "arguments": {},
        "request_id": "request-one",
        **changes,
    }
    return env.store.propose_execution(env.principal, **env.auth, **payload)


def approve(env, execution, **changes):
    payload = {"expected_digest": execution["digest"], "expires_at": env.clock.now + 60, **changes}
    return env.store.approve_execution(env.principal, execution["id"], **env.auth, **payload)


def denied(call, code="not_found", status=404):
    with pytest.raises(StoreError) as caught:
        call()
    assert (caught.value.code, caught.value.status) == (code, status)


def test_inventory_pending_enrollment_and_reopen_preserve_exact_public_records(compute_env):
    env = compute_env
    assert env.store.inventory(env.principal, **env.auth) == {
        "gateways": [],
        "nodes": [],
        "scopes": [],
        "executions": [],
        "capabilities": {"transport": False, "passive_discovery": False, "scripts": False},
        "can_manage": True,
    }
    gateway, node = enroll(env)
    assert gateway["state"] == node["state"] == "pending_verification"
    assert set(gateway) == {
        "id",
        "name",
        "board",
        "address",
        "port",
        "account",
        "host_key",
        "credential_id",
        "credential_version",
        "epoch",
        "state",
        "created_at",
    }
    assert set(node) == set(gateway) | {"gateway_id", "interface"}
    scope = env.store.create_scope(env.principal, **env.auth, **scope_payload(env, gateway["id"]))
    execution, created = propose(env, node)
    assert created and execution["state"] == "awaiting_approval"
    reopened = open_store(env.gateway, env.workspace)
    assert reopened.list_gateways(env.principal, **env.auth) == [gateway]
    assert reopened.list_nodes(env.principal, **env.auth) == [node]
    assert reopened.list_scopes(env.principal, **env.auth) == [scope]
    assert reopened.list_executions(env.principal, **env.auth) == [execution]
    assert not (env.path.parent / "compute-credentials.sqlite3").exists()
    assert not hasattr(reopened, "set_verified")


def test_all_store_methods_require_actual_current_owner_session(compute_env):
    env = compute_env
    gateway, node = enroll(env)
    execution, _ = propose(env, node)
    scope = env.store.create_scope(env.principal, **env.auth, **scope_payload(env, gateway["id"]))
    calls = (
        lambda p: env.store.inventory(p, **env.auth),
        lambda p: env.store.create_gateway(p, **env.auth, **gateway_payload()),
        lambda p: env.store.create_node(p, **env.auth, **node_payload(gateway["id"])),
        lambda p: env.store.create_scope(p, **env.auth, **scope_payload(env, gateway["id"])),
        lambda p: env.store.revoke_scope(p, scope["id"], **env.auth),
        lambda p: env.store.propose_execution(
            p,
            **env.auth,
            node_id=node["id"],
            task_number=env.task,
            operation="system.info.v1",
            arguments={},
            request_id="other",
        ),
        lambda p: env.store.approve_execution(
            p,
            execution["id"],
            **env.auth,
            expected_digest=execution["digest"],
            expires_at=env.clock.now + 60,
        ),
        lambda p: env.store.cancel_execution(p, execution["id"], **env.auth),
    )
    for invalid in (True, env.owner, replace(env.principal, person=False, auth="pat")):
        for call in calls:
            denied(lambda call=call, invalid=invalid: call(invalid), "owner_required", 403)
    # A stale owner Principal cannot preserve a removed role.
    env.db._conn.execute(
        "UPDATE memberships SET role='member' WHERE workspace_id=? AND user_id=?",
        (env.workspace, env.owner),
    )
    for call in calls:
        denied(lambda call=call: call(env.principal), "owner_required", 403)


@pytest.mark.parametrize(
    "change", ["session_revoked", "session_expired", "membership", "user_disabled"]
)
def test_authority_revocation_is_read_from_control_on_every_operation(compute_env, change):
    env = compute_env
    if change == "session_revoked":
        sessions.end_session(env.db, env.cookie)
    elif change == "session_expired":
        env.db._conn.execute("UPDATE sessions SET expires_at='2000-01-01T00:00:00Z'")
    elif change == "membership":
        env.db._conn.execute(
            "DELETE FROM memberships WHERE workspace_id=? AND user_id=?", (env.workspace, env.owner)
        )
    else:
        env.db._conn.execute(
            "UPDATE users SET disabled_at='2020-01-01T00:00:00Z' WHERE id=?", (env.owner,)
        )
    denied(
        lambda: env.store.create_gateway(env.principal, **env.auth, **gateway_payload()),
        "sign_in",
        401,
    )
    assert not env.path.exists()


def test_scope_context_and_current_agent_board_task_permission(compute_env):
    env = compute_env
    gateway, _ = enroll(env)
    scope = env.store.create_scope(env.principal, **env.auth, **scope_payload(env, gateway["id"]))
    assert scope["context"] == {
        "workspace": env.workspace,
        "project": "compute",
        "gateway": gateway["id"],
        "user": env.owner,
        "agent": env.principal.actor,
        "task": str(env.task),
    }
    connection, _ = env.db.create_token(
        workspace_ref=env.workspace,
        user_ref=env.owner,
        name="compute-agent",
        client="tests",
        policy=sessions.person_policy(),
        actor=env.owner,
    )
    bound = env.store.create_scope(
        env.principal, **env.auth, **scope_payload(env, gateway["id"], agent_id=connection)
    )
    assert bound["context"]["agent"] == connection
    env.db._conn.execute(
        "UPDATE connections SET revoked_at='2020-01-01T00:00:00Z' WHERE id=?", (connection,)
    )
    denied(
        lambda: env.store.create_scope(
            env.principal, **env.auth, **scope_payload(env, gateway["id"], agent_id=connection)
        )
    )
    denied(
        lambda: env.store.create_scope(
            env.principal,
            **env.auth,
            **scope_payload(env, gateway["id"], task_number=env.other_task),
        )
    )
    assert env.store.revoke_scope(env.principal, bound["id"], **env.auth)["revoked"] is True
    assert env.store.revoke_scope(env.principal, bound["id"], **env.auth)["revoked"] is True


def test_scope_rechecks_changed_agent_policy_and_rejects_foreign_live_connection(compute_env):
    env = compute_env
    gateway, _ = enroll(env)
    connection, _ = env.db.create_token(
        workspace_ref=env.workspace,
        user_ref=env.owner,
        name="scoped-agent",
        client="tests",
        policy=sessions.person_policy(),
        actor=env.owner,
    )
    env.store.create_scope(
        env.principal, **env.auth, **scope_payload(env, gateway["id"], agent_id=connection)
    )
    policy = Policy.build(
        profile="full",
        scopes=["memory:read", "memory:write"],
        read_wings=["project_other"],
        write_wings=["project_other"],
        trust="trusted",
    )
    env.db._conn.execute(
        "UPDATE connections SET policy=? WHERE id=?", (policy.to_json(), connection)
    )
    denied(
        lambda: env.store.create_scope(
            env.principal, **env.auth, **scope_payload(env, gateway["id"], agent_id=connection)
        )
    )
    foreign, _ = env.db.create_token(
        workspace_ref=env.other_workspace,
        user_ref=env.foreign,
        name="foreign-agent",
        client="tests",
        policy=sessions.person_policy(),
        actor=env.foreign,
    )
    denied(
        lambda: env.store.create_scope(
            env.principal, **env.auth, **scope_payload(env, gateway["id"], agent_id=foreign)
        )
    )


def test_public_collections_cannot_mutate_stored_scope_or_action_bindings(compute_env):
    env = compute_env
    gateway, node = enroll(env)
    payload = scope_payload(env, gateway["id"])
    scope = env.store.create_scope(env.principal, **env.auth, **payload)
    payload["cidrs"].append("192.168.0.0/16")
    scope["methods"].append("active")
    scope["limits"]["hosts"] = 4096
    stored = env.store.get_scope(env.principal, scope["id"], **env.auth)
    assert stored["cidrs"] == ["10.20.0.0/24"]
    assert stored["methods"] == ["passive"] and stored["limits"]["hosts"] == 8
    execution, _ = propose(env, node)
    second, _ = propose(env, node, request_id="second")
    assert second["digest"] != execution["digest"]
    execution["arguments"]["command"] = "id"
    assert env.store.get_execution(env.principal, execution["id"], **env.auth)["arguments"] == {}


@pytest.mark.parametrize(
    "changes",
    [
        {"cidrs": ["0.0.0.0/0"]},
        {"cidrs": ["10.20.0.1/24"]},
        {"methods": ["active"]},
        {"methods": ["passive", "active"]},
        {"interfaces": ["eth0;id"]},
        {"cidrs": []},
        {"expires_at": float("inf")},
        {"expires_at": float("nan")},
        {"expires_at": True},
        {"expires_at": 10**1000},
        {"limits": {"hosts": 0}},
    ],
)
def test_scopes_reject_widened_nonfinite_or_unbounded_values(compute_env, changes):
    env = compute_env
    gateway, _ = enroll(env)
    denied(
        lambda: env.store.create_scope(
            env.principal, **env.auth, **scope_payload(env, gateway["id"], **changes)
        ),
        "invalid_request",
        400,
    )
    assert env.store.list_scopes(env.principal, **env.auth) == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("hosts", 0),
        ("hosts", 4097),
        ("concurrency", True),
        ("rate_per_second", 101),
        ("duration_seconds", 301),
        ("results", 1.5),
    ],
)
def test_discovery_limits_use_existing_finite_domain_validator(compute_env, field, value):
    env = compute_env
    gateway, _ = enroll(env)
    payload = scope_payload(env, gateway["id"])
    payload["limits"][field] = value
    denied(
        lambda: env.store.create_scope(env.principal, **env.auth, **payload), "invalid_request", 400
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"address": "example.test"},
        {"address": "127.0.0.1"},
        {"port": True},
        {"credential_version": 0},
        {"pin_confirmed": False},
        {"pin_confirmed": 1},
        {"name": "x" * 101},
        {"host_key": {"algorithm": "ssh-ed25519", "key": "SHA256:display-only"}},
        {"host_key": {"algorithm": "ssh-rsa", "key": public_pin()["key"]}},
    ],
)
def test_enrollment_requires_numeric_route_full_matching_public_pin_and_explicit_confirmation(
    compute_env, changes
):
    env = compute_env
    denied(
        lambda: env.store.create_gateway(env.principal, **env.auth, **gateway_payload(**changes)),
        "invalid_request",
        400,
    )
    assert env.store.list_gateways(env.principal, **env.auth) == []


def test_target_must_be_private_and_cannot_reuse_gateway_endpoint(compute_env):
    env = compute_env
    gateway = env.store.create_gateway(
        env.principal, **env.auth, **gateway_payload(address="10.20.0.1")
    )
    for address in ("8.8.8.8", "::ffff:10.20.0.2", "fe80::1", "10.20.0.1", "host.test"):
        denied(
            lambda address=address: env.store.create_node(
                env.principal, **env.auth, **node_payload(gateway["id"], address=address)
            ),
            "invalid_request",
            400,
        )


def test_proposal_idempotency_is_durable_and_changed_payload_conflicts(compute_env):
    env = compute_env
    _, node = enroll(env)
    execution, created = propose(env, node)
    assert created
    assert propose(env, node) == (execution, False)
    reopened = open_store(env.gateway, env.workspace)
    assert reopened.propose_execution(
        env.principal,
        **env.auth,
        node_id=node["id"],
        task_number=env.task,
        operation="system.info.v1",
        arguments={},
        request_id="request-one",
    ) == (execution, False)
    other_node = env.store.create_node(
        env.principal, **env.auth, **node_payload(node["gateway_id"], address="10.20.0.3")
    )
    denied(lambda: propose(env, other_node), "conflict", 409)
    denied(lambda: propose(env, node, operation="shell"), "conflict", 409)
    denied(lambda: propose(env, node, arguments={"command": "id"}), "conflict", 409)
    for operation, arguments in (
        ("shell", {}),
        ("system.info.v1", {"command": "id"}),
        ("system.info.v1", []),
    ):
        denied(
            lambda operation=operation, arguments=arguments: propose(
                env, node, operation=operation, arguments=arguments, request_id="unsupported"
            ),
            "unsupported_operation",
            400,
        )
    with sqlite3.connect(env.path) as conn:
        assert conn.execute("SELECT count(*) FROM executions").fetchone()[0] == 1


def test_action_digest_binds_internal_session_and_approval_remains_inert(compute_env):
    env = compute_env
    _, node = enroll(env)
    execution, _ = propose(env, node)
    with sqlite3.connect(env.path) as conn:
        binding, digest = conn.execute("SELECT binding,digest FROM executions").fetchone()
        assert (
            hashlib.sha256(canonical(json.loads(binding))).hexdigest()
            == digest
            == execution["digest"]
        )
        assert json.loads(binding)["session_hash"] == env.auth["session_hash"]
    approved = approve(env, execution)
    assert approved["state"] == "approved"
    assert approved["reason"] == "transport_unavailable"
    serialized = json.dumps(approved)
    assert env.cookie not in serialized and env.auth["session_hash"] not in serialized
    assert set(approved) == {
        "id",
        "node_id",
        "gateway_id",
        "board",
        "task_number",
        "operation",
        "arguments",
        "digest",
        "state",
        "reason",
        "created_at",
        "updated_at",
        "limits",
    }
    with sqlite3.connect(env.path) as conn:
        assert conn.execute(
            "SELECT session_hash,approval_session_hash FROM executions"
        ).fetchone() == (env.auth["session_hash"], env.auth["session_hash"])
    denied(lambda: approve(env, execution), "conflict", 409)


@pytest.mark.parametrize(
    "table,field,value",
    [
        ("gateways", "epoch", 2),
        ("nodes", "epoch", 2),
        ("gateways", "host_key", public_pin(3)),
        ("nodes", "host_key", public_pin(4)),
        ("nodes", "credential_version", 2),
        ("gateways", "credential_id", "other-reference"),
        ("nodes", "address", "10.20.0.9"),
        ("nodes", "state", "verified"),
    ],
)
def test_approval_recomputes_current_enrollment_instead_of_trusting_digest(
    compute_env, table, field, value
):
    env = compute_env
    gateway, node = enroll(env)
    execution, _ = propose(env, node)
    object_id = gateway["id"] if table == "gateways" else node["id"]
    with sqlite3.connect(env.path) as conn:
        metadata = json.loads(
            conn.execute(f"SELECT metadata FROM {table} WHERE id=?", (object_id,)).fetchone()[0]
        )
        metadata[field] = value
        conn.execute(f"UPDATE {table} SET metadata=? WHERE id=?", (json.dumps(metadata), object_id))
    denied(lambda: approve(env, execution), "conflict", 409)
    assert (
        env.store.get_execution(env.principal, execution["id"], **env.auth)["state"]
        == "awaiting_approval"
    )


def test_changed_task_permission_invalidates_approval_but_progress_does_not(compute_env):
    env = compute_env
    _, node = enroll(env)
    execution, _ = propose(env, node)
    with env.service.store.connection() as conn:
        conn.execute("UPDATE tasks SET assignee=? WHERE id=?", ("user:" + env.member, env.task))
    denied(lambda: approve(env, execution), "conflict", 409)
    newer, _ = propose(env, node, request_id="request-two")
    with env.service.store.connection() as conn:
        conn.execute(
            "UPDATE tasks SET status_line='ordinary progress',version=version+1 WHERE id=?",
            (env.task,),
        )
    assert approve(env, newer)["state"] == "approved"


def test_original_session_revocation_denies_approval_from_new_session(compute_env):
    env = compute_env
    _, node = enroll(env)
    execution, _ = propose(env, node)
    sessions.end_session(env.db, env.cookie)
    replacement = sessions.start_session(env.db, env.owner, env.workspace)
    with pytest.raises(StoreError):
        env.store.approve_execution(
            env.principal,
            execution["id"],
            session_hash=sessions._hash(replacement),
            expected_digest=execution["digest"],
            expires_at=env.clock.now + 60,
        )
    with sqlite3.connect(env.path) as conn:
        assert conn.execute("SELECT state FROM executions").fetchone()[0] == "awaiting_approval"
    cancelled = env.store.cancel_execution(
        env.principal, execution["id"], session_hash=sessions._hash(replacement)
    )
    assert cancelled["state"] == "cancelled"


@pytest.mark.parametrize("expiry", [float("nan"), float("inf"), True, -1, 10**1000])
def test_approval_expiry_is_finite_and_bounded(compute_env, expiry):
    env = compute_env
    _, node = enroll(env)
    execution, _ = propose(env, node)
    denied(lambda expiry=expiry: approve(env, execution, expires_at=expiry), "invalid_request", 400)


def test_approval_exclusive_deadlines_and_stored_digest_tamper(compute_env):
    env = compute_env
    _, node = enroll(env)
    execution, _ = propose(env, node)
    for expiry in (env.clock.now, env.clock.now + control.MAX_APPROVAL_SECONDS + 1):
        denied(
            lambda expiry=expiry: approve(env, execution, expires_at=expiry), "invalid_request", 400
        )
    denied(lambda: approve(env, execution, expected_digest="0" * 64), "conflict", 409)
    with sqlite3.connect(env.path) as conn:
        conn.execute("UPDATE executions SET digest=?", ("0" * 64,))
    denied(lambda: approve(env, execution, expected_digest="0" * 64), "conflict", 409)
    env.clock.now += control.MAX_PROPOSAL_SECONDS
    denied(lambda: approve(env, execution), "conflict", 409)


@pytest.mark.parametrize("approved", [False, True])
def test_cancellation_is_durable_idempotent_and_never_claims_remote_termination(
    compute_env, approved
):
    env = compute_env
    _, node = enroll(env)
    execution, _ = propose(env, node)
    if approved:
        approve(env, execution)
    cancelled = env.store.cancel_execution(env.principal, execution["id"], **env.auth)
    assert cancelled["state"] == "cancelled" and cancelled["reason"] == "cancelled_locally"
    assert env.store.cancel_execution(env.principal, execution["id"], **env.auth) == cancelled
    assert (
        open_store(env.gateway, env.workspace).get_execution(
            env.principal, execution["id"], **env.auth
        )
        == cancelled
    )
    denied(lambda: approve(env, execution), "conflict", 409)
    assert propose(env, node) == (cancelled, False)


def test_foreign_ids_and_current_archived_board_are_concealed(compute_env):
    env = compute_env
    gateway, node = enroll(env)
    execution, _ = propose(env, node)
    cookie = sessions.start_session(env.db, env.foreign, env.other_workspace)
    principal = sessions.session_principal(env.db, cookie)
    foreign_store = open_store(env.gateway, env.other_workspace)
    for method, object_id in (
        (foreign_store.get_gateway, gateway["id"]),
        (foreign_store.get_node, node["id"]),
        (foreign_store.cancel_execution, execution["id"]),
    ):
        denied(
            lambda method=method, object_id=object_id: method(
                principal, object_id, session_hash=sessions._hash(cookie)
            )
        )
    with env.service.store.connection() as conn:
        conn.execute("UPDATE boards SET archived=1 WHERE slug='compute'")
    denied(lambda: env.store.get_execution(env.principal, execution["id"], **env.auth))
    denied(lambda: env.store.cancel_execution(env.principal, execution["id"], **env.auth))
    assert env.store.list_gateways(env.principal, **env.auth) == []


def test_transaction_failure_rolls_back_proposal_and_idempotency_key(compute_env):
    env = compute_env
    _, node = enroll(env)
    with sqlite3.connect(env.path) as conn:
        conn.execute(
            "CREATE TRIGGER fail_audit BEFORE INSERT ON audit WHEN NEW.action='execution.proposed' BEGIN SELECT RAISE(ABORT,'synthetic'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        propose(env, node)
    with sqlite3.connect(env.path) as conn:
        assert conn.execute("SELECT count(*) FROM executions").fetchone()[0] == 0
        conn.execute("DROP TRIGGER fail_audit")
    assert propose(env, node)[1] is True


def test_crypto_aware_opening_is_required_and_never_falls_back(compute_env, monkeypatch):
    env = compute_env
    from mempalace.sqlite_crypto import EncryptionError

    def encrypted_unavailable(*args, **kwargs):
        raise EncryptionError("synthetic locked workspace")

    monkeypatch.setattr(control, "connect", encrypted_unavailable)
    with pytest.raises(EncryptionError):
        env.store.inventory(env.principal, **env.auth)
    assert not env.path.exists()


def test_inventory_and_creation_capacity_are_bounded(compute_env, monkeypatch):
    env = compute_env
    monkeypatch.setattr(control, "MAX_GATEWAYS", 2)
    monkeypatch.setattr(control, "MAX_LIST", 1)
    env.store.create_gateway(env.principal, **env.auth, **gateway_payload(name="one"))
    env.store.create_gateway(env.principal, **env.auth, **gateway_payload(name="two"))
    denied(
        lambda: env.store.create_gateway(env.principal, **env.auth, **gateway_payload()),
        "conflict",
        409,
    )
    assert len(env.store.list_gateways(env.principal, **env.auth)) == 1
