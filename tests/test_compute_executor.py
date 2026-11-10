"""Real canonical control/custody with an in-memory synthetic SSH driver.

No endpoints, real devices, installed worker, production fence or release claims.
The fixture fence serializes participating *test* writers through SQLite; it says
nothing about unmodified application control/board/gateway writers. Parent runs
these tests in its grouped verification, not in an implementation worker.
"""

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from commonplace import sessions
from commonplace.compute.control import Store, StoreError
from commonplace.compute.credentials import (
    CredentialBinding,
    CredentialStore,
    initialize_wrapping_key,
)
from commonplace.compute.executor import MAX_RESULT_BYTES, EnrollmentReceipt, ExecutionService
from commonplace.compute.fence import REQUIRED_DOMAINS, AuthorityFence, AuthorityLease
from commonplace.compute.protocol import (
    ExecutionRequest,
    admission_message,
    canonical,
    decode_frame,
    encode_frame,
    enrollment_digest,
)
from commonplace.control_db import ControlDB
from tests.test_compute_control import (
    approve,
    compute_env as compute_env,
    enroll,
    propose,
    public_pin,
)
from tests.test_compute_transport import BytesChannel, Process, RemoteKey

pytestmark = pytest.mark.skipif(os.name != "posix", reason="protected POSIX custody")


