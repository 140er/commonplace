"""Compute target installer: plan, idempotent install and manifest-driven uninstall.

Everything runs against a temporary ``--root`` with the recording ``StagedHost``:
no account is created, nothing is mounted and no system file is touched. Ownership
is only applied by a live host, so these tests assert modes and the recorded system
commands; owner checks need a disposable root VM.
"""

import base64
import hashlib
import json
import os
import shutil
import stat
import struct
import subprocess
import sys
import types
from dataclasses import asdict
from pathlib import Path

import pytest

from commonplace.compute import target_installer as install
from commonplace.compute.script_sandbox import ScriptTargetConfig
from commonplace.compute.target_helper import TARGET_CONFIG_PATH, TARGET_HELPER_COMMAND

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Linux target provisioning")

REAL_RUNNING_PACKAGES = install.running_packages
SITE_DIR = "usr/local/lib/python3.12/dist-packages"
LIB = "opt/commonplace-compute/lib"

WIRE = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + bytes(range(32))
KEY = "ssh-ed25519 " + base64.b64encode(WIRE).decode("ascii")
PRINCIPAL = hashlib.sha256(WIRE).hexdigest()
IDENTITY = ["--workspace", "fixture-script-workspace", "--gateway", "gw1", "--node", "node1"]
SCRATCH_UNIT = "var-lib-commonplace\\x2dcompute-scratch.mount"
RUNTIME_UNIT = "opt-commonplace\\x2dcompute-runtime.mount"


def fake_runtime(base):
    base = Path(base)
    (base / "stdlib" / "encodings").mkdir(parents=True)
    (base / "stdlib" / "test").mkdir()
    (base / "stdlib" / "__pycache__").mkdir()
    (base / "dynload").mkdir()
    (base / "stdlib" / "os.py").write_text("# fake os\n")
    (base / "stdlib" / "encodings" / "__init__.py").write_text("# fake\n")
    (base / "stdlib" / "test" / "skipped.py").write_text("# excluded\n")
    (base / "stdlib" / "__pycache__" / "os.cpython-312.pyc").write_bytes(b"x")
    (base / "dynload" / "_fake.cpython-312-x86_64-linux-gnu.so").write_bytes(b"\x7fELF so")
    (base / "python3").write_bytes(b"\x7fELF fake python")
    (base / "libm.so.6").write_bytes(b"\x7fELF libm")
    (base / "ld.so").write_bytes(b"\x7fELF loader")
    return install.RuntimeSpec(
        binary=base / "python3",
        libs=(
            ("lib/x86_64-linux-gnu/libm.so.6", base / "libm.so.6"),
            ("lib64/ld-linux-x86-64.so.2", base / "ld.so"),
        ),
        stdlib=base / "stdlib",
        dynload=base / "dynload",
        version=(3, 12),
    )


PACKAGE_TREE = {
    "commonplace",
    "commonplace/__init__.py",
    "commonplace/compute",
    "commonplace/compute/__init__.py",
    "commonplace/compute/target_helper.py",
    "mempalace",
    "mempalace/__init__.py",
}


@pytest.fixture(autouse=True)
def fake_packages(tmp_path, monkeypatch):
    base = tmp_path / "pkgs"
    for name in PACKAGE_TREE:
        if name.endswith(".py"):
            (base / name).parent.mkdir(parents=True, exist_ok=True)
            (base / name).write_text("# fake\n")
    (base / "commonplace/__pycache__").mkdir()
    (base / "commonplace/__pycache__/x.cpython-312.pyc").write_bytes(b"x")
    (base / "commonplace/data.txt").write_text("not python")
    monkeypatch.setattr(
        install, "running_packages", lambda: (base / "commonplace", base / "mempalace")
    )


RUNTIME_TREE = {
    "usr",
    "usr/bin",
    "usr/bin/python3",
    "usr/lib",
    "usr/lib/python312.zip",
    "usr/lib/python3.12",
    "usr/lib/python3.12/lib-dynload",
    "usr/lib/python3.12/lib-dynload/_fake.cpython-312-x86_64-linux-gnu.so",
    "lib",
    "lib/x86_64-linux-gnu",
    "lib/x86_64-linux-gnu/libm.so.6",
    "lib64",
    "lib64/ld-linux-x86-64.so.2",
    "job",
    "proc",
    "dev",
    "tmp",
}


def expected_paths():
    src = "opt/commonplace-compute/runtime-src/"
    return {
        "etc",
        "etc/commonplace",
        "etc/commonplace/compute-authorized-keys",
        "etc/commonplace/compute-install-manifest.json",
        "etc/commonplace/compute-script-target-v1.json",
        "etc/ssh",
        "etc/ssh/sshd_config",
        "etc/ssh/sshd_config.d",
        "etc/ssh/sshd_config.d/60-commonplace-compute.conf",
        "etc/systemd",
        "etc/systemd/system",
        "etc/systemd/system/" + SCRATCH_UNIT,
        "etc/systemd/system/" + RUNTIME_UNIT,
        "opt",
        "opt/commonplace-compute",
        "opt/commonplace-compute/runtime",
        "opt/commonplace-compute/runtime-src",
        *(src + name for name in RUNTIME_TREE),
        "usr",
        "usr/local",
        "usr/local/libexec",
        "usr/local/libexec/commonplace-compute",
        "usr/local/libexec/commonplace-compute/helper-entry",
        "usr/local/lib",
        "usr/local/lib/python3.12",
        SITE_DIR,
        SITE_DIR + "/commonplace-compute.pth",
        LIB,
        *(LIB + "/" + name for name in PACKAGE_TREE),
        "var",
        "var/lib",
        "var/lib/commonplace-compute",
        "var/lib/commonplace-compute/attempts",
        "var/lib/commonplace-compute/inputs",
        "var/lib/commonplace-compute/scratch",
    }


@pytest.fixture
def root(tmp_path):
    path = tmp_path / "root"
    (path / "etc/ssh/sshd_config.d").mkdir(parents=True)
    (path / "etc/ssh/sshd_config").write_text(
        "Include /etc/ssh/sshd_config.d/*.conf\nAcceptEnv LANG LC_*\n"
    )
    return path


