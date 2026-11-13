"""Approved installation receipts: real HTTP authority and isolated private caches."""

# ruff: noqa: F811

import base64
import copy
import json
import secrets
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from commonplace.runner import agent, installer, registry
from commonplace.runner.install_host import FakeHost, InstallError
from tests.runner_harness import http, owners, signup_hub  # noqa: F401
from tests.test_runner_enrollment import (
    DIGEST,
    decrypt,
    enable,
    exchange,
    issue,
    machine_key,  # noqa: F401
    public,
)

PATH = "/api/runner/install-manifest"
PROJECTS = {"demo": {"project_id": "project-demo", "checkout": "shared", "runtime_ceiling": "auto"}}


@pytest.fixture
def installed(signup_hub, owners, machine_key, tmp_path):
    hub, owner = signup_hub, owners["A"]
    assert (
        http(
            hub.port,
            "POST",
            "/api/memory",
            cookie=owner["cookie"],
            body={"text": "Fixture", "theme": "shared"},
        )[0]
        == 201
    )
    assert (
        http(
            hub.port,
            "POST",
            "/api/boards",
            cookie=owner["cookie"],
            body={"slug": "demo", "name": "Demo", "wing": "shared"},
        )[0]
        == 201
    )
    enable(hub, owner)
    status, issued = issue(hub, owner)
    assert status == 201
    data = {
        "workspace_id": owner["workspace"],
        "machine_label": "test-box",
        "manifest_digest": DIGEST,
        "code": issued["code"],
        "installation_id": "install-test",
        "public_key": public(machine_key),
    }
    status, envelope = exchange(hub, data)
    assert status == 201
    credentials = decrypt(machine_key, envelope)
    key = Ed25519PrivateKey.generate()
    hub.gateway.runner_receipt_signer = key
    return SimpleNamespace(
        hub=hub,
        owner=owner,
        credentials=credentials,
        key=key,
        machine_key=machine_key,
        issued=issued,
        data=data,
        tmp=tmp_path,
    )


def machine(s):
    return registry.get_machine(s.hub.db, s.owner["workspace"], s.credentials["machine_id"])


def approve(s, projects=None):
    row = machine(s)
    status, listing = http(s.hub.port, "GET", "/api/runners", cookie=s.owner["cookie"])
    assert status == 200
    current = next(m for m in listing["machines"] if m["id"] == row["id"])
    revision = current.get("install_settings", {}).get("settings_version", 0)
    return http(
        s.hub.port,
        "POST",
        "/api/runners/" + row["id"] + "/attest",
        cookie=s.owner["cookie"],
        body={
            "topics": json.loads(row["topics"]),
            "provider_instance_id": "fixture-provider",
            "worker_connection_id": row["worker_connection_id"],
            "install_settings": {
                "grant_version": row["grant_version"],
                "binding_epoch": row["binding_epoch"],
                "settings_version": revision,
                "projects": projects
                or {"demo": {**PROJECTS["demo"], "runtime_ceiling": "approval-required"}},
            },
        },
    )


def fetch(s, token=None, nonce=None):
    return http(
        s.hub.port,
        "GET",
        PATH + "?nonce=" + (nonce or secrets.token_hex(16)),
        token=token or s.credentials["machine_token"],
    )


def test_clean_install_without_owner_approval_refuses_manifest(installed):
    s = installed
    status, body = fetch(s)
    assert (status, body) == (409, {"error": "approval_required"})
    queue = http(s.hub.port, "GET", "/api/needs-me", cookie=s.owner["cookie"])[1]
    assert any(item["kind"] == "runner_settings" for item in queue["runner_setup"])
    assert machine(s)["enabled"] == 0


def test_owner_settings_produce_machine_bound_signed_receipt(installed):
    s = installed
    assert approve(s)[0] == 200
    nonce = secrets.token_hex(16)
    status, result = fetch(s, nonce=nonce)
    assert status == 200
    receipt = result["receipt"]
    s.key.public_key().verify(
        base64.b64decode(result["signature"]),
        json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode(),
    )
    assert receipt["nonce"] == nonce
    assert receipt["machine_id"] == s.credentials["machine_id"]
    assert receipt["worker_connection_id"] == s.credentials["worker_connection_id"]
    assert receipt["projects"]["demo"]["checkout"] == "shared"
    assert receipt["agent_ready"] is False


