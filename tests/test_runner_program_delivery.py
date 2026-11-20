"""Real offline program installs and negative code/prefix boundaries; no runtime authority."""

# ruff: noqa: F811

import hashlib
import importlib.metadata
import io
import json
import os
import platform
import stat
import subprocess
import sys
import venv
import zipfile
from http.client import HTTPConnection
from pathlib import Path
from types import SimpleNamespace

import pytest

from commonplace.compute import pairing
from commonplace.runner import delivery, machine_setup, registry
from tests.runner_harness import http, owners, signup_hub  # noqa: F401
from tests.test_compute_pairing import flow, machine_key  # noqa: F401


@pytest.fixture
def program_flow(flow):
    raw = b"Synthetic program delivery contract; never executed.\n"
    pin = {
        "version": 1,
        "purpose": machine_setup.PROGRAM_PURPOSE,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size": len(raw),
        "python": sys.implementation.cache_tag,
        "system": platform.system(),
        "machine": platform.machine(),
    }
    flow.hub.gateway.runner_program_release = SimpleNamespace(pin=pin, raw=raw)
    return flow, pin, raw


@pytest.fixture(scope="module")
def release(tmp_path_factory):
    folder = tmp_path_factory.mktemp("real-runner-program")
    archive = folder / "program.zip"
    versions = {name: importlib.metadata.version(name) for name in delivery.DEPENDENCIES}
    pin = delivery.build(Path(__file__).resolve().parents[1], archive, versions)
    return SimpleNamespace(raw=archive.read_bytes(), pin=pin, archive=archive, versions=versions)


