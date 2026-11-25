"""Pinned startup semantics and ordinary-entry enrollment recovery; no live host effects."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_machine_setup import encode, machine as machine
from test_runner_program_delivery import release as release

from commonplace.runner import installer, machine_setup


@pytest.mark.parametrize("key", ["Include-System-Site-Packages", "HOME"])
def test_cached_program_refuses_cpython_normalized_configuration_duplicates(release, tmp_path, key):
    python = machine_setup.install_program(tmp_path, release.raw, release.pin)
    config = python.parent.parent / "pyvenv.cfg"
    value = "true" if key == "Include-System-Site-Packages" else str(tmp_path / "foreign-python")
    changed = config.read_text() + f"{key} = {value}\n"
    config.write_text(changed)
    with pytest.raises((ValueError, OSError)):
        machine_setup.install_program(tmp_path, release.raw, release.pin)
    assert config.read_text() == changed, "refusal must preserve the changed configuration"


@pytest.mark.parametrize("state", ["interrupted", "installed", "foreign"])
def test_original_interpreter_reuses_verified_program_before_enrollment_recovery(
    machine, release, monkeypatch, state
):
    m = machine
    m.transport.drop = state != "installed"
    first = machine_setup.main(
        [m.setup["envelope"]],
        code=m.setup["code"],
        probe=m.probe,
        hub=m.hub,
        make_installer=m.make,
        out=lambda line: None,
    )
    assert first == (0 if state == "installed" else 1)
    answer = m.hub.check(
        {"workspace_id": m.owner.workspace_id, "pairing_id": m.item["id"], "code": m.setup["code"]}
    )
    assert answer["reason"] == "used" and not answer["usable"]
    journal_path = m.home / ".commonplace-runner" / "manifest.json"
    journal = json.loads(journal_path.read_text())
    if state != "installed":
        assert journal["phase"] == "exchanging"
    if state == "foreign":
        journal["intent"]["enrollment_id"] = "enr_different_installation"
        journal_path.write_text(json.dumps(journal))
    python = machine_setup.install_program(m.home, release.raw, release.pin)
    assert Path(sys.prefix) != python.parent.parent, "entry must be the original interpreter"
    envelope = machine_setup.parse_envelope(m.setup["envelope"]) | {"program": release.pin}
    encoded = encode(envelope)
    lines: list[str] = []
    launches: list[list[str]] = []

    def no_download(*args):
        pytest.fail("a verified local program must not download again with a spent code")

    m.hub.program = no_download

    def reexecute(argv, *, input, env):
        # Only the OS reexec/user-manager boundary is substituted. Real archive verification,
        # real pairing/enrollment handlers, saved key, journal and recovery proof all run.
        assert argv == [str(python), "-I", "-B", "-m", "commonplace.runner.machine_setup", encoded]
        assert input == m.setup["code"].encode()
        launches.append(argv)
        with monkeypatch.context() as child:
            child.setattr(sys, "prefix", str(python.parent.parent))
            status = machine_setup.main(
                [encoded],
                code=input.decode(),
                probe=m.probe,
                hub=m.hub,
                make_installer=m.make,
                out=lines.append,
            )
        return SimpleNamespace(returncode=status)

    monkeypatch.setattr(machine_setup.subprocess, "run", reexecute)
    before = {str(p): p.read_bytes() for p in m.home.rglob("*") if p.is_file()}
    status = machine_setup.main(
        [encoded],
        code=m.setup["code"],
        probe=m.probe,
        hub=m.hub,
        make_installer=m.make,
        out=lines.append,
    )
    assert status == (1 if state == "foreign" else 0), lines
    assert len([route for route, _ in m.transport.calls if route == installer.EXCHANGE]) == 1
    if state == "foreign":
        assert {str(p): p.read_bytes() for p in m.home.rglob("*") if p.is_file()} == before
        assert not m.host.active and not m.host.enabled
    else:
        assert len(launches) == 1
        assert any("Continuing the setup" in line for line in lines)
        assert json.loads(journal_path.read_text())["phase"] == "installed"
        if state == "interrupted":
            assert any(body.get("action") == "recover" for _, body in m.transport.calls)


def test_spent_code_without_cached_program_cannot_download_or_resume(machine, release):
    m = machine
    m.transport.drop = True
    assert (
        machine_setup.main(
            [m.setup["envelope"]],
            code=m.setup["code"],
            probe=m.probe,
            hub=m.hub,
            make_installer=m.make,
            out=lambda line: None,
        )
        == 1
    )
    envelope = machine_setup.parse_envelope(m.setup["envelope"]) | {"program": release.pin}
    before = {str(p): p.read_bytes() for p in m.home.rglob("*") if p.is_file()}
    assert (
        machine_setup.main(
            [encode(envelope)],
            code=m.setup["code"],
            probe=m.probe,
            hub=m.hub,
            make_installer=m.make,
            out=lambda line: None,
        )
        == 1
    )
    assert {str(p): p.read_bytes() for p in m.home.rglob("*") if p.is_file()} == before
    assert not (m.home / machine_setup.PROGRAM_ROOT).exists()