def test_changed_grant_invalidates_receipt_and_needs_owner(installed):
    s = installed
    assert approve(s)[0] == 200
    assert fetch(s)[0] == 200
    assert (
        http(
            s.hub.port,
            "PATCH",
            "/api/runners/" + machine(s)["id"],
            cookie=s.owner["cookie"],
            body={"topics": ["review"]},
        )[0]
        == 200
    )
    assert fetch(s) == (409, {"error": "approval_required"})
    assert approve(s)[0] == 200
    assert fetch(s)[1]["receipt"]["topics"] == ["review"]


@pytest.fixture
def cached(installed):
    from commonplace.runner.install_files import STATE
    from tests.test_runner_install import RouteTransport

    s = installed
    status, issued = issue(s.hub, s.owner, request_id="cached", label="cache-box")
    assert status == 201
    home = s.tmp / "cache-home"
    home.mkdir(mode=0o700)
    transport = RouteTransport(s.hub.db)
    transport.receipt = lambda token, nonce: fetch(s, token=token, nonce=nonce)[1]
    intent = {
        "hub_url": "https://hub.example.test",
        "workspace_id": s.owner["workspace"],
        "machine_label": "cache-box",
        "manifest_digest": DIGEST,
        "enrollment_id": issued["id"],
    }
    local = installer.Installer(home, FakeHost(), transport)
    raw_key = s.key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    local.install(intent, accepted=True, code_source=lambda: issued["code"], approval_key=raw_key)
    config = json.loads(local.fs.read(installer.CONFIG))
    row = registry.get_machine(s.hub.db, s.owner["workspace"], config["machine_id"])
    s.credentials = {
        "machine_id": row["id"],
        "worker_connection_id": row["worker_connection_id"],
        "machine_token": local.fs.read(STATE + "/machine.token").decode(),
        "worker_token": local.fs.read(STATE + "/worker.token").decode(),
    }
    s.local, s.transport, s.home = local, transport, home
    return s


def cache(s):
    from commonplace.runner.receipt_cache import Cache

    s.local.load()
    return Cache(
        s.local.fs, s.local.journal, json.loads(s.local.fs.read(installer.CONFIG)), s.transport
    )


def test_edited_local_ack_is_rejected_and_refetched(cached):
    from commonplace.runner.receipt_cache import CACHE

    s = cached
    assert approve(s)[0] == 200
    original = s.local.sync_receipts()
    assert original["binding_acks"] == ["demo:work"]
    stored = json.loads(s.local.fs.read(CACHE))
    stored["config"]["projects"]["demo"]["runtime_ceiling"] = "full-access"
    stored["config"]["binding_acks"].append("demo:unapproved")
    s.local.fs.write(CACHE, json.dumps(stored).encode(), replace=True)
    with pytest.raises(ValueError, match="cached acknowledgements changed"):
        cache(s).read()
    calls = []
    real = s.transport.receipt
    s.transport.receipt = lambda token, nonce: calls.append(nonce) or real(token, nonce)
    assert s.local.sync_receipts() == original
    assert len(calls) == 1
    assert cache(s).read() == original
    assert s.local.status() == 0
    s.local.uninstall()
    assert not (s.home / CACHE).exists()