class FixtureFence(AuthorityFence):
    """Only participating synthetic writers, never production revocation proof."""

    def __init__(self, path):
        self.path = path
        self.local = threading.local()
        self.domains = REQUIRED_DOMAINS
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS authority(epoch INTEGER)")
            if conn.execute("SELECT count(*) FROM authority").fetchone()[0] == 0:
                conn.execute("INSERT INTO authority VALUES (1)")

    @property
    def active(self):
        return getattr(self.local, "active", False)

    @contextmanager
    def hold(self, workspace):
        assert not self.active, "test lock order must not reenter"
        with sqlite3.connect(self.path, timeout=2, isolation_level=None) as conn:
            conn.execute("BEGIN IMMEDIATE")
            self.local.active = True
            try:
                epoch = conn.execute("SELECT epoch FROM authority").fetchone()[0]
                yield AuthorityLease(workspace, epoch, self.domains)
                conn.commit()
            finally:
                self.local.active = False

    def advance(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute("UPDATE authority SET epoch=epoch+1")


class Helper(BytesChannel):
    def __init__(self, driver):
        super().__init__()
        self.driver = driver
        self.messages = []

    def sendall(self, data):
        message = decode_frame(data)
        self.messages.append(message)
        if message["kind"] == "open_enrolled_ssh":
            request = ExecutionRequest.from_message(message)
            self.ready = admission_message("ready", request, "c" * 64, "a" * 64, request.start_by)
            self.request = request
            self.incoming.extend(encode_frame(self.ready))
        elif message["kind"] == "go":
            # Model durable admission, as the real helper adapter does. A
            # dropped open acknowledgement still cannot start target auth.
            assert self.driver.service._gateway_admission(self.request, "admit")
            self.driver.hit("go")
            if not self.driver.lost_open_ack:
                self.incoming.extend(encode_frame({**self.ready, "kind": "opened"}))
        super().sendall(data)


class SyntheticDriver:
    def __init__(self, env, fence):
        self.env, self.fence = env, fence
        self.calls = []
        self.hooks = {}
        self.public_keys = {}
        self.process = Process(b"Linux 6.1.0 x86_64\n")
        self.lost_open_ack = False
        self.helper_channel = Helper(self)

    def hit(self, name):
        # Transport operations never run under local authority/control locks.
        assert not self.fence.active
        assert not self.env.db._lock._is_owned()
        self.calls.append(name)
        if name in self.hooks:
            self.hooks[name]()

    def connect_gateway(self, gateway, timeout):
        self.hit("connect")
        with sqlite3.connect(self.env.path) as conn:
            # Independent connection sees committed consent consumption first.
            assert (
                conn.execute(
                    "SELECT count(*) FROM execution_runs WHERE stage=1 AND reserved=1"
                ).fetchone()[0]
                == 1
            )
        return BytesChannel()

    def handshake(self, channel, pin, hop, timeout):
        self.hit(hop + "_pin")
        pin.verify(RemoteKey(pin), hop)
        return BytesChannel()

    def authenticate(self, transport, account, key, timeout):
        self.hit("before_auth:" + account)
        raw = key._take_for_transport()
        private = serialization.load_ssh_private_key(raw, password=None)
        public = (
            private.public_key()
            .public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
            .decode()
        )
        assert public == self.public_keys[key.credential_id]
        del raw, private
        self.hit("auth:" + account)

    def helper(self, transport, timeout):
        self.hit("helper")
        return self.helper_channel

    def command(self, transport, command, timeout):
        assert command == "/usr/bin/uname -srm"
        self.hit("exec")
        return self.process


@pytest.fixture
def execution_env(compute_env, tmp_path, compute_private_filesystem):
    env = compute_env
    fence = FixtureFence(tmp_path / "fixture-fence.sqlite3")
    env.store = Store(
        env.path,
        gateway=env.gateway,
        workspace_id=env.workspace,
        clock=lambda: env.clock.now,
        authority_fence=fence,
    )
    gateway, node = enroll(env)
    env.path.parent.chmod(0o700)
    service_root = tmp_path / "synthetic-service"
    service_root.mkdir(mode=0o700)
    key_path = service_root / "fixture.wrap"
    custody_path = env.path.parent / "compute-credentials.sqlite3"
    # These keys are generated only in the isolated fixture, not operator keys.
    # Provision both synthetic service directories privately. The host umask is
    # group-writable; custody correctly refuses a writable ancestor even when
    # the immediate workspace directory is private.
    env.path.parent.parent.chmod(0o700)
    initialize_wrapping_key(key_path, env.path.parent, database_path=custody_path)
    context = object()
    custody = CredentialStore.initialize(
        custody_path,
        key_path,
        env.path.parent,
        lambda request, supplied: supplied is context,
        clock=lambda: env.clock.now,
    )
    credentials = []
    for purpose, record, account in (("gateway", gateway, "compute"), ("target", node, "inspect")):
        binding = CredentialBinding(
            env.workspace,
            env.owner,
            gateway["id"],
            "" if purpose == "gateway" else node["id"],
            account,
            purpose,
            1,
        )
        credential = custody.generate(binding, context, expires_at=env.clock.now + 600)
        custody.activate(credential.credential_id, binding, context)
        credentials.append(credential)
        table = "gateways" if purpose == "gateway" else "nodes"
        with env.store._journal() as conn:
            metadata = json.loads(
                conn.execute(
                    f"SELECT metadata FROM {table} WHERE id=?", (record["id"],)
                ).fetchone()[0]
            )
            metadata["credential_id"] = credential.credential_id
            conn.execute(
                f"UPDATE {table} SET metadata=? WHERE id=?",
                (canonical(metadata).decode(), record["id"]),
            )
    provisioner = Ed25519PrivateKey.generate()
    provisioner_public = provisioner.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    driver = SyntheticDriver(env, fence)
    driver.public_keys = {c.credential_id: c.public_key for c in credentials}
    service = ExecutionService(
        env.store,
        enabled=True,
        fence=fence,
        wrapping_key_path=key_path,
        provisioning_keys={"fixture-provisioner": provisioner_public},
        driver=driver,
    )
    driver.service = service
    state = SimpleNamespace(
        env=env,
        fence=fence,
        service=service,
        gateway=gateway,
        node=node,
        driver=driver,
        custody=custody,
        context=context,
        credentials=credentials,
        provisioner=provisioner,
        key_path=key_path,
    )
    try:
        yield state
    finally:
        service.stop()
        custody.close()


def receipt_for(state, **changes):
    env = state.env
    with env.store._journal() as conn:
        node, nm, gateway, gm = env.store._node(conn, env.principal, state.node["id"])
        digest = enrollment_digest(
            env.store._enrollment(gateway, gm), env.store._route(node, nm, gateway["id"])
        )
    receipt = EnrollmentReceipt(
        "fixture-provisioner",
        env.workspace,
        gateway["id"],
        node["id"],
        digest,
        env.clock.now,
        env.clock.now + 300,
        b"",
    )
    receipt = replace(receipt, **changes)
    return replace(receipt, signature=state.provisioner.sign(receipt.signed_bytes()))


def prepare(state, *, request_id="one", verified=True, approval=True):
    env = state.env
    if verified:
        state.service._accept_enrollment(
            env.principal, state.node["id"], **env.auth, receipt=receipt_for(state)
        )
    proposal, _ = propose(env, state.node, request_id=request_id)
    return approve(env, proposal) if approval else proposal


def view(state, proposal):
    env = state.env
    return env.store.get_execution(env.principal, proposal["id"], **env.auth)


def run_row(state, proposal):
    with sqlite3.connect(state.env.path) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            "SELECT * FROM execution_runs WHERE execution_id=?", (proposal["id"],)
        ).fetchone()


