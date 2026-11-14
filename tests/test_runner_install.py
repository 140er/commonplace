"""Private, recoverable runner installation with an isolated filesystem and host."""

import io
import json
import os
import socket
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from commonplace import cli, sessions
from commonplace.control_db import ControlDB
from commonplace.runner import bootstrap, enrollment, enrollment_api, installer, registry
from commonplace.runner.install_files import MANIFEST, STATE, Files
from commonplace.runner.install_host import FakeHost, InstallError
from commonplace.runner.installer import CONFIG, KEY

CODE = "SyntheticEnrollmentSentinel_" + "x" * 15  # 43 characters
TOKENS = tuple(
    "cmmn_" + value.ljust(43, "x")
    for value in ("SyntheticMachineTokenSentinel_", "SyntheticWorkerTokenSentinel_")
)


@pytest.fixture(autouse=True)
def no_live_effects(monkeypatch):
    def refuse(*args, **kwargs):
        pytest.fail("installer tests must use the fake host and enrollment route seam")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(installer.LiveHost, "__init__", refuse)


class RouteTransport:
    """Real slice-1 handlers with in-memory request/response streams, no socket."""

    def __init__(self, db):
        self.gateway = SimpleNamespace(db=db, principal_for=lambda _: None)
        self.calls = []
        self.envelopes = []
        self.drop = False
        self.mutate = None

    def post(self, route, body):
        self.calls.append((route, dict(body)))
        raw = json.dumps(body).encode()
        handler = SimpleNamespace(
            path=route,
            command="POST",
            close_connection=False,
            headers={"Content-Length": str(len(raw))},
            rfile=io.BytesIO(raw),
            wfile=io.BytesIO(),
            send_header=lambda *a: None,
            end_headers=lambda: None,
        )
        handler.send_response = lambda status: setattr(handler, "status", status)
        enrollment_api._bootstrap(self.gateway, handler)
        result = json.loads(handler.wfile.getvalue())
        if handler.status not in (200, 201):
            raise InstallError("enrollment refused")
        if "ciphertext" in result:
            self.envelopes.append(result)
            if self.mutate:
                result = self.mutate(dict(result))
        if self.drop and route == installer.EXCHANGE:
            self.drop = False
            raise OSError(TOKENS[0])  # reflection must never escape the command boundary
        return result


@pytest.fixture
def setup(tmp_path, monkeypatch):
    db = ControlDB(str(tmp_path / "control.sqlite3"))
    user = db.create_user("owner@example.test")
    workspace = db.create_workspace("fresh", user)
    owner = sessions.person_principal(db, user, workspace)
    registry.ensure(db)
    enrollment.ensure(db)
    enrollment.set_enabled(db, owner, True)
    with monkeypatch.context() as patch:
        patch.setattr(enrollment.secrets, "token_urlsafe", lambda _: CODE)
        _, issued = enrollment.issue(
            db,
            owner,
            {
                "request_id": "request",
                "manifest_digest": "a" * 64,
                "grant": {
                    "label": "fixture",
                    "projects": ["demo"],
                    "topics": ["work"],
                    "wings": ["shared"],
                },
            },
        )
    from commonplace import tokens

    token_values = iter(TOKENS)
    monkeypatch.setattr(tokens, "new_token", lambda: next(token_values))
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    intent = {
        "hub_url": "https://hub.example.test",
        "workspace_id": workspace,
        "machine_label": "fixture",
        "manifest_digest": "a" * 64,
        "enrollment_id": issued["id"],
    }
    transport = RouteTransport(db)
    host = FakeHost()
    try:
        yield SimpleNamespace(db=db, home=home, intent=intent, transport=transport, host=host)
    finally:
        db.close()


def build(s, **kwargs):
    return installer.Installer(s.home, kwargs.pop("host", s.host), s.transport, **kwargs)


def apply(s, **kwargs):
    return build(s).install(s.intent, accepted=True, code_source=lambda: CODE, **kwargs)