@pytest.fixture
def runtime(tmp_path):
    spec = fake_runtime(tmp_path / "host")
    return lambda: spec


def snapshot(root, *, mtimes=True):
    result = {}
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        stamp = info.st_mtime_ns if mtimes else None
        result[str(path.relative_to(root))] = (stat.S_IMODE(info.st_mode), stamp, digest)
    return result


def run(root, command, runtime, *extra, host=None, pin=None):
    args = [command, "--root", str(root), *extra]
    return install.main(args, host=host or install.StagedHost(), runtime_source=runtime, pin=pin)


def installed(root, runtime, *extra, host=None, pin=None):
    host = host or install.StagedHost()
    code = run(
        root, "install", runtime, *IDENTITY, "--controller-key", KEY, *extra, host=host, pin=pin
    )
    assert code == 0
    return host


def test_plan_lists_every_action_and_touches_nothing(root, runtime, capsys):
    before = snapshot(root)
    host = install.StagedHost()
    code = run(root, "plan", runtime, *IDENTITY, "--controller-key", KEY, host=host)
    out = capsys.readouterr().out
    assert code == 0
    assert snapshot(root) == before and host.commands == []
    assert "read-only python probe" in out and "changes nothing on disk" in out
    assert "touches nothing" not in out
    for needle in (
        "account commonplace-compute",
        "dir /var/lib/commonplace-compute 0700",
        "dir /var/lib/commonplace-compute/attempts 0700",
        "dir /var/lib/commonplace-compute/inputs 0700",
        "tmpfs-mount /var/lib/commonplace-compute/scratch size=32M nr_inodes=4096",
        "runtime-tree /opt/commonplace-compute/runtime-src",
        "ro-bind-mount /opt/commonplace-compute/runtime",
        "file /usr/local/libexec/commonplace-compute/helper-entry 0755",
        "authorized-key /etc/commonplace/compute-authorized-keys",
        "sshd-dropin /etc/ssh/sshd_config.d/60-commonplace-compute.conf",
        f"target-config {TARGET_CONFIG_PATH} 0644",
    ):
        assert needle in out, needle
    assert KEY not in out  # the public key is data, the plan names only its fingerprint
    assert PRINCIPAL in out
    assert "approval-keys" not in out


def test_plan_includes_the_pin_step_only_when_a_keyset_is_given(root, runtime, capsys):
    run(root, "plan", runtime, *IDENTITY, "--controller-key", KEY, "--approval-keyset", "/keys/set")
    assert "approval-keys /keys/set (seam: awaiting #431 format)" in capsys.readouterr().out


def test_install_creates_exactly_the_expected_tree_and_modes(root, runtime):
    host = installed(root, runtime)
    seen = {str(p.relative_to(root)) for p in root.rglob("*")}
    assert seen == expected_paths()
    modes = {name: stat.S_IMODE((root / name).lstat().st_mode) for name in seen}
    private = "var/lib/commonplace-compute"
    for name in (private, private + "/attempts", private + "/inputs", private + "/scratch"):
        assert modes[name] == 0o700, name
    assert modes["etc/commonplace"] == 0o755
    assert modes["etc/commonplace/compute-script-target-v1.json"] == 0o644
    assert modes["etc/commonplace/compute-authorized-keys"] == 0o644
    assert modes["etc/commonplace/compute-install-manifest.json"] == 0o600
    assert modes["usr/local/libexec/commonplace-compute/helper-entry"] == 0o755
    assert modes["etc/ssh/sshd_config.d/60-commonplace-compute.conf"] == 0o644
    for name in expected_paths():
        if name.startswith("opt/commonplace-compute/runtime-src/"):
            assert not modes[name] & 0o022, name  # runtime_inventory rejects group/world write
    # Only the installer's own targets plus the preexisting sshd_config; no foreign files.
    assert [c[0] for c in host.commands].count("useradd") == 1


def test_config_has_exact_fields_and_binds_the_key_and_runtime(root, runtime):
    installed(root, runtime)
    raw = json.loads((root / "etc/commonplace/compute-script-target-v1.json").read_text())
    assert set(raw) == set(ScriptTargetConfig.__dataclass_fields__)
    config = ScriptTargetConfig(**raw)
    assert config.enabled is True
    assert (config.workspace, config.gateway, config.node) == (
        "fixture-script-workspace",
        "gw1",
        "node1",
    )
    assert config.key_principal == PRINCIPAL
    assert config.workspace_root == "/var/lib/commonplace-compute"
    assert config.scratch_root == "/var/lib/commonplace-compute/scratch"
    assert config.runtime_root == "/opt/commonplace-compute/runtime"
    assert config.runtime_digest == install.staged_inventory(
        root / "opt/commonplace-compute/runtime-src"
    )
    assert config.cgroup_root.startswith("/sys/fs/cgroup/user.slice/user-")
    assert config.cgroup_root.endswith("/commonplace-compute-helper.scope/fixture")
    assert config.controller_contract == "controller-ssh-permit-v1"
    assert TARGET_CONFIG_PATH == "/" + "etc/commonplace/compute-script-target-v1.json"


def test_sshd_dropin_authorized_key_and_entry_content(root, runtime):
    installed(root, runtime)
    dropin = (root / "etc/ssh/sshd_config.d/60-commonplace-compute.conf").read_text().splitlines()
    block = [line.strip() for line in dropin if line.strip() and not line.startswith("#")]
    assert block[0] == "Match User commonplace-compute"
    assert block[-1] == "Match all"
    for line in (
        "ExposeAuthInfo yes",
        "ForceCommand /usr/local/libexec/commonplace-compute/helper-entry",
        "AuthorizedKeysFile /etc/commonplace/compute-authorized-keys",
        "AuthenticationMethods publickey",
        "PasswordAuthentication no",
        "PermitTTY no",
        "AllowTcpForwarding no",
        "AllowAgentForwarding no",
    ):
        assert line in block, line
    assert not any(line.startswith("AcceptEnv") for line in block)
    keys = (root / "etc/commonplace/compute-authorized-keys").read_text()
    assert keys == "restrict " + KEY + "\n"
    entry = root / "usr/local/libexec/commonplace-compute/helper-entry"
    text = entry.read_text()
    assert text.startswith("#!/bin/sh\n")
    assert f"exec {TARGET_HELPER_COMMAND}\n" in text
    assert "Delegate=yes" in text and "+cpu +memory +pids" in text
    assert subprocess.run(["sh", "-n", str(entry)]).returncode == 0