def mutate(state, change):
    env = state.env
    if change in ("origin_session", "approver_session"):
        cookie = env.cookie if change == "origin_session" else state.approver_cookie
        sessions.end_session(env.db, cookie)
    elif change == "session_expiry":
        env.db._conn.execute("UPDATE sessions SET expires_at='2000-01-01T00:00:00Z'")
    elif change == "membership":
        env.db._conn.execute(
            "UPDATE memberships SET role='member' WHERE workspace_id=?", (env.workspace,)
        )
    elif change == "task":
        with env.service.store.connection() as conn:
            conn.execute("UPDATE tasks SET assignee=? WHERE id=?", ("user:" + env.member, env.task))
    elif change in ("approval_expiry", "verification_expiry"):
        env.clock.now += 60 if change == "approval_expiry" else 300
    elif change == "fence_epoch":
        state.fence.advance()
    elif change == "credential_revocation":
        credential = state.credentials[1]
        state.custody.revoke(credential.credential_id, credential.binding, state.context)
    elif change == "verification_removed":
        with env.store._journal() as conn:
            conn.execute("DELETE FROM enrollment_verifications")
    else:
        table = "gateways" if change.startswith("gateway") else "nodes"
        field = "host_key" if change.endswith("pin") else "epoch"
        record = state.gateway if table == "gateways" else state.node
        with env.store._journal() as conn:
            metadata = json.loads(
                conn.execute(
                    f"SELECT metadata FROM {table} WHERE id=?", (record["id"],)
                ).fetchone()[0]
            )
            metadata[field] = public_pin(9) if field == "host_key" else 2
            conn.execute(
                f"UPDATE {table} SET metadata=? WHERE id=?",
                (canonical(metadata).decode(), record["id"]),
            )


def test_verified_approved_path_real_keys_results_inventory_reopen_without_task_completion(
    execution_env,
):
    state = execution_env
    proposal = prepare(state)
    assert state.service._run_once()
    row = view(state, proposal)
    assert row["state"] == "approved"  # consent remains distinct from remote facts
    assert row["run"]["phase"] == "terminal"
    assert row["run"]["certainty"] == "known"
    assert row["run"]["exit_code"] == 0
    assert row["run"]["result"]["output"] == "Linux 6.1.0 x86_64\n"
    assert row["run"]["capacity_reserved"] is False
    assert state.driver.calls.count("connect") == state.driver.calls.count("exec") == 1
    assert state.driver.calls.index("gateway_pin") < state.driver.calls.index("auth:compute")
    assert state.driver.calls.index("target_pin") < state.driver.calls.index("auth:inspect")
    reopened = Store(
        state.env.path,
        gateway=state.env.gateway,
        workspace_id=state.env.workspace,
        clock=lambda: state.env.clock.now,
        authority_fence=state.fence,
    )
    assert reopened.inventory(state.env.principal, **state.env.auth)["executions"] == [row]
    assert reopened.get_execution(state.env.principal, proposal["id"], **state.env.auth) == row
    with state.env.service.store.connection() as conn:
        assert (
            conn.execute("SELECT status FROM tasks WHERE id=?", (state.env.task,)).fetchone()[0]
            == "todo"
        )
    public = json.dumps(row)
    assert "session_hash" not in public and state.env.auth["session_hash"] not in public
    assert "worker_id" not in public and "BEGIN OPENSSH PRIVATE KEY" not in public
    assert not state.service._run_once()


