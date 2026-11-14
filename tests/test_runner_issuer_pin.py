"""Owner-selected local trust never comes from enrollment or a host response."""

# ruff: noqa: F811
import json
import stat

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from commonplace.runner import host_interface, installer
from commonplace.runner.install_files import digest, encoded
from tests.test_runner_host_interface import select_pin
from tests.test_runner_install import (
    CODE,
    args,
    build,
    setup,  # noqa: F401
    snapshot,
)
from tests.test_runner_setup_approvals import consented, finish, manual, ready  # noqa: F401
from tests.test_runner_setup_lifecycle import host_envelope


def pin(signer):
    return {
        "issuer": "approved-issuer",
        "public_key": signer.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode(),
    }


def test_plan_explicitly_shows_missing_owner_issuer_pin_without_side_effects(setup, capsys):
    s = setup
    assert installer.Installer(s.home, s.host, s.transport).plan() == 0
    assert "Host issuer pin: not supplied; verification and execution stay disabled." in (
        capsys.readouterr().out
    )
    assert list(s.home.iterdir()) == []
    assert s.host.commands == s.transport.calls == []


def test_enrollment_record_cannot_supply_the_host_issuer_pin(consented, monkeypatch):
    s = consented
    finish(s, monkeypatch)
    signer = Ed25519PrivateKey.generate()
    # An enrollment delivery may carry attacker-chosen metadata. It is not the
    # separate owner-accepted plan and must never establish local issuer trust.
    document = json.loads(s.local.fs.read(installer.DELIVERY))
    document["host_issuer_pin"] = pin(signer)
    raw = encoded(document)
    item = next(i for i in s.local.journal["items"] if i["path"] == installer.DELIVERY)
    item["digest"] = digest(raw)
    s.local.flush()
    s.local.fs.write(installer.DELIVERY, raw, replace=True)
    calls = []

    def invoke(command, fd, home, payload=b"", **kwargs):
        calls.append(command[-1])
        if command[-1] == "--help":
            return 0, b"serve runner-status"
        return 0, json.dumps(host_envelope(s, signer, "1" * 32)).encode()

    monkeypatch.setattr(host_interface, "invoke", invoke)
    assert host_interface.HostStatus(s.local)("1" * 32) is None, (
        "a signed host response cannot be accepted without an owner-supplied issuer pin"
    )
    assert calls == [], "no scoped grant may reach a producer without the explicit owner pin"


def test_owner_pin_plan_accept_storage_reinstall_and_removal(setup, tmp_path, capsys):
    from commonplace import cli
    from commonplace.runner import bootstrap, host_authority

    s = setup
    selected = pin(Ed25519PrivateKey.generate())
    source = tmp_path / "issuer-pin.json"
    source.write_text(json.dumps(selected))
    parsed = args(s, "--host-issuer-pin", str(source), "--plan")
    assert installer.run(parsed, host=s.host, transport=s.transport) == 0
    assert bootstrap.describe_issuer_pin(selected) in capsys.readouterr().out
    assert list(s.home.iterdir()) == [] and s.host.commands == s.transport.calls == []
    parsed.plan = False
    assert installer.run(parsed, host=s.host, transport=s.transport, code_source=lambda: CODE) == 0
    local = build(s)
    local.load()
    assert host_authority.load(local) == selected
    assert stat.S_IMODE((s.home / host_authority.PIN).stat().st_mode) == 0o600
    assert local.journal["host_issuer_pin"] == selected
    assert json.loads(local.fs.read(installer.CONFIG))["agent_ready"] is False
    before = snapshot(s.home)
    assert installer.run(parsed, host=s.host, transport=s.transport) == 0
    assert snapshot(s.home) == before
    parsed.host_issuer_pin = None
    assert installer.run(parsed, host=s.host, transport=s.transport) == 1
    source.write_text(json.dumps(pin(Ed25519PrivateKey.generate())))
    parsed.host_issuer_pin = str(source)
    assert installer.run(parsed, host=s.host, transport=s.transport) == 1
    assert snapshot(s.home) == before
    assert local.uninstall() == 0
    assert not (s.home / host_authority.PIN).exists()
    # The new field has no effect on the existing check/run command defaults.
    assert cli.build_parser().parse_args(["runner", "check", "--config", "fixture.json"]).config


@pytest.mark.parametrize(
    "damage",
    [
        "private-key",
        "extra-field",
        "missing-key",
        "bad-issuer",
        "null",
        "symlink",
        "writable",
        "oversized",
    ],
)
def test_malformed_source_pin_refuses_before_enrollment_or_service(setup, tmp_path, capsys, damage):
    s = setup
    signer = Ed25519PrivateKey.generate()
    selected = pin(signer)
    if damage == "private-key":
        selected["public_key"] = signer.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
    elif damage == "extra-field":
        selected["unexpected"] = "SyntheticIssuerInputSentinel"
    elif damage == "missing-key":
        selected.pop("public_key")
    elif damage == "bad-issuer":
        selected["issuer"] = "SyntheticIssuerInputSentinel\n"
    elif damage == "null":
        selected = None
    source = tmp_path / "source-pin"
    source.write_text(json.dumps(selected))
    if damage == "symlink":
        link = tmp_path / "pin-link"
        link.symlink_to(source)
        source = link
    elif damage == "writable":
        source.chmod(0o666)
    elif damage == "oversized":
        source.write_bytes(b"x" * 16385)
    assert (
        installer.run(
            args(s, "--host-issuer-pin", str(source)),
            host=s.host,
            transport=s.transport,
            code_source=lambda: pytest.fail("enrollment code read"),
        )
        == 1
    )
    assert list(s.home.iterdir()) == []
    assert s.host.commands == s.transport.calls == []
    output = capsys.readouterr()
    assert "SyntheticIssuerInputSentinel" not in output.out + output.err
    assert "PRIVATE KEY" not in output.out + output.err