def test_live_host_commands_and_unit_content(root, runtime):
    host = LiveStub()
    installed(root, runtime, host=host)
    assert host.commands[0][:4] == ("useradd", "--system", "--user-group", "--home-dir")
    assert "--no-create-home" in host.commands[0] and host.commands[0][-1] == "commonplace-compute"
    names = [c for c in host.commands if c[0] == "systemctl"]
    assert ("systemctl", "daemon-reload") in names
    assert ("systemctl", "enable", "--now", SCRATCH_UNIT) in names
    assert ("systemctl", "enable", "--now", RUNTIME_UNIT) in names
    assert ("sshd", "-t") in host.commands
    assert ("systemctl", "reload", "ssh.service") in names
    scratch = (root / "etc/systemd/system" / SCRATCH_UNIT).read_text()
    assert "Type=tmpfs" in scratch
    assert "Options=mode=0700,uid=1234,gid=1234,size=32M,nr_inodes=4096,nosuid,nodev" in scratch
    runtime_unit = (root / "etc/systemd/system" / RUNTIME_UNIT).read_text()
    assert "What=/opt/commonplace-compute/runtime-src" in runtime_unit
    assert "Options=bind,ro" in runtime_unit
    owned = {path for path, uid, gid in host.chowns}
    assert {
        "/var/lib/commonplace-compute",
        "/var/lib/commonplace-compute/attempts",
        "/var/lib/commonplace-compute/inputs",
        "/var/lib/commonplace-compute/scratch",
    } <= {p.removeprefix(str(root)) for p in owned}
    assert all(uid == gid == 1234 for _, uid, gid in host.chowns)


class LiveStub(install.StagedHost):
    """Pretends to be root on a real host but only records; never executes anything."""

    live = True

    def __init__(self):
        super().__init__(uid=1234, gid=1234)
        self.chowns = []
        self.owned = set()

    def euid(self):
        return 0

    def owner_of(self, path):
        return (self.uid, self.gid) if str(path) in self.owned else (-1, -1)

    def chown(self, path, uid, gid):
        self.chowns.append((str(path), uid, gid))
        self.owned.add(str(path))


def test_second_install_is_a_noop(root, runtime, capsys):
    host = installed(root, runtime)
    before, commands = snapshot(root), list(host.commands)
    manifest = (root / "etc/commonplace/compute-install-manifest.json").read_bytes()
    capsys.readouterr()
    code = run(root, "install", runtime, *IDENTITY, "--controller-key", KEY, host=host)
    assert code == 0
    assert "already installed; nothing to do" in capsys.readouterr().out
    assert snapshot(root) == before
    assert host.commands == commands
    assert (root / "etc/commonplace/compute-install-manifest.json").read_bytes() == manifest


def test_changed_settings_refuse_instead_of_overwriting(root, runtime, capsys):
    installed(root, runtime)
    before = snapshot(root)
    code = run(root, "install", runtime, *IDENTITY[:-1], "node2", "--controller-key", KEY)
    assert code == 1
    assert "uninstall first" in capsys.readouterr().err
    assert snapshot(root) == before


def test_uninstall_restores_root_to_its_initial_state(root, runtime):
    initial = snapshot(root, mtimes=False)
    host = installed(root, runtime)
    assert run(root, "uninstall", runtime, host=host) == 0
    assert snapshot(root, mtimes=False) == initial
    assert ("userdel", "commonplace-compute") in host.commands


def test_uninstall_restores_an_empty_root(tmp_path, runtime):
    empty = tmp_path / "empty"
    empty.mkdir()
    host = install.StagedHost()
    # With no sshd_config the staged install skips the sshd preflight and creates its own dirs.
    assert run(empty, "install", runtime, *IDENTITY, "--controller-key", KEY, host=host) == 0
    assert (empty / "etc/commonplace/compute-script-target-v1.json").is_file()
    assert run(empty, "uninstall", runtime, host=host) == 0
    assert list(empty.rglob("*")) == []


def test_uninstall_stops_units_and_reloads_sshd(root, runtime):
    host = LiveStub()
    installed(root, runtime, host=host)
    host.commands.clear()
    assert run(root, "uninstall", runtime, host=host) == 0
    assert ("systemctl", "disable", "--now", RUNTIME_UNIT) in host.commands
    assert ("systemctl", "disable", "--now", SCRATCH_UNIT) in host.commands
    assert ("systemctl", "reload", "ssh.service") in host.commands
    assert host.commands.index(
        ("systemctl", "disable", "--now", RUNTIME_UNIT)
    ) < host.commands.index(("userdel", "commonplace-compute"))


def test_uninstall_keeps_attempt_evidence_unless_discarded(root, runtime, capsys):
    installed(root, runtime)
    claim = root / "var/lib/commonplace-compute/attempts/claim0"
    claim.mkdir()
    (claim / "claim.json").write_text("{}")
    before = snapshot(root)
    assert run(root, "uninstall", runtime) == 1
    assert "evidence" in capsys.readouterr().err
    assert snapshot(root) == before
    assert run(root, "uninstall", runtime, "--discard-workspace-data") == 0
    assert not (root / "var").exists()


def test_uninstall_leaves_files_changed_after_install(root, runtime, capsys):
    installed(root, runtime)
    dropin = root / "etc/ssh/sshd_config.d/60-commonplace-compute.conf"
    dropin.write_text(dropin.read_text() + "# operator edit\n")
    assert run(root, "uninstall", runtime) == 1
    assert "changed since install" in capsys.readouterr().err
    assert dropin.exists()
    assert (root / "etc/commonplace/compute-install-manifest.json").exists()
    dropin.unlink()  # operator resolves it; uninstall now finishes
    assert run(root, "uninstall", runtime) == 0
    assert not (root / "opt").exists() and not (root / "var").exists()