@pytest.mark.parametrize("changed", ["topics", "wings", "max_runtime_mode"])
def test_hub_change_narrows_next_sync_without_machine_edit(cached, changed):
    from commonplace.runner.receipt_cache import CACHE

    s = cached
    if changed == "wings":
        assert (
            http(
                s.hub.port,
                "POST",
                "/api/memory",
                cookie=s.owner["cookie"],
                body={"text": "Fixture scope", "theme": "team"},
            )[0]
            == 201
        )
        assert (
            http(
                s.hub.port,
                "PATCH",
                "/api/runners/" + machine(s)["id"],
                cookie=s.owner["cookie"],
                body={"wings": ["shared", "team"]},
            )[0]
            == 200
        )
    if changed == "topics":
        assert (
            http(
                s.hub.port,
                "PATCH",
                "/api/runners/" + machine(s)["id"],
                cookie=s.owner["cookie"],
                body={"topics": ["work", "review"]},
            )[0]
            == 200
        )
    if changed == "max_runtime_mode":
        assert (
            http(
                s.hub.port,
                "PATCH",
                "/api/runners/" + machine(s)["id"],
                cookie=s.owner["cookie"],
                body={"max_runtime_mode": "auto"},
            )[0]
            == 200
        )
    assert approve(s, PROJECTS if changed == "max_runtime_mode" else None)[0] == 200
    s.local.sync_receipts()
    immutable = s.local.fs.read(installer.CONFIG)
    patch = {
        "topics": ["work"],
        "wings": ["shared"],
        "max_runtime_mode": "approval-required",
    }
    assert (
        http(
            s.hub.port,
            "PATCH",
            "/api/runners/" + machine(s)["id"],
            cookie=s.owner["cookie"],
            body={changed: patch[changed]},
        )[0]
        == 200
    )
    with pytest.raises(InstallError):
        s.local.sync_receipts()
    assert json.loads(s.local.fs.read(CACHE)) == {}
    queue = http(s.hub.port, "GET", "/api/needs-me", cookie=s.owner["cookie"])[1]
    assert any(item["kind"] == "runner_settings" for item in queue["runner_setup"])
    assert approve(s)[0] == 200
    narrowed = s.local.sync_receipts()
    assert narrowed[changed] == patch[changed]
    assert s.local.fs.read(installer.CONFIG) == immutable
    assert machine(s)["enabled"] == 0


@pytest.mark.parametrize(
    "mutation",
    [
        "signature",
        "wrong-key",
        "workspace_id",
        "machine_id",
        "machine_key_digest",
        "installation_id",
        "manifest_digest",
        "worker_connection_id",
        "provider_instance_id",
        "expired",
        "nonce",
        "rollback",
    ],
)
def test_wrong_stale_or_tampered_receipt_clears_cache(cached, mutation):
    from commonplace.runner.receipt_cache import CACHE

    s = cached
    assert approve(s)[0] == 200
    s.local.sync_receipts()
    old = json.loads(s.local.fs.read(CACHE))["signed"]
    real = s.transport.receipt

    def changed(token, nonce):
        envelope = copy.deepcopy(real(token, nonce))
        receipt = envelope["receipt"]
        if mutation == "signature":
            receipt["topics"] = ["unapproved"]
            return envelope
        if mutation == "wrong-key":
            signing = Ed25519PrivateKey.generate()
        else:
            signing = s.key
        if mutation == "expired":
            receipt["expires_at"] = receipt["issued_at"]
        elif mutation == "nonce":
            return old
        elif mutation == "rollback":
            receipt["grant_version"] = 0
        elif mutation == "provider_instance_id":
            # Transport modification cannot update a signed provider identity.
            receipt["provider_instance_id"] = "wrong-provider"
            return envelope
        elif mutation not in ("wrong-key",):
            receipt[mutation] = "wrong"
        envelope["signature"] = base64.b64encode(
            signing.sign(json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode())
        ).decode()
        return envelope

    s.transport.receipt = changed
    with pytest.raises(InstallError):
        s.local.sync_receipts()
    assert json.loads(s.local.fs.read(CACHE)) == {}


def test_runner_never_launches_outside_fetched_grant(cached, monkeypatch):
    import sys

    s = cached
    assert approve(s)[0] == 200
    approved = s.local.sync_receipts()
    config = {**approved, "t3_executable": [sys.executable], "min_free_memory_mb": 0}
    good = {"project": "demo", "topic": "work", "runtime_mode": "approval-required"}
    assert agent._local_grant_code(config, good) is None
    launches, reports = [], []
    monkeypatch.setattr(agent, "_spawn", lambda *args: launches.append(args))
    hub = SimpleNamespace(post=lambda *args: reports.append(args))
    for change in ({"project": "outside"}, {"topic": "outside"}, {"runtime_mode": "full-access"}):
        job = {
            **good,
            **change,
            "task_number": 1,
            "job_id": "job_" + "a" * 16,
            "title_marker": "commonplace-job:job_" + "a" * 16,
            "lease_token": "synthetic-lease",
        }
        agent._start(hub, config, job, s.tmp, {}, ())
    assert launches == [] and len(reports) == 3
    assert all(route == "result" for route, _ in reports)
    assert agent.run(s.home / installer.CONFIG, once=True) == 4
    # Even an owner cannot make an incomplete installation serve agent work.
    assert (
        http(
            s.hub.port,
            "PATCH",
            "/api/runners/" + machine(s)["id"],
            cookie=s.owner["cookie"],
            body={"enabled": True},
        )[0]
        == 409
    )


