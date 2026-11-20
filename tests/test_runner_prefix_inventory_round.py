"""Exact startup trees and real marker-dependent wheel requirements; no host writes."""

import base64
import hashlib
import importlib.metadata
import io
import json
import os
import platform
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from commonplace.runner import delivery, machine_setup


def applicable_pins():
    """Independent wheel-metadata oracle in the actual interpreter, including Python 3.10."""
    pins = {}
    pending = ["cryptography"]
    while pending:
        name = pending.pop()
        if name in pins:
            continue
        dist = importlib.metadata.distribution(name)
        pins[name] = dist.version
        for text in dist.requires or ():
            req = Requirement(text)
            if req.marker is None or req.marker.evaluate({"extra": ""}):
                pending.append(canonicalize_name(req.name))
    return pins


def actual_archive():
    """Assemble real installed RECORD-verified wheels independently of the builder under test."""
    files = {"site/commonplace/runner/installer.py": b"# Manifest-only fixture; never executed.\n"}
    pins = applicable_pins()
    for name in pins:
        dist = importlib.metadata.distribution(name)
        for record in dist.files or ():
            if record.hash is None:
                continue
            raw = Path(str(dist.locate_file(record))).read_bytes()
            assert record.hash.mode == "sha256"
            assert (
                base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")
                == record.hash.value
            )
            # Console launchers live outside site-packages and are not runtime library files.
            if (
                name == "cffi"
                and str(record) == "../../../bin/cffi-gen-src"
                and [
                    (entry.name, entry.value)
                    for entry in dist.entry_points
                    if entry.group == "console_scripts"
                ]
                == [("cffi-gen-src", "cffi._cffi_gen_src:run")]
            ):
                continue
            files["site/" + str(record)] = raw
    manifest = {
        "version": 1,
        "purpose": machine_setup.PROGRAM_PURPOSE,
        "python": sys.implementation.cache_tag,
        "system": platform.system(),
        "machine": platform.machine(),
        "dependencies": pins,
    }
    return files, manifest