def test_real_install_refuses_without_root(root, runtime):
    class NotRoot(LiveStub):
        def euid(self):
            return 1000

    host = NotRoot()
    before = snapshot(root)
    with pytest.raises(install.InstallError, match="must run as root"):
        install.Installer(
            install.InstallOptions(
                root=root,
                workspace="w",
                gateway="g",
                node="n",
                controller_key=install.parse_controller_key(KEY),
            ),
            host,
            runtime_source=runtime,
        ).install()
    assert snapshot(root) == before and host.commands == [] and host.chowns == []


def test_cli_without_root_prefix_selects_the_live_host_and_refuses(monkeypatch, runtime, capsys):
    class NotRoot(LiveStub):
        def euid(self):
            return 1000

    monkeypatch.setattr(install, "LiveHost", NotRoot)
    code = install.main(["install", *IDENTITY, "--controller-key", KEY], runtime_source=runtime)
    assert code == 1
    assert "must run as root" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv, message",
    [
        ([], "controller key"),
        (["--controller-key-file", "/nonexistent/key.pub"], "controller key"),
        (["--controller-key", "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAAAgQ"], "ed25519"),
        (["--controller-key", 'command="id" ' + KEY], "ed25519"),
        (["--controller-key", KEY + "\n" + KEY], "single"),
        (["--controller-key=-----BEGIN OPENSSH PRIVATE KEY-----"], "ed25519"),
        (["--controller-key", "ssh-ed25519 AAAA"], "ed25519"),
    ],
)
def test_missing_or_unsuitable_controller_key_is_refused(root, runtime, capsys, argv, message):
    before = snapshot(root)
    host = install.StagedHost()
    code = run(root, "install", runtime, *IDENTITY, *argv, host=host)
    err = capsys.readouterr().err
    assert code in (1, 2)
    assert message in err
    assert snapshot(root) == before and host.commands == []
    assert "PRIVATE KEY" not in err


def test_private_key_material_is_never_echoed(root, runtime, tmp_path, capsys):
    secret = tmp_path / "secret.pem"
    secret.write_text(
        "-----BEGIN OPENSSH PRIVATE KEY-----\nTOPSECRETBODY\n-----END OPENSSH PRIVATE KEY-----\n"
    )
    assert run(root, "install", runtime, *IDENTITY, "--controller-key-file", str(secret)) == 1
    captured = capsys.readouterr()
    assert "TOPSECRETBODY" not in captured.out + captured.err
    assert "PRIVATE KEY" not in captured.out + captured.err


def test_key_file_is_accepted_and_comment_is_dropped(root, runtime, tmp_path):
    key_file = tmp_path / "controller.pub"
    key_file.write_text(KEY + " controller@example.test\n")
    code = run(root, "install", runtime, *IDENTITY, "--controller-key-file", str(key_file))
    assert code == 0
    assert (
        root / "etc/commonplace/compute-authorized-keys"
    ).read_text() == "restrict " + KEY + "\n"


@pytest.mark.parametrize(
    "line",
    ["AcceptEnv SSH_USER_AUTH", "AcceptEnv LANG SSH_*", "AcceptEnv *"],
)
def test_sshd_accepting_the_auth_info_variable_is_refused(root, runtime, capsys, line):
    (root / "etc/ssh/sshd_config.d/10-other.conf").write_text(line + "\n")
    before = snapshot(root)
    assert run(root, "install", runtime, *IDENTITY, "--controller-key", KEY) == 1
    assert "SSH_USER_AUTH" in capsys.readouterr().err
    assert snapshot(root) == before


def test_sshd_without_dropin_include_is_refused(root, runtime, capsys):
    (root / "etc/ssh/sshd_config").write_text("Port 22\n")
    assert run(root, "install", runtime, *IDENTITY, "--controller-key", KEY) == 1
    assert "sshd_config.d" in capsys.readouterr().err


def test_foreign_files_and_accounts_are_never_adopted(root, runtime, capsys):
    foreign = root / "etc/ssh/sshd_config.d/60-commonplace-compute.conf"
    foreign.write_text("# somebody else's\n")
    before = snapshot(root)
    assert run(root, "install", runtime, *IDENTITY, "--controller-key", KEY) == 1
    assert "not installed by this installer" in capsys.readouterr().err
    assert snapshot(root) == before

    foreign.unlink()

    class Existing(install.StagedHost):
        def account_exists(self, name):
            return True

    host = Existing()
    assert run(root, "install", runtime, *IDENTITY, "--controller-key", KEY, host=host) == 1
    assert "account commonplace-compute already exists" in capsys.readouterr().err
    assert host.commands == []


def test_pin_seam_is_called_with_the_keyset_path_before_activation(root, runtime):
    calls = []

    def pin(path):
        calls.append(path)
        assert not (root / "etc/commonplace/compute-script-target-v1.json").exists()

    installed(root, runtime, "--approval-keyset", "/keys/owner.set", pin=pin)
    assert calls == ["/keys/owner.set"]
    assert (root / "etc/commonplace/compute-script-target-v1.json").is_file()


def test_pin_seam_defaults_to_not_implemented_and_blocks_activation(root, runtime, capsys):
    with pytest.raises(NotImplementedError, match="awaiting #431 format"):
        install.pin_approval_keys("/keys/owner.set")
    code = run(
        root, "install", runtime, *IDENTITY, "--controller-key", KEY, "--approval-keyset", "/k"
    )
    assert code == 1
    assert "awaiting #431 format" in capsys.readouterr().err
    # The helper stays disabled: no config was written, so activation never happened.
    assert not (root / "etc/commonplace/compute-script-target-v1.json").exists()


