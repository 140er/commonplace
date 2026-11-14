"""Dormant enrollment, atomic exchange and private interrupted-delivery recovery."""

# ruff: noqa: F811

import base64
import hashlib
import io
import json
import secrets
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from commonplace import control_db
from commonplace.account import export, tables
from tests.runner_harness import (
    cli_token,
    http,
    member,
    owners,  # noqa: F401
    signup_hub,  # noqa: F401
)

SETTINGS = "/api/runners/install-settings"
ISSUE = "/api/runners/enrollments"
EXCHANGE = "/api/runner-enrollment/exchange"
RECOVER = "/api/runner-enrollment/recover"
DIGEST = "a" * 64
GRANT = {"label": "test-box", "projects": ["demo"], "topics": ["work"], "wings": ["shared"]}


def enable(hub, owner, value=True):
    response = http(hub.port, "PUT", SETTINGS, cookie=owner["cookie"], body={"enabled": value})
    assert response[0] == 200, response
    return response[1]


def issue(hub, owner, *, request_id="test-request", digest=DIGEST, label="test-box"):
    return http(
        hub.port,
        "POST",
        ISSUE,
        cookie=owner["cookie"],
        body={
            "request_id": request_id,
            "manifest_digest": digest,
            "grant": {**GRANT, "label": label},
        },
    )


@pytest.fixture(scope="module")
def machine_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=3072)


def public(key):
    return (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )


@pytest.fixture
def setup(signup_hub, owners, machine_key):
    hub, owner = signup_hub, owners["A"]
    assert (
        http(
            hub.port,
            "POST",
            "/api/memory",
            cookie=owner["cookie"],
            body={"text": "Synthetic enrollment fixture", "theme": "shared"},
        )[0]
        == 201
    )

    def prepare():
        enable(hub, owner)
        status, issued = issue(hub, owner)
        assert status == 201, (status, issued)
        data = {
            "workspace_id": owner["workspace"],
            "machine_label": "test-box",
            "manifest_digest": DIGEST,
            "code": issued["code"],
            "installation_id": "install-test",
            "public_key": public(machine_key),
        }
        return hub, owner, issued, data

    return prepare


def exchange(hub, data, **auth):
    return http(hub.port, "POST", EXCHANGE, body=data, origin=False, **auth)


def recovery_identity(issued, data):
    return {k: data[k] for k in ("workspace_id", "installation_id", "public_key")} | {
        "enrollment_id": issued["id"]
    }


def recover(hub, identity, key, *, action="recover"):
    status, challenge = http(
        hub.port, "POST", RECOVER, body={**identity, "action": "challenge"}, origin=False
    )
    assert status == 200, (status, challenge)
    signature = key.sign(
        (action + ":" + challenge["challenge"]).encode(),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
        hashes.SHA256(),
    )
    body = {
        **identity,
        "action": action,
        "challenge": challenge["challenge"],
        "signature": base64.b64encode(signature).decode(),
    }
    return http(hub.port, "POST", RECOVER, body=body, origin=False), body


def decrypt(key, envelope):
    secret = key.decrypt(
        base64.b64decode(envelope["wrapped_key"]),
        padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )
    return json.loads(
        AESGCM(secret).decrypt(
            base64.b64decode(envelope["nonce"]),
            base64.b64decode(envelope["ciphertext"]),
            envelope["context"].encode(),
        )
    )


