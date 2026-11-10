"""Durable real authority/custody with synthetic outer SSH and cache bytes only.

No live endpoints, current host caches, operator keys or production participation
claims. Parent runs these tests in its grouped verification; builder runs lint only.
"""

import json
import multiprocessing
import os
import socket
import sqlite3
import subprocess
from dataclasses import replace
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from commonplace import sessions
from commonplace.compute import discovery_store
from commonplace.compute.control import Store, StoreError
from commonplace.compute.credentials import (
    CredentialBinding,
    CredentialStore,
    initialize_wrapping_key,
)
from commonplace.compute.discovery import CacheSnapshot, OSCacheSupplier, PassiveCollector
from commonplace.compute.discovery_service import (
    DiscoveryPolicy,
    DiscoveryService,
    GatewayDiscoveryReceipt,
    configure,
)
from commonplace.compute.discovery_store import digest, gateway_document, open_store
from commonplace.compute.protocol import (
    GatewayHelper,
    Limits,
    PassiveDiscoveryRequest,
    ProtocolDenied,
    ReplayJournal,
    canonical,
    decode_frame,
    encode_frame,
)
from commonplace.compute.scopes import DiscoveryLimits
from commonplace.control_db import ControlDB
from tests.test_compute_control import compute_env as compute_env, gateway_payload, scope_payload
from tests.test_compute_executor import FixtureFence
from tests.test_compute_transport import BytesChannel, RemoteKey

pytestmark = pytest.mark.skipif(os.name != "posix", reason="protected POSIX custody")


class SyntheticCache:
    sources = frozenset({"linux_arp", "ip_neigh"})

    def __init__(self, state):
        self.state = state

    def read(self, source, *, max_bytes, max_lines, checkpoint):
        checkpoint()
        if source == "linux_arp":
            data = b"10.20.0.2 0x1 0x2 02:00:00:00:00:02 * eth0\n"
        else:
            data = b"10.20.0.2 dev eth0 lladdr 02:00:00:00:00:02 STALE\n"
        return CacheSnapshot(source, data, self.state.env.clock.now)


class SyntheticHelper(BytesChannel):
    def __init__(self, driver):
        super().__init__()
        self.driver = driver

    def sendall(self, data):
        driver, state = self.driver, self.driver.state
        request = PassiveDiscoveryRequest.from_message(decode_frame(data))
        driver.hit("collect")
        with state.store._journal() as conn:
            assert (
                conn.execute(
                    "SELECT state FROM discoveries WHERE id=?", (request.discovery,)
                ).fetchone()[0]
                == "dispatching"
            )
        if driver.lost_response:
            return
        scope = state.scope_object()
        outer = state.outer()
        collector = PassiveCollector(SyntheticCache(state), wall=lambda: state.env.clock.now)
        with sqlite3.connect(":memory:", isolation_level=None) as conn:
            helper = GatewayHelper(
                outer,
                [],
                ReplayJournal(conn),
                lambda request, phase: True,
                lambda *args: pytest.fail("passive helper opened a target"),
                binding=(outer.workspace, outer.gateway),
                limits=Limits(),
                enabled=True,
                wall=lambda: state.env.clock.now,
                passive_collector=collector,
                passive_ceiling=scope,
                passive_scope=lambda request: state.scope_object(),
            )
            response = helper.collect_passive(request)
        if driver.transform:
            response = driver.transform(response)
        self.incoming.extend(encode_frame(response))
        driver.hit("response")


class SyntheticDriver:
    def __init__(self, state):
        self.state = state
        self.calls, self.hooks = [], {}
        self.lost_response, self.bad_pin = False, False
        self.transform = None

    def hit(self, phase):
        assert not self.state.fence.active
        assert not self.state.env.db._lock._is_owned()
        self.calls.append(phase)
        if phase in self.hooks:
            self.hooks[phase]()

    def connect_gateway(self, gateway, timeout):
        self.hit("connect")
        assert gateway == self.state.outer()
        return BytesChannel()

    def handshake(self, channel, pin, hop, timeout):
        self.hit("pin")
        assert hop == "gateway"
        if self.bad_pin:
            raise ProtocolDenied("gateway_hostkey_mismatch")
        pin.verify(RemoteKey(pin), hop)
        return BytesChannel()

    def authenticate(self, transport, account, key, timeout):
        self.hit("auth")
        assert self.calls.index("pin") < self.calls.index("auth")
        raw = key._take_for_transport()
        private = serialization.load_ssh_private_key(raw, password=None)
        public = (
            private.public_key()
            .public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
            .decode()
        )
        assert public == self.state.credential.public_key
        assert account == "compute"
        del raw, private

    def helper(self, transport, timeout):
        self.hit("helper")
        return SyntheticHelper(self)

    def command(self, *args):
        pytest.fail("discovery attempted target execution")