def test_install_resumes_after_a_failed_pin_and_pins_once(root, runtime):
    calls = []
    assert (
        run(root, "install", runtime, *IDENTITY, "--controller-key", KEY, "--approval-keyset", "/k")
        == 1
    )
    code = run(
        root,
        "install",
        runtime,
        *IDENTITY,
        "--controller-key",
        KEY,
        "--approval-keyset",
        "/k",
        pin=calls.append,
    )
    assert code == 0 and calls == ["/k"]
    assert {str(p.relative_to(root)) for p in root.rglob("*")} == expected_paths()
    assert (
        run(
            root,
            "install",
            runtime,
            *IDENTITY,
            "--controller-key",
            KEY,
            "--approval-keyset",
            "/k",
            pin=lambda path: pytest.fail("pinned twice"),
        )
        == 0
    )
    assert run(root, "uninstall", runtime) == 0


def test_failed_sshd_validation_rolls_back_the_dropin(root, runtime, capsys):
    class Broken(LiveStub):
        def run(self, argv):
            super().run(argv)
            if tuple(argv) == ("sshd", "-t"):
                raise install.InstallError("sshd rejected the configuration")

    host = Broken()
    code = run(root, "install", runtime, *IDENTITY, "--controller-key", KEY, host=host)
    assert code == 1
    assert not (root / "etc/ssh/sshd_config.d/60-commonplace-compute.conf").exists()
    assert ("systemctl", "reload", "ssh.service") not in host.commands
    assert not (root / "etc/commonplace/compute-script-target-v1.json").exists()


def test_install_can_be_resumed_after_an_interruption(root, runtime):
    class Interrupt(install.StagedHost):
        def run(self, argv):
            super().run(argv)
            if argv[0] == "systemctl" and "enable" in argv:
                raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run(root, "install", runtime, *IDENTITY, "--controller-key", KEY, host=Interrupt())
    assert run(root, "install", runtime, *IDENTITY, "--controller-key", KEY) == 0
    assert {str(p.relative_to(root)) for p in root.rglob("*")} == expected_paths()
    assert run(root, "uninstall", runtime) == 0
    assert {str(p.relative_to(root)) for p in root.rglob("*")} == {
        "etc",
        "etc/ssh",
        "etc/ssh/sshd_config",
        "etc/ssh/sshd_config.d",
    }


def test_installed_config_is_what_the_sandbox_fixture_loads(root, runtime):
    """COMMONPLACE_COMPUTE_SANDBOX_FIXTURE can point at the config this installer writes."""
    host = installed(root, runtime)
    config_path = root / "etc/commonplace/compute-script-target-v1.json"
    settings = ScriptTargetConfig(**json.loads(config_path.read_text()))
    assert settings.workspace == "fixture-script-workspace"  # the fixture test's reserved identity
    assert asdict(settings) == json.loads(config_path.read_text())
    from commonplace.compute.target_helper import TargetHelper

    helper = TargetHelper(settings, key_principal=settings.key_principal)
    assert helper.config.scratch_root == settings.workspace_root + "/scratch"
    assert host.commands  # the staged host recorded what a live install would run


def test_install_prints_the_fixture_variable(root, runtime, capsys):
    run(root, "install", runtime, *IDENTITY, "--controller-key", KEY)
    out = capsys.readouterr().out
    assert (
        f"COMMONPLACE_COMPUTE_SANDBOX_FIXTURE={root.resolve()}/etc/commonplace/compute-script-target-v1.json"
        in out
    )


def test_systemd_escaping_matches_systemd_escape():
    assert install.systemd_escape_path("/var/lib/commonplace-compute/scratch") == SCRATCH_UNIT[:-6]
    assert install.systemd_escape_path("/a b/c") == "a\\x20b-c"
    assert install.systemd_escape_path("/.c") == "\\x2ec"


def test_parse_ldd_ignores_the_vdso_and_keeps_the_loader():
    text = (
        "\tlinux-vdso.so.1 (0x00007ffc)\n"
        "\tlibm.so.6 => /lib/x86_64-linux-gnu/libm.so.6 (0x00007f0)\n"
        "\tlibc.so.6 => /lib/x86_64-linux-gnu/libc.so.6 (0x00007f1)\n"
        "\t/lib64/ld-linux-x86-64.so.2 (0x00007f2)\n"
        "\tlibmissing.so => not found\n"
    )
    assert install.parse_ldd(text) == [
        "/lib/x86_64-linux-gnu/libm.so.6",
        "/lib/x86_64-linux-gnu/libc.so.6",
        "/lib64/ld-linux-x86-64.so.2",
    ]
    with pytest.raises(install.InstallError, match="not found"):
        install.parse_ldd(text, strict=True)


def test_runtime_tree_is_reproducible_and_excludes_tests_and_bytecode(tmp_path):
    spec = fake_runtime(tmp_path / "host")
    first, second = tmp_path / "a", tmp_path / "b"
    install.build_runtime_tree(first, spec)
    install.build_runtime_tree(second, spec)
    assert install.staged_inventory(first) == install.staged_inventory(second)
    import zipfile

    names = zipfile.ZipFile(first / "usr/lib/python312.zip").namelist()
    assert sorted(names) == ["encodings/__init__.py", "os.py"]
    for path in first.rglob("*"):
        assert not stat.S_IMODE(path.lstat().st_mode) & 0o022


@pytest.mark.skipif(
    not (Path("/usr/bin/ldd").exists() and Path("/usr/bin/python3").exists()),
    reason="needs a host python3 and ldd",
)
def test_host_runtime_discovery_builds_a_self_contained_tree(tmp_path):
    spec = install.discover_runtime("/usr/bin/python3")
    tree = tmp_path / "tree"
    install.build_runtime_tree(tree, spec)
    version = "%d%d" % spec.version
    assert (tree / "usr/bin/python3").is_file()
    assert (tree / f"usr/lib/python{version}.zip").is_file()
    assert any(dest.endswith(("libc.so.6",)) for dest, _ in spec.libs)
    assert not any(path.is_symlink() for path in tree.rglob("*"))
    assert len(install.staged_inventory(tree)) == 64
    shutil.rmtree(tree)


# --- fix round 1: sshd Match validity, placed package, account stop, bare uninstall ---


def block_keywords(text):
    inside, keywords = False, []
    for line in text.splitlines():
        words = line.split()
        if not words or line.startswith("#"):
            continue
        if words[0] == "Match":
            inside = words[1:] != ["all"]
            continue
        if inside:
            keywords.append(words[0])
    return keywords