def binary(flow, made, pin, *, changes=None, headers=None):
    values = flow.envelope(made)
    body = {
        "workspace_id": values["workspace_id"],
        "pairing_id": values["pairing_id"],
        "code": made["setup"]["code"],
        "delivery": {"purpose": pin["purpose"], "sha256": pin["sha256"]},
        **(changes or {}),
    }
    host, port = flow.hub.server.server_address
    connection = HTTPConnection(host, port)
    try:
        connection.request(
            "POST",
            "/api/runner-pairing/check",
            json.dumps(body),
            {"Content-Type": "application/json", **(headers or {})},
        )
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def test_clean_prefix_installs_real_program_dependencies_and_reuses_them(release, tmp_path):
    # No system-site-packages or pip: the target interpreter really starts without either module.
    clean = tmp_path / "before"
    venv.EnvBuilder(with_pip=False).create(clean)
    check = subprocess.run(
        [
            str(clean / "bin/python"),
            "-I",
            "-B",
            "-c",
            "import importlib.util; assert importlib.util.find_spec('cryptography') is None; "
            "assert importlib.util.find_spec('commonplace') is None",
        ],
        capture_output=True,
    )
    assert check.returncode == 0
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    python = machine_setup.install_program(home, release.raw, release.pin)
    result = subprocess.run(
        [
            str(python),
            "-I",
            "-B",
            "-c",
            "import commonplace.runner.installer, cryptography, cffi, pycparser; "
            "from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey; "
            "k=Ed25519PrivateKey.generate(); k.public_key().verify(k.sign(b'actual'),b'actual'); "
            "print('real program and cryptography usable')",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "real program and cryptography usable"
    assert machine_setup.install_program(home, release.raw, release.pin) == python
    assert not (home / ".commonplace-runner").exists()
    assert not (home / ".config").exists()
    assert stat.S_IMODE(python.parent.parent.stat().st_mode) == 0o700


def test_tampered_download_is_refused_before_any_prefix_write(release, tmp_path):
    with pytest.raises(ValueError, match="integrity"):
        machine_setup.install_program(tmp_path, release.raw[:-1] + b"x", release.pin)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "kind",
    [
        "file",
        "parent-link",
        "file-link",
        "hard-link",
        "permissions",
        "interpreter",
        "python-config",
        "extra-file",
        "startup-file",
        "interpreter-startup-file",
    ],
)
def test_changed_or_foreign_prefix_is_preserved_and_refused(release, tmp_path, kind):
    if kind == "parent-link":
        elsewhere = tmp_path / "unrelated"
        elsewhere.mkdir()
        (tmp_path / machine_setup.PROGRAM_ROOT).symlink_to(elsewhere, target_is_directory=True)
        with pytest.raises(OSError):
            machine_setup.install_program(tmp_path, release.raw, release.pin)
        assert list(elsewhere.iterdir()) == []
        return
    python = machine_setup.install_program(tmp_path, release.raw, release.pin)
    target = (
        python.parent.parent
        / f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages/commonplace/runner/installer.py"
    )
    original = target.read_bytes()
    if kind == "file":
        target.write_bytes(b"# replaced, must not execute\n")
    elif kind == "file-link":
        target.parent.chmod(0o700)
        target.unlink()
        foreign = tmp_path / "foreign.py"
        foreign.write_bytes(original)
        foreign.chmod(0o600)
        target.symlink_to(foreign)
    elif kind == "hard-link":
        os.link(target, tmp_path / "other-owner-file")
    elif kind == "interpreter":
        python.write_bytes(b"#!/bin/sh\nexit 0\n")
    elif kind == "python-config":
        config = python.parent.parent / "pyvenv.cfg"
        config.write_text(
            config.read_text().replace(
                "include-system-site-packages = false", "include-system-site-packages = true"
            )
        )
    elif kind in ("extra-file", "startup-file", "interpreter-startup-file"):
        site = (
            python.parent.parent
            / f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
        )
        site.chmod(0o700)
        extra = (
            (python.parent / "python._pth")
            if kind == "interpreter-startup-file"
            else site / ("extra.py" if kind == "extra-file" else "untrusted.pth")
        )
        extra.write_text("must not be imported\n")
        extra.chmod(0o600)
    else:
        target.chmod(0o644)
    with pytest.raises((ValueError, OSError)):
        machine_setup.install_program(tmp_path, release.raw, release.pin)
    if kind == "file":
        assert target.read_bytes() == b"# replaced, must not execute\n"


def changed_archive(release, change):
    with zipfile.ZipFile(io.BytesIO(release.raw)) as original:
        entries = {name: original.read(name) for name in original.namelist()}
    manifest = json.loads(entries["program.json"])
    change(entries, manifest)
    entries["program.json"] = json.dumps(manifest).encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in entries.items():
            archive.writestr(name, raw)
    raw = output.getvalue()
    pin = {**release.pin, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    return raw, pin


@pytest.mark.parametrize("kind", ["traversal", "purpose", "platform", "missing-file"])
def test_invalid_package_cannot_change_a_target(release, tmp_path, kind):
    def change(entries, manifest):
        if kind == "traversal":
            name, value = "site/../../escape", b"must not write"
            entries[name] = value
            manifest["files"][name] = {
                "size": len(value),
                "sha256": hashlib.sha256(value).hexdigest(),
            }
        elif kind == "purpose":
            manifest["purpose"] = "runtime-authority"
        elif kind == "platform":
            manifest["machine"] = "unsupported-machine"
        else:
            entries.pop("site/commonplace/runner/installer.py")

    raw, pin = changed_archive(release, change)
    if kind == "platform":
        pin["machine"] = "unsupported-machine"
    with pytest.raises(ValueError):
        machine_setup.install_program(tmp_path, raw, pin)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "case", ["used", "revoked", "foreign", "purpose", "digest", "credential", "unavailable"]
)
def test_delivery_respects_code_workspace_purpose_and_release_authority(program_flow, case):
    flow, pin, _ = program_flow
    made = flow.made()
    changes: dict[str, object] = {}
    headers: dict[str, str] = {}
    if case == "used":
        flow.connect(made)
    elif case == "revoked":
        assert flow.close(made)[0] == 200
    elif case == "foreign":
        changes["workspace_id"] = "ws_foreign"
    elif case in ("purpose", "digest"):
        changes["delivery"] = {
            "purpose": "runtime-authority" if case == "purpose" else pin["purpose"],
            "sha256": "0" * 64 if case == "digest" else pin["sha256"],
        }
    elif case == "credential":
        headers["Authorization"] = "Bearer refused-credential"
    else:
        del flow.hub.gateway.runner_program_release
    status, raw = binary(flow, made, pin, changes=changes, headers=headers)
    assert status in (400, 404, 409)
    assert raw != b"Synthetic program delivery contract; never executed.\n"


def test_delivery_never_attests_enrolls_or_enables_work(program_flow):
    flow, pin, _ = program_flow
    made = flow.made()
    assert binary(flow, made, pin)[0] == 200
    assert flow.get(made)["state"] == "waiting"
    credentials = flow.connect(made)
    assert binary(flow, made, pin)[0] == 409
    machine = registry.get_machine(flow.hub.db, flow.owner["workspace"], credentials["machine_id"])
    assert not machine["enabled"] and machine["binding_state"] == "unbound"
    assert flow.accept(made)[0] == 200
    machine = registry.get_machine(flow.hub.db, flow.owner["workspace"], credentials["machine_id"])
    from commonplace.runner import predicate

    assert predicate.setup_only_refusal(flow.hub.gateway, machine) == "setup_only"


def test_explicit_release_configuration_validates_bytes_and_never_uses_network(
    release, monkeypatch
):
    monkeypatch.setenv("COMMONPLACE_RUNNER_PROGRAM_FILE", str(release.archive) + ".json")
    gateway = SimpleNamespace()
    delivery.configure(gateway)
    assert delivery.pin_for(gateway) == release.pin
    gateway.runner_program_release.raw += b"tampered"
    with pytest.raises(ValueError, match="integrity"):
        delivery.pin_for(gateway)


def test_builder_refuses_unpinned_dependency_or_overwrite(release, tmp_path):
    output = tmp_path / "never.zip"
    with pytest.raises(ValueError):
        delivery.build(Path.cwd(), output, {"cryptography": "0.0.0"})
    assert not output.exists()
    before = release.archive.read_bytes()
    with pytest.raises(FileExistsError):
        delivery.build(Path.cwd(), release.archive, release.versions)
    assert release.archive.read_bytes() == before


def test_launcher_tamper_is_refused_before_running_any_downloaded_code(tmp_path):
    sentinel = tmp_path / "must-not-exist"
    curl = tmp_path / "curl"
    curl.write_text("#!/bin/sh\nprintf '%s\\n' 'touch " + str(sentinel) + "'\n")
    curl.chmod(0o700)
    command = pairing.render_command(
        "https://example.invalid", "public-envelope", "unused-code", pinned_launcher=True
    )
    result = subprocess.run(
        ["/bin/sh", "-c", command],
        env={"PATH": str(tmp_path) + ":/usr/bin:/bin"},
        capture_output=True,
    )
    assert result.returncode != 0
    assert not sentinel.exists()