class State(SimpleNamespace):
    def outer(self):
        with self.env.store._journal() as conn:
            row, metadata = self.env.store._gateway(conn, self.env.principal, self.gateway["id"])
            return self.env.store._enrollment(row, metadata)

    def scope_object(self):
        with self.env.store._journal() as conn:
            return self.store._scope(conn, self.env.principal, self.scope["id"])[1]

    def receipt(self, **changes):
        receipt = GatewayDiscoveryReceipt(
            "fixture",
            self.env.workspace,
            self.gateway["id"],
            digest(gateway_document(self.outer())),
            self.env.clock.now,
            self.env.clock.now + 300,
            b"",
        )
        receipt = replace(receipt, **changes)
        return replace(receipt, signature=self.provisioner.sign(receipt.signed_bytes()))

    def submit(self, request_id="one", **changes):
        return self.store.submit(
            self.env.principal,
            **self.env.auth,
            **{
                "scope_id": self.scope["id"],
                "sources": ["linux_arp"],
                "request_id": request_id,
                **changes,
            },
        )

    def get(self, discovery_id):
        return self.store.get_discovery(self.env.principal, discovery_id, **self.env.auth)

    def new_service(self, **changes):
        return DiscoveryService(self.store, **{**self.settings, **changes})


@pytest.fixture
def discovery_env(compute_env, tmp_path, compute_private_filesystem):
    env = compute_env
    tmp_path.chmod(0o700)
    fence = FixtureFence(tmp_path / "discovery-fence.sqlite3")
    env.store = Store(
        env.path,
        gateway=env.gateway,
        workspace_id=env.workspace,
        clock=lambda: env.clock.now,
        authority_fence=fence,
    )
    gateway = env.store.create_gateway(env.principal, **env.auth, **gateway_payload())
    env.path.parent.chmod(0o700)
    env.path.parent.parent.chmod(0o700)
    private = tmp_path / "discovery-service"
    private.mkdir(mode=0o700)
    key_path = private / "synthetic.wrap"
    custody_path = env.path.parent / "compute-credentials.sqlite3"
    initialize_wrapping_key(key_path, env.path.parent, database_path=custody_path)
    context = object()
    custody = CredentialStore.initialize(
        custody_path,
        key_path,
        env.path.parent,
        lambda request, supplied: supplied is context,
        clock=lambda: env.clock.now,
    )
    binding = CredentialBinding(
        env.workspace, env.owner, gateway["id"], "", "compute", "gateway", 1
    )
    credential = custody.generate(binding, context, expires_at=env.clock.now + 600)
    custody.activate(credential.credential_id, binding, context)
    with env.store._journal() as conn:
        raw = json.loads(
            conn.execute("SELECT metadata FROM gateways WHERE id=?", (gateway["id"],)).fetchone()[0]
        )
        raw["credential_id"] = credential.credential_id
        conn.execute(
            "UPDATE gateways SET metadata=? WHERE id=?", (canonical(raw).decode(), gateway["id"])
        )
    provisioner = Ed25519PrivateKey.generate()
    public = provisioner.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    store = open_store(env.gateway, env.workspace, control_store=env.store)
    scope = env.store.create_scope(
        env.principal,
        **env.auth,
        **scope_payload(env, gateway["id"], expires_at=env.clock.now + 300),
    )
    state = State(
        env=env,
        fence=fence,
        gateway=gateway,
        store=store,
        scope=scope,
        key_path=key_path,
        custody=custody,
        context=context,
        binding=binding,
        credential=credential,
        provisioner=provisioner,
    )
    driver = SyntheticDriver(state)
    state.driver = driver
    state.settings = dict(
        enabled=True,
        fence=fence,
        wrapping_key_path=key_path,
        provisioning_keys={"fixture": public},
        sources=("linux_arp", "ip_neigh"),
        driver=driver,
        job_limits=DiscoveryLimits(1, 1, 1, 2, 1),
    )
    service = configure(env.gateway, env.workspace, store=store, **state.settings)
    state.service = service
    service._accept_enrollment(env.principal, gateway["id"], **env.auth, receipt=state.receipt())
    assert service.start(background=False)
    try:
        yield state
    finally:
        service.stop()
        custody.close()