def test_match_block_leaves_out_global_only_directives(root, runtime):
    installed(root, runtime)
    text = (root / "etc/ssh/sshd_config.d/60-commonplace-compute.conf").read_text()
    # sshd rejects this one inside Match ("not allowed within a Match block").
    assert "PermitUserEnvironment" not in text


def test_match_block_uses_only_directives_allowed_in_match(root, runtime):
    installed(root, runtime)
    text = (root / "etc/ssh/sshd_config.d/60-commonplace-compute.conf").read_text()
    keywords = block_keywords(text)
    assert keywords
    assert set(keywords) <= install.SSHD_MATCH_KEYWORDS
    assert "PermitUserEnvironment" not in install.SSHD_MATCH_KEYWORDS


def test_match_validator_rejects_a_global_only_directive():
    good = install.sshd_dropin("commonplace-compute")
    install.validate_match_block(good)
    bad = good.replace("    PermitTTY no\n", "    PermitTTY no\n    PermitUserEnvironment no\n")
    with pytest.raises(install.InstallError, match="PermitUserEnvironment"):
        install.validate_match_block(bad)


@pytest.mark.skipif(
    not (Path("/usr/sbin/sshd").exists() and shutil.which("ssh-keygen")), reason="needs sshd"
)
def test_real_sshd_accepts_the_generated_match_block(tmp_path):
    key = tmp_path / "host_key"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    config = tmp_path / "sshd_config"
    config.write_text(install.sshd_dropin("commonplace-compute"))
    done = subprocess.run(
        ["/usr/sbin/sshd", "-t", "-f", str(config), "-h", str(key)], capture_output=True, text=True
    )
    if "privilege separation" in done.stderr or "Missing" in done.stderr:
        pytest.skip("sshd -t cannot run unprivileged here")
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize(
    "effective", ["permituserenvironment yes\n", "permituserenvironment all\n", "port 22\n"]
)
def test_global_permit_user_environment_must_be_no(root, runtime, capsys, effective):
    class Effective(install.StagedHost):
        def sshd_effective_config(self):
            return effective

    host = Effective()
    before = snapshot(root)
    assert run(root, "install", runtime, *IDENTITY, "--controller-key", KEY, host=host) == 1
    assert "PermitUserEnvironment" in capsys.readouterr().err
    assert snapshot(root) == before and host.commands == []


def test_global_permit_user_environment_no_is_accepted(root, runtime):
    class Effective(install.StagedHost):
        def sshd_effective_config(self):
            return "port 22\npermituserenvironment no\n"

    installed(root, runtime, host=Effective())


def test_install_places_the_python_packages_and_a_pth(root, runtime):
    installed(root, runtime)
    lib = root / LIB
    placed = {str(p.relative_to(lib)) for p in lib.rglob("*")}
    assert placed == PACKAGE_TREE  # python sources only: no bytecode, no data files
    for path in [lib, *lib.rglob("*")]:
        assert not stat.S_IMODE(path.lstat().st_mode) & 0o022
    pth = root / SITE_DIR / "commonplace-compute.pth"
    assert pth.read_text() == "/" + LIB + "\n"
    assert stat.S_IMODE(pth.stat().st_mode) == 0o644
    manifest = json.loads((root / "etc/commonplace/compute-install-manifest.json").read_text())
    kinds = {(item["kind"], item.get("path")) for item in manifest["items"]}
    assert ("tree", "/" + LIB) in kinds
    assert ("file", "/" + SITE_DIR + "/commonplace-compute.pth") in kinds


def test_install_verifies_the_import_after_placing_it(root, runtime):
    host = LiveStub()
    installed(root, runtime, host=host)
    check = next(c for c in host.commands if "import commonplace.compute.target_helper" in c[-1])
    assert check[:4] == ("/usr/bin/python3", "-I", "-B", "-c")
    assert host.commands.count(check) == 1
    assert host.commands.index(check) < host.commands.index(("sshd", "-t"))
    again = len(host.commands)
    run(root, "install", runtime, *IDENTITY, "--controller-key", KEY, host=host)
    assert len(host.commands) == again


def test_preflight_does_not_require_the_package_to_be_installed_already(monkeypatch):
    seen = []
    monkeypatch.setattr(install, "_host_run", lambda argv, timeout=30: seen.append(argv) or "")
    install.LiveHost().preflight(install.InstallOptions())
    assert not any("target_helper" in " ".join(argv) for argv in seen)


def test_a_failed_import_check_stops_before_sshd_changes(root, runtime):
    class Broken(LiveStub):
        def run(self, argv):
            super().run(argv)
            if "target_helper" in " ".join(argv):
                raise install.InstallError("import failed")

    host = Broken()
    assert run(root, "install", runtime, *IDENTITY, "--controller-key", KEY, host=host) == 1
    assert not (root / "etc/ssh/sshd_config.d/60-commonplace-compute.conf").exists()
    assert not (root / "etc/commonplace/compute-script-target-v1.json").exists()


