"""Browser consent and private machine delivery, with synthetic credentials only."""

# ruff: noqa: F811
import base64
import copy
import io
import json
import stat
import time
import zipfile
from dataclasses import replace
from datetime import timedelta
from email.message import Message
from types import SimpleNamespace

import pytest
from cryptography.exceptions import InvalidSignature

from commonplace import control_db, sessions
from commonplace.runner import (
    enrollment,
    install_receipts,
    registry,
    setup_api,
    setup_approvals as approvals,
)
from commonplace.runner.install_files import MANIFEST, STATE, digest
from commonplace.runner.install_host import InstallError
from commonplace.runner.runtime_install import Runtime
from commonplace.runner.setup_client import GRANT, Setup
from tests.test_runner_runtime_install import approve, ready, selection, setup  # noqa: F401

SECRET = "SyntheticHostGrantSentinel_" + "x" * 20


@pytest.fixture
def consented(ready):
    s = ready
    s.runtime, s.artifacts = selection(("codex",))
    s.runtime_host.artifacts = {digest(raw): raw for raw in s.artifacts.values()}
    assert approve(s, s.runtime)
    s.local.sync_receipts()
    s.gateway = SimpleNamespace(
        db=s.db, runner_receipt_signer=s.key, principal_for=lambda _: s.principal
    )

    def transport(token, path, body=None, *, nonce=None):
        principal = s.db.authenticate_token(token)
        if nonce is not None:
            return approvals.fetch(s.gateway, principal, nonce)
        if path == "/api/runner/secret-delivery":
            return approvals.delivery(s.gateway, principal, body)
        if body["action"] == "device_code":
            return approvals.present(s.gateway, principal, body)
        return approvals.report(s.gateway, principal, body)

    s.transport.setup = transport
    s.client = Setup(s.local, Runtime(s.local, s.config, s.transport, s.runtime_host))
    s.document = approvals.fetch(s.gateway, s.principal, "1" * 32)["receipt"]
    s.requests = {r["kind"]: r for r in s.document["requests"]}
    return s


def decision(s, row, **changes):
    return {
        "approve": True,
        "scope_digest": row["scope_digest"],
        "fingerprint": s.document["scope"]["machine_key_digest"],
        **changes,
    }


def allow(s, kind):
    row = s.requests[kind]
    return approvals.decide(s.gateway, s.owner, s.config["machine_id"], row["id"], decision(s, row))


def finish(s, monkeypatch):
    allow(s, "login")
    approvals.report(
        s.gateway, s.principal, s.client._proof(s.requests["login"], "login_succeeded")
    )
    with monkeypatch.context() as patch:
        patch.setattr(approvals.secrets, "token_urlsafe", lambda _: SECRET)
        allow(s, "grant")
    s.client._grant(s.requests["grant"], s.document["scope"])
    return next(
        row for row in approvals.attention(s.db, s.owner) if row["approval_kind"] == "attest"
    )


def final_body(s, row):
    return {
        **row["binding"],
        "setup_attestation": {
            "request_id": row["id"],
            "scope_digest": row["scope_digest"],
            "fingerprint": row["fingerprint"],
        },
    }


def test_fresh_owner_browser_grant_login_and_final_attestation(consented, monkeypatch):
    s = consented
    final = finish(s, monkeypatch)
    assert SECRET in json.loads(s.local.fs.read(GRANT))["secret"]
    assert stat.S_IMODE((s.home / GRANT).stat().st_mode) == 0o600
    grant = s.db._conn.execute("SELECT * FROM runner_host_grants").fetchone()
    assert grant["ciphertext"] is None and grant["acknowledged_at"]
    assert grant["secret_hash"] == enrollment._hash(SECRET)
    assert (
        registry.attest(s.db, s.owner, s.config["machine_id"], final_body(s, final))["verified"]
        is False
    )
    current = registry.get_machine(s.db, s.owner.workspace_id, s.config["machine_id"])
    assert current["binding_state"] == "owner_attested" and not current["enabled"]
    with pytest.raises(registry.Conflict):
        registry.patch(s.db, s.owner, current["id"], {"enabled": True})
    with pytest.raises(registry.Conflict):
        registry.attest(s.db, s.owner, current["id"], final_body(s, final))
    audits = s.db._conn.execute(
        "SELECT * FROM audit_log WHERE action IN ('runner.setup.approve','runner.setup.attest')"
    ).fetchall()
    assert len(audits) == 3 and all(
        r["user_id"] == s.owner.user_id and r["auth"] == "session" for r in audits
    )