def test_disabled_is_lazy_and_does_no_path_key_or_network_work(compute_env, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("disabled service must not perform I/O")

    monkeypatch.setattr(CredentialStore, "__init__", forbidden)
    store = Store(forbidden, gateway=compute_env.gateway, workspace_id=compute_env.workspace)
    service = ExecutionService(store, wrapping_key_path="unread-operator-path")
    assert not service.start()
    assert not service._run_once()
    assert service.stop()
    assert not compute_env.path.exists()


@pytest.mark.parametrize("kind", ["missing", "boolean", "unbound_store", "unparticipating"])
def test_missing_or_unparticipating_fence_never_opens_custody(execution_env, monkeypatch, kind):
    state = execution_env
    prepare(state)
    if kind == "missing":
        state.service.fence = None
    elif kind == "boolean":
        state.service.fence = True
        state.env.store._authority_fence = True
    elif kind == "unbound_store":
        state.env.store._authority_fence = None
    else:
        state.fence.domains = REQUIRED_DOMAINS - {"boards"}
    monkeypatch.setattr(CredentialStore, "__init__", lambda *a, **k: pytest.fail("custody opened"))
    assert not state.service.start()
    assert not state.service._run_once()
    assert state.driver.calls == []


@pytest.mark.parametrize("kind", ["boolean", "bad_signature", "foreign", "expired", "untrusted"])
def test_provisioning_requires_independent_exact_signature_not_owner_boolean(execution_env, kind):
    state = execution_env
    receipt = receipt_for(state)
    if kind == "boolean":
        receipt = True
    elif kind == "bad_signature":
        receipt = replace(receipt, signature=b"x" * 64)
    elif kind == "foreign":
        receipt = receipt_for(state, node="foreign-node")
    elif kind == "expired":
        receipt = receipt_for(
            state, verified_at=state.env.clock.now - 2, expires_at=state.env.clock.now
        )
    else:
        receipt = receipt_for(state, key_id="untrusted")
    with pytest.raises(StoreError, match="verification_unavailable"):
        state.service._accept_enrollment(
            state.env.principal, state.node["id"], **state.env.auth, receipt=receipt
        )
    assert (
        state.env.store.get_node(state.env.principal, state.node["id"], **state.env.auth)["state"]
        == "pending_verification"
    )
    assert not hasattr(state.env.store, "dispatch")
    assert not hasattr(state.env.store, "verify_enrollment")


@pytest.mark.parametrize("kind", ["pending", "metadata_boolean", "no_approval"])
def test_unverified_records_and_unsigned_metadata_never_dispatch(execution_env, kind):
    state = execution_env
    if kind == "metadata_boolean":
        with state.env.store._journal() as conn:
            for table in ("gateways", "nodes"):
                row = conn.execute(f"SELECT * FROM {table}").fetchone()
                metadata = json.loads(row["metadata"])
                metadata["state"] = "verified"
                conn.execute(f"UPDATE {table} SET metadata=?", (canonical(metadata).decode(),))
    prepare(state, verified=kind == "no_approval", approval=kind != "no_approval")
    assert not state.service._run_once()
    assert state.driver.calls == []


def test_verification_after_proposal_invalidates_previous_consent(execution_env):
    state = execution_env
    proposal = prepare(state, verified=False, approval=False)
    state.service._accept_enrollment(
        state.env.principal, state.node["id"], **state.env.auth, receipt=receipt_for(state)
    )
    with pytest.raises(StoreError, match="conflict"):
        approve(state.env, proposal)
    assert state.driver.calls == []


@pytest.mark.parametrize(
    "change",
    [
        "origin_session",
        "approver_session",
        "session_expiry",
        "membership",
        "task",
        "approval_expiry",
        "gateway_pin",
        "node_pin",
        "gateway_epoch",
        "node_epoch",
        "verification_removed",
    ],
)
def test_stale_authority_before_claim_makes_zero_connections(execution_env, change):
    state = execution_env
    proposal = prepare(state, approval=False)
    state.approver_cookie = sessions.start_session(
        state.env.db, state.env.owner, state.env.workspace
    )
    state.env.store.approve_execution(
        state.env.principal,
        proposal["id"],
        session_hash=sessions._hash(state.approver_cookie),
        expected_digest=proposal["digest"],
        expires_at=state.env.clock.now + 60,
    )
    mutate(state, change)
    assert not state.service._run_once()
    assert state.driver.calls == []
    assert run_row(state, proposal) is None


@pytest.mark.parametrize(
    "boundary,forbidden",
    [
        ("before_custody", "connect"),
        ("helper", "go"),
        ("target_pin", "auth:inspect"),
        ("auth:inspect", "exec"),
    ],
)
@pytest.mark.parametrize(
    "change", ["origin_session", "node_pin", "node_epoch", "task", "fence_epoch", "approval_expiry"]
)
def test_every_admission_stage_rechecks_live_binding(
    execution_env, boundary, forbidden, change, monkeypatch
):
    state = execution_env
    proposal = prepare(state)
    if boundary == "before_custody":
        original = state.service._custody

        def before(*args):
            mutate(state, change)
            return original(*args)

        monkeypatch.setattr(state.service, "_custody", before)
    else:
        state.driver.hooks[boundary] = lambda: mutate(state, change)
    assert state.service._run_once()
    assert forbidden not in state.driver.calls
    assert run_row(state, proposal)["exit_code"] is None
    assert not state.service._run_once()


def test_revoked_custody_handle_is_checked_again_at_actual_target_auth(execution_env):
    state = execution_env
    proposal = prepare(state)
    state.driver.hooks["target_pin"] = lambda: mutate(state, "credential_revocation")
    assert state.service._run_once()
    assert "auth:compute" in state.driver.calls
    assert "auth:inspect" not in state.driver.calls
    assert "exec" not in state.driver.calls
    assert view(state, proposal)["run"]["capacity_reserved"]


def test_double_claim_uses_independent_connections_and_one_durable_attempt(execution_env, tmp_path):
    state = execution_env
    proposal = prepare(state)
    second_control = ControlDB(str(tmp_path / "control.sqlite3"))
    second_gateway = SimpleNamespace(**vars(state.env.gateway))
    second_gateway.db = second_control
    second_store = Store(
        state.env.path,
        gateway=second_gateway,
        workspace_id=state.env.workspace,
        clock=lambda: state.env.clock.now,
        authority_fence=state.fence,
    )
    second = ExecutionService(second_store, enabled=True, fence=state.fence)
    barrier = threading.Barrier(2)
    claims, errors = [], []

    def claim(service):
        try:
            barrier.wait(timeout=3)
            claims.append(service._claim())
        except BaseException as exc:
            errors.append(type(exc).__name__)

    threads = [
        threading.Thread(target=claim, args=(service,)) for service in (state.service, second)
    ]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        assert all(not thread.is_alive() for thread in threads)
        assert not errors
        assert sum(claimed is not None for claimed in claims) == 1
        assert run_row(state, proposal)["phase"] == "admitted"
        assert state.driver.calls == []
    finally:
        second_control.close()


@pytest.mark.parametrize("crash_stage", [0, 1, 2, 3, 4])
def test_crash_reopen_never_reexecutes_and_retains_only_dispatched_capacity(
    execution_env, crash_stage
):
    state = execution_env
    proposal = prepare(state)
    request, _, _ = state.service._claim()
    for phase in list(("connect", "dispatch", "target_auth", "execute"))[:crash_stage]:
        assert state.service._authorize(request, phase)
    state.env.clock.now = request.run_until
    reopened = ExecutionService(state.env.store, enabled=True, fence=state.fence)
    assert not reopened._run_once()
    run = view(state, proposal)["run"]
    if crash_stage == 0:
        assert run["phase"] == "terminal" and not run["capacity_reserved"]
        assert run["certainty"] == "known" and run["reason"] == "interrupted_before_start"
    else:
        assert run["phase"] == "uncertain" and run["capacity_reserved"]
        assert run["certainty"] == "unknown" and run["reason"] == "interrupted"
    assert state.driver.calls == []


def test_lost_open_ack_before_target_auth_settles_without_reexecution(execution_env):
    state = execution_env
    proposal = prepare(state)
    state.driver.lost_open_ack = True
    assert state.service._run_once()
    assert view(state, proposal)["run"]["phase"] == "terminal"
    assert not view(state, proposal)["run"]["capacity_reserved"]
    assert state.driver.calls.count("connect") == 1
    assert "exec" not in state.driver.calls
    assert not state.service._run_once()


def test_uncertain_capacity_never_expires_into_free_slots(execution_env):
    state = execution_env
    for number in range(2):
        prepare(state, request_id=str(number))
        request, _, _ = state.service._claim()
        for phase in ("connect", "dispatch"):
            assert state.service._authorize(request, phase)
        assert state.service._gateway_admission(request, "admit")
        assert state.service._authorize(request, "target_auth")
    state.env.clock.now += 31
    proposal = prepare(state, request_id="third")
    assert not state.service._run_once()
    assert run_row(state, proposal) is None
    with state.env.store._journal() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM execution_runs WHERE reserved=1 AND phase='uncertain'"
            ).fetchone()[0]
            == 2
        )