@pytest.mark.parametrize("caller", ["worker", "person", "foreign", "anonymous", "ordinary"])
def test_only_own_machine_credential_fetches_and_never_attests(installed, owners, caller):
    from tests.runner_harness import cli_token

    s = installed
    assert approve(s)[0] == 200
    auth = {}
    if caller == "worker":
        auth = {"token": s.credentials["worker_token"]}
    elif caller in ("person", "foreign"):
        auth = {"cookie": (s.owner if caller == "person" else owners["B"])["cookie"]}
    elif caller == "ordinary":
        _, token = cli_token(s.owner, "receipt-probe", profile="full")
        auth = {"token": token}
    assert http(s.hub.port, "GET", PATH + "?nonce=" + "a" * 32, **auth)[0] in (401, 404)
    token = s.credentials["machine_token"]
    assert (
        http(
            s.hub.port, "POST", "/api/runners/" + machine(s)["id"] + "/attest", token=token, body={}
        )[0]
        == 404
    )
    assert fetch(s)[0] == 200


def test_missing_signer_and_audit_fail_closed_without_secret_reflection(
    installed, monkeypatch, caplog
):
    s = installed
    assert approve(s)[0] == 200
    sentinel = s.credentials["machine_token"]
    s.hub.gateway.runner_receipt_signer = None
    assert fetch(s) == (503, {"error": "receipt unavailable"})
    s.hub.gateway.runner_receipt_signer = s.key
    real = s.hub.db.audit

    def fail(**kwargs):
        if kwargs["action"] == "runner.installation.fetch":
            raise RuntimeError(sentinel)
        return real(**kwargs)

    monkeypatch.setattr(s.hub.db, "audit", fail)
    assert fetch(s) == (503, {"error": "receipt unavailable"})
    assert sentinel not in caplog.text


def test_default_off_is_invisible_and_public_pin_unchanged(signup_hub, owners):
    from commonplace.gateway import _public_route
    from tests.runner_harness import cli_token

    hub, owner = signup_hub, owners["A"]
    _, token = cli_token(owner, "old-receipt-token", profile="full")
    listing = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    before = http(hub.port, "POST", "/mcp", token=token, body=listing)
    assert http(hub.port, "GET", PATH, token=token) == (404, {"error": "not found"})
    assert http(hub.port, "POST", "/mcp", token=token, body=listing) == before
    assert not _public_route("GET", PATH)
    with hub.db._lock:
        assert hub.db._conn.execute("SELECT count(*) FROM runner_setup_requests").fetchone()[0] == 0
        assert (
            hub.db._conn.execute("SELECT count(*) FROM runner_install_manifests").fetchone()[0] == 0
        )


def test_stale_owner_settings_roll_back_attestation(installed):
    s = installed
    row = machine(s)
    body = {
        "topics": ["work"],
        "provider_instance_id": "fixture-provider",
        "worker_connection_id": row["worker_connection_id"],
        "install_settings": {
            "grant_version": row["grant_version"] - 1,
            "binding_epoch": row["binding_epoch"],
            "settings_version": 0,
            "projects": PROJECTS,
        },
    }
    assert (
        http(
            s.hub.port,
            "POST",
            "/api/runners/" + row["id"] + "/attest",
            cookie=s.owner["cookie"],
            body=body,
        )[0]
        == 409
    )
    assert machine(s)["binding_state"] == "unbound"


