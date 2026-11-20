"""Existing-prefix bootstrap verification, with no enrollment or host-service effects."""

import base64
import json
import subprocess
import sys
from pathlib import Path

import pytest

from commonplace.runner import machine_setup
from tests.test_runner_program_delivery import release as release


@pytest.mark.parametrize("changed", [False, True], ids=["valid-control", "changed-installer"])
def test_bootstrap_from_its_prefix_verifies_before_importing_installer(release, tmp_path, changed):
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    python = machine_setup.install_program(home, release.raw, release.pin)
    prefix = python.parent.parent
    marker = tmp_path / "unverified-import"
    installer = (
        prefix
        / f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
        / "commonplace/runner/installer.py"
    )
    if changed:
        installer.write_text(
            "from pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('unverified code executed')\n"
            "raise RuntimeError('Synthetic changed installer')\n"
        )
    envelope = {
        "v": 1,
        "hub": "https://example.invalid",
        "workspace_id": "ws_integrity",
        "pairing_id": "pair_integrity",
        "enrollment_id": "enr_integrity",
        "label": "integrity-check",
        "manifest_digest": "a" * 64,
        "kind": "runner",
        "program": release.pin,
    }
    payload = {
        "bootstrap": str(Path(machine_setup.__file__).resolve()),
        "home": str(home),
        "prefix": str(prefix),
        "envelope": base64.urlsafe_b64encode(json.dumps(envelope).encode()).decode().rstrip("="),
        "refuse": not changed,
    }
    # Run the real standalone bootstrap under the delivered interpreter. The probe replaces only
    # platform/service discovery and the hub's live-code answer; no installer is mocked or imported
    # by this harness. A valid-code refusal stops the control before enrollment or service writes.
    script = """
import json, os, sys
from pathlib import Path
from types import SimpleNamespace
p = json.loads(sys.stdin.read())
assert Path(sys.prefix) == Path(p['prefix'])
ns = {'__name__': 'standalone_bootstrap'}
exec(compile(Path(p['bootstrap']).read_text(), p['bootstrap'], 'exec'), ns)
probe = SimpleNamespace(
    home=lambda: Path(p['home']), uid=os.getuid,
    system=lambda: ('Linux', 'ubuntu', '24.04'), python=lambda: sys.version_info[:2],
    can_import=lambda name: True, user_manager=lambda: True,
    free_bytes=lambda path: 1024**3, linger=lambda: True,
)
hub = SimpleNamespace(check=lambda body: {'usable': not p['refuse'], 'text': 'SYNTHETIC_CODE_REFUSAL'})
lines = []
status = ns['main']([p['envelope']], code='A'*43, probe=probe, hub=hub, out=lines.append)
print(json.dumps({'status': status, 'lines': lines}))
"""
    result = subprocess.run(
        [str(python), "-I", "-B", "-c", script],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(home), "LANG": "C"},
    )
    assert not marker.exists(), "the existing-prefix bootstrap imported an unverified installer"
    assert result.returncode == 0, result.stderr
    answer = json.loads(result.stdout)
    assert answer["status"] == 1
    if changed:
        assert any("verified" in line for line in answer["lines"])
    else:
        assert "SYNTHETIC_CODE_REFUSAL" in answer["lines"]
    assert not (home / ".commonplace-runner").exists()
    assert not (home / ".config").exists()


@pytest.mark.parametrize("entry", ["python3", "activate"])
@pytest.mark.parametrize("replacement", ["content", "link"])
def test_reuse_refuses_unverified_interpreter_aliases_and_activation_files(
    release, tmp_path, entry, replacement
):
    python = machine_setup.install_program(tmp_path, release.raw, release.pin)
    target = python.parent / entry
    raw = b"unverified startup entry\n"
    if replacement == "link":
        if target.exists():
            target.unlink()
        foreign = tmp_path / "unrelated-startup"
        foreign.write_bytes(raw)
        target.symlink_to(foreign)
    else:
        target.write_bytes(raw)
        target.chmod(0o700)
    with pytest.raises((ValueError, OSError)):
        machine_setup.install_program(tmp_path, release.raw, release.pin)
    assert target.read_bytes() == raw, "refusal must preserve the changed prefix"


def test_reuse_refuses_a_changed_pinned_archive(release, tmp_path):
    python = machine_setup.install_program(tmp_path, release.raw, release.pin)
    archive = python.parent.parent / "program.zip"
    archive.write_bytes(b"changed cached release")
    archive.chmod(0o600)
    with pytest.raises((ValueError, OSError)):
        machine_setup.install_program(tmp_path, release.raw, release.pin)
    assert archive.read_bytes() == b"changed cached release"