def test_cancel_before_claim_preserves_existing_public_semantics(execution_env):
    state = execution_env
    proposal = prepare(state)
    cancelled = state.env.store.cancel_execution(
        state.env.principal, proposal["id"], **state.env.auth
    )
    assert cancelled["state"] == "cancelled" and "run" not in cancelled
    assert not state.service._run_once()
    assert state.driver.calls == []


def test_cancel_claim_before_first_effect_is_confirmed_and_idempotent(execution_env):
    state = execution_env
    proposal = prepare(state)
    request, _, _ = state.service._claim()
    cancelled = state.env.store.cancel_execution(
        state.env.principal, proposal["id"], **state.env.auth
    )
    assert cancelled["run"]["reason"] == "cancelled_before_start"
    assert cancelled["run"]["certainty"] == "known"
    assert not cancelled["run"]["capacity_reserved"]
    assert not state.service._authorize(request, "connect")
    assert (
        state.env.store.cancel_execution(state.env.principal, proposal["id"], **state.env.auth)
        == cancelled
    )


def test_cancel_after_exec_is_intent_without_observed_termination(execution_env):
    state = execution_env
    proposal = prepare(state)
    state.driver.hooks["exec"] = lambda: state.env.store.cancel_execution(
        state.env.principal, proposal["id"], **state.env.auth
    )
    assert state.service._run_once()
    run = view(state, proposal)["run"]
    assert run["cancel_requested_at"] is not None
    assert run["phase"] == "uncertain" and run["capacity_reserved"]
    assert run["exit_code"] is None and run["certainty"] == "unknown"