def args(s, *extra):
    argv = ["runner", "install", "--home", str(s.home), "--accept"]
    for key, flag in (
        ("hub_url", "hub"),
        ("workspace_id", "workspace"),
        ("machine_label", "label"),
        ("manifest_digest", "manifest-digest"),
        ("enrollment_id", "enrollment-id"),
    ):
        argv += ["--" + flag, s.intent[key]]
    return cli.build_parser().parse_args([*argv, *extra])


def snapshot(home):
    return {
        str(p.relative_to(home)): (
            p.stat().st_mtime_ns if p.is_file() else None,
            stat.S_IMODE(p.stat().st_mode),
            p.read_bytes() if p.is_file() else None,
        )
        for p in home.rglob("*")
    }


def test_plan_and_no_accept_change_nothing(setup, capsys):
    s = setup
    before = snapshot(s.home)
    assert build(s).plan() == 0
    assert (
        build(s).install(s.intent, code_source=lambda: pytest.fail("code read without accept")) == 0
    )
    assert snapshot(s.home) == before
    assert s.host.commands == s.transport.calls == []
    out = capsys.readouterr()
    for term in ("0700", "0600", "linger", "signed", "uninstall", "stop", "reload", "Verify"):
        assert term.lower() in out.out.lower()
    assert CODE not in out.out + out.err


@pytest.mark.parametrize("linger", [False, True])
def test_private_install_converges_and_uninstall_restores_only_owned_state(setup, linger, capsys):
    s = setup
    s.host.linger = linger
    (s.home / ".config").mkdir(mode=0o700)
    (s.home / ".config/keep").write_text("unrelated")
    before = snapshot(s.home)
    assert apply(s) == 0
    journal = json.loads((s.home / MANIFEST).read_text())
    assert journal["phase"] == "installed"
    credentials = [s.home / STATE / f"{kind}.token" for kind in ("machine", "worker")]
    values = [p.read_text() for p in credentials]
    assert values[0] != values[1]
    config = json.loads((s.home / CONFIG).read_text())
    assert config["agent_ready"] is False and config["mode"] == "setup-only"
    assert not {"binding_acks", "runtime_ack", "shared_checkout_ack"} & config.keys()
    for path in (s.home / STATE).iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    for path in s.home.rglob("*"):
        if path.is_dir():
            assert stat.S_IMODE(path.stat().st_mode) == 0o700
    unit = s.home / installer.UNIT_PATH
    assert stat.S_IMODE(unit.stat().st_mode) == 0o600
    assert "setup" in unit.read_text()
    assert "Environment=" not in unit.read_text()
    installed, commands, requests = snapshot(s.home), list(s.host.commands), list(s.transport.calls)
    assert apply(s) == 0
    assert build(s).status() == 0
    assert snapshot(s.home) == installed
    assert s.host.commands == commands and s.transport.calls == requests
    output = capsys.readouterr()
    for secret in (CODE, *values):
        assert secret not in output.out + output.err + json.dumps(journal) + unit.read_text()
        assert not any(secret in str(command) for command in s.host.commands)
    for path in s.home.rglob("*"):
        if path.is_file() and path not in credentials:
            assert not any(value.encode() in path.read_bytes() for value in values)
    assert build(s).uninstall() == 0
    assert snapshot(s.home) == before
    assert s.host.state() == {"linger": linger, "enabled": False, "active": False}
    assert build(s).uninstall() == 0