def test_disable_and_worker_rotation_invalidate_receipts(cached):
    s = cached
    assert approve(s)[0] == 200
    s.local.sync_receipts()
    assert (
        http(
            s.hub.port,
            "POST",
            "/api/runners/" + machine(s)["id"] + "/rotate",
            cookie=s.owner["cookie"],
            body={"credential": "worker"},
        )[0]
        == 200
    )
    with pytest.raises(InstallError):
        s.local.sync_receipts()
    assert approve(s)[0] == 200
    assert s.local.sync_receipts()["agent_ready"] is False
    # Rotation keeps the connection identity. It cannot refresh the installed
    # credential or enable work; encrypted credential delivery belongs to a later slice.
    assert s.hub.db.authenticate_token(s.credentials["worker_token"]) is None
    enable(s.hub, s.owner, False)
    with pytest.raises(InstallError):
        s.local.sync_receipts()


def test_needs_me_setup_reminder_uses_existing_settings_navigation():
    import subprocess
    from pathlib import Path

    result = subprocess.run(
        ["node", str(Path(__file__).with_name("runner_setup_needs.mjs"))],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_transport_never_puts_credentials_in_argv_or_reflects_errors(monkeypatch):
    sentinel = "SyntheticReceiptCredentialSentinel"
    transport = installer.Transport("https://hub.example.test")
    requests = []

    def opened(request, **kwargs):
        requests.append(request)
        raise RuntimeError(sentinel)

    monkeypatch.setattr(transport.opener, "open", opened)
    with pytest.raises(InstallError) as error:
        transport.receipt(sentinel, "a" * 32)
    assert sentinel not in str(error.value)
    assert sentinel not in requests[0].full_url
    assert requests[0].get_header("Authorization") == "Bearer " + sentinel


def test_setup_service_fetches_approved_settings_without_starting_work(cached, monkeypatch):
    s = cached
    assert approve(s)[0] == 200
    real_sync = installer.Installer.sync_receipts
    calls = []

    class Stop:
        def __init__(self):
            self.stopped = False

        def is_set(self):
            return self.stopped

        def set(self):
            self.stopped = True

        def wait(self, timeout=None):
            self.stopped = True

    def sync(local):
        local.transport = s.transport
        calls.append(real_sync(local))

    monkeypatch.setattr(installer.threading, "Event", Stop)
    monkeypatch.setattr(installer.signal, "signal", lambda *args: None)
    monkeypatch.setattr(installer.Installer, "sync_receipts", sync)
    monkeypatch.setattr(agent, "_spawn", lambda *args: pytest.fail("setup service launched work"))
    assert installer.serve(s.home) == 0
    assert len(calls) == 1 and calls[0]["binding_acks"] == ["demo:work"]
    assert machine(s)["enabled"] == 0


def test_check_fetches_acknowledgements_and_keeps_execution_disabled(cached, monkeypatch, capsys):
    s = cached
    assert approve(s)[0] == 200
    monkeypatch.setattr(installer, "Transport", lambda origin: s.transport)
    assert agent.check(s.home / installer.CONFIG) == 0
    assert "execution disabled" in capsys.readouterr().out
    enable(s.hub, s.owner, False)
    assert agent.check(s.home / installer.CONFIG) == 4


def test_approval_records_are_classified_and_deleted_with_workspace(installed, monkeypatch):
    from commonplace.account import deletion, tables

    s = installed
    assert fetch(s)[0] == 409
    assert approve(s)[0] == 200
    with s.hub.db._lock:
        present = tables.present(s.hub.db._conn)
        assert tables.unclassified(present) == []
        assert tables.RULES["runner_install_manifests"].workspace == ("workspace_id",)
        assert tables.RULES["runner_setup_requests"].workspace == ("workspace_id",)
    monkeypatch.setenv("COMMONPLACE_ACCOUNT_DELETION", "on")
    assert (
        http(
            s.hub.port,
            "POST",
            "/api/account/deletion",
            cookie=s.owner["cookie"],
            body={"confirm": s.owner["email"]},
        )[0]
        == 200
    )
    assert deletion.purge(s.hub.gateway, s.owner["user_id"])["state"] == "purged"
    with s.hub.db._lock:
        assert (
            s.hub.db._conn.execute("SELECT count(*) FROM runner_install_manifests").fetchone()[0]
            == 0
        )
        assert (
            s.hub.db._conn.execute("SELECT count(*) FROM runner_setup_requests").fetchone()[0] == 0
        )


def test_validly_signed_lower_settings_version_is_rejected(cached):
    s = cached
    assert approve(s)[0] == 200
    assert approve(s)[0] == 200
    assert s.local.sync_receipts()["settings_version"] == 2
    real = s.transport.receipt

    def rollback(token, nonce):
        envelope = real(token, nonce)
        envelope["receipt"]["settings_version"] = 1
        envelope["signature"] = base64.b64encode(
            s.key.sign(
                json.dumps(envelope["receipt"], sort_keys=True, separators=(",", ":")).encode()
            )
        ).decode()
        return envelope

    s.transport.receipt = rollback
    with pytest.raises(InstallError):
        s.local.sync_receipts()


def test_receipt_and_reset_are_serialized_then_next_fetch_refuses(installed):
    from threading import Event, Thread

    from commonplace import sessions
    from commonplace.runner import install_receipts

    s = installed
    assert approve(s)[0] == 200
    entered, release, attempted = Event(), Event(), Event()
    replies, errors = [], []

    class Signing:
        def sign(self, raw):
            entered.set()
            assert release.wait(5)
            return s.key.sign(raw)

    s.hub.gateway.runner_receipt_signer = Signing()

    def fetching():
        try:
            replies.append(fetch(s))
        except Exception as exc:
            errors.append(type(exc).__name__)

    def resetting():
        attempted.set()
        owner = sessions.person_principal(s.hub.db, s.owner["user_id"], s.owner["workspace"])
        registry.patch(s.hub.db, owner, s.credentials["machine_id"], {"topics": ["review"]})

    reader = Thread(target=fetching)
    reader.start()
    assert entered.wait(5)
    writer = Thread(target=resetting)
    writer.start()
    assert attempted.wait(5)
    release.set()
    for thread in (reader, writer):
        thread.join(10)
        assert not thread.is_alive()
    assert errors == [] and replies[0][0] == 200
    assert replies[0][1]["receipt"]["binding_epoch"] < machine(s)["binding_epoch"]
    s.hub.gateway.runner_receipt_signer = s.key
    assert fetch(s) == (409, {"error": "approval_required"})
    with pytest.raises(install_receipts.ApprovalRequired):
        install_receipts.fetch(
            s.hub.gateway,
            s.hub.db.authenticate_token(s.credentials["machine_token"]),
            secrets.token_hex(16),
        )


def test_private_cache_and_service_output_have_no_credentials(cached, capsys):
    from commonplace.runner.receipt_cache import CACHE

    s = cached
    assert approve(s)[0] == 200
    s.local.sync_receipts()
    output = capsys.readouterr()
    with s.hub.db._lock:
        audit = json.dumps([dict(row) for row in s.hub.db._conn.execute("SELECT * FROM audit_log")])
    evidence = (
        output.out
        + output.err
        + audit
        + s.local.fs.read(CACHE).decode()
        + s.local.fs.read(installer.UNIT_PATH).decode()
        + json.dumps(s.local.host.commands)
    )
    for secret in (s.credentials["machine_token"], s.credentials["worker_token"]):
        assert secret not in evidence
    assert (s.home / CACHE).stat().st_mode & 0o777 == 0o600


def test_settings_revision_compare_and_set_refuses_stale_reapproval(installed):
    s = installed
    assert approve(s)[0] == 200
    assert approve(s)[0] == 200
    row = machine(s)
    body = {
        "topics": ["work"],
        "provider_instance_id": "fixture-provider",
        "worker_connection_id": row["worker_connection_id"],
        "install_settings": {
            "grant_version": row["grant_version"],
            "binding_epoch": row["binding_epoch"],
            "settings_version": 1,
            "projects": {"demo": {**PROJECTS["demo"], "runtime_ceiling": "approval-required"}},
        },
    }
    assert (
        http(
            s.hub.port,
            "POST",
            "/api/runners/" + row["id"] + "/attest",
            cookie=s.owner["cookie"],
            body=body,
        )[0]
        == 409
    )
    assert fetch(s)[1]["receipt"]["settings_version"] == 2