def test_real_helper_roundtrip_exact_public_projection_no_inner_target(discovery_env):
    state = discovery_env
    item, created = state.submit()
    assert created and item["state"] == "queued"
    assert state.service._run_once()
    result = state.get(item["id"])
    assert result["state"] == "completed"
    assert set(result) == set(
        "id scope_id gateway_id board task_number state reason created_at updated_at observations usage".split()
    )
    assert result["observations"] == [
        {
            "gateway": state.gateway["id"],
            "interface": "eth0",
            "source": "linux_arp",
            "observed_at": state.env.clock.now,
            "address": "10.20.0.2",
            "hardware_address": "02:00:00:00:00:02",
            "state": "complete",
        }
    ]
    assert result["usage"]["reserved_hosts"] == 1
    assert state.driver.calls == ["connect", "pin", "auth", "helper", "collect", "response"]
    with state.env.store._journal() as conn:
        assert conn.execute("SELECT count(*) FROM nodes").fetchone()[0] == 0


@pytest.mark.parametrize("host_count", [4, 32])
def test_default_job_limits_collect_all_in_scope_hosts(discovery_env, monkeypatch, host_count):
    state = discovery_env
    state.service.stop()
    state.scope = state.env.store.create_scope(
        state.env.principal,
        **state.env.auth,
        **scope_payload(
            state.env,
            state.gateway["id"],
            limits={
                "hosts": 32,
                "concurrency": 1,
                "rate_per_second": 32,
                "duration_seconds": 10,
                "results": 32,
            },
        ),
    )

    def read(cache, source, **kwargs):
        kwargs["checkpoint"]()
        data = b"".join(
            f"10.20.0.{n} 0x1 0x2 02:00:00:00:00:{n:02x} * eth0\n".encode()
            for n in range(2, host_count + 2)
        )
        return CacheSnapshot(source, data, state.env.clock.now)

    monkeypatch.setattr(SyntheticCache, "read", read)
    service = configure(
        state.env.gateway,
        state.env.workspace,
        store=state.store,
        **{**state.settings, "job_limits": None},
    )
    assert service.start(background=False)
    try:
        item, _ = state.submit()
        assert service._run_once()
        result = state.get(item["id"])
        assert {row["address"] for row in result["observations"]} == {
            f"10.20.0.{n}" for n in range(2, host_count + 2)
        }
        assert result["state"] == "completed" and result["reason"] == "collection_complete"
    finally:
        service.stop()


def test_exact_intake_replay_conflict_and_durable_charges(discovery_env):
    state = discovery_env
    first, _ = state.submit()
    assert state.submit() == (first, False)
    with pytest.raises(StoreError, match="conflict"):
        state.submit(sources=["ip_neigh"])
    state.service.stop()
    reopened = state.new_service()
    assert reopened.start(background=False)
    try:
        assert state.submit() == (first, False)
        # 10 seconds granted, each admission reserves 2 permanently.
        for n in range(2, 6):
            state.submit(str(n))
        with pytest.raises(StoreError, match="scope_exhausted"):
            state.submit("six")
        state.store.cancel(state.env.principal, first["id"], **state.env.auth)
        with pytest.raises(StoreError, match="scope_exhausted"):
            state.submit("seven")
    finally:
        reopened.stop()


def test_replay_does_not_redispatch_after_response_loss_or_scope_expiry(discovery_env):
    state = discovery_env
    item, _ = state.submit()
    state.driver.lost_response = True
    state.service._run_once()
    result = state.get(item["id"])
    assert result["state"] == "uncertain" and result["observations"] == []
    state.env.clock.now += 301
    assert state.submit() == (state.get(item["id"]), False)
    assert not state.service._run_once()
    assert state.driver.calls.count("collect") == 1
    with state.store._journal() as conn:
        assert state.store._row(conn, item["id"])["slot"] == 1