def package(files, manifest):
    manifest = {
        **manifest,
        "files": {
            name: {"sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}
            for name, raw in files.items()
        },
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in files.items():
            archive.writestr(name, raw)
        archive.writestr("program.json", json.dumps(manifest))
    raw = output.getvalue()
    pin = {key: manifest[key] for key in ("version", "purpose", "python", "system", "machine")}
    return raw, {**pin, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


@pytest.fixture(scope="module")
def startup_release(tmp_path_factory):
    output = tmp_path_factory.mktemp("prefix-startup-release") / "program.zip"
    pin = delivery.build(Path(__file__).resolve().parents[1], output, applicable_pins())
    return SimpleNamespace(raw=output.read_bytes(), pin=pin)


def observe(value):
    """The private real-interpreter harness retains marker observations without public paths."""
    target = os.environ.get("PREFIX_PROOF_LOG")
    if target:
        with Path(target).open("a") as stream:
            stream.write(json.dumps(value) + "\n")


def inject_startup(prefix, marker, relative):
    target = prefix / relative
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.write_text(
        f"import pathlib; pathlib.Path({str(marker)!r}).write_text('startup executed')\n"
    )
    target.chmod(0o600)
    return target


def test_actual_interpreter_pth_marker_control(startup_release, tmp_path):
    python = machine_setup.install_program(tmp_path, startup_release.raw, startup_release.pin)
    prefix = python.parent.parent
    marker = tmp_path / "startup-marker"
    injected = inject_startup(prefix, marker, "lib/python3/dist-packages/control.pth")
    result = subprocess.run([str(python), "-I", "-B", "-c", "pass"], capture_output=True)
    assert result.returncode == 0
    facts = Path("/etc/os-release").read_text()
    debian12 = "ID=debian\n" in facts and 'VERSION_ID="12"' in facts
    if os.environ.get("PREFIX_REQUIRE_DEBIAN12"):
        assert debian12, "the required proof needs a genuine Debian 12 system"
        assert hasattr(sys, "_base_executable")
        assert Path(sys._base_executable).resolve() == Path("/usr/bin/python3.11").resolve()
    if debian12:
        assert marker.read_text() == "startup executed", (
            "Debian system Python must really execute the injected .pth"
        )
    observe(
        {
            "case": "actual-interpreter-control",
            "debian12": debian12,
            "marker": marker.exists(),
            "exit": result.returncode,
        }
    )
    injected.unlink()
    marker.unlink(missing_ok=True)
    injected.parent.rmdir()
    injected.parent.parent.rmdir()
    assert (
        machine_setup.install_program(tmp_path, startup_release.raw, startup_release.pin) == python
    )


@pytest.mark.parametrize(
    "relative",
    [
        "lib/python3/dist-packages/injected.pth",
        "lib/python3/dist-packages/other-name.PTH",
        "local/lib/python3/dist-packages/injected.pth",
        "local/lib/pythonX.Y/dist-packages/injected.pth",
        "lib/pythonX.Y/dist-packages/injected.pth",
        "lib/pythonX.Y/sitecustomize.py",
        "lib/pythonX.Y/os.py",
        "lib64/python3/dist-packages/injected.pth",
        "include/startup.py",
        "python._pth",
        "lib/pythonX.Y.zip",
        "__pycache__/startup.pyc",
    ],
)
def test_cached_prefix_refuses_every_untracked_startup_location(
    startup_release, tmp_path, relative
):
    python = machine_setup.install_program(tmp_path, startup_release.raw, startup_release.pin)
    prefix = python.parent.parent
    marker = tmp_path / "startup-marker"
    relative = relative.replace("X.Y", f"{sys.version_info.major}.{sys.version_info.minor}")
    target = inject_startup(prefix, marker, relative)
    before = target.read_bytes()
    refused = False
    try:
        approved = machine_setup.install_program(tmp_path, startup_release.raw, startup_release.pin)
    except (ValueError, OSError):
        refused = True
    else:
        result = subprocess.run([str(approved), "-I", "-B", "-c", "pass"], capture_output=True)
        assert result.returncode == 0
    observe({"case": relative, "refused": refused, "marker": marker.exists()})
    assert refused, f"untracked prefix startup location was approved; marker={marker.exists()}"
    assert not marker.exists(), "untracked startup code executed before refusal"
    assert target.read_bytes() == before, "refusal must preserve the changed prefix"


@pytest.mark.parametrize(
    "kind", ["empty-directory", "directory-link", "file-link", "hard-link", "mode"]
)
def test_whole_prefix_refuses_unsafe_or_untracked_tree_entries(startup_release, tmp_path, kind):
    python = machine_setup.install_program(tmp_path, startup_release.raw, startup_release.pin)
    prefix = python.parent.parent
    if kind == "mode":
        (prefix / "include").chmod(0o755)
    elif kind == "empty-directory":
        (prefix / "lib/untracked").mkdir(mode=0o700)
    elif kind == "directory-link":
        (prefix / "lib64").symlink_to("lib", target_is_directory=True)
    elif kind == "file-link":
        (prefix / "python3").symlink_to("bin/python")
    else:
        os.link(python, tmp_path / "aliased-interpreter")
    with pytest.raises((ValueError, OSError)):
        machine_setup.install_program(tmp_path, startup_release.raw, startup_release.pin)


def test_builder_packages_actual_marker_appropriate_pins(tmp_path):
    output = tmp_path / "program.zip"
    pins = applicable_pins()
    error = None
    try:
        pin = delivery.build(Path(__file__).resolve().parents[1], output, pins)
    except ValueError as exc:
        error = str(exc)
    assert error is None, f"actual {sys.version_info[:2]} applicable pins refused: {error}"
    manifest, _ = machine_setup.program_contents(output.read_bytes(), pin)
    assert manifest["dependencies"] == pins
    if sys.version_info[:2] == (3, 10):
        assert pins["cryptography"] == "48.0.0"
        assert pins["typing-extensions"] == "4.15.0"
    observe({"case": "real-builder", "python": list(sys.version_info[:3]), "dependencies": pins})


def test_builder_refuses_a_missing_applicable_pin(tmp_path):
    pins = applicable_pins()
    pins.pop("typing-extensions" if "typing-extensions" in pins else "cffi")
    output = tmp_path / "missing.zip"
    with pytest.raises(ValueError):
        delivery.build(Path(__file__).resolve().parents[1], output, pins)
    assert not output.exists()


def test_manifest_accepts_actual_marker_appropriate_distribution_metadata():
    files, manifest = actual_archive()
    raw, pin = package(files, manifest)
    error = None
    try:
        checked, installed = machine_setup.program_contents(raw, pin)
    except ValueError as exc:
        error = str(exc)
    assert error is None, f"real distribution metadata refused: {error}"
    assert checked["dependencies"] == applicable_pins()
    assert {"site/" + name for name in installed} == set(files)


@pytest.mark.parametrize(
    "kind",
    [
        "missing-metadata",
        "wrong-version",
        "duplicate-identity",
        "extra-distribution",
        "missing-required-pin",
        "unsatisfied-version",
        "invalid-requirement",
        "invalid-marker",
        "invalid-python",
    ],
)
def test_manifest_refuses_missing_wrong_extra_or_invalid_dependency_metadata(kind):
    files, manifest = actual_archive()
    key = next(
        name
        for name in files
        if name.startswith("site/cryptography-") and name.endswith("/METADATA")
    )
    text = files[key].decode()
    if kind == "missing-metadata":
        del files[key]
    elif kind == "wrong-version":
        files[key] = text.replace(
            "Version: " + manifest["dependencies"]["cryptography"] + "\n", "Version: 0.0.0\n"
        ).encode()
        assert files[key] != text.encode()
    elif kind == "duplicate-identity":
        files[key] += b"\n"
        files["site/duplicate.dist-info/METADATA"] = files[key]
    elif kind == "extra-distribution":
        files["site/extra-1.0.dist-info/METADATA"] = (
            b"Metadata-Version: 2.4\nName: extra\nVersion: 1.0\n\n"
        )
    elif kind == "missing-required-pin":
        files[key] = text.replace("Requires-Dist: cffi", "Requires-Dist: missing-package").encode()
    elif kind == "unsatisfied-version":
        files[key] = text.replace("cffi>=2.0.0", "cffi>=9999.0.0").encode()
    elif kind == "invalid-requirement":
        files[key] = text.replace("Requires-Dist: cffi", "Requires-Dist: @@@ cffi").encode()
    elif kind == "invalid-marker":
        files[key] = text.replace("platform_python_implementation", "invalid_marker").encode()
    else:
        files[key] = text.replace("Requires-Python: >=3.9", "Requires-Python: invalid").encode()
    raw, pin = package(files, manifest)
    with pytest.raises(ValueError):
        machine_setup.program_contents(raw, pin)


def test_probe_reconstructs_session_environment_without_inherited_credentials(monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/synthetic-untrusted-session")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "synthetic-untrusted-bus")
    monkeypatch.setenv("SYNTHETIC_PROVIDER_CREDENTIAL", "must-not-reach-child")
    result = machine_setup.LiveProbe()._run(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            "import json,os; print(json.dumps({k:os.environ.get(k) for k in ('XDG_RUNTIME_DIR','DBUS_SESSION_BUS_ADDRESS','SYNTHETIC_PROVIDER_CREDENTIAL')}))",
        ]
    )
    assert result is not None and result.returncode == 0
    env = json.loads(result.stdout)
    runtime = f"/run/user/{os.getuid()}"
    assert env == {
        "XDG_RUNTIME_DIR": runtime,
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime}/bus",
        "SYNTHETIC_PROVIDER_CREDENTIAL": None,
    }