def test_sentinel_absent_from_responses_logs_audit_needs_export_and_journal(
    consented, monkeypatch, caplog, capsys
):
    from commonplace.account import export, tables

    s = consented
    finish(s, monkeypatch)
    machine = registry.get_machine(s.db, s.owner.workspace_id, s.config["machine_id"])
    values = [
        install_receipts.owner_view(s.db, machine),
        approvals.attention(s.db, s.owner),
        approvals.fetch(s.gateway, s.principal, "2" * 32),
    ]
    values.extend(dict(r) for r in s.db._conn.execute("SELECT * FROM audit_log"))
    values.append(s.local.fs.read(MANIFEST).decode())
    output = io.BytesIO()
    archive = export._Archive(output)
    for table in ("runner_browser_requests", "runner_host_grants"):
        archive.rows(table, s.db._conn.execute("SELECT * FROM " + tables.quote(table)), redact=True)
    archive.zip.close()
    with zipfile.ZipFile(io.BytesIO(output.getvalue())) as z:
        values.extend(z.read(name).decode() for name in z.namelist())
    captured = capsys.readouterr()
    assert SECRET not in json.dumps(values) + caplog.text + captured.out + captured.err
    assert tables.unclassified(tables.present(s.db._conn)) == []


@pytest.mark.parametrize("caller", ["machine", "worker", "agent", "foreign-owner", "member"])
def test_no_token_foreign_person_or_nonowner_can_approve(consented, caller):
    s = consented
    if caller == "machine":
        who = s.principal
    elif caller == "worker":
        who = s.db.authenticate_token(s.local.fs.read(STATE + "/worker.token").decode())
    elif caller == "agent":
        who = replace(s.owner, auth="pat", person=False)
    else:
        user = s.db.create_user(caller + "@example.test")
        workspace = s.db.create_workspace("other", user)
        who = sessions.person_principal(s.db, user, workspace)
        if caller == "member":
            who = replace(who, workspace_id=s.owner.workspace_id, role="member")
    row = s.requests["grant"]
    with pytest.raises((enrollment.Refused, registry.NotFound)):
        approvals.decide(s.gateway, who, s.config["machine_id"], row["id"], decision(s, row))
    assert s.db._conn.execute("SELECT count(*) FROM runner_host_grants").fetchone()[0] == 0


@pytest.mark.parametrize(
    "change", ["fingerprint", "digest", "replay", "expiry", "scope", "revoke", "disable"]
)
def test_stale_replayed_or_mismatched_decision_cannot_mint(consented, change, monkeypatch):
    s = consented
    row = s.requests["grant"]
    data = decision(s, row)
    if change == "fingerprint":
        data["fingerprint"] = "f" * 64
    elif change == "digest":
        data["scope_digest"] = "f" * 64
    elif change == "replay":
        allow(s, "grant")
    elif change == "expiry":
        now = control_db._now()
        monkeypatch.setattr(control_db, "_now", lambda: now + timedelta(minutes=6))
    elif change == "scope":
        registry.patch(s.db, s.owner, s.config["machine_id"], {"max_runtime_mode": "auto"})
    elif change == "revoke":
        registry.revoke(s.db, s.owner, s.config["machine_id"])
    else:
        enrollment.set_enabled(s.db, s.owner, False)
    with pytest.raises((enrollment.Refused, registry.Conflict, install_receipts.ApprovalRequired)):
        approvals.decide(s.gateway, s.owner, s.config["machine_id"], row["id"], data)
    assert s.db._conn.execute("SELECT count(*) FROM runner_host_grants").fetchone()[0] == (
        change == "replay"
    )


def test_encrypted_delivery_recovery_ack_and_wrong_machine_proof(consented):
    s = consented
    allow(s, "grant")
    row = s.requests["grant"]
    body = s.client._proof(row, "fetch")
    first = approvals.delivery(s.gateway, s.principal, body)
    assert first == approvals.delivery(s.gateway, s.principal, body)
    assert SECRET not in json.dumps(first)
    corrupt = {**body, "scope_digest": "0" * 64}
    with pytest.raises(enrollment.Refused):
        approvals.delivery(s.gateway, s.principal, corrupt)
    corrupt = {**body, "signature": base64.b64encode(b"wrong-key").decode()}
    with pytest.raises(InvalidSignature):
        approvals.delivery(s.gateway, s.principal, corrupt)
    s.client._grant(row, s.document["scope"])
    with pytest.raises(registry.Conflict):
        approvals.delivery(s.gateway, s.principal, body)