def test_late_cancel_preserves_observed_exit(execution_env):
    state = execution_env
    proposal = prepare(state)
    assert state.service._run_once()
    row = state.env.store.cancel_execution(state.env.principal, proposal["id"], **state.env.auth)
    assert row["run"]["exit_code"] == 0 and row["run"]["reason"] == "exit_observed"
    assert row["run"]["cancel_requested_at"] is not None


@pytest.mark.parametrize("boundary,known", [("helper", True), ("go", True)])
def test_cancel_around_helper_admission_does_not_claim_remote_kill(execution_env, boundary, known):
    state = execution_env
    proposal = prepare(state)
    state.driver.hooks[boundary] = lambda: state.env.store.cancel_execution(
        state.env.principal, proposal["id"], **state.env.auth
    )
    assert state.service._run_once()
    run = view(state, proposal)["run"]
    assert run["cancel_requested_at"] is not None
    assert run["certainty"] == ("known" if known else "unknown")
    assert run["capacity_reserved"] is not known
    assert "exec" not in state.driver.calls


def test_background_worker_releases_authority_while_transport_blocks(execution_env, monkeypatch):
    state = execution_env
    proposal = prepare(state)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def blocked():
        entered.set()
        assert release.wait(4)

    original_finish = state.service._finish

    def finish(*args):
        try:
            return original_finish(*args)
        finally:
            finished.set()

    state.driver.hooks["connect"] = blocked
    monkeypatch.setattr(state.service, "_finish", finish)
    try:
        assert state.service.start()
        assert entered.wait(3)
        # Actual owner/canonical metadata access completes during network work.
        assert view(state, proposal)["run"]["phase"] == "dispatching"
        release.set()
        assert finished.wait(4)
        assert view(state, proposal)["run"]["exit_code"] == 0
    finally:
        release.set()
        assert state.service.stop(timeout=5)


