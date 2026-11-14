"""Signed lifecycle, issuer isolation and real owned-process termination."""

# ruff: noqa: F811
import base64
import copy
import io
import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import timedelta
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from commonplace import control_db, sessions
from commonplace.runner import enrollment, enrollment_api, install_receipts, installer, registry
from tests.test_runner_setup_approvals import consented, finish, manual, ready, setup  # noqa: F401


def pulse(s, sequence=1, action="pulse", host_status=None):
    body = {
        "action": "lifecycle",
        "purpose": "runner-lifecycle",
        "operation": action,
        "workspace_id": s.owner.workspace_id,
        "enrollment_id": s.intent["enrollment_id"],
        "installation_id": s.config["installation_id"],
        "machine_id": s.config["machine_id"],
        "sequence": sequence,
        "nonce": f"{sequence:032x}",
        "host_status": host_status,
    }
    signature = s.local._key().sign(
        install_receipts.canonical(body),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
        hashes.SHA256(),
    )
    return {**body, "signature": base64.b64encode(signature).decode()}


def request(s, body, *, principal=None):
    raw = install_receipts.canonical(body)
    h = SimpleNamespace(
        path="/api/runner-enrollment/recover",
        command="POST",
        headers={"Content-Type": "application/json", "Content-Length": str(len(raw))},
        rfile=io.BytesIO(raw),
        wfile=io.BytesIO(),
        close_connection=False,
        send_header=lambda *a: None,
        end_headers=lambda: None,
    )
    h.send_response = lambda status: setattr(h, "status", status)
    gateway = SimpleNamespace(
        db=s.db,
        principal_for=lambda _: principal,
        runner_receipt_signer=s.key,
        runner_host_issuers=getattr(s, "issuers", {}),
    )
    enrollment_api._bootstrap(gateway, h)
    return h.status, json.loads(h.wfile.getvalue())


def test_revoke_keeps_stop_unconfirmed_until_supervisor_ack(consented):
    s = consented
    mid = s.config["machine_id"]
    registry.revoke(s.db, s.owner, mid)
    row = registry.get_machine(s.db, s.owner.workspace_id, mid)
    view = install_receipts.owner_view(s.db, row)
    assert view.get("lifecycle", {}).get("service") == "Revoked; stop unconfirmed", (
        "revoking credentials does not prove the service stopped"
    )


def test_setup_unit_bounds_and_cleans_all_owned_processes(tmp_path):
    unit = installer.service_text(tmp_path).decode()
    assert "KillMode=control-group" in unit, "escaped login children must stop too"
    assert "TimeoutStopSec=1" in unit
    assert "Restart=no" in unit, "revocation must not restart setup"
    assert "ExecStopPost=" in unit


def test_machine_signed_lifecycle_accepts_only_fresh_identity(consented):
    s = consented
    body = pulse(s)
    code, response = request(s, body)
    assert code == 200, "the bound machine needs a signed, finite setup lease"
    receipt = response["receipt"]
    s.key.public_key().verify(
        base64.b64decode(response["signature"]), install_receipts.canonical(receipt)
    )
    assert receipt["command"] == "continue" and 0 < receipt["lease_seconds"] <= 5
    assert receipt["nonce"] == body["nonce"]
    assert receipt["agent_ready"] is False
    assert request(s, body)[0] == 403, "replayed pulse must not renew authority"
    altered = pulse(s, 2)
    altered["installation_id"] = "another-installation"
    assert request(s, altered)[0] == 403


def host_envelope(s, signer, nonce):
    from commonplace.runner import setup_approvals

    machine = registry.get_machine(s.db, s.owner.workspace_id, s.config["machine_id"])
    _, scope, frozen = setup_approvals.context(s.db, machine)
    now = control_db._now()
    claims = {
        "version": 1,
        "purpose": "runner-host-status",
        "issuer": "approved-issuer",
        "nonce": nonce,
        "scope_digest": frozen,
        "identity": {
            k: scope[k]
            for k in (
                "workspace_id",
                "machine_id",
                "enrollment_id",
                "installation_id",
                "machine_key_digest",
                "provider_instance_id",
                "worker_connection_id",
                "grant_version",
                "binding_epoch",
                "settings_version",
            )
        },
        "scope_enforced": True,
        "watchdog_seconds": 4,
        "issued_at": control_db._iso(now),
        "expires_at": control_db._iso(now + timedelta(seconds=4)),
    }
    return {
        "receipt": claims,
        "signature": base64.b64encode(signer.sign(install_receipts.canonical(claims))).decode(),
    }


def test_host_status_requires_real_signature_against_configured_pin(consented, monkeypatch):
    s = consented
    finish(s, monkeypatch)
    signer = Ed25519PrivateKey.generate()
    s.issuers = {
        "approved-issuer": signer.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    }
    status = host_envelope(s, signer, f"{1:032x}")
    code, response = request(s, pulse(s, host_status=status))
    assert code == 200, "a real signature and an independently configured pin must verify"
    assert response["receipt"]["host_verification"] == "verified"
    wrong = Ed25519PrivateKey.generate()
    status = host_envelope(s, wrong, f"{2:032x}")
    assert request(s, pulse(s, 2, host_status=status))[0] == 403
    assert request(s, pulse(s, 3))[1]["receipt"]["host_verification"] == "unavailable"