def test_grant_expiry_revoke_and_kill_switch_remove_authority(consented, monkeypatch):
    s = consented
    finish(s, monkeypatch)
    registry.revoke(s.db, s.owner, s.config["machine_id"])
    grant = s.db._conn.execute("SELECT * FROM runner_host_grants").fetchone()
    assert grant["secret_hash"] is None and grant["ciphertext"] is None and grant["revoked_at"]
    assert approvals.attention(s.db, s.owner) == []
    assert (
        s.db._conn.execute(
            "SELECT count(*) FROM runner_browser_requests WHERE state!='revoked'"
        ).fetchone()[0]
        == 0
    )
    with pytest.raises(InstallError):
        s.local.sync_receipts()
    assert json.loads(s.local.fs.read(GRANT)) == {}


def test_device_code_is_volatile_once_hashed_and_bound_to_owner(consented):
    s = consented
    allow(s, "login")
    row = s.requests["login"]
    code = "ABCD-EFGH"
    body = s.client._proof(row, "device_code", user_code=code)
    approvals.present(s.gateway, s.principal, body)
    stored = s.db._conn.execute(
        "SELECT * FROM runner_browser_requests WHERE id=?", (row["id"],)
    ).fetchone()
    assert stored["proof_hash"] == enrollment._hash(code) and code not in str(dict(stored))
    result = approvals.reveal(s.gateway, s.owner, s.config["machine_id"], row["id"])
    assert result["url"] == "https://auth.openai.com/codex/device" and result["user_code"] == code
    with pytest.raises(registry.Conflict):
        approvals.reveal(s.gateway, s.owner, s.config["machine_id"], row["id"])
    with pytest.raises(registry.Conflict):
        approvals.present(s.gateway, s.principal, body)


def test_missing_supported_flow_is_a_precise_refusal_without_login(ready):
    s = ready
    s.local.sync_receipts()
    gateway = SimpleNamespace(db=s.db, runner_receipt_signer=s.key)
    document = approvals.fetch(gateway, s.principal, "0" * 32)["receipt"]
    row = next(r for r in document["requests"] if r["kind"] == "login")
    s.document = document
    assert row["provider"] == "claude"
    with pytest.raises(registry.Conflict, match="browser-only login unavailable"):
        approvals.decide(gateway, s.owner, s.config["machine_id"], row["id"], decision(s, row))
    assert next(r for r in approvals.attention(s.db, s.owner) if r.get("provider") == "claude")[
        "unsupported"
    ]


def handler(s, path, body, *, origin="https://hub.example.test"):
    raw = json.dumps(body).encode()
    headers = Message()
    headers["Content-Type"] = "application/json"
    headers["Content-Length"] = str(len(raw))
    headers["Host"] = "hub.example.test"
    headers["Origin"] = origin
    h = SimpleNamespace(
        path=path,
        command="POST",
        headers=headers,
        rfile=io.BytesIO(raw),
        wfile=io.BytesIO(),
        close_connection=False,
        send_header=lambda *a: None,
        end_headers=lambda: None,
    )
    h.send_response = lambda status: setattr(h, "status", status)
    s.gateway.allowed_hosts = {"hub.example.test"}
    s.gateway.secure_cookies = True
    return h


def test_routes_require_person_session_and_same_origin(consented):
    s = consented
    row = s.requests["grant"]
    path = f"/api/runners/{s.config['machine_id']}/setup-requests/{row['id']}/decide"
    s.gateway.principal_for = lambda _: s.owner
    h = handler(s, path, decision(s, row), origin="https://foreign.example.test")
    assert setup_api.owner_dispatch(s.gateway, h) and h.status == 403
    s.gateway.principal_for = lambda _: s.principal
    h = handler(s, path, decision(s, row))
    assert setup_api.owner_dispatch(s.gateway, h) and h.status == 404
    s.gateway.principal_for = lambda _: s.owner
    h = handler(s, path, decision(s, row))
    assert setup_api.owner_dispatch(s.gateway, h) and h.status == 200