def test_lost_exchange_recovers_same_ciphertext_and_key_without_redeeming_twice(setup):
    s = setup
    s.transport.drop = True
    with pytest.raises(OSError):
        apply(s)
    key = (s.home / KEY).read_bytes()
    assert not s.host.enabled
    assert apply(s) == 0
    assert (s.home / KEY).read_bytes() == key
    assert len([r for r, _ in s.transport.calls if r == installer.EXCHANGE]) == 1
    assert s.transport.envelopes[0] == s.transport.envelopes[1]
    assert any(body.get("action") == "recover" for _, body in s.transport.calls)
    machine = registry.machines(s.db, s.intent["workspace_id"])
    assert (
        len(machine) == 1 and not machine[0]["enabled"] and machine[0]["binding_state"] == "unbound"
    )
    # Successful durable receipt purges the original delivery through the slice-1 route.
    resumed = build(s)
    resumed.load()
    with pytest.raises(InstallError):
        resumed._proof("recover")


@pytest.mark.parametrize(
    "boundary", ["delivery.json", "machine.token", "worker.token", "config.json"]
)
def test_interrupted_private_writes_resume_without_partial_active_state(
    setup, monkeypatch, boundary
):
    s = setup
    original = Files.write
    tripped = False

    def fail(fs, path, data, **kwargs):
        nonlocal tripped
        if path.endswith("/" + boundary) and not tripped:
            tripped = True
            raise OSError(TOKENS[1])
        return original(fs, path, data, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Files, "write", fail)
        assert (
            installer.run(args(s), host=s.host, transport=s.transport, code_source=lambda: CODE)
            == 1
        )
    assert not s.host.active and not s.host.enabled and not s.host.linger
    assert apply(s) == 0
    assert len([r for r, _ in s.transport.calls if r == installer.EXCHANGE]) == 1


@pytest.mark.parametrize("failure", ["privilege", "occupied"])
def test_preflight_refuses_before_code_redemption_or_files(setup, failure):
    s = setup
    s.host.permitted = failure != "privilege"
    s.host.occupied = failure == "occupied"
    with pytest.raises(InstallError):
        apply(s)
    assert not s.transport.calls and not list(s.home.iterdir())


@pytest.mark.parametrize("kind", ["symlink", "permissive", "foreign", "occupied-unit"])
def test_unsafe_existing_paths_are_refused_without_exchange(setup, monkeypatch, kind):
    s = setup
    if kind == "symlink":
        (s.home / ".config").symlink_to(s.home, target_is_directory=True)
    elif kind == "permissive":
        (s.home / STATE).mkdir(mode=0o777)
        (s.home / STATE).chmod(0o777)
    elif kind == "foreign":
        monkeypatch.setattr(
            Files, "_check", lambda *a, **k: (_ for _ in ()).throw(InstallError("foreign owner"))
        )
    else:
        (s.home / installer.UNIT_PATH).parent.mkdir(parents=True, mode=0o700)
        (s.home / installer.UNIT_PATH).write_text("unrelated")
        (s.home / installer.UNIT_PATH).chmod(0o600)
    with pytest.raises((InstallError, OSError)):
        apply(s)
    assert not s.transport.calls


def test_stop_failure_preserves_every_private_artifact(setup):
    s = setup
    apply(s)
    before = snapshot(s.home)
    original = s.host.change

    def change(action):
        if action == "disable":
            raise InstallError("stop unconfirmed")
        original(action)

    s.host.change = change
    with pytest.raises(InstallError):
        build(s).uninstall()
    after = snapshot(s.home)
    assert set(before) == set(after)
    for path in before.keys() - {MANIFEST}:
        assert before[path] == after[path]
    s.host.change = original
    assert build(s).uninstall() == 0


def test_failed_enable_rolls_back_service_and_linger_then_can_retry(setup):
    s = setup
    original = s.host.change

    def change(action):
        original(action)
        if action == "enable":
            raise OSError("injected start failure")

    s.host.change = change
    with pytest.raises(OSError):
        apply(s)
    assert not any(s.host.state().values())
    assert (s.home / KEY).is_file()
    s.host.change = original
    assert apply(s) == 0