def test_unknown_exec_ack_is_not_replayed_or_logged(execution_env):
    state = execution_env
    proposal = prepare(state)

    def lost():
        raise OSError("synthetic-exception-canary")

    state.driver.hooks["exec"] = lost
    assert state.service._run_once()
    row = view(state, proposal)
    assert row["run"]["phase"] == "uncertain"
    assert row["run"]["capacity_reserved"]
    assert "synthetic-exception-canary" not in json.dumps(dict(run_row(state, proposal)))
    assert not state.service._run_once()
    assert state.driver.calls.count("exec") == 1


@pytest.mark.parametrize(
    "output",
    [
        b"password=synthetic-secret-canary\n",
        b"-----BEGIN OPENSSH PRIVATE KEY-----\nsynthetic-private-canary\n",
        b"x" * 70000,
        b"binary\xffoutput",
    ],
    ids=["token", "private-key", "oversized", "binary"],
)
def test_bounded_redaction_before_workspace_persistence_and_projection(execution_env, output):
    state = execution_env
    proposal = prepare(state)
    state.driver.process = Process(output)
    assert state.service._run_once()
    result = view(state, proposal)["run"]["result"]
    serialized = json.dumps(result)
    assert len(canonical(result)) <= MAX_RESULT_BYTES
    assert "synthetic-secret-canary" not in serialized
    assert "synthetic-private-canary" not in serialized
    with sqlite3.connect(state.env.path) as conn:
        values = " ".join(str(row) for row in conn.execute("SELECT * FROM execution_runs"))
        audit = " ".join(str(row) for row in conn.execute("SELECT * FROM audit"))
    assert "synthetic-secret-canary" not in values
    assert "synthetic-private-canary" not in values
    assert "output" not in audit


def test_output_redaction_sees_tokens_split_across_transport_chunks(execution_env):
    state = execution_env
    proposal = prepare(state)
    state.driver.process = Process(b"password=synthetic-secret-canary\n")
    state.driver.process.fragment = 3
    assert state.service._run_once()
    result = json.dumps(view(state, proposal)["run"]["result"])
    assert "synthetic-secret-canary" not in result
    assert "[redacted:assignment]" in result


def test_verification_expiry_is_checked_with_still_valid_approval(execution_env):
    state = execution_env
    state.service._accept_enrollment(
        state.env.principal,
        state.node["id"],
        **state.env.auth,
        receipt=receipt_for(state, expires_at=state.env.clock.now + 10),
    )
    proposal = prepare(state, verified=False)
    state.env.clock.now += 10
    assert not state.service._run_once()
    assert run_row(state, proposal) is None
    assert state.driver.calls == []


def test_missing_custody_never_regenerates_keys_or_connects(execution_env):
    state = execution_env
    proposal = prepare(state)
    state.key_path.unlink()  # only this test's generated fixture wrapping key
    assert state.service._run_once()
    assert not state.key_path.exists()
    assert state.driver.calls == []
    assert view(state, proposal)["run"]["certainty"] == "known"