def test_revoked_credentials_cannot_continue_but_bound_key_can_ack_stop(consented):
    s = consented
    assert request(s, pulse(s))[0] == 200
    machine_token = s.local.fs.read(installer.STATE + "/machine.token").decode()
    registry.revoke(s.db, s.owner, s.config["machine_id"])
    assert s.db.authenticate_token(machine_token) is None
    code, reply = request(s, pulse(s, 2))
    assert code == 200 and reply["receipt"]["command"] == "stop"
    assert reply["receipt"]["lease_seconds"] == 0
    assert request(s, pulse(s, 3, "stopped"))[0] == 200
    row = registry.get_machine(s.db, s.owner.workspace_id, s.config["machine_id"])
    assert install_receipts.owner_view(s.db, row)["lifecycle"]["service"] == "Stopped"
    assert request(s, pulse(s, 3, "stopped"))[0] == 403
    assert request(s, pulse(s, 4))[1]["receipt"]["command"] == "stop"


@pytest.mark.parametrize("field", ["nonce", "scope_digest", "identity", "expires_at"])
def test_host_signature_does_not_authorize_stale_or_foreign_status(consented, monkeypatch, field):
    s = consented
    finish(s, monkeypatch)
    signer = Ed25519PrivateKey.generate()
    s.issuers = {
        "approved-issuer": signer.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    }
    value = host_envelope(s, signer, f"{1:032x}")
    claims = copy.deepcopy(value["receipt"])
    claims[field] = "wrong" if field != "identity" else {**claims[field], "machine_id": "other"}
    value = {
        "receipt": claims,
        "signature": base64.b64encode(signer.sign(install_receipts.canonical(claims))).decode(),
    }
    assert request(s, pulse(s, host_status=value))[0] == 403


def test_disable_uses_stop_channel_without_restoring_any_authority(consented):
    s = consented
    assert request(s, pulse(s))[0] == 200
    enrollment.set_enabled(s.db, s.owner, False)
    assert request(s, pulse(s, 2))[1]["receipt"]["command"] == "stop"
    assert request(s, pulse(s, 3, "stopped"))[0] == 200
    assert install_receipts.attention(s.db, s.owner) == []
    enrollment.set_enabled(s.db, s.owner, True)
    assert request(s, pulse(s, 4))[1]["receipt"]["command"] == "stop"


def test_tokens_cannot_use_the_post_revocation_channel(consented):
    s = consented
    for principal in (s.principal, s.owner):
        assert request(s, pulse(s), principal=principal)[0] == 404


def test_terminal_cli_is_supervised_with_its_own_unit_scope(manual, monkeypatch):
    s = manual
    real = s.runtime_host.probe

    def terminal(name, release, raw, home, **options):
        if options.get("sign_in"):
            s.runtime_host.facts[name] = {"availability": "present", "login": "unverified"}
        return real(name, release, raw, home)

    monkeypatch.setattr(s.runtime_host, "probe", terminal)
    assert s.local.sign_in("claude") == 0
    assert "terminal-scope" in s.host.commands, (
        "revocation must terminate the owner's pending CLI too"
    )
    with pytest.raises(installer.InstallError, match="supervised terminal sign-in is required"):
        s.local.sign_in("claude", supervised=True)


def test_unrequested_stop_ack_and_foreign_identity_are_refused(consented):
    s = consented
    assert request(s, pulse(s, action="stopped"))[0] == 403
    # A different identity cannot borrow the installed key's signature.
    body = pulse(s)
    body["workspace_id"] = "other-workspace"
    assert request(s, body)[0] == 403
    assert request(s, pulse(s))[0] == 200


def test_audit_failure_rolls_back_shutdown_and_lease_state(consented, monkeypatch):
    s = consented
    with monkeypatch.context() as patch:
        patch.setattr(s.db, "audit", lambda **_: (_ for _ in ()).throw(RuntimeError("unavailable")))
        assert request(s, pulse(s))[0] == 503
    assert request(s, pulse(s))[0] == 200
    with monkeypatch.context() as patch:
        patch.setattr(s.db, "audit", lambda **_: (_ for _ in ()).throw(RuntimeError("unavailable")))
        with pytest.raises(RuntimeError):
            registry.revoke(s.db, s.owner, s.config["machine_id"])
    assert s.db.authenticate_token(s.local.fs.read(installer.STATE + "/machine.token").decode())
    assert request(s, pulse(s, 2))[1]["receipt"]["command"] == "continue"