def test_uninstall_preserves_linger_needed_by_another_service_and_modified_files(setup):
    s = setup
    apply(s)
    s.host.other_services = True
    token = s.home / STATE / "worker.token"
    token.write_text("changed")
    with pytest.raises(InstallError):
        build(s).uninstall()
    assert token.exists() and s.host.linger and not s.host.active
    journal = json.loads((s.home / MANIFEST).read_text())
    assert any(i["path"].endswith("worker.token") for i in journal["items"])


def test_signed_archive_bootstraps_without_installed_runner_and_rejects_tampering(setup, tmp_path):
    s = setup
    key = Ed25519PrivateKey.generate()
    archive, signature, trust = (
        tmp_path / name for name in ("runner.pyz", "release.sig", "release.pub")
    )
    archive.write_bytes(b"PK synthetic signed runner archive")
    signature.write_bytes(key.sign(archive.read_bytes()))
    trust.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    command = [
        "--release",
        str(archive),
        "--release-signature",
        str(signature),
        "--release-key",
        str(trust),
    ]
    for key, flag in (
        ("hub_url", "hub"),
        ("workspace_id", "workspace"),
        ("machine_label", "label"),
        ("manifest_digest", "manifest-digest"),
        ("enrollment_id", "enrollment-id"),
    ):
        command += ["--" + flag, s.intent[key]]
    command += ["--home", str(s.home), "--accept"]
    launched = []

    def launch(argv):
        launched.append(argv)
        assert Path(argv[2]).read_bytes() == archive.read_bytes()
        assert stat.S_IMODE(Path(argv[2]).stat().st_mode) == 0o600
        assert stat.S_IMODE(Path(argv[2]).parent.stat().st_mode) == 0o700
        assert CODE not in str(argv)
        parsed = cli.build_parser().parse_args(argv[3:])
        return installer.run(parsed, host=s.host, transport=s.transport, code_source=lambda: CODE)

    assert bootstrap.main(command, launch=launch) == 0
    assert (s.home / STATE / "runner.pyz").read_bytes() == archive.read_bytes()
    assert str(s.home / STATE / "runner.pyz") in (s.home / installer.UNIT_PATH).read_text()
    assert not Path(launched[0][2]).exists()
    before = snapshot(s.home)
    archive.write_bytes(b"tampered")
    assert bootstrap.main(command, launch=lambda _: pytest.fail("unverified release launched")) == 1
    assert snapshot(s.home) == before


def test_creation_uses_exclusive_private_modes_from_the_start(setup, monkeypatch):
    s = setup
    original_open, original_mkdir = os.open, os.mkdir
    created = []

    def opened(path, flags, mode=0o777, **kwargs):
        if flags & os.O_CREAT:
            assert flags & os.O_EXCL and flags & os.O_NOFOLLOW
            assert mode == 0o600
            created.append(str(path))
        return original_open(path, flags, mode, **kwargs)

    def mkdir(path, mode=0o777, **kwargs):
        assert mode == 0o700
        return original_mkdir(path, mode, **kwargs)

    monkeypatch.setattr(os, "open", opened)
    monkeypatch.setattr(os, "mkdir", mkdir)
    assert apply(s) == 0
    assert any("machine.token" in p for p in created)


def test_errors_and_plan_never_reflect_sentinel_secrets(setup, capsys):
    s = setup

    def fail(*args):
        raise ValueError(" ".join((CODE, *TOKENS)))

    s.transport.post = fail
    assert installer.run(args(s), host=s.host, transport=s.transport, code_source=lambda: CODE) == 1
    build(s).plan()
    output = capsys.readouterr()
    for secret in (CODE, *TOKENS):
        assert secret not in output.out + output.err + (s.home / MANIFEST).read_text()


def test_reinstall_restarts_owned_service_without_redeeming_or_rewriting(setup):
    s = setup
    apply(s)
    before = snapshot(s.home)
    requests = list(s.transport.calls)
    s.host.active = False  # recording equivalent of a user-manager restart
    assert apply(s) == 0
    assert s.host.active and s.host.enabled and s.host.linger
    assert snapshot(s.home) == before
    assert s.transport.calls == requests


