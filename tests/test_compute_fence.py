"""Fixture-only local cross-process ordering; parent owns test execution.

No servers, endpoints, devices or ambient credentials. Synthetic custody keys
come exclusively from execution_env. Remote helper channel installation remains
absent; assertions never treat a local fence or synthetic transport as deployment
proof. Multiprocess rendezvous uses pipes/events with bounded waits.
"""

import multiprocessing
import os
import sqlite3
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from commonplace import compute, sessions
from commonplace.boards.store import BoardStore
from commonplace.compute import ExecutionConfig, WorkspaceExecutionConfig
from commonplace.compute.control import StoreError
from commonplace.compute.credentials import CredentialStore
from commonplace.compute.fence import (
    LOCAL_DOMAINS,
    FenceDenied,
    _Coordinator,
    _guards,
    _held,
)
from commonplace.compute.protocol import GatewayHelper, ProtocolDenied, ReplayJournal
from commonplace.control_db import ControlDB
from commonplace.policy import Policy
from tests.test_compute_control import compute_env as compute_env, scope_payload
from tests.test_compute_executor import (
    execution_env as execution_env,
    prepare,
    run_row,
)

pytestmark = [
    pytest.mark.skipif(os.name != "posix", reason="local POSIX authority fence"),
    pytest.mark.usefixtures("compute_private_filesystem"),
]


def _gateway(db, workspace_config):
    root = Path(workspace_config.directory).parent
    return SimpleNamespace(
        db=db,
        supervisor=SimpleNamespace(workspace_dir=lambda workspace: root / workspace),
        workspace_dir=lambda workspace: str(root / workspace),
        add_route=lambda *args: None,
        add_prefix_route=lambda *args: None,
    )


@pytest.fixture
def local_state(execution_env, tmp_path):
    state = execution_env
    env = state.env
    state.blocker = env.service.task_add("Synthetic same-board blocker", board="compute")["number"]
    state.connection, _ = env.db.create_token(
        workspace_ref=env.workspace,
        user_ref=env.owner,
        name="fixture-writer",
        client="test",
        policy=Policy.build(scopes=["memory:read"]),
        actor=env.owner,
    )
    state.scope = env.store.create_scope(
        env.principal, **env.auth, **scope_payload(env, state.gateway["id"])
    )["id"]
    state.proposal = prepare(state)
    # Provision fixture files privately, preserving custody's stricter checks.
    for path in (env.path, env.path.parent / "boards.sqlite3"):
        path.chmod(0o600)
    directory = tmp_path / "authority"
    directory.mkdir(mode=0o700)
    public = state.service._provisioning_keys
    workspace = WorkspaceExecutionConfig(
        env.workspace, env.path.parent, state.key_path, public, enabled=True
    )
    state.config = ExecutionConfig(directory, (workspace,))
    # Exercise the same trusted startup path as ControlDB(compute_config=...).
    # The source fixture has already provisioned all its synthetic stores.
    compute._configure_control(env.db, state.config)
    env.gateway.add_route = lambda *args: None
    env.gateway.add_prefix_route = lambda *args: None
    compute.install(env.gateway)
    assert env.gateway._compute_execution_reasons[env.workspace] == "gateway_admission_unintegrated"
    state.fence = env.gateway._compute_fences[env.workspace]
    state.coordinator = env.db._compute_coordinator
    state.service = env.gateway._compute_services[env.workspace]
    env.store = state.service.store
    env.store.clock = lambda: env.clock.now
    state.driver.fence = state.fence
    yield state