@pytest.mark.parametrize("reason", ["revoke", "network", "blocked-response"])
def test_supervision_stops_owned_processes_within_offline_bound_and_keeps_unrelated(
    tmp_path,
    reason,
):
    from commonplace.runner.supervisor import supervise

    pidfile = tmp_path / "owned-child"
    # A child ignoring graceful shutdown must still die; the unrelated process
    # is deliberately outside the owned session.
    program = (
        "import subprocess,sys,time,signal;"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
        "p=subprocess.Popen([sys.executable,'-c','import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(60)']);"
        "open(sys.argv[1],'w').write(str(p.pid));time.sleep(60)"
    )
    unrelated = subprocess.Popen(
        [sys.executable, "-c", "import time;time.sleep(60)"], start_new_session=True
    )
    calls = 0
    child = None

    def renew():
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"command": "continue", "lease_seconds": 4}
        if reason == "network":
            raise OSError("unreachable")
        if reason == "blocked-response":
            threading.Event().wait(10)
        return {"command": "stop", "lease_seconds": 0}

    def started(process):
        nonlocal child
        child = process

    start = time.monotonic()
    try:
        assert (
            supervise([sys.executable, "-c", program, str(pidfile)], renew, on_start=started) == 0
        )
        assert time.monotonic() - start < 5
        assert child.poll() is not None
        assert unrelated.poll() is None
        pid = int(pidfile.read_text())
        status = __import__("pathlib").Path(f"/proc/{pid}/stat")
        assert not status.exists() or status.read_text().split()[2] == "Z", (
            "no owned child is left running"
        )
    finally:
        if child and child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=2)
        unrelated.terminate()
        unrelated.wait(timeout=2)


def test_post_stop_clears_private_credentials_before_ack(consented):
    from commonplace.runner import supervisor

    s = consented
    s.transport.lifecycle = lambda body: _successful_request(s, body)
    channel = supervisor.Channel(s.local)
    assert channel.exchange()["command"] == "continue"
    registry.revoke(s.db, s.owner, s.config["machine_id"])
    with pytest.raises(installer.InstallError, match="service stop unconfirmed"):
        supervisor.stopped(s.home, transport=s.transport, host=s.host)
    s.host.change("disable")
    assert supervisor.stopped(s.home, transport=s.transport, host=s.host) == 0
    assert s.local.fs.read(installer.STATE + "/machine.token") == b""
    assert s.local.fs.read(installer.STATE + "/worker.token") == b""


def _successful_request(s, body):
    code, reply = request(s, body)
    assert code == 200
    return reply


def test_trust_configuration_uses_only_private_explicit_files(tmp_path, monkeypatch):
    from commonplace.runner import trust

    signer = Ed25519PrivateKey.generate()
    key = tmp_path / "approval.pem"
    key.write_bytes(
        signer.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key.chmod(0o600)
    path = tmp_path / "trust.json"
    path.write_text(json.dumps({"approval_private_key_file": str(key), "host_issuers": {}}))
    path.chmod(0o600)
    gateway = SimpleNamespace()
    trust.configure(gateway, str(path))
    assert gateway.runner_host_issuers == {}
    message = b"fresh challenge"
    signer.public_key().verify(gateway.runner_receipt_signer.sign(message), message)
    key.chmod(0o644)
    with pytest.raises(ValueError, match="runner trust configuration unavailable"):
        trust.configure(SimpleNamespace(), str(path))
    key.chmod(0o600)
    link = tmp_path / "untrusted-link"
    link.symlink_to(path)
    with pytest.raises(ValueError):
        trust.configure(SimpleNamespace(), str(link))
    monkeypatch.delenv("COMMONPLACE_RUNNER_TRUST_FILE", raising=False)
    empty = SimpleNamespace()
    trust.configure(empty)
    assert not vars(empty), "there is no generated or fixture trust fallback"


def test_lifecycle_export_deletion_and_secret_sentinel(
    consented, monkeypatch, tmp_path, caplog, capsys
):
    from commonplace.account import deletion, export, tables
    from tests.test_runner_setup_approvals import SECRET

    s = consented
    finish(s, monkeypatch)
    code, response = request(s, pulse(s))
    assert code == 200
    output = io.BytesIO()
    archive = export._Archive(output)
    archive.rows(
        "runner_lifecycle", s.db._conn.execute("SELECT * FROM runner_lifecycle"), redact=True
    )
    archive.zip.close()
    assert tables.unclassified(tables.present(s.db._conn)) == []
    events = [dict(row) for row in s.db._conn.execute("SELECT * FROM audit_log")]
    captured = capsys.readouterr()
    assert SECRET not in json.dumps([response, events]) + caplog.text + captured.out + captured.err
    assert SECRET.encode() not in output.getvalue()
    sessions.ensure_schema(s.db)
    deletion.ensure_schema(s.db)
    session = sessions.start_session(s.db, s.owner.user_id, s.owner.workspace_id)
    gateway = SimpleNamespace(
        db=s.db,
        wall_clock=time.time,
        supervisor=SimpleNamespace(
            root=tmp_path / "data/workspaces",
            stop=lambda _: None,
        ),
    )
    monkeypatch.setenv("COMMONPLACE_ACCOUNT_DELETION", "on")
    deletion.request(gateway, s.owner, session, s.owner.user_email)
    assert deletion.purge(gateway, s.owner.user_id)["state"] == "purged"
    assert s.db._conn.execute("SELECT count(*) FROM runner_lifecycle").fetchone()[0] == 0