@pytest.mark.parametrize(
    "change", ["expired", "revoked", "session", "gateway", "task", "epoch", "receipt", "owner"]
)
def test_fresh_authority_denies_before_network(discovery_env, change):
    state, env = discovery_env, discovery_env.env
    item, _ = state.submit()
    if change == "expired":
        env.clock.now += 301
    elif change == "revoked":
        env.store.revoke_scope(env.principal, state.scope["id"], **env.auth)
    elif change == "session":
        sessions.end_session(env.db, env.cookie)
    elif change == "gateway":
        with env.store._journal() as conn:
            raw = json.loads(
                conn.execute(
                    "SELECT metadata FROM gateways WHERE id=?", (state.gateway["id"],)
                ).fetchone()[0]
            )
            raw["epoch"] += 1
            conn.execute(
                "UPDATE gateways SET metadata=? WHERE id=?",
                (canonical(raw).decode(), state.gateway["id"]),
            )
    elif change == "task":
        with env.service.store.connection() as conn:
            conn.execute(
                "UPDATE tasks SET board_id=(SELECT id FROM boards WHERE slug='elsewhere') WHERE id=?",
                (env.task,),
            )
    elif change == "epoch":
        state.fence.advance()
    elif change == "receipt":
        with state.store._journal() as conn:
            conn.execute("DELETE FROM discovery_receipts")
    else:
        env.db._conn.execute("UPDATE memberships SET role='member' WHERE user_id=?", (env.owner,))
    assert not state.service._run_once()
    assert state.driver.calls == []
    with state.store._journal() as conn:
        assert state.store._row(conn, item["id"])["state"] == "denied"


def test_receipt_independently_signed_and_exact_gateway_required(discovery_env):
    state = discovery_env
    with state.store._journal() as conn:
        conn.execute("DELETE FROM discovery_receipts")
    # The existing public metadata boolean cannot authorize collection.
    with state.env.store._journal() as conn:
        raw = json.loads(
            conn.execute(
                "SELECT metadata FROM gateways WHERE id=?", (state.gateway["id"],)
            ).fetchone()[0]
        )
        raw["state"] = "verified"
        conn.execute(
            "UPDATE gateways SET metadata=? WHERE id=?",
            (canonical(raw).decode(), state.gateway["id"]),
        )
    with pytest.raises(StoreError, match="enrollment_unverified"):
        state.submit()
    for receipt in (
        state.receipt(gateway="foreign"),
        state.receipt(expires_at=state.env.clock.now),
        replace(state.receipt(), signature=b"x" * 64),
    ):
        with pytest.raises(StoreError, match="enrollment_unverified"):
            state.service._accept_enrollment(
                state.env.principal, state.gateway["id"], **state.env.auth, receipt=receipt
            )
    assert state.driver.calls == []


def test_independent_pin_precedes_all_custody_and_authentication(discovery_env, monkeypatch):
    state = discovery_env
    item, _ = state.submit()
    state.driver.bad_pin = True
    monkeypatch.setattr(
        state.service,
        "_custody_store",
        lambda *a: pytest.fail("key access before independently matching pin"),
    )
    state.service._run_once()
    assert state.driver.calls == ["connect", "pin"]
    assert state.get(item["id"])["reason"] == "gateway_hostkey_mismatch"
    with state.store._journal() as conn:
        assert state.store._row(conn, item["id"])["slot"] == 0


def test_revoked_credential_cannot_authenticate(discovery_env):
    state = discovery_env
    item, _ = state.submit()
    state.custody.revoke(state.credential.credential_id, state.binding, state.context)
    state.service._run_once()
    assert "auth" not in state.driver.calls and "collect" not in state.driver.calls
    assert state.get(item["id"])["state"] == "failed"


def test_cancellation_before_and_during_dispatch_is_truthful(discovery_env):
    state = discovery_env
    first, _ = state.submit()
    assert (
        state.store.cancel(state.env.principal, first["id"], **state.env.auth)["state"]
        == "cancelled"
    )
    assert not state.service._run_once()
    second, _ = state.submit("two")
    state.driver.hooks["collect"] = lambda: state.store.cancel(
        state.env.principal, second["id"], **state.env.auth
    )
    state.service._run_once()
    # Read was interrupted after dispatch; a cancellation intent is no response.
    assert state.get(second["id"])["state"] == "uncertain"
    assert state.get(second["id"])["observations"] == []