def _mutate(db, gateway, store, action, data, config):
    if action == "session":
        sessions.end_session(db, data["cookie"])
    elif action in ("role", "user", "connection"):
        with db._lock, db._tx() as conn:
            if action == "role":
                conn.execute(
                    "UPDATE memberships SET role='member' WHERE workspace_id=? AND user_id=?",
                    (data["workspace"], data["owner"]),
                )
            elif action == "user":
                conn.execute(
                    "UPDATE users SET disabled_at='2026-01-01T00:00:00Z' WHERE id=?",
                    (data["owner"],),
                )
            else:
                conn.execute(
                    "UPDATE connections SET revoked_at='2026-01-01T00:00:00Z' WHERE id=?",
                    (data["connection"],),
                )
    elif action in ("task", "blocker", "assignment", "approval"):
        boards = BoardStore(config.workspaces[0].directory, create=False)
        with boards.transaction() as conn:
            if action == "task":
                conn.execute("UPDATE tasks SET status='done' WHERE id=?", (data["task"],))
            elif action == "assignment":
                conn.execute(
                    "UPDATE tasks SET assignee=? WHERE id=?",
                    ("user:" + data["member"], data["task"]),
                )
            elif action == "approval":
                conn.execute(
                    "UPDATE tasks SET proposal_status='pending' WHERE id=?", (data["task"],)
                )
            else:
                conn.execute(
                    "INSERT INTO task_blockers VALUES(?,?,?,?)",
                    (
                        data["task"],
                        data["blocker"],
                        "user:" + data["owner"],
                        "2026-01-01T00:00:00Z",
                    ),
                )
    elif action in ("binding", "scope", "consent"):
        with store._journal() as conn:
            if action == "binding":
                # Exact metadata binding change, not a SQL-text guess.
                conn.execute(
                    "UPDATE nodes SET metadata=json_set(metadata,'$.epoch',2) WHERE id=?",
                    (data["node"],),
                )
            elif action == "scope":
                conn.execute("UPDATE scopes SET revoked=1 WHERE id=?", (data["scope"],))
            else:
                conn.execute(
                    "UPDATE executions SET state='cancelled' WHERE id=?", (data["execution"],)
                )
    elif action in ("credential", "rotation"):
        with CredentialStore(
            Path(config.workspaces[0].directory) / "compute-credentials.sqlite3",
            config.workspaces[0].wrapping_key_path,
            config.workspaces[0].directory,
            lambda request, context: context == "synthetic-owner",
        ) as custody:
            if action == "rotation":
                # Real generate/activate/revoke paths, with synthetic keys only.
                current = data["credential"]
                with db._compute_coordinator.locked():
                    replacement = custody.generate(
                        replace(current.binding, version=2), "synthetic-owner"
                    )
                    custody.activate(
                        replacement.credential_id, replacement.binding, "synthetic-owner"
                    )
                    custody.revoke(current.credential_id, current.binding, "synthetic-owner")
                return
            custody.revoke(
                data["credential"].credential_id, data["credential"].binding, "synthetic-owner"
            )
    else:
        raise AssertionError("unknown fixture mutation")


def _child(control_path, config, action, data, channel):
    from tests._compute_filesystem import private_filesystem

    with private_filesystem(Path(control_path).parent):
        _child_with_filesystem(control_path, config, action, data, channel)


def _child_with_filesystem(control_path, config, action, data, channel):
    db = None
    try:
        db = ControlDB(control_path, compute_config=config)
        gateway = _gateway(db, config.workspaces[0])
        compute.install(gateway)
        service = gateway._compute_services[data["workspace"]]
        if "now" in data:
            service.store.clock = lambda: data["now"]
        channel.send("ready")
        command = channel.recv()
        if command != "go":
            return
        if action == "claim":
            claimed = service._claim()
            channel.send(("claimed", claimed[0].attempt if claimed else None))
        elif action == "crash":
            with service._guard():
                with db._lock, db._tx() as conn:
                    conn.execute(
                        "UPDATE users SET disabled_at='2026-11-10T00:00:00Z' WHERE id=?",
                        (data["owner"],),
                    )
                    channel.send("uncommitted")
                    os._exit(27)
        elif action == "crash_custody":
            with CredentialStore(
                Path(config.workspaces[0].directory) / "compute-credentials.sqlite3",
                config.workspaces[0].wrapping_key_path,
                config.workspaces[0].directory,
                lambda *args: True,
            ) as custody:
                with custody._transaction(write=True):
                    channel.send("uncommitted")
                    os._exit(28)
        else:
            _mutate(db, gateway, service.store, action, data, config)
            channel.send("committed")
    except Exception as error:
        channel.send(("error", type(error).__name__))
    finally:
        if db is not None:
            db.close()
        channel.close()