def test_final_attestation_requires_fingerprint_prerequisites_and_cannot_replay(
    consented, monkeypatch
):
    s = consented
    assert not any(r.get("approval_kind") == "attest" for r in approvals.attention(s.db, s.owner))
    final = finish(s, monkeypatch)
    body = final_body(s, final)
    body["setup_attestation"]["fingerprint"] = "0" * 64
    with pytest.raises(registry.Conflict):
        registry.attest(s.db, s.owner, s.config["machine_id"], body)
    body = final_body(s, final)
    body["worker_connection_id"] = "wrong-worker"
    with pytest.raises(ValueError):
        registry.attest(s.db, s.owner, s.config["machine_id"], body)
    stored = s.db._conn.execute(
        "SELECT state FROM runner_browser_requests WHERE id=?", (final["id"],)
    ).fetchone()[0]
    assert stored == "pending", "a failed attestation rolls its consent consumption back"


def test_signed_wrong_scope_and_tampered_grant_are_not_written(consented):
    s = consented
    allow(s, "grant")
    real = s.transport.setup

    def changed(token, path, body=None, **kwargs):
        result = copy.deepcopy(real(token, path, body, **kwargs))
        if path.endswith("secret-delivery"):
            result["context"] += "wrong-machine"
        return result

    s.transport.setup = changed
    with pytest.raises(ValueError):
        s.client._grant(s.requests["grant"], s.document["scope"])
    assert not (s.home / GRANT).exists()
    assert (
        s.db._conn.execute("SELECT acknowledged_at FROM runner_host_grants").fetchone()[0] is None
    )


def test_account_export_and_deletion_cover_new_consent_rows(consented, monkeypatch, tmp_path):
    from commonplace.account import deletion

    s = consented
    finish(s, monkeypatch)
    sessions.ensure_schema(s.db)
    deletion.ensure_schema(s.db)
    session = sessions.start_session(s.db, s.owner.user_id, s.owner.workspace_id)
    gateway = SimpleNamespace(
        db=s.db,
        wall_clock=time.time,
        supervisor=SimpleNamespace(root=tmp_path / "data/workspaces", stop=lambda _: None),
    )
    monkeypatch.setenv("COMMONPLACE_ACCOUNT_DELETION", "on")
    deletion.request(gateway, s.owner, session, s.owner.user_email)
    assert deletion.purge(gateway, s.owner.user_id)["state"] == "purged"
    assert s.db._conn.execute("SELECT count(*) FROM runner_browser_requests").fetchone()[0] == 0
    assert s.db._conn.execute("SELECT count(*) FROM runner_host_grants").fetchone()[0] == 0


def test_setup_route_inventory_is_explicit_and_exempts_only_bound_facts_or_people():
    from commonplace.rules.enforce import RUNNER_SETUP_ROUTES

    assert {r.path for r in RUNNER_SETUP_ROUTES} == {
        "/api/runners/<machine>/setup-requests/<request>/decide",
        "/api/runners/<machine>/setup-requests/<request>/device",
        "/api/runners/<machine>/attest",
        "/api/runner/setup-requests",
        "/api/runner/secret-delivery",
    }
    assert all(r.method == "POST" and r.kind == "exempt" and r.reason for r in RUNNER_SETUP_ROUTES)


@pytest.mark.parametrize("path", ["/api/runner/setup-requests", "/api/runner/secret-delivery"])
def test_off_state_has_identical_refusals_and_no_extra_status(consented, path):
    s = consented
    worker = s.db.authenticate_token(s.local.fs.read(STATE + "/worker.token").decode())
    enrollment.set_enabled(s.db, s.owner, False)
    for principal in (s.owner, s.principal, worker):
        s.gateway.principal_for = lambda _, p=principal: p
        h = handler(s, path, {})
        setup_api.machine_dispatch(s.gateway, h)
        assert h.status == 404 and json.loads(h.wfile.getvalue()) == {"error": "not found"}
    assert approvals.attention(s.db, s.owner) == []


def test_expiry_and_scope_change_purge_grant_verifier(consented, monkeypatch):
    s = consented
    finish(s, monkeypatch)
    original = control_db._now()
    monkeypatch.setattr(control_db, "_now", lambda: original + timedelta(minutes=11))
    with s.db._tx():
        approvals.sweep_in(s.db)
    row = s.db._conn.execute("SELECT * FROM runner_host_grants").fetchone()
    assert row["secret_hash"] is None and row["ciphertext"] is None and row["revoked_at"]
    value = json.loads(s.local.fs.read(GRANT))
    value["signed"]["receipt"]["expires_at"] = "2000-01-01T00:00:00Z"
    value["signed"]["signature"] = base64.b64encode(
        s.key.sign(install_receipts.canonical(value["signed"]["receipt"]))
    ).decode()
    s.local.fs.write(GRANT, json.dumps(value).encode(), replace=True)
    with pytest.raises(ValueError):
        s.client._check_grant(s.requests["grant"], s.document["scope"])