def test_the_real_packages_import_with_only_the_stdlib_and_the_placed_copy(tmp_path):
    lib = tmp_path / "lib"
    install.copy_python_packages(lib, REAL_RUNNING_PACKAGES())
    site = tmp_path / "site"
    site.mkdir()
    (site / "commonplace-compute.pth").write_text(str(lib) + "\n")
    code = (
        "import site,sys;site.addsitedir(sys.argv[1]);"
        "import commonplace.compute.target_helper as h;"
        "bad=[m for m,v in sys.modules.items() if getattr(v,'__file__',None)"
        " and v.__file__.startswith(sys.argv[2]) is False and 'site-packages' in v.__file__];"
        "print(h.TARGET_HELPER_COMMAND, bad)"
    )
    done = subprocess.run(
        [sys.executable, "-I", "-B", "-S", "-c", code, str(site), str(lib)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip().endswith("[]")
    for path in lib.rglob("*"):
        assert path.is_dir() or path.suffix == ".py"
        assert not stat.S_IMODE(path.lstat().st_mode) & 0o022


def test_site_directory_choice_prefers_the_local_dist_packages():
    lines = ["/usr/lib/python3/dist-packages", "/usr/local/lib/python3.12/dist-packages"]
    assert install.choose_site_dir(lines) == "/usr/local/lib/python3.12/dist-packages"
    with pytest.raises(install.InstallError, match="site"):
        install.choose_site_dir([])


def test_uninstall_stops_the_account_before_userdel(root, runtime):
    host = LiveStub()
    installed(root, runtime, host=host)
    host.linger = True
    host.commands.clear()
    assert run(root, "uninstall", runtime, host=host) == 0
    commands = host.commands
    stop = commands.index(("loginctl", "terminate-user", "commonplace-compute"))
    assert ("loginctl", "disable-linger", "commonplace-compute") in commands
    assert stop < commands.index(("userdel", "commonplace-compute"))
    assert commands.index(("loginctl", "disable-linger", "commonplace-compute")) < commands.index(
        ("userdel", "commonplace-compute")
    )


def test_uninstall_skips_disable_linger_when_it_is_not_set(root, runtime):
    host = LiveStub()
    installed(root, runtime, host=host)
    host.commands.clear()
    assert run(root, "uninstall", runtime, host=host) == 0
    assert ("loginctl", "terminate-user", "commonplace-compute") in host.commands
    assert not any(c[:2] == ("loginctl", "disable-linger") for c in host.commands)


def test_uninstall_leaves_processes_alone_while_evidence_blocks_it(root, runtime):
    host = LiveStub()
    installed(root, runtime, host=host)
    (root / "var/lib/commonplace-compute/attempts/claim0").mkdir()
    (root / "var/lib/commonplace-compute/attempts/claim0/claim.json").write_text("{}")
    host.commands.clear()
    assert run(root, "uninstall", runtime, host=host) == 1
    assert host.commands == []


def test_uninstall_needs_no_install_arguments(root, runtime):
    installed(root, runtime)
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "commonplace.compute.target_installer",
            "uninstall",
            "--root",
            str(root),
        ],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert done.returncode == 0, done.stderr
    assert "uninstalled" in done.stdout
    assert {str(p.relative_to(root)) for p in root.rglob("*")} == {
        "etc",
        "etc/ssh",
        "etc/ssh/sshd_config",
        "etc/ssh/sshd_config.d",
    }


# --- fix round 2: recorded before created, bytecode tolerance ---


def new_installer(root, host, runtime):
    return install.Installer(
        install.InstallOptions(
            root=root,
            workspace="fixture-script-workspace",
            gateway="gw1",
            node="node1",
            controller_key=install.parse_controller_key(KEY),
        ),
        host,
        runtime_source=runtime,
    )


@pytest.mark.parametrize(
    "after_mkdir", [False, True], ids=["fails-before-mkdir", "fails-after-mkdir"]
)
@pytest.mark.parametrize("then", ["install", "uninstall"])
def test_an_interrupted_mkdir_leaves_a_recorded_directory_and_both_converge(
    root, runtime, monkeypatch, after_mkdir, then
):
    initial = snapshot(root, mtimes=False)
    real = install._mkdir
    hits = []

    def flaky(path, mode=0o755):
        if (
            Path(path).name == "commonplace-compute"
            and Path(path).parent.name == "lib"
            and not hits
        ):
            hits.append(path)
            if after_mkdir:
                real(path, mode)
            raise OSError("synthetic mkdir failure")
        return real(path, mode)

    monkeypatch.setattr(install, "_mkdir", flaky)
    with pytest.raises(OSError, match="synthetic"):
        new_installer(root, install.StagedHost(), runtime).install()
    monkeypatch.setattr(install, "_mkdir", real)
    manifest = json.loads((root / "etc/commonplace/compute-install-manifest.json").read_text())
    assert {"kind": "dir", "path": "/var/lib/commonplace-compute"} in manifest["items"]
    if then == "install":
        assert run(root, "install", runtime, *IDENTITY, "--controller-key", KEY) == 0
        assert {str(p.relative_to(root)) for p in root.rglob("*")} == expected_paths()
    assert run(root, "uninstall", runtime) == 0
    assert snapshot(root, mtimes=False) == initial


def test_an_interrupted_chown_is_repaired_on_the_next_install_and_removed_by_uninstall(
    root, runtime
):
    initial = snapshot(root, mtimes=False)

    class FailingChown(LiveStub):
        failed = False

        def chown(self, path, uid, gid):
            if not self.failed:
                self.failed = True
                raise OSError("synthetic chown failure")
            super().chown(path, uid, gid)

    host = FailingChown()
    with pytest.raises(OSError, match="synthetic"):
        new_installer(root, host, runtime).install()
    assert host.chowns == []
    assert run(root, "install", runtime, *IDENTITY, "--controller-key", KEY, host=host) == 0
    workspace = str(root / "var/lib/commonplace-compute")
    assert workspace in {path for path, _, _ in host.chowns}  # the dir that missed its chown
    assert {str(p.relative_to(root)) for p in root.rglob("*")} == expected_paths()
    assert run(root, "uninstall", runtime, host=host) == 0
    assert snapshot(root, mtimes=False) == initial


def test_an_interrupted_account_creation_is_recorded_and_converges(root, runtime):
    initial = snapshot(root, mtimes=False)

    class FailingUseradd(install.StagedHost):
        failed = False

        def run(self, argv):
            if argv[0] == "useradd" and not self.failed:
                self.failed = True
                raise install.InstallError("synthetic useradd failure")
            super().run(argv)

    host = FailingUseradd()
    assert run(root, "install", runtime, *IDENTITY, "--controller-key", KEY, host=host) == 1
    manifest = json.loads((root / "etc/commonplace/compute-install-manifest.json").read_text())
    assert any(item["kind"] == "account" for item in manifest["items"])
    assert run(root, "install", runtime, *IDENTITY, "--controller-key", KEY, host=host) == 0
    assert [c[0] for c in host.commands].count("useradd") == 1
    assert run(root, "uninstall", runtime, host=host) == 0
    assert snapshot(root, mtimes=False) == initial


def write_bytecode(root):
    cache = root / LIB / "commonplace/compute/__pycache__"
    cache.mkdir(mode=0o755)
    (cache / "target_helper.cpython-312.pyc").write_bytes(b"\x00pyc")
    (cache / "__init__.cpython-312.pyc").write_bytes(b"\x00pyc")
    os.chmod(cache, 0o775)  # python creates these with the caller's umask, not ours
    top = root / LIB / "mempalace/__pycache__"
    top.mkdir()
    (top / "__init__.cpython-312.pyc").write_bytes(b"\x00pyc")


def test_bytecode_written_into_the_placed_tree_does_not_break_reinstall(root, runtime):
    host = installed(root, runtime)
    write_bytecode(root)
    commands = list(host.commands)
    before = snapshot(root)
    code = run(root, "install", runtime, *IDENTITY, "--controller-key", KEY, host=host)
    assert code == 0
    assert host.commands == commands
    assert snapshot(root) == before


def test_uninstall_removes_the_bytecode_it_did_not_install(root, runtime):
    initial = snapshot(root, mtimes=False)
    installed(root, runtime)
    write_bytecode(root)
    assert run(root, "uninstall", runtime) == 0
    assert snapshot(root, mtimes=False) == initial


def test_real_changes_to_the_placed_sources_are_still_detected(root, runtime, capsys):
    installed(root, runtime)
    write_bytecode(root)
    (root / LIB / "commonplace/compute/target_helper.py").write_text("# tampered\n")
    assert run(root, "install", runtime, *IDENTITY, "--controller-key", KEY) == 1
    assert "uninstall first" in capsys.readouterr().err
    extra = root / LIB / "commonplace/compute/extra.py"
    extra.write_text("# new file\n")
    os.chmod(extra, 0o644)
    assert run(root, "uninstall", runtime) == 1
    assert "changed since install" in capsys.readouterr().err


def test_staged_inventory_can_ignore_bytecode_only_on_request(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "a.py").write_text("x = 1\n")
    os.chmod(tree / "a.py", 0o644)
    os.chmod(tree, 0o755)
    plain = install.staged_inventory(tree, ignore_bytecode=True)
    (tree / "__pycache__").mkdir()
    (tree / "__pycache__" / "a.cpython-312.pyc").write_bytes(b"x")
    os.chmod(tree / "__pycache__", 0o775)
    assert install.staged_inventory(tree, ignore_bytecode=True) == plain
    with pytest.raises(install.InstallError):
        install.staged_inventory(tree)


# --- follow-up to the target installer gate findings ---

STRAY = "usr/local/libexec/commonplace-compute/operator-note"


def test_uninstall_stops_the_account_before_discarding_workspace_data(root, runtime):
    seen = []

    class Watching(install.StagedHost):
        def stop_account(self, name):
            seen.append((root / "var/lib/commonplace-compute/attempts/claim0").exists())

    host = Watching()
    installed(root, runtime, host=host)
    (root / "var/lib/commonplace-compute/attempts/claim0").mkdir()
    assert run(root, "uninstall", runtime, "--discard-workspace-data", host=host) == 0
    assert seen == [True]  # the evidence was still there when the processes were stopped


def test_uninstall_removes_nothing_when_the_account_cannot_be_stopped(root, runtime, capsys):
    class Stuck(install.StagedHost):
        def stop_account(self, name):
            raise install.InstallError(f"processes of {name} are still running")

    host = Stuck()
    installed(root, runtime, host=host)
    claim = root / "var/lib/commonplace-compute/attempts/claim0"
    claim.mkdir()
    (claim / "claim.json").write_text("{}")
    before = snapshot(root)
    assert run(root, "uninstall", runtime, "--discard-workspace-data", host=host) == 1
    assert "still running" in capsys.readouterr().err
    assert snapshot(root) == before


def test_live_stop_account_refuses_while_processes_remain(monkeypatch):
    clock = iter(range(0, 1000, 10))
    monkeypatch.setattr(
        install,
        "time",
        types.SimpleNamespace(monotonic=lambda: next(clock), sleep=lambda seconds: None),
    )
    monkeypatch.setattr(install, "_has_processes", lambda uid: True)
    monkeypatch.setattr(install.pwd, "getpwnam", lambda name: types.SimpleNamespace(pw_uid=4242))
    host = install.LiveHost()
    monkeypatch.setattr(host, "run", lambda argv: None)
    with pytest.raises(install.InstallError, match="still running"):
        host.stop_account("commonplace-compute")


def test_a_failed_import_check_leaves_no_pth_and_runs_before_it_exists(root, runtime):
    pth = root / SITE_DIR / "commonplace-compute.pth"
    at_check = []

    class Broken(LiveStub):
        def run(self, argv):
            super().run(argv)
            if "target_helper" in " ".join(argv):
                at_check.append(pth.exists())
                raise install.InstallError("import failed")

    host = Broken()
    assert run(root, "install", runtime, *IDENTITY, "--controller-key", KEY, host=host) == 1
    assert at_check == [False]
    assert not pth.exists()
    manifest = json.loads((root / "etc/commonplace/compute-install-manifest.json").read_text())
    assert not any(item.get("path", "").endswith(".pth") for item in manifest["items"])


def test_uninstall_reports_a_kept_directory_as_a_partial_result(root, runtime, capsys):
    host = installed(root, runtime)
    (root / STRAY).write_text("keep me")
    assert run(root, "uninstall", runtime, host=host) == 1
    err = capsys.readouterr()
    assert "uninstalled" not in err.out and "operator-note" not in err.out
    assert "not empty" in err.err and "commonplace-compute" in err.err
    manifest = json.loads((root / "etc/commonplace/compute-install-manifest.json").read_text())
    assert "/usr/local/libexec/commonplace-compute" in {i.get("path") for i in manifest["items"]}
    (root / STRAY).unlink()  # the operator resolves it; the next uninstall finishes
    assert run(root, "uninstall", runtime, host=host) == 0
    assert "uninstalled" in capsys.readouterr().out
    assert not (root / "usr").exists() and not (root / "opt").exists()