def test_standalone_launcher_freezes_the_owner_pin_before_child_install(setup, tmp_path, capsys):
    from commonplace import cli
    from commonplace.runner import bootstrap, host_authority

    s = setup
    key = Ed25519PrivateKey.generate()
    archive = tmp_path / "runner.pyz"
    archive.write_bytes(b"signed installer fixture")
    signature = tmp_path / "signature"
    signature.write_bytes(key.sign(archive.read_bytes()))
    trusted = tmp_path / "release-public.pem"
    trusted.write_text(pin(key)["public_key"])
    source = tmp_path / "issuer-pin.json"
    selected = pin(Ed25519PrivateKey.generate())
    source.write_text(json.dumps(selected))
    command = [
        "--release",
        str(archive),
        "--release-signature",
        str(signature),
        "--release-key",
        str(trusted),
    ]
    command.extend(["--home", str(s.home), "--accept", "--host-issuer-pin", str(source)])
    for field, flag in (
        ("hub_url", "hub"),
        ("workspace_id", "workspace"),
        ("machine_label", "label"),
        ("manifest_digest", "manifest-digest"),
        ("enrollment_id", "enrollment-id"),
    ):
        command.extend(["--" + flag, s.intent[field]])
    launched = []

    def launch(argv):
        source.write_text(json.dumps(pin(Ed25519PrivateKey.generate())))
        parsed = cli.build_parser().parse_args(argv[3:])
        snapshot = type(source)(parsed.host_issuer_pin)
        assert snapshot != source and json.loads(snapshot.read_text()) == selected
        assert stat.S_IMODE(snapshot.stat().st_mode) == 0o600
        launched.append(snapshot)
        return installer.run(parsed, host=s.host, transport=s.transport, code_source=lambda: CODE)

    assert bootstrap.main(command, launch=launch) == 0
    assert len(launched) == 1 and not launched[0].exists()
    local = build(s)
    local.load()
    assert host_authority.load(local) == selected
    assert bootstrap.describe_issuer_pin(selected) in capsys.readouterr().out


@pytest.mark.parametrize(
    "damage", ["missing", "replacement", "symlink", "permissions", "unmanaged"]
)
def test_local_pin_tampering_refuses_before_producer_or_hub(consented, monkeypatch, damage):
    from commonplace.runner import host_authority
    from commonplace.runner.install_host import InstallError
    from commonplace.runner.supervisor import Channel

    s = consented
    finish(s, monkeypatch)
    signer = Ed25519PrivateKey.generate()
    select_pin(s, signer)
    path = s.home / host_authority.PIN
    if damage == "missing":
        path.unlink()
    elif damage == "replacement":
        path.write_text(json.dumps(pin(Ed25519PrivateKey.generate())))
    elif damage == "symlink":
        external = s.home / "external-pin"
        path.rename(external)
        path.symlink_to(external)
    elif damage == "permissions":
        path.chmod(0o644)
    else:
        s.local.journal["items"] = [
            i for i in s.local.journal["items"] if i["path"] != host_authority.PIN
        ]
        s.local.flush()
    monkeypatch.setattr(host_interface, "invoke", lambda *a, **k: pytest.fail("producer started"))
    s.transport.lifecycle = lambda body: pytest.fail("unverified status reached the hub")
    with pytest.raises(InstallError):
        Channel(s.local).exchange(host_status=host_envelope(s, signer, "1" * 32))


@pytest.mark.parametrize(
    "damage",
    [None, "signature", "issuer", "nonce", "expiry", "machine", "workspace", "scope", "watchdog"],
)
def test_local_status_verification_uses_the_owner_pin_and_current_scope(
    consented, monkeypatch, damage
):
    import base64

    from commonplace.runner import host_authority, install_receipts
    from commonplace.runner.install_host import InstallError

    s = consented
    finish(s, monkeypatch)
    signer = Ed25519PrivateKey.generate()
    select_pin(s, signer)
    envelope = host_envelope(s, signer, "1" * 32)
    claims = envelope["receipt"]
    if damage == "signature":
        signer = Ed25519PrivateKey.generate()
    elif damage == "issuer":
        claims["issuer"] = "unselected-issuer"
    elif damage == "nonce":
        claims["nonce"] = "2" * 32
    elif damage == "expiry":
        claims["issued_at"] = "2000-01-01T00:00:00Z"
        claims["expires_at"] = "2000-01-01T00:00:04Z"
    elif damage in ("machine", "workspace"):
        claims["identity"][damage + "_id"] = "another-identity"
    elif damage == "scope":
        claims["scope_digest"] = "0" * 64
    elif damage == "watchdog":
        claims["watchdog_seconds"] = 60
    envelope["signature"] = base64.b64encode(
        signer.sign(install_receipts.canonical(claims))
    ).decode()
    if damage:
        with pytest.raises(InstallError, match="pinned host scope unavailable"):
            host_authority.accept(s.local, envelope, "1" * 32)
    else:
        assert host_authority.accept(s.local, envelope, "1" * 32) == {"agent_ready": False}