def test_consumed_approval_cannot_be_rebound_to_another_valid_session(execution_env):
    state = execution_env
    proposal = prepare(state)
    request, _, _ = state.service._claim()
    replacement = sessions.start_session(state.env.db, state.env.owner, state.env.workspace)
    with state.env.store._journal() as conn:
        conn.execute(
            "UPDATE executions SET approval_session_hash=? WHERE id=?",
            (sessions._hash(replacement), proposal["id"]),
        )
    assert not state.service._authorize(request, "connect")
    assert state.driver.calls == []


def test_claim_tombstones_cannot_be_deleted_or_rebound(execution_env):
    state = execution_env
    proposal = prepare(state)
    state.service._claim()
    with sqlite3.connect(state.env.path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM execution_runs")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE execution_runs SET worker_id='different-worker'")
    assert run_row(state, proposal) is not None


def test_results_require_current_owner_and_canonical_task_audience(execution_env):
    state = execution_env
    proposal = prepare(state)
    state.service._run_once()
    member_cookie = sessions.start_session(state.env.db, state.env.member, state.env.workspace)
    member = sessions.session_principal(state.env.db, member_cookie)
    with pytest.raises(StoreError, match="owner_required"):
        state.env.store.get_execution(
            member, proposal["id"], session_hash=sessions._hash(member_cookie)
        )
    with state.env.service.store.connection() as conn:
        other_board = conn.execute("SELECT id FROM boards WHERE slug='elsewhere'").fetchone()[0]
        conn.execute("UPDATE tasks SET board_id=? WHERE id=?", (other_board, state.env.task))
    with pytest.raises(StoreError, match="not_found"):
        view(state, proposal)


def test_publication_rechecks_origin_and_withholds_text_after_revoke(execution_env):
    state = execution_env
    proposal = prepare(state)
    state.driver.hooks["exec"] = lambda: sessions.end_session(state.env.db, state.env.cookie)
    assert state.service._run_once()
    run = run_row(state, proposal)
    assert json.loads(run["result"]) == {}
    assert run["exit_code"] == 0  # exact-attempt exit evidence still settles capacity
    assert not run["reserved"]
    replacement = sessions.start_session(state.env.db, state.env.owner, state.env.workspace)
    assert (
        state.env.store.get_execution(
            state.env.principal, proposal["id"], session_hash=sessions._hash(replacement)
        )["run"]["result"]
        == {}
    )
    with pytest.raises(StoreError, match="sign_in"):
        view(state, proposal)


def test_enabled_executor_ui_approval_authorizes_automatic_background_execution(execution_env):
    import shutil
    import subprocess
    from pathlib import Path

    from tests.test_compute_api import API

    state, env = execution_env, execution_env.env
    proposal = prepare(state, approval=False)
    env.gateway._compute_services = {env.workspace: state.service}
    executed = threading.Event()
    state.driver.hooks["exec"] = executed.set
    api = API(env)
    assert api.request()[1]["capabilities"]["transport"] is False
    try:
        assert state.service.start()
        status, inventory = api.request()
        assert status == 200 and inventory["capabilities"]["transport"] is True
        node = shutil.which("node")
        assert node, "Node is required for the enabled-executor UI check"
        result = subprocess.run(
            [node, str(Path(__file__).with_name("compute_ui.mjs")), "enabled-executor"],
            input=json.dumps({"inventory": inventory, "now": env.clock.now}),
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert not state.driver.calls
        approval = json.loads(result.stdout.splitlines()[0])
        assert approval["expected_digest"] == proposal["digest"]
        assert api.request("POST", f"/executions/{proposal['id']}/approve", approval)[0] == 200
        assert executed.wait(5), "approved proposal did not reach the automatic worker"
        assert "connect" in state.driver.calls and "exec" in state.driver.calls
    finally:
        assert state.service.stop(timeout=5)
    assert api.request()[1]["capabilities"]["transport"] is False