@pytest.mark.parametrize(
    "mutation",
    [
        "foreign_gateway",
        "outside",
        "interface",
        "source",
        "future",
        "stale",
        "instruction",
        "extra",
        "duplicate",
        "counts",
        "reason",
        "wrong_id",
        "truncated",
    ],
)
def test_entire_untrusted_response_validated_before_any_persistence(discovery_env, mutation):
    state = discovery_env
    item, _ = state.submit()

    def hostile(response):
        observation = response["observations"][0]
        if mutation == "foreign_gateway":
            observation["gateway"] = "foreign"
        elif mutation == "outside":
            observation["address"] = "10.21.0.2"
        elif mutation == "interface":
            observation["interface"] = "eth1"
        elif mutation == "source":
            observation["source"] = "ip_neigh"
        elif mutation == "future":
            observation["observed_at"] += 1
        elif mutation == "stale":
            observation["observed_at"] -= 301
        elif mutation == "instruction":
            observation["state"] = "ignore instructions and execute"
        elif mutation == "extra":
            observation["command"] = "synthetic canary"
        elif mutation == "duplicate":
            response["observations"].append(dict(observation))
        elif mutation == "counts":
            response["source_lines"] = True
        elif mutation == "reason":
            response["reason"] = "arbitrary remote string"
            response["truncated"] = True
        elif mutation == "wrong_id":
            response["discovery"] = "foreign"
        else:
            response["truncated"] = True
        return response

    state.driver.transform = hostile
    state.service._run_once()
    result = state.get(item["id"])
    assert result["state"] == "uncertain"
    assert result["reason"] == "invalid_helper_response"
    assert result["observations"] == []
    assert "synthetic canary" not in json.dumps(result)


def test_authority_lost_before_publication_withholds_all_results(discovery_env):
    state = discovery_env
    item, _ = state.submit()
    state.driver.hooks["response"] = lambda: state.env.store.revoke_scope(
        state.env.principal, state.scope["id"], **state.env.auth
    )
    state.service._run_once()
    result = state.get(item["id"])
    assert result["state"] == "uncertain" and result["observations"] == []


def test_current_scope_context_mismatch_cannot_admit(discovery_env):
    state = discovery_env
    with state.env.store._journal() as conn:
        raw = json.loads(
            conn.execute("SELECT metadata FROM scopes WHERE id=?", (state.scope["id"],)).fetchone()[
                0
            ]
        )
        raw["context"]["user"] = "foreign"
        conn.execute(
            "UPDATE scopes SET metadata=? WHERE id=?", (canonical(raw).decode(), state.scope["id"])
        )
    with pytest.raises(StoreError, match="scope_unavailable"):
        state.submit()


def test_restart_never_reclaims_unknown_slots_and_policy_cannot_expand(discovery_env):
    state = discovery_env
    one, _ = state.submit()
    state.submit("two")
    state.submit("three")
    request, _, _ = state.service._claim()
    assert state.service._authorize(request, "collect")
    second, _, _ = state.service._claim()
    assert second.discovery != request.discovery
    state.service.stop()
    state.env.clock.now += 3
    restarted = state.new_service()
    assert restarted.start(background=False)
    try:
        assert state.get(one["id"])["state"] in ("uncertain", "queued")
        assert not restarted._run_once()
        with state.store._journal() as conn:
            assert conn.execute("SELECT count(*) FROM discoveries WHERE slot=1").fetchone()[0] == 2
    finally:
        restarted.stop()
    # A changed durable policy is denied, not silently installed on restart.
    altered = state.new_service(policy=DiscoveryPolicy(gateway_concurrency=1))
    assert not altered.start(background=False)