def _data(state):
    env = state.env
    return {
        "workspace": env.workspace,
        "owner": env.owner,
        "member": env.member,
        "cookie": env.cookie,
        "task": env.task,
        "blocker": state.blocker,
        "connection": state.connection,
        "scope": state.scope,
        "node": state.node["id"],
        "execution": state.proposal["id"],
        "credential": state.credentials[1],
        "now": env.clock.now,
    }


class Child:
    def __init__(self, state, action, **extra):
        context = multiprocessing.get_context("spawn")
        self.channel, child = context.Pipe()
        self.process = context.Process(
            target=_child,
            args=(state.env.db.path, state.config, action, {**_data(state), **extra}, child),
        )
        self.process.start()
        child.close()
        assert self.receive() == "ready"

    def receive(self):
        assert self.channel.poll(10), "fixture child failed to reply"
        return self.channel.recv()

    def go(self):
        self.channel.send("go")

    def close(self):
        # Bounded fixture cleanup; no shared/other worker processes are touched.
        if self.process.is_alive():
            try:
                self.channel.send("stop")
            except (BrokenPipeError, OSError):
                pass
        self.process.join(10)
        if self.process.is_alive():
            self.process.terminate()
            self.process.join(5)
            pytest.fail("fixture child exceeded bounded deadline")
        self.channel.close()


def test_local_domains_are_evidence_based_and_remote_execution_stays_off(local_state):
    state = local_state
    with state.fence.hold(state.env.workspace) as lease:
        assert lease.domains == LOCAL_DOMAINS
    with pytest.raises(FenceDenied):
        with _held(state.fence, state.env.workspace):
            pass
    assert not state.service.start()
    assert not state.service._run_once()
    assert state.service.last_reason == "gateway_admission_unintegrated"
    assert state.driver.calls == []
    assert run_row(state, state.proposal) is None
    inventory = state.env.store.inventory(state.env.principal, **state.env.auth)
    assert inventory["capabilities"]["transport"] is False
    assert not hasattr(state.env.store, "set_verified")
    assert not hasattr(state.env.store, "set_fence")


@pytest.mark.parametrize(
    "action",
    [
        "session",
        "role",
        "user",
        "task",
        "blocker",
        "assignment",
        "approval",
        "binding",
        "consent",
        "credential",
        "rotation",
    ],
)
def test_committed_revocation_before_local_claim_wins(local_state, action):
    child = Child(local_state, action)
    try:
        child.go()
        assert child.receive() == "committed"
        assert local_state.service._claim() is None
        assert run_row(local_state, local_state.proposal) is None
    finally:
        child.close()


@pytest.mark.parametrize(
    "action",
    [
        "session",
        "role",
        "user",
        "task",
        "assignment",
        "approval",
        "binding",
        "consent",
        "credential",
        "rotation",
        "connection",
        "scope",
        "blocker",
    ],
)
def test_central_writers_cannot_slip_between_fresh_check_and_claim(local_state, action):
    state = local_state
    child = Child(state, action)
    try:
        with state.service._guard() as lease:
            child.go()
            # Child has initialized its own actual DB/store before the lock.
            # It cannot commit while current reads and durable claim run.
            assert not child.channel.poll(0.1)
            claimed = state.service._claim()
            assert claimed is not None
            assert claimed[0].authority_epoch == lease.epoch
            assert not child.channel.poll(0)
        assert child.receive() == "committed"
        request = claimed[0]
        assert state.service._authorize(request, "connect") is False
        assert state.service._gateway_admission(request, "admit") is False
        assert run_row(state, state.proposal)["stage"] == 0
    finally:
        child.close()