@pytest.mark.parametrize(
    "field,value",
    [
        ("project", "foreign"),
        ("topic", "foreign"),
        ("runtime_mode", "auto"),
        ("runtime_mode", "full-access"),
        ("runtime_mode", "unknown"),
    ],
)
def test_local_scope_requests_cannot_exceed_signed_project_topic_or_ceiling(
    consented, monkeypatch, field, value
):
    from commonplace.runner import host_authority
    from commonplace.runner.install_host import InstallError

    s = consented
    finish(s, monkeypatch)
    signer = Ed25519PrivateKey.generate()
    select_pin(s, signer)
    envelope = host_envelope(s, signer, "1" * 32)
    request = {"project": "demo", "topic": "work", "runtime_mode": "approval-required"}
    assert host_authority.accept(s.local, envelope, "1" * 32, request=request) == {
        "agent_ready": False
    }
    outside = {**request, field: value}
    with pytest.raises(InstallError):
        host_authority.accept(s.local, envelope, "1" * 32, request=outside)


def test_explicit_status_cannot_bypass_missing_pin_or_replace_hub_pin(consented, monkeypatch):
    from commonplace.runner import host_authority
    from commonplace.runner.install_host import InstallError
    from commonplace.runner.supervisor import Channel
    from tests.test_runner_setup_lifecycle import request

    s = consented
    finish(s, monkeypatch)
    signer = Ed25519PrivateKey.generate()
    calls = []

    def transport(body):
        calls.append(body)
        code, value = request(s, body)
        if code != 200:
            raise InstallError("independent hub pin refused")
        return value

    s.transport.lifecycle = transport
    channel = Channel(s.local)
    channel.host_status = lambda nonce: host_envelope(s, signer, nonce)
    with pytest.raises(InstallError):
        channel.exchange()
    assert calls == [] and host_authority.load(s.local) is None
    select_pin(s, signer)
    with pytest.raises(InstallError, match="independent hub pin refused"):
        channel.exchange()
    assert len(calls) == 1, "local pin selection must never configure trust at the hub"
    s.issuers = {"approved-issuer": pin(signer)["public_key"].encode()}
    assert channel.exchange()["host_verification"] == "verified"
    assert channel.exchange()["agent_ready"] is False


def test_hub_signature_cannot_claim_local_verification_without_owner_pin(consented):
    import base64

    from commonplace.runner import install_receipts
    from commonplace.runner.install_host import InstallError
    from commonplace.runner.supervisor import Channel
    from tests.test_runner_setup_lifecycle import request

    s = consented

    def transport(body):
        code, value = request(s, body)
        assert code == 200
        value["receipt"]["host_verification"] = "verified"
        value["signature"] = base64.b64encode(
            s.key.sign(install_receipts.canonical(value["receipt"]))
        ).decode()
        return value

    s.transport.lifecycle = transport
    with pytest.raises((ValueError, InstallError)):
        Channel(s.local).exchange()


def test_lifecycle_scope_preflight_refuses_before_forwarding_and_never_enables_work(
    consented, monkeypatch
):
    from commonplace.runner.install_host import InstallError
    from commonplace.runner.supervisor import Channel
    from tests.test_runner_setup_lifecycle import request

    s = consented
    finish(s, monkeypatch)
    signer = Ed25519PrivateKey.generate()
    local = Channel(s.local)
    wanted = {"project": "demo", "topic": "work", "runtime_mode": "approval-required"}
    calls = []

    def transport(body):
        calls.append(body)
        code, value = request(s, body)
        assert code == 200
        return value

    s.transport.lifecycle = transport
    with pytest.raises(InstallError):
        local.exchange(scope_request=wanted)
    assert calls == []
    select_pin(s, signer)
    s.issuers = {"approved-issuer": pin(signer)["public_key"].encode()}
    local.host_status = lambda nonce: host_envelope(s, signer, nonce)
    for outside in ({**wanted, "project": "other"}, {**wanted, "runtime_mode": "full-access"}):
        with pytest.raises(InstallError):
            local.exchange(scope_request=outside)
    assert calls == []
    assert local.exchange(scope_request=wanted)["agent_ready"] is False
    assert len(calls) == 1
    previous = calls[0]["host_status"]
    local.host_status = lambda nonce: previous
    with pytest.raises(InstallError):
        local.exchange(scope_request=wanted)
    assert len(calls) == 1, "a former nonce cannot authorize another scope preflight"