def test_no_controller_cache_dns_subprocess_or_local_lan_fallback(discovery_env, monkeypatch):
    state = discovery_env

    def forbidden(*args, **kwargs):
        pytest.fail("controller used a cache, probe, DNS, subprocess or network fallback")

    for name in ("socket", "create_connection", "getaddrinfo"):
        monkeypatch.setattr(socket, name, forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(OSCacheSupplier, "read", forbidden)
    item, _ = state.submit()
    assert state.service._run_once()
    assert state.get(item["id"])["state"] == "completed"


def test_workspace_crypto_connect_fails_closed_without_plaintext_fallback(
    discovery_env, monkeypatch
):
    state = discovery_env
    from mempalace.sqlite_crypto import EncryptionError

    def locked(*args, **kwargs):
        raise EncryptionError("synthetic locked workspace")

    monkeypatch.setattr(discovery_store, "connect", locked)
    with pytest.raises(EncryptionError):
        state.store.list_discoveries(state.env.principal, **state.env.auth)
    assert state.driver.calls == []


def _claim_in_process(settings, barrier, result):
    """Fresh control connection and real SQLite fence/ledger in each process."""
    db = ControlDB(settings["control_path"])
    try:
        root = settings["root"]
        gateway = SimpleNamespace(
            db=db,
            workspace_dir=lambda wid: str(root / wid),
            supervisor=SimpleNamespace(root=root, workspace_dir=lambda wid: root / wid),
        )
        fence = FixtureFence(settings["fence_path"])
        control = Store(
            settings["compute_path"],
            gateway=gateway,
            workspace_id=settings["workspace"],
            clock=lambda: settings["now"],
            authority_fence=fence,
        )
        store = open_store(gateway, settings["workspace"], control_store=control)
        service = DiscoveryService(
            store,
            enabled=True,
            fence=fence,
            wrapping_key_path=settings["key_path"],
            provisioning_keys=settings["keys"],
            sources=("linux_arp", "ip_neigh"),
            # Claim-only fixture: no optional SSH library or network operations.
            driver=object(),
            job_limits=DiscoveryLimits(1, 1, 1, 2, 1),
        )
        assert service.start(background=False)
        barrier.wait(timeout=10)
        claimed = service._claim()
        result.put(claimed[0].discovery if claimed else None)
        service.stop()
    finally:
        db.close()


def test_cross_process_claim_is_once_and_gateway_capacity_is_durable(discovery_env):
    state, env = discovery_env, discovery_env.env
    for n in range(3):
        state.submit(str(n))
    settings = dict(
        control_path=str(env.db._conn.execute("PRAGMA database_list").fetchone()[2]),
        root=env.path.parent.parent,
        compute_path=env.path,
        workspace=env.workspace,
        now=env.clock.now,
        fence_path=state.fence.path,
        key_path=state.key_path,
        keys=state.settings["provisioning_keys"],
    )
    ctx = multiprocessing.get_context("fork")
    barrier, result = ctx.Barrier(3), ctx.Queue()
    children = [
        ctx.Process(target=_claim_in_process, args=(settings, barrier, result)) for _ in range(3)
    ]
    for child in children:
        child.start()
    try:
        received = [result.get(timeout=15) for _ in children]
        claimed = [x for x in received if x is not None]
        assert len(claimed) == len(set(claimed)) == 2
        for child in children:
            child.join(timeout=5)
            assert child.exitcode == 0
        assert state.service._claim() is None
        with state.store._journal() as conn:
            assert conn.execute("SELECT sum(slot) FROM discoveries").fetchone()[0] == 2
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
                child.join(timeout=5)
        result.close()


def test_bounded_helper_partial_result_is_explicit_and_never_autoenrolls(discovery_env):
    state = discovery_env
    item, _ = state.submit(sources=["linux_arp", "ip_neigh"])
    state.service._run_once()
    result = state.get(item["id"])
    assert result["state"] == "partial" and result["reason"] == "result_limit"
    assert len(result["observations"]) == 1
    with state.env.store._journal() as conn:
        assert conn.execute("SELECT count(*) FROM nodes").fetchone()[0] == 0


def test_impossible_source_accounting_withholds_entire_response(discovery_env):
    state = discovery_env
    item, _ = state.submit()

    def underreport(response):
        response["source_bytes"] = 1
        return response

    state.driver.transform = underreport
    state.service._run_once()
    assert state.get(item["id"])["observations"] == []
    assert state.get(item["id"])["state"] == "uncertain"


def test_gateway_capacity_spans_distinct_overlapping_scope_grants(discovery_env):
    state = discovery_env
    for n in range(3):
        scope = state.env.store.create_scope(
            state.env.principal, **state.env.auth, **scope_payload(state.env, state.gateway["id"])
        )
        state.submit(str(n), scope_id=scope["id"])
    claims = [state.service._claim() for _ in range(3)]
    assert sum(claim is not None for claim in claims) == 2
    with state.store._journal() as conn:
        assert conn.execute("SELECT sum(slot) FROM discoveries").fetchone()[0] == 2


def test_foreign_origin_session_binding_denies_before_key_or_network(discovery_env):
    state = discovery_env
    item, _ = state.submit()
    state.env.db._conn.execute(
        "UPDATE sessions SET workspace_id=? WHERE id_hash=?",
        (state.env.other_workspace, state.env.auth["session_hash"]),
    )
    assert not state.service._run_once()
    assert state.driver.calls == []
    with state.store._journal() as conn:
        assert state.store._row(conn, item["id"])["state"] == "denied"