def test_custody_revoke_invalidates_already_issued_local_permit_and_old_handle(local_state):
    state = local_state
    credential = state.credentials[1]
    handle = state.custody.acquire(credential.credential_id, credential.binding, state.context)
    claimed = state.service._claim()
    assert claimed
    child = Child(state, "credential")
    try:
        child.go()
        assert child.receive() == "committed"
        assert not state.service._authorize(claimed[0], "connect")
        from commonplace.compute.credentials import (
            CredentialError,
            internal_transport_private_bytes,
        )

        with pytest.raises(CredentialError):
            internal_transport_private_bytes(handle)
    finally:
        handle.close()
        child.close()


def test_gateway_current_callback_permit_is_once_and_never_holds_network_lock(local_state):
    state = local_state
    request, _, _ = state.service._claim()
    assert state.service._authorize(request, "connect")
    assert not state.fence.active
    assert state.service._gateway_admission(request, "ready")
    assert state.service._authorize(request, "dispatch")
    assert state.service._gateway_admission(request, "admit")
    assert not state.service._gateway_admission(request, "admit")
    # Network/effect belongs after callback return. A separate actual process
    # can revoke then; already-issued remote permits cannot be retracted here.
    assert not state.fence.active
    assert not state.env.db._lock._is_owned()
    child = Child(state, "session")
    try:
        child.go()
        assert child.receive() == "committed"
        assert not state.service._authorize(request, "target_auth")
        assert not state.service._gateway_admission(request, "admit")
        with state.env.store._journal() as conn:
            assert conn.execute("SELECT count(*) FROM gateway_admissions").fetchone()[0] == 1
    finally:
        child.close()


@pytest.mark.parametrize("revocation", ["before", "after", "never"])
def test_actual_helper_nonce_epoch_contract_has_no_duplicate_synthetic_effect(
    local_state, tmp_path, revocation
):
    state = local_state
    child = Child(state, "session") if revocation != "never" else None
    connection = sqlite3.connect(tmp_path / "fixture-helper.sqlite3", isolation_level=None)
    effects = []
    try:
        request, gateway, route = state.service._claim()
        journal = ReplayJournal(connection)

        def open_route(enrolled, timeout):
            assert not state.fence.active
            assert not state.env.db._lock._is_owned()
            assert journal.state(request) == "consumed"
            if revocation == "after":
                # Prove a real second process commits during this synthetic
                # network effect; no local authority lock spans it.
                child.go()
                assert child.receive() == "committed"
            effects.append(enrolled.node)
            return "synthetic-channel"

        helper = GatewayHelper(
            gateway,
            [route],
            journal,
            state.service._gateway_admission,
            open_route,
            binding=(request.workspace, request.gateway),
            enabled=True,
            wall=lambda: state.env.clock.now,
        )
        assert state.service._authorize(request, "connect")
        ready = helper.prepare(request)
        assert state.service._authorize(request, "dispatch")
        if revocation == "before":
            child.go()
            assert child.receive() == "committed"
            with pytest.raises(ProtocolDenied):
                helper.admit(request, ready, {**ready, "kind": "go"})
            assert effects == []
            assert journal.state(request) is None
        else:
            assert helper.admit(request, ready, {**ready, "kind": "go"}) == "synthetic-channel"
            assert effects == [route.node]
            # Changed/reused nonce cannot replay even without a revocation.
            with pytest.raises(ProtocolDenied):
                helper.admit(request, ready, {**ready, "kind": "go"})
            if revocation == "never":
                another_ready = helper.prepare(request)
                with pytest.raises(ProtocolDenied):
                    helper.admit(request, another_ready, {**another_ready, "kind": "go"})
            assert effects == [route.node]
    finally:
        connection.close()
        if child is not None:
            child.close()


def test_two_processes_claim_same_approval_once_and_restart_never_replays(local_state):
    state = local_state
    first, second = Child(state, "claim"), Child(state, "claim")
    try:
        first.go()
        second.go()
        outcomes = [first.receive(), second.receive()]
        assert all(item[0] == "claimed" for item in outcomes)
        assert sum(item[1] is not None for item in outcomes) == 1
        assert state.service._claim() is None
    finally:
        first.close()
        second.close()
    restarted = Child(state, "claim")
    try:
        restarted.go()
        assert restarted.receive() == ("claimed", None)
        assert run_row(state, state.proposal)["reserved"] == 1
    finally:
        restarted.close()