def test_clean_workspace_default_off_creates_nothing(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    before = list(hub.data_dir.rglob("*"))
    with hub.db._lock:
        existing = {
            r[0] for r in hub.db._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        audit = hub.db._conn.execute("SELECT count(*) FROM audit_log").fetchone()[0]
    for method, path, body in (
        ("GET", SETTINGS, None),
        ("POST", ISSUE, {}),
        ("GET", ISSUE + "/missing", None),
        ("DELETE", ISSUE + "/missing", None),
        ("POST", EXCHANGE, {"workspace_id": owner["workspace"]}),
        ("POST", RECOVER, {"workspace_id": owner["workspace"]}),
    ):
        assert http(hub.port, method, path, cookie=owner["cookie"], body=body)[0] == 404
        assert http(hub.port, method, path, body=body)[0] == 404
    with hub.db._lock:
        assert hub.db._conn.execute("SELECT count(*) FROM runner_machines").fetchone()[0] == 0
        assert hub.db._conn.execute("SELECT count(*) FROM audit_log").fetchone()[0] == audit
        for name in (
            "runner_install_settings",
            "runner_enrollments",
            "runner_secret_deliveries",
            "runner_enrollment_attempts",
        ):
            if name in existing:
                assert (
                    hub.db._conn.execute('SELECT count(*) FROM "' + name + '"').fetchone()[0] == 0
                )
    assert list(hub.data_dir.rglob("*")) == before


def test_one_code_enrolls_one_workspace_label_key_without_activation(setup, machine_key):
    hub, owner, issued, data = setup()
    status, envelope = exchange(hub, data)
    assert status == 201, (status, envelope)
    credentials = decrypt(machine_key, envelope)
    assert credentials["machine_token"] != credentials["worker_token"]
    from commonplace.runner import registry

    row = registry.get_machine(hub.db, owner["workspace"], credentials["machine_id"])
    assert row["label"] == data["machine_label"]
    assert row["binding_state"] == "unbound" and row["enabled"] == 0
    for kind in ("machine", "worker"):
        principal = hub.db.authenticate_token(credentials[f"{kind}_token"])
        assert principal.workspace_id == owner["workspace"] and principal.runner == kind
    assert exchange(hub, data) == (403, {"error": "enrollment refused"})
    assert issued["code"] not in json.dumps(envelope)
    assert len(base64.urlsafe_b64decode(issued["code"] + "=")) == 32


@pytest.mark.parametrize(
    "mutation", ["code", "digest", "workspace", "label", "expired", "owner", "settings"]
)
def test_invalid_expired_and_stale_intent_fail_closed(setup, owners, monkeypatch, mutation):
    hub, owner, issued, original = setup()
    data = dict(original)
    if mutation == "code":
        data["code"] = secrets.token_urlsafe(32)
    elif mutation == "digest":
        data["manifest_digest"] = "b" * 64
    elif mutation == "workspace":
        enable(hub, owners["B"])
        data["workspace_id"] = owners["B"]["workspace"]
    elif mutation == "label":
        data["machine_label"] = "foreign-box"
    elif mutation == "expired":
        now = control_db._now()
        monkeypatch.setattr(control_db, "_now", lambda: now + timedelta(minutes=5))
    elif mutation == "owner":
        with hub.db._lock:
            hub.db._conn.execute(
                "UPDATE users SET disabled_at=? WHERE id=?",
                (control_db._iso(control_db._now()), owner["user_id"]),
            )
    else:
        with hub.db._lock:
            hub.db._conn.execute(
                "UPDATE runner_install_settings SET version=version+1 WHERE workspace_id=?",
                (owner["workspace"],),
            )
    assert exchange(hub, data) == (403, {"error": "enrollment refused"})
    with hub.db._lock:
        assert hub.db._conn.execute("SELECT count(*) FROM runner_machines").fetchone()[0] == 0


def test_two_concurrent_redeems_have_exactly_one_winner(setup):
    hub, _, _, data = setup()
    barrier = threading.Barrier(2)

    def run():
        barrier.wait(timeout=5)
        return exchange(hub, data)[0]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert sorted(results) == [201, 403]
    with hub.db._lock:
        assert hub.db._conn.execute("SELECT count(*) FROM runner_machines").fetchone()[0] == 1
        assert hub.db._conn.execute("SELECT count(*) FROM runner_connections").fetchone()[0] == 2


@pytest.mark.parametrize("boundary", ["second_token", "encryption", "audit", "delivery"])
def test_crash_boundaries_roll_back_code_machine_credentials_and_delivery(
    setup, monkeypatch, boundary
):
    hub, _, issued, data = setup()
    from commonplace.runner import enrollment_crypto

    with monkeypatch.context() as patch:

        def fail(*args, **kwargs):
            raise RuntimeError(data["code"])

        if boundary == "second_token":
            real, calls = hub.db.create_token, []

            def token(**kwargs):
                calls.append(1)
                return fail() if len(calls) == 2 else real(**kwargs)

            patch.setattr(hub.db, "create_token", token)
        elif boundary == "encryption":
            patch.setattr(enrollment_crypto, "seal", fail)
        elif boundary == "audit":
            real = hub.db.audit

            def audit(**kwargs):
                return (
                    fail() if kwargs["action"] == "runner.enrollment.exchange" else real(**kwargs)
                )

            patch.setattr(hub.db, "audit", audit)
        else:
            with hub.db._lock:
                hub.db._conn.execute(
                    "CREATE TRIGGER fail_delivery BEFORE INSERT ON runner_secret_deliveries BEGIN SELECT RAISE(ABORT, 'synthetic'); END"
                )
        assert exchange(hub, data) == (503, {"error": "enrollment unavailable"})
    with hub.db._lock:
        hub.db._conn.execute("DROP TRIGGER IF EXISTS fail_delivery")
        assert (
            hub.db._conn.execute(
                "SELECT consumed_at FROM runner_enrollments WHERE id=?", (issued["id"],)
            ).fetchone()[0]
            is None
        )
        assert hub.db._conn.execute("SELECT count(*) FROM runner_machines").fetchone()[0] == 0
        assert (
            hub.db._conn.execute("SELECT count(*) FROM runner_secret_deliveries").fetchone()[0] == 0
        )
        assert hub.db._conn.execute("SELECT count(*) FROM runner_connections").fetchone()[0] == 0
        assert (
            hub.db._conn.execute(
                "SELECT count(*) FROM connections WHERE client='commonplace-runner'"
            ).fetchone()[0]
            == 0
        )
    assert exchange(hub, data)[0] == 201


def test_lost_response_recovers_same_ciphertext_and_ack_purges(setup, machine_key):
    hub, _, issued, data = setup()
    status, first = exchange(hub, data)
    assert status == 201
    identity = recovery_identity(issued, data)
    (status, recovered), replay = recover(hub, identity, machine_key)
    assert status == 200 and recovered == first
    assert http(hub.port, "POST", RECOVER, body=replay, origin=False)[0] == 403
    (status, acknowledged), _ = recover(hub, identity, machine_key, action="ack")
    assert (status, acknowledged) == (200, {"ok": True})
    assert (
        http(hub.port, "POST", RECOVER, body={**identity, "action": "challenge"}, origin=False)[0]
        == 403
    )
    with hub.db._lock:
        assert (
            hub.db._conn.execute("SELECT ciphertext FROM runner_secret_deliveries").fetchone()[0]
            is None
        )
        assert hub.db._conn.execute("SELECT count(*) FROM runner_machines").fetchone()[0] == 1


@pytest.mark.parametrize(
    "mutation", ["key", "installation", "workspace", "signature", "expired", "revoked", "disabled"]
)
def test_recovery_is_key_bound_current_and_short_lived(
    setup, machine_key, owners, monkeypatch, mutation
):
    hub, owner, issued, data = setup()
    assert exchange(hub, data)[0] == 201
    identity = recovery_identity(issued, data)
    if mutation == "key":
        identity["public_key"] = public(
            rsa.generate_private_key(public_exponent=65537, key_size=3072)
        )
    elif mutation == "installation":
        identity["installation_id"] = "wrong-install"
    elif mutation == "workspace":
        enable(hub, owners["B"])
        identity["workspace_id"] = owners["B"]["workspace"]
    elif mutation == "expired":
        now = control_db._now()
        monkeypatch.setattr(control_db, "_now", lambda: now + timedelta(minutes=10))
    elif mutation == "revoked":
        assert (
            http(hub.port, "DELETE", ISSUE + "/" + issued["id"], cookie=owner["cookie"])[0] == 200
        )
    elif mutation == "disabled":
        enable(hub, owner, False)
    if mutation == "signature":
        status, challenge = http(
            hub.port, "POST", RECOVER, body={**identity, "action": "challenge"}, origin=False
        )
        assert status == 200
        body = {
            **identity,
            "action": "recover",
            "challenge": challenge["challenge"],
            "signature": base64.b64encode(b"wrong").decode(),
        }
    else:
        body = {**identity, "action": "challenge"}
    status, response = http(hub.port, "POST", RECOVER, body=body, origin=False)
    assert status == (404 if mutation == "disabled" else 403)
    assert "ciphertext" not in response


def test_one_time_code_never_returns_or_persists_in_any_later_surface(setup, machine_key, caplog):
    hub, owner, issued, data = setup()
    code = issued["code"]
    status, repeated = issue(hub, owner)
    assert status == 200 and "code" not in repeated
    assert issue(hub, owner, digest="b" * 64)[0] == 409
    assert issue(hub, owner, request_id="second-request")[0] == 409
    responses = [repeated]
    responses.append(http(hub.port, "GET", ISSUE + "/" + issued["id"], cookie=owner["cookie"])[1])
    status, envelope = exchange(hub, data)
    assert status == 201
    credentials = decrypt(machine_key, envelope)
    responses += [envelope, exchange(hub, data)[1]]
    responses.append(http(hub.port, "GET", "/api/runners", cookie=owner["cookie"])[1])
    status, pending = issue(hub, owner, request_id="pending-export", label="pending-box")
    assert status == 201
    raw = io.BytesIO()
    export.write(hub.gateway, export.plan(hub.gateway, owner["user_id"]), raw)
    archive = zipfile.ZipFile(io.BytesIO(raw.getvalue()))
    exported = b"\n".join(archive.read(n) for n in archive.namelist())
    with hub.db._lock:
        rows = "\n".join(hub.db._conn.iterdump())
    assert code not in json.dumps(responses) + caplog.text + rows
    assert (
        code.encode() not in exported
        and hashlib.sha256(code.encode()).hexdigest().encode() not in exported
    )
    for kind in ("machine", "worker"):
        assert credentials[f"{kind}_token"].encode() not in exported
    for path in hub.data_dir.rglob("*"):
        if path.is_file():
            assert code.encode() not in path.read_bytes()
    assert pending["code"].encode() not in exported
    assert hashlib.sha256(pending["code"].encode()).hexdigest().encode() not in exported
    assert b'"code_hash": "[redacted]"' in exported
    assert b'"ciphertext": "[redacted]"' in exported


def test_disable_reenable_and_revoke_never_revive_codes_or_credentials(setup, machine_key):
    hub, owner, issued, data = setup()
    status, envelope = exchange(hub, data)
    assert status == 201
    credentials = decrypt(machine_key, envelope)
    enable(hub, owner, False)
    for kind in ("machine", "worker"):
        assert hub.db.authenticate_token(credentials[f"{kind}_token"]) is None
    enable(hub, owner)
    assert exchange(hub, data)[0] == 403
    status, fresh = issue(hub, owner, request_id="new-request")
    assert status == 201
    assert http(hub.port, "DELETE", ISSUE + "/" + fresh["id"], cookie=owner["cookie"])[0] == 200
    assert exchange(hub, {**data, "code": fresh["code"]})[0] == 403


@pytest.mark.parametrize("condition", ["wrong", "expired", "used"])
def test_invalid_attempts_are_counted_and_rate_limited_uniformly(setup, monkeypatch, condition):
    hub, owner, _, data = setup()
    frozen = control_db._now()
    monkeypatch.setattr(control_db, "_now", lambda: frozen)
    if condition == "used":
        assert exchange(hub, data)[0] == 201
    elif condition == "expired":
        now = control_db._now()
        monkeypatch.setattr(control_db, "_now", lambda: now + timedelta(minutes=5))
    else:
        data = {**data, "code": secrets.token_urlsafe(32)}
    results = [exchange(hub, data) for _ in range(12)]
    assert results[0] == (403, {"error": "enrollment refused"})
    assert results[-1] == (429, {"error": "enrollment refused"})
    with hub.db._lock:
        row = hub.db._conn.execute(
            "SELECT attempts FROM runner_enrollment_attempts WHERE workspace_id=? AND purpose='exchange'",
            (owner["workspace"],),
        ).fetchone()
        assert row[0] == (13 if condition == "used" else 12)
        reasons = [
            r[0]
            for r in hub.db._conn.execute(
                "SELECT DISTINCT reason FROM audit_log WHERE action='runner.enrollment.denied'"
            )
        ]
    assert set(reasons) <= {"refused", "rate_limited"}


@pytest.mark.parametrize("caller", ["member", "agent", "machine", "worker", "app", "foreign"])
def test_only_current_person_owner_issues_and_tokens_never_exchange(
    setup, owners, machine_key, caller
):
    hub, owner, issued, data = setup()
    if caller == "member":
        auth = {"cookie": member(hub, owner, owners["B"])["cookie"]}
    elif caller == "foreign":
        enable(hub, owners["B"])
        auth = {"cookie": owners["B"]["cookie"]}
    elif caller in ("machine", "worker"):
        status, envelope = exchange(hub, data)
        assert status == 201
        credentials = decrypt(machine_key, envelope)
        auth = {"token": credentials[f"{caller}_token"]}
    elif caller == "app":
        # OAuth credentials remain application authority even for an owner account.
        from tests.test_commonplace_oauth import Client

        client = Client(hub)
        name, value = owner["cookie"].split("=", 1)
        client.cookies[name] = value
        cid = client.register()
        code = client.consent(cid)
        status, _, text = client.exchange(cid, code)
        assert status == 200
        auth = {"token": json.loads(text)["access_token"]}
    else:
        _, token = cli_token(owner, "enrollment-test")
        auth = {"token": token}
    assert http(
        hub.port,
        "POST",
        ISSUE,
        body={
            "workspace_id": owner["workspace"],
            "request_id": "forbidden",
            "manifest_digest": DIGEST,
            "grant": GRANT,
        },
        **auth,
    )[0] in (400, 404)
    assert http(hub.port, "GET", ISSUE + "/" + issued["id"], **auth)[0] == 404
    assert exchange(hub, data, **auth)[0] == 404
    if caller != "foreign":
        assert http(hub.port, "PUT", SETTINGS, body={"enabled": True}, **auth)[0] == 404


def test_owner_writes_require_same_origin_including_settings_put(setup):
    hub, owner, _, _ = setup()
    for method, path, body in (("PUT", SETTINGS, {"enabled": False}), ("POST", ISSUE, {})):
        assert (
            http(
                hub.port,
                method,
                path,
                cookie=owner["cookie"],
                body=body,
                origin="https://foreign.example.test",
            )[0]
            == 403
        )
    assert http(hub.port, "GET", SETTINGS, cookie=owner["cookie"])[1]["enabled"] is True


def test_expiry_sweep_purges_recovery_and_expired_pending_codes(setup, machine_key, monkeypatch):
    hub, owner, issued, data = setup()
    assert exchange(hub, data)[0] == 201
    status, pending = issue(hub, owner, request_id="pending", label="second-box")
    assert status == 201
    now = control_db._now()
    monkeypatch.setattr(control_db, "_now", lambda: now + timedelta(minutes=10))
    from commonplace.runner import enrollment

    enrollment.sweep(hub.db)
    with hub.db._lock:
        assert (
            hub.db._conn.execute("SELECT ciphertext FROM runner_secret_deliveries").fetchone()[0]
            is None
        )
        row = hub.db._conn.execute(
            "SELECT code_hash, revoked_at FROM runner_enrollments WHERE id=?", (pending["id"],)
        ).fetchone()
        assert row[0] is None and row[1]
    assert issue(hub, owner, request_id="replacement", label="second-box")[0] == 201


def test_new_control_tables_are_classified_and_workspace_deletable(setup):
    hub, owner, _, data = setup()
    assert exchange(hub, data)[0] == 201
    with hub.db._lock:
        present = tables.present(hub.db._conn)
        assert tables.unclassified(present) == []
        for name in (
            "runner_install_settings",
            "runner_enrollments",
            "runner_secret_deliveries",
            "runner_enrollment_attempts",
        ):
            assert tables.RULES[name].workspace == ("workspace_id",)
            assert tables.workspace_clause(name, present) is not None
    assert tables.secret("code_hash") and tables.secret("ciphertext")


def test_existing_machine_revoke_purges_recovery_in_same_transaction(setup, machine_key):
    hub, owner, issued, data = setup()
    status, envelope = exchange(hub, data)
    assert status == 201
    credentials = decrypt(machine_key, envelope)
    assert (
        http(
            hub.port, "DELETE", "/api/runners/" + credentials["machine_id"], cookie=owner["cookie"]
        )[0]
        == 200
    )
    with hub.db._lock:
        row = hub.db._conn.execute(
            "SELECT revoked_at FROM runner_enrollments WHERE id=?", (issued["id"],)
        ).fetchone()
        assert row[0]
        assert (
            hub.db._conn.execute("SELECT ciphertext FROM runner_secret_deliveries").fetchone()[0]
            is None
        )
    assert (
        http(
            hub.port,
            "POST",
            RECOVER,
            body={**recovery_identity(issued, data), "action": "challenge"},
            origin=False,
        )[0]
        == 403
    )


def test_account_purge_removes_setup_state_and_preserves_other_workspace(
    setup, owners, monkeypatch
):
    hub, owner, _, data = setup()
    assert exchange(hub, data)[0] == 201
    enable(hub, owners["B"])
    monkeypatch.setenv("COMMONPLACE_ACCOUNT_DELETION", "on")
    assert (
        http(
            hub.port,
            "POST",
            "/api/account/deletion",
            cookie=owner["cookie"],
            body={"confirm": owner["email"]},
        )[0]
        == 200
    )
    from commonplace.account import deletion

    result = deletion.purge(hub.gateway, owner["user_id"])
    assert result["state"] == "purged", result
    with hub.db._lock:
        assert hub.db._conn.execute("SELECT count(*) FROM runner_enrollments").fetchone()[0] == 0
        assert (
            hub.db._conn.execute("SELECT count(*) FROM runner_secret_deliveries").fetchone()[0] == 0
        )
        assert (
            hub.db._conn.execute("SELECT count(*) FROM runner_enrollment_attempts").fetchone()[0]
            == 0
        )
        assert [
            r[0] for r in hub.db._conn.execute("SELECT workspace_id FROM runner_install_settings")
        ] == [owners["B"]["workspace"]]


@pytest.mark.parametrize(
    "invalid", ["unknown", "weak-key", "malformed-key", "empty-installation", "extra-workspace"]
)
def test_malformed_bootstrap_never_echoes_or_consumes_code(setup, invalid):
    hub, _, _, data = setup()
    changed = dict(data)
    if invalid == "unknown":
        changed["extra"] = data["code"]
    elif invalid == "weak-key":
        changed["public_key"] = public(
            rsa.generate_private_key(public_exponent=65537, key_size=2048)
        )
    elif invalid == "malformed-key":
        changed["public_key"] = data["code"]
    elif invalid == "empty-installation":
        changed["installation_id"] = ""
    else:
        changed["workspace_id"] = [data["workspace_id"]]
    status, body = exchange(hub, changed)
    assert status in (403, 404)
    assert data["code"] not in json.dumps(body)
    assert exchange(hub, data)[0] == 201


def test_recovery_signature_is_bound_to_action_and_challenge_expiry(
    setup, machine_key, monkeypatch
):
    hub, _, issued, data = setup()
    assert exchange(hub, data)[0] == 201
    identity = recovery_identity(issued, data)
    status, reply = http(
        hub.port, "POST", RECOVER, body={**identity, "action": "challenge"}, origin=False
    )
    assert status == 200
    signature = machine_key.sign(
        ("recover:" + reply["challenge"]).encode(),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
        hashes.SHA256(),
    )
    body = {**identity, **reply, "action": "ack", "signature": base64.b64encode(signature).decode()}
    assert http(hub.port, "POST", RECOVER, body=body, origin=False)[0] == 403
    body["action"] = "recover"
    now = control_db._now()
    monkeypatch.setattr(control_db, "_now", lambda: now + timedelta(seconds=60))
    assert http(hub.port, "POST", RECOVER, body=body, origin=False)[0] == 403


def test_workspace_kill_switch_rolls_back_when_audit_is_unavailable(
    setup, machine_key, monkeypatch
):
    hub, owner, _, data = setup()
    status, envelope = exchange(hub, data)
    assert status == 201
    credentials = decrypt(machine_key, envelope)
    original = hub.db.audit

    def fail(**kwargs):
        if kwargs["action"] == "runner.enrollment.disable":
            raise RuntimeError(data["code"])
        return original(**kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(hub.db, "audit", fail)
        assert (
            http(hub.port, "PUT", SETTINGS, cookie=owner["cookie"], body={"enabled": False})[0]
            == 503
        )
    assert http(hub.port, "GET", SETTINGS, cookie=owner["cookie"])[1]["enabled"] is True
    for kind in ("machine", "worker"):
        assert hub.db.authenticate_token(credentials[f"{kind}_token"]) is not None
    enable(hub, owner, False)
    for kind in ("machine", "worker"):
        assert hub.db.authenticate_token(credentials[f"{kind}_token"]) is None


def test_other_workspace_opt_in_exposes_nothing_to_existing_tokens(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    _, token = cli_token(owner, "off-token", profile="full")
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    before_tools = http(hub.port, "POST", "/mcp", token=token, body=request)
    before_events = http(hub.port, "GET", "/api/events", token=token)
    enable(hub, owners["B"])
    for method, path, body in (
        ("PUT", SETTINGS, {"enabled": True}),
        ("POST", ISSUE, {}),
        ("GET", ISSUE + "/missing", None),
        ("POST", EXCHANGE, {}),
        ("POST", RECOVER, {}),
    ):
        assert http(hub.port, method, path, token=token, body=body)[0] == 404
    assert http(hub.port, "POST", "/mcp", token=token, body=request) == before_tools
    assert http(hub.port, "GET", "/api/events", token=token) == before_events
    with hub.db._lock:
        assert (
            hub.db._conn.execute(
                "SELECT count(*) FROM runner_install_settings WHERE workspace_id=?",
                (owner["workspace"],),
            ).fetchone()[0]
            == 0
        )
        assert hub.db._conn.execute("SELECT count(*) FROM runner_enrollments").fetchone()[0] == 0
        assert (
            hub.db._conn.execute("SELECT count(*) FROM runner_enrollment_attempts").fetchone()[0]
            == 0
        )