def test_failure_during_private_write_leaves_no_partial_token(setup, monkeypatch):
    s = setup
    original = os.fsync
    failed = False

    def fsync(fd):
        nonlocal failed
        path = os.readlink(f"/proc/self/fd/{fd}")
        if path.endswith("machine.token.pending") and not failed:
            failed = True
            raise OSError(TOKENS[0])
        return original(fd)

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", fsync)
        assert (
            installer.run(args(s), host=s.host, transport=s.transport, code_source=lambda: CODE)
            == 1
        )
    assert failed
    assert not (s.home / STATE / "machine.token").exists()
    assert not list(s.home.rglob("*.pending"))
    assert not s.host.active
    assert apply(s) == 0


def test_unsupported_transport_and_changed_intent_refuse_without_redemption(setup):
    s = setup
    for origin in (
        "http://hub.example.test",
        "https://user@hub.example.test",
        "https://hub.example.test/path",
    ):
        with pytest.raises(InstallError):
            installer.Transport(origin)
    assert apply(s) == 0
    with pytest.raises(InstallError):
        build(s).install({**s.intent, "machine_label": "changed"}, accepted=True)
    assert len([r for r, _ in s.transport.calls if r == installer.EXCHANGE]) == 1


def test_corrupt_encrypted_delivery_never_creates_credentials_or_service(setup):
    s = setup
    s.transport.mutate = lambda envelope: {**envelope, "ciphertext": "AAAA"}
    assert installer.run(args(s), host=s.host, transport=s.transport, code_source=lambda: CODE) == 1
    assert not (s.home / STATE / "machine.token").exists()
    assert not (s.home / installer.UNIT_PATH).exists()
    assert not s.host.active
    s.transport.mutate = None
    assert apply(s) == 0


def test_standalone_launcher_needs_no_runner_import_and_suppresses_child_secrets(tmp_path):
    import subprocess
    import sys
    import zipapp

    source = tmp_path / "source"
    source.mkdir()
    (source / "__main__.py").write_text(
        "import os,sys\n"
        "assert sys.argv[1:3] == ['runner', 'install']\n"
        "assert 'COMMONPLACE_TOKEN' not in os.environ\n"
        "assert not any('commonplace' in str(p) for p in sys.modules)\n"
        "secret = sys.stdin.read()\nprint(secret)\nprint(secret, file=sys.stderr)\n"
    )
    archive = tmp_path / "signed.pyz"
    zipapp.create_archive(source, archive)
    key = Ed25519PrivateKey.generate()
    signature, trust = tmp_path / "release.sig", tmp_path / "release.pub"
    signature.write_bytes(key.sign(archive.read_bytes()))
    trust.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    command = [
        sys.executable,
        "-I",
        str(Path(bootstrap.__file__).resolve()),
        "--release",
        str(archive),
        "--release-signature",
        str(signature),
        "--release-key",
        str(trust),
        "--hub",
        "https://hub.example.test",
        "--workspace",
        "fixture",
        "--label",
        "fixture",
        "--enrollment-id",
        "fixture",
        "--manifest-digest",
        "a" * 64,
        "--home",
        str(tmp_path),
        "--accept",
    ]
    result = subprocess.run(
        command,
        input=CODE,
        capture_output=True,
        text=True,
        timeout=10,
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin", "COMMONPLACE_TOKEN": TOKENS[0]},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "runner bootstrap: complete\n" and not result.stderr
    assert not any(
        secret in result.stdout + result.stderr + str(command) for secret in (CODE, *TOKENS)
    )


def test_fifo_manifest_is_refused_without_blocking_or_redemption(setup):
    s = setup
    (s.home / STATE).mkdir(mode=0o700)
    os.mkfifo(s.home / MANIFEST, mode=0o600)
    with pytest.raises(InstallError):
        apply(s)
    assert not s.transport.calls
