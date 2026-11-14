"""Real release shapes and the independent issuer producer boundary."""

# ruff: noqa: F811
import io
import json
import os
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from commonplace.runner.install_files import digest
from commonplace.runner.runtime_host import LiveRuntimeHost
from commonplace.runner.supervisor import Channel
from tests.test_runner_install import setup  # noqa: F401
from tests.test_runner_runtime_install import approve, bundle, prepare, selection
from tests.test_runner_setup_approvals import consented, finish, manual, ready  # noqa: F401
from tests.test_runner_setup_lifecycle import host_envelope, request


def select_pin(s, signer):
    from cryptography.hazmat.primitives import serialization

    from commonplace.runner.host_authority import PIN
    from commonplace.runner.install_files import encoded

    value = {
        "issuer": "approved-issuer",
        "public_key": signer.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode(),
    }
    s.local.journal["host_issuer_pin"] = value
    s.local.flush()
    s.local.file(PIN, encoded(value))


def test_headless_host_discovery_does_not_require_hidden_command_in_help(tmp_path):
    original = bundle("host")
    with zipfile.ZipFile(io.BytesIO(original)) as archive:
        script = archive.read("__main__.py").replace(b"acp-mcp-call serve", b"serve auth")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("__main__.py", script)
    raw = out.getvalue()
    release = {"version": "1.2.3", "sha256": digest(raw), "format": "python-zipapp"}
    facts = LiveRuntimeHost().probe("host", release, raw, tmp_path)
    assert facts == {"availability": "present", "login": "not-applicable"}


def test_file_backed_host_discovery_keeps_verified_real_filename(tmp_path, monkeypatch):
    # A native embedded runtime resolves its own filename while parsing help.
    # Anonymous executable descriptors do not have a resolvable module filename.
    raw = Path("/usr/bin/true").read_bytes()
    path = tmp_path / "installed-host"
    path.write_bytes(raw)
    path.chmod(0o700)
    host = LiveRuntimeHost()
    monkeypatch.setattr(host, "locate", lambda _: {"path": str(path), "raw": raw})

    def run(args, **kwargs):
        fd = kwargs["pass_fds"][0]
        assert os.readlink(f"/proc/self/fd/{fd}") == str(path)
        assert kwargs["stdin"] is not None
        return SimpleNamespace(
            returncode=0, stdout=b"t3 v1.2.3" if args[-1] == "--version" else b"serve", stderr=b""
        )

    monkeypatch.setattr("commonplace.runner.runtime_host.subprocess.run", run)
    assert (
        host.probe(
            "host", {"sha256": digest(raw), "version": "1.2.3", "format": "native"}, raw, tmp_path
        )["availability"]
        == "present"
    )


@pytest.mark.parametrize("damage", ["bytes", "permissions"])
def test_file_backed_discovery_rechecks_the_open_inode_before_execution(
    tmp_path, monkeypatch, damage
):
    from commonplace.runner.install_host import InstallError

    raw = Path("/usr/bin/true").read_bytes()
    path = tmp_path / "installed-host"
    path.write_bytes(raw if damage == "permissions" else b"changed")
    path.chmod(0o777 if damage == "permissions" else 0o700)
    host = LiveRuntimeHost()
    monkeypatch.setattr(host, "locate", lambda _: {"path": str(path), "raw": raw})
    monkeypatch.setattr(
        "commonplace.runner.runtime_host.subprocess.run",
        lambda *a, **k: pytest.fail("unverified inode executed"),
    )
    with pytest.raises(InstallError, match="runtime integrity verification failed"):
        host.probe(
            "host", {"sha256": digest(raw), "version": "1.2.3", "format": "native"}, raw, tmp_path
        )


def test_pinned_native_release_above_old_limit_is_installed_without_execution_authority(setup):
    from commonplace.runner.runtime_host import FakeRuntimeHost
    from commonplace.runner.runtime_install import RUNTIME_CACHE

    s = prepare(setup)
    runtime, artifacts = selection()
    # Contemporary subscription binaries exceed the old 128 MiB ceiling.
    raw = b"\x7fELF" + bytes(129 * 1024 * 1024)
    artifacts["claude"] = raw
    runtime["components"]["claude"].update(sha256=digest(raw), format="native")
    assert approve(s, runtime)
    host = FakeRuntimeHost()
    host.artifacts = {digest(value): value for value in artifacts.values()}
    s.local.runtime_host = host
    s.local.sync_receipts()
    config = json.loads(s.local.fs.read(RUNTIME_CACHE))
    assert config["components"]["claude"]["availability"] == "present"
    assert config["agent_ready"] is False