def test_cross_process_lock_timeout_is_bounded_and_does_not_poison_successor(local_state):
    state = local_state
    child = Child(state, "session")
    try:
        with state.fence.hold(state.env.workspace):
            child.go()
            result = child.receive()
            assert result == ("error", "FenceDenied")
        assert state.service._claim() is not None
        assert not state.fence.active
    finally:
        child.close()


@pytest.mark.parametrize("action,exitcode", [("crash", 27), ("crash_custody", 28)])
def test_crash_rollback_preserves_guards_and_durable_epoch(local_state, action, exitcode):
    state = local_state
    with state.fence.hold(state.env.workspace) as lease:
        before = lease.epoch
    child = Child(state, action)
    try:
        child.go()
        assert child.receive() == "uncommitted"
        child.process.join(10)
        assert child.process.exitcode == exitcode
        with state.fence.hold(state.env.workspace) as lease:
            assert lease.epoch > before
            after = lease.epoch
        # A new coordinator reading the same disk epoch is not a reset to 1.
        independent = _Coordinator(state.config.authority_directory)
        try:
            with independent.locked():
                assert independent.epoch() == after
        finally:
            independent.close()
        if action == "crash":
            assert state.env.db.user(state.env.owner)["disabled_at"] is None
        else:
            with sqlite3.connect(state.env.path.parent / "compute-credentials.sqlite3") as conn:
                with pytest.raises(sqlite3.IntegrityError, match="authority_writer_unavailable"):
                    conn.execute("UPDATE compute_credentials SET state='revoked'")
    finally:
        child.close()


@pytest.mark.parametrize("domain", ["control", "boards", "compute", "custody"])
def test_legacy_or_unconfigured_connections_cannot_write_guarded_authority(local_state, domain):
    state = local_state
    paths = {
        "control": state.env.db.path,
        "boards": state.env.path.parent / "boards.sqlite3",
        "compute": state.env.path,
        "custody": state.env.path.parent / "compute-credentials.sqlite3",
    }
    writes = {
        "control": ("UPDATE memberships SET role='member'", ()),
        "boards": ("UPDATE tasks SET assignee=NULL", ()),
        "compute": ("UPDATE nodes SET metadata=metadata", ()),
        "custody": ("UPDATE compute_credentials SET state='revoked'", ()),
    }
    with sqlite3.connect(paths[domain]) as conn:
        with pytest.raises(sqlite3.Error):
            conn.execute(*writes[domain])


def test_writer_outside_central_control_lock_is_denied_even_with_registered_function(local_state):
    with pytest.raises(sqlite3.Error):
        local_state.env.db._conn.execute("UPDATE users SET disabled_at='2026-11-10T00:00:00Z'")
    assert local_state.env.db.user(local_state.env.owner)["disabled_at"] is None


def test_missing_guard_unknown_workspace_and_new_control_plane_deny(local_state, tmp_path):
    state = local_state
    with pytest.raises(FenceDenied):
        with state.fence.hold("unknown-workspace"):
            pass
    # Missing guards are never silently re-created by an already-sealed process.
    name, _ = next(_guards("compute", state.coordinator.identity))
    with sqlite3.connect(state.env.path) as conn:
        conn.execute(f"DROP TRIGGER {name}")
    with pytest.raises(FenceDenied):
        with state.fence.hold(state.env.workspace):
            pass
    other = tmp_path / "other-authority"
    other.mkdir(mode=0o700)
    independent = _Coordinator(other)
    try:
        assert independent.identity != state.coordinator.identity
        # Existing guards bind the original authority plane, including restart.
        with independent.locked(), sqlite3.connect(state.env.path) as conn:
            independent._participants[str(state.env.path)] = ("compute", None)
            with pytest.raises(FenceDenied):
                independent.attach(conn, str(state.env.path), "compute")
    finally:
        independent.close()


