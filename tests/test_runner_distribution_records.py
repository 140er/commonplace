"""Real wheel delivery and the narrow, verified build-tool RECORD exception."""

import base64
import hashlib
import importlib.metadata
import sysconfig
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_runner_prefix_inventory_round import actual_archive, applicable_pins, package

from commonplace.runner import delivery, machine_setup


def test_real_builder_preserves_library_and_distribution_metadata_bytes(tmp_path):
    files, _ = actual_archive()
    output = tmp_path / "program.zip"
    pin = delivery.build(Path(__file__).resolve().parents[1], output, applicable_pins())
    _, delivered = machine_setup.program_contents(output.read_bytes(), pin)
    for name, raw in files.items():
        if name != "site/commonplace/runner/installer.py":
            assert delivered[name[5:]] == raw
    assert all(".." not in name.split("/") for name in delivered)
    assert not any(name.endswith("/cffi-gen-src") for name in delivered)


@pytest.mark.parametrize(
    "kind",
    [
        "valid",
        "undeclared",
        "wrong-target",
        "duplicate-declaration",
        "other-script",
        "other-traversal",
        "wrong-root",
        "other-distribution",
        "symlink",
        "hard-link",
        "writable",
        "bad-hash",
    ],
)
def test_build_tool_omission_requires_exact_declaration_location_and_integrity(
    tmp_path, monkeypatch, kind
):
    installed = importlib.metadata.distribution
    pins = applicable_pins()
    name = "cryptography" if kind == "other-distribution" else "cffi"
    dist = installed(name)
    scripts = tmp_path / "bin"
    scripts.mkdir()
    launcher = scripts / "cffi-gen-src"
    raw = b"# Synthetic build tool; never executed.\n"
    launcher.write_bytes(raw)
    launcher.chmod(0o700)
    if kind == "symlink":
        target = tmp_path / "elsewhere"
        launcher.rename(target)
        launcher.symlink_to(target)
    elif kind == "hard-link":
        (tmp_path / "alias").hardlink_to(launcher)
    elif kind == "writable":
        launcher.chmod(0o722)
    digest = hashlib.sha256(b"changed" if kind == "bad-hash" else raw).digest()
    relative = {
        "other-script": "../../../bin/another-tool",
        "other-traversal": "../../../../bin/cffi-gen-src",
    }.get(kind, "../../../bin/cffi-gen-src")
    record = importlib.metadata.PackagePath(relative)
    record.hash = importlib.metadata.FileHash(
        "sha256=" + base64.urlsafe_b64encode(digest).decode().rstrip("=")
    )
    record.size = len(raw)
    entry = importlib.metadata.EntryPoint(
        name="cffi-gen-src",
        value="cffi._cffi_gen_src:other" if kind == "wrong-target" else "cffi._cffi_gen_src:run",
        group="console_scripts",
    )
    entries = (
        []
        if kind == "undeclared"
        else [entry, entry]
        if kind == "duplicate-declaration"
        else [entry]
    )
    proxy = SimpleNamespace(
        version=dist.version,
        entry_points=entries,
        files=[item for item in dist.files or () if str(item) != "../../../bin/cffi-gen-src"]
        + [record],
        locate_file=lambda item: launcher if item is record else dist.locate_file(item),
    )
    original_path = sysconfig.get_path
    monkeypatch.setattr(
        sysconfig,
        "get_path",
        lambda key: (
            str(tmp_path / "different" if kind == "wrong-root" else scripts)
            if key == "scripts"
            else original_path(key)
        ),
    )
    monkeypatch.setattr(
        importlib.metadata, "distribution", lambda key: proxy if key == name else installed(key)
    )
    output = tmp_path / "program.zip"
    if kind == "valid":
        pin = delivery.build(Path(__file__).resolve().parents[1], output, pins)
        _, delivered = machine_setup.program_contents(output.read_bytes(), pin)
        assert not any(path.endswith("/cffi-gen-src") for path in delivered)
    else:
        with pytest.raises((ValueError, OSError)):
            delivery.build(Path(__file__).resolve().parents[1], output, pins)
        assert not output.exists()
    assert launcher.read_bytes() == raw


@pytest.mark.parametrize("relative", ["../../../bin/cffi-gen-src", "/bin/cffi-gen-src"])
def test_archive_never_accepts_an_external_build_tool_path(relative):
    files, manifest = actual_archive()
    files["site/" + relative] = b"# Never an archive member.\n"
    raw, pin = package(files, manifest)
    with pytest.raises(ValueError, match="unsafe program entry"):
        machine_setup.program_contents(raw, pin)