def test_failed_audit_rolls_back_grant_and_decision(consented, monkeypatch):
    s = consented
    original = s.db.audit

    def fail(**kwargs):
        if kwargs["action"] == "runner.setup.approve":
            raise RuntimeError("SyntheticAuditFailureSentinel")
        return original(**kwargs)

    monkeypatch.setattr(s.db, "audit", fail)
    with pytest.raises(RuntimeError):
        allow(s, "grant")
    assert s.db._conn.execute("SELECT count(*) FROM runner_host_grants").fetchone()[0] == 0
    assert (
        s.db._conn.execute(
            "SELECT state FROM runner_browser_requests WHERE id=?", (s.requests["grant"]["id"],)
        ).fetchone()[0]
        == "pending"
    )


def test_installer_syncs_fresh_signed_decisions_and_clears_tampered_reply(consented, monkeypatch):
    from commonplace.runner.device_login import DeviceLogin

    s = consented
    # No provider process is needed until a browser has consented.
    monkeypatch.setattr(DeviceLogin, "advance", lambda *_: pytest.fail("unapproved login"))
    status = s.client.sync()
    assert status["providers"] == {"codex": "pending"} and status["agent_ready"] is False
    allow(s, "grant")
    assert s.client.sync()["grant"] == "received"
    real = s.transport.setup

    def corrupt(token, path, body=None, **kwargs):
        result = copy.deepcopy(real(token, path, body, **kwargs))
        if kwargs.get("nonce"):
            result["receipt"]["scope"]["machine_id"] = "wrong-machine"
        return result

    s.transport.setup = corrupt
    with pytest.raises(InstallError):
        s.client.sync()
    assert json.loads(s.local.fs.read(GRANT)) == {}


def test_verified_device_adapter_exchanges_on_machine_without_inherited_credentials(
    consented, capsys, caplog
):
    import signal

    from commonplace.runner.device_login import DeviceLogin, cleanup
    from commonplace.runner.runtime_install import HOME

    s = consented
    allow(s, "login")
    script = """import json,os,signal,sys
assert sys.stdin.read() == ''
assert not any('TOKEN' in k or 'GRANT' in k for k in os.environ)
assert sys.argv[-2:] == ['login','--device-auth']
def complete(*args):
    with open(os.path.join(os.environ['HOME'],'.codex','auth.json'),'w') as f:
        json.dump({'synthetic':'SyntheticProviderCredentialSentinel'},f)
    sys.exit(0)
signal.signal(signal.SIGUSR1,complete)
print('https://auth.openai.com/codex/device',flush=True)
print('ABCD-EFGH',flush=True)
signal.pause()
"""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("__main__.py", script)
    raw = output.getvalue()
    path = STATE + "/device-fixture"
    s.local.fs.write(path, raw)
    original = s.client.runtime
    s.client.runtime = SimpleNamespace(
        read=lambda: {"components": {"codex": {"path": path, "format": "python-zipapp"}}}
    )
    scope = copy.deepcopy(s.document["scope"])
    scope["runtime"]["components"]["codex"]["sha256"] = digest(raw)
    login = DeviceLogin()
    try:
        login.advance(s.client, s.requests["login"], scope)
        assert login.selector.select(timeout=5), "the synthetic CLI must provide its device prompt"
        login.advance(s.client, s.requests["login"], scope)
        assert login.presented
        shown = approvals.reveal(
            s.gateway, s.owner, s.config["machine_id"], s.requests["login"]["id"]
        )
        assert shown["user_code"] == "ABCD-EFGH"
        login.process.send_signal(signal.SIGUSR1)
        login.process.wait(timeout=5)
        login.advance(s.client, s.requests["login"], scope)
        assert login.process is None
        credential = s.home / HOME / ".codex/auth.json"
        assert stat.S_IMODE(credential.stat().st_mode) == 0o600
        assert "SyntheticProviderCredentialSentinel" in credential.read_text()
        captured = capsys.readouterr()
        assert (
            "SyntheticProviderCredentialSentinel" not in captured.out + captured.err + caplog.text
        )
        cleanup(s.local.fs, s.local.journal)
        assert not credential.exists()
    finally:
        login.stop()
        s.client.runtime = original