def test_epoch_file_loss_is_not_silently_reinitialized(tmp_path):
    directory = tmp_path / "authority"
    directory.mkdir(mode=0o700)
    coordinator = _Coordinator(directory)
    coordinator.close()
    (directory / "authority.sqlite3").unlink()
    with pytest.raises(FenceDenied):
        _Coordinator(directory)


def test_nested_control_board_custody_order_has_no_inversion(local_state):
    state = local_state
    result = []

    def read():
        credential = state.credentials[0]
        with state.env.db._lock:
            with BoardStore(state.env.path.parent, create=False).connection() as conn:
                assert conn.execute("SELECT count(*) FROM tasks").fetchone()[0] >= 1
                handle = state.custody.acquire(
                    credential.credential_id, credential.binding, state.context
                )
                handle.close()
        result.append(True)

    thread = threading.Thread(target=read)
    thread.start()
    thread.join(5)
    assert not thread.is_alive()
    assert result == [True]
    assert not state.fence.active


@pytest.mark.parametrize("gate", ["plan", "blocked", "assigned", "origin", "proposal", "review"])
def test_exact_probe_consent_cannot_bypass_canonical_task_gates(local_state, gate):
    state = local_state
    env = state.env
    with BoardStore(env.path.parent, create=False).transaction() as conn:
        if gate == "plan":
            conn.execute("UPDATE tasks SET labels='[\"plan\"]' WHERE id=?", (env.task,))
        elif gate == "blocked":
            conn.execute(
                "INSERT INTO task_blockers VALUES(?,?,?,?)",
                (env.task, state.blocker, env.principal.actor, "2026-01-01T00:00:00Z"),
            )
        elif gate == "assigned":
            conn.execute("UPDATE tasks SET assignee=? WHERE id=?", ("user:" + env.member, env.task))
        elif gate == "origin":
            conn.execute(
                "UPDATE tasks SET restricted_origin=1,approved_by=NULL WHERE id=?", (env.task,)
            )
        elif gate == "proposal":
            conn.execute("UPDATE tasks SET proposal_status='pending' WHERE id=?", (env.task,))
        else:
            conn.execute(
                "UPDATE tasks SET status='review',ask='fixture decision' WHERE id=?", (env.task,)
            )
    with pytest.raises(StoreError, match="task_not_startable"):
        env.store._execution_task(env.principal, env.task)
    assert state.service._claim() is None


def test_default_install_is_lazy_and_missing_provisioning_never_creates_key(
    compute_env, monkeypatch, tmp_path
):
    env = compute_env
    gateway = _gateway(
        env.db, WorkspaceExecutionConfig(env.workspace, env.path.parent, tmp_path / "absent")
    )
    from commonplace.compute import credentials

    def forbidden(*args, **kwargs):
        raise AssertionError("ordinary management must not read or initialize a key")

    monkeypatch.setattr(credentials, "_read_key", forbidden)
    monkeypatch.setattr(credentials, "initialize_wrapping_key", forbidden)
    compute.install(gateway)
    assert not hasattr(gateway, "_compute_services")
    assert not (tmp_path / "absent").exists()


def test_missing_workspace_provisioning_is_disabled_without_key_initialization(
    compute_env, tmp_path
):
    env = compute_env
    directory = tmp_path / "authority"
    directory.mkdir(mode=0o700)
    workspace = WorkspaceExecutionConfig(
        env.workspace, env.path.parent, tmp_path / "missing.wrap", enabled=True
    )
    compute._configure_control(env.db, ExecutionConfig(directory, (workspace,)))
    gateway = _gateway(env.db, workspace)
    compute.install(gateway)
    assert gateway._compute_execution_reasons[env.workspace] == "execution_unavailable"
    assert gateway._compute_services == {}
    assert not workspace.wrapping_key_path.exists()


def test_explicit_startup_config_is_consumed_by_normal_gateway_extension(local_state):
    state = local_state
    assert state.env.gateway.compute_installed
    assert state.env.db._compute_services == [state.service]
    assert state.service._threads == []
    assert state.env.store._authority_fence is state.fence
    assert not state.env.db._closed