def test_lifecycle_uses_fresh_host_producer_instead_of_silently_dropping_status(
    consented, monkeypatch
):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    s = consented
    finish(s, monkeypatch)
    signer = Ed25519PrivateKey.generate()
    select_pin(s, signer)
    s.issuers = {
        "approved-issuer": signer.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    }
    s.transport.lifecycle = lambda body: _reply(s, body)
    channel = Channel(s.local)
    seen = []

    def produce(nonce):
        seen.append(nonce)
        return host_envelope(s, signer, nonce)

    channel.host_status = produce
    assert channel.exchange()["host_verification"] == "verified"
    assert channel.exchange()["host_verification"] == "verified"
    assert len(set(seen)) == 2, "every issuer request must bind the new machine challenge"


def _reply(s, body):
    code, reply = request(s, body)
    assert code == 200
    return reply


def test_install_initializes_lifecycle_before_enabling_service(setup):
    from commonplace.runner.supervisor import CONTROL, CONTROL_LOCK

    s = prepare(setup)
    assert s.local.fs.info(CONTROL) is not None
    assert s.local.fs.info(CONTROL_LOCK) is not None


def test_provider_home_is_owned_before_a_read_only_cli_probe(setup):
    from commonplace.runner.runtime_host import FakeRuntimeHost
    from commonplace.runner.runtime_install import HOME

    s = prepare(setup)
    runtime, artifacts = selection(("codex",))
    assert approve(s, runtime)
    host = FakeRuntimeHost()
    host.artifacts = {digest(raw): raw for raw in artifacts.values()}
    s.local.runtime_host = host
    s.local.sync_receipts()
    assert any(
        i["path"] == HOME + "/.codex" and i["kind"] == "dir" for i in s.local.journal["items"]
    )


@pytest.mark.parametrize("target_directory", [False, True])
def test_stopped_provider_cleanup_unlinks_helper_links_without_touching_targets(
    consented, tmp_path, target_directory
):
    from commonplace.runner.device_login import cleanup
    from commonplace.runner.install_host import InstallError
    from commonplace.runner.runtime_install import HOME

    s = consented
    root = HOME + "/.codex"
    s.local.directory(root)
    target = tmp_path / "external"
    if target_directory:
        target.mkdir()
        sentinel = target / "untouched"
    else:
        sentinel = target
    sentinel.write_text("outside the private provider directory")
    link = root + "/native-helper"
    (s.home / link).symlink_to(target, target_is_directory=target_directory)
    # Normal private reads and file removal must still reject all symlinks.
    with pytest.raises((OSError, InstallError)):
        s.local.fs.read(link)
    with pytest.raises((OSError, InstallError)):
        s.local.fs.remove(link)
    cleanup(s.local.fs, s.local.journal)
    assert not (s.home / root).exists()
    assert sentinel.read_text() == "outside the private provider directory"


def test_supervisor_start_does_not_compete_with_settings_worker_lock(consented):
    s = consented
    s.transport.lifecycle = lambda body: _reply(s, body)
    Channel(s.local)
    with s.local.locked():
        channel = Channel(s.local)
        assert channel.exchange()["command"] == "continue"


def test_provider_cleanup_refuses_replaced_root_and_preserves_both_trees(consented, tmp_path):
    from commonplace.runner.device_login import cleanup
    from commonplace.runner.install_host import InstallError
    from commonplace.runner.runtime_install import HOME

    s = consented
    root = HOME + "/.codex"
    s.local.directory(root)
    (s.home / root).rmdir()
    external = tmp_path / "external-provider"
    external.mkdir()
    (s.home / root).symlink_to(external, target_is_directory=True)
    other = HOME + "/.claude"
    s.local.directory(other)
    s.local.fs.write(other + "/auth", b"must survive failed validation")
    with pytest.raises(InstallError):
        cleanup(s.local.fs, s.local.journal)
    assert (s.home / root).is_symlink()
    assert external.is_dir()
    assert s.local.fs.read(other + "/auth") == b"must survive failed validation"


def test_real_producer_adapter_keeps_grant_out_of_arguments_environment_and_output(
    consented, monkeypatch, capsys, caplog
):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from commonplace.runner import host_interface
    from tests.test_runner_setup_approvals import SECRET

    s = consented
    finish(s, monkeypatch)
    signer = Ed25519PrivateKey.generate()
    select_pin(s, signer)
    calls = []

    def invoke(command, fd, home, payload=b"", **kwargs):
        assert SECRET not in " ".join(command)
        assert os.fstat(fd).st_mode & 0o777 == 0o600
        calls.append(command[-1])
        if command[-1] == "--help":
            assert payload == b""
            return 0, b"serve runner-status"
        body = json.loads(payload)
        assert body["nonce"] == "1" * 32
        assert body["grant"]["secret"] == SECRET
        return 0, json.dumps(host_envelope(s, signer, body["nonce"])).encode()

    monkeypatch.setattr(host_interface, "invoke", invoke)
    result = host_interface.HostStatus(s.local)("1" * 32)
    assert calls == ["--help", "runner-status"]
    capture = capsys.readouterr()
    assert SECRET not in json.dumps(result) + capture.out + capture.err + caplog.text


def test_missing_issuer_capability_never_receives_grant_and_never_reuses_old_status(
    consented, monkeypatch
):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from commonplace.runner import host_interface

    s = consented
    finish(s, monkeypatch)
    select_pin(s, Ed25519PrivateKey.generate())
    calls = []

    def invoke(command, fd, home, payload=b"", **kwargs):
        calls.append(command[-1])
        assert payload == b""
        return 0, b"serve"

    monkeypatch.setattr(host_interface, "invoke", invoke)
    producer = host_interface.HostStatus(s.local)
    assert producer("1" * 32) is None
    assert producer("2" * 32) is None
    assert calls == ["--help"]


@pytest.mark.parametrize("damage", ["signature", "scope", "expired"])
def test_producer_refuses_bad_or_stale_grants_before_starting_host(consented, monkeypatch, damage):
    import base64

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from commonplace.runner import host_interface, install_receipts
    from commonplace.runner.install_host import InstallError
    from commonplace.runner.setup_client import GRANT

    s = consented
    finish(s, monkeypatch)
    select_pin(s, Ed25519PrivateKey.generate())
    value = json.loads(s.local.fs.read(GRANT))
    if damage == "signature":
        value["signed"]["signature"] = base64.b64encode(b"wrong").decode()
    elif damage == "scope":
        value["signed"]["receipt"]["scope"]["machine_id"] = "another-machine"
    else:
        value["signed"]["receipt"]["expires_at"] = "2000-01-01T00:00:00Z"
        value["signed"]["signature"] = base64.b64encode(
            s.key.sign(install_receipts.canonical(value["signed"]["receipt"]))
        ).decode()
    s.local.fs.write(GRANT, json.dumps(value).encode(), replace=True)
    monkeypatch.setattr(
        host_interface, "invoke", lambda *a, **k: pytest.fail("host ran before grant validation")
    )
    with pytest.raises(InstallError, match="host status producer unavailable"):
        host_interface.HostStatus(s.local)("1" * 32)


@pytest.mark.parametrize("behavior", ["blocked-input", "oversized-output"])
def test_producer_io_is_bounded_and_cannot_keep_child_running(tmp_path, behavior):
    from commonplace.runner.host_interface import invoke

    fd = os.memfd_create("producer-test", os.MFD_CLOEXEC)
    script = (
        "import time;time.sleep(60)"
        if behavior == "blocked-input"
        else "import os;os.write(1,b'x'*65536)"
    )
    try:
        with pytest.raises((ValueError, OSError)):
            invoke(
                [sys.executable, "-I", "-c", script], fd, tmp_path, b"private" * 2000, seconds=0.2
            )
    finally:
        os.close(fd)
