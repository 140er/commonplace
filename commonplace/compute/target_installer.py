"""One-shot, idempotent, reversible Compute target installer.

    python -I -m commonplace.compute.target_installer {plan,install,uninstall} ...

Run as root with an explicit trusted interpreter and -I (see docs/compute/RUNNING.md): plain
`python -m` puts the current directory on sys.path.

Provisions what target_helper.py and script_sandbox.py document as operator
prerequisites: a dedicated account, a private workspace with attempts/inputs and a
bounded tmpfs scratch, a root-owned read-only runtime tree, delegated cgroups, an
sshd Match block with ExposeAuthInfo, the exclusive controller key and the protected
ScriptTargetConfig. A real install must run once as root; that is the only
elevation. It reads no secrets (only the controller's public ed25519 key, passed in
and never fetched), contacts no server, scans no network and installs no packages
(bubblewrap must already be present; preflight refuses otherwise). It does place the
commonplace and mempalace python sources the helper imports under /opt/commonplace-compute/lib
with a root-owned .pth in the system python's dist-packages, and checks the import with only
the stdlib; uninstall stops the account's sessions and user manager before removing it.

Every artifact is recorded in a root-owned manifest before it is created, so
uninstall removes exactly what install added and never anything it did not create.
Paths that exist but are not in the manifest, and files changed after install, are
never overwritten or deleted. The ScriptTargetConfig is written last: until then the
helper has no configuration and refuses to run. Re-running install with the same
settings is a no-op; different settings refuse until uninstall.

--root PREFIX stages the whole tree under PREFIX with a recording host: no account,
mount, systemctl or sshd command runs and no ownership changes, so tests and dry
runs need no root. Uninstall keeps attempt evidence unless asked to discard it.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import fnmatch
import hashlib
import json
import os
import platform
import pwd
import re
import shutil
import stat
import struct
import subprocess
import sys
import time
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

from .protocol import canonical
from .script_sandbox import ScriptTargetConfig, runtime_inventory
from .target_helper import TARGET_CONFIG_PATH, TARGET_HELPER_COMMAND, _remove_owned_job

DEFAULT_ACCOUNT = "commonplace-compute"
WORKSPACE_ROOT = "/var/lib/commonplace-compute"
RUNTIME_SOURCE = "/opt/commonplace-compute/runtime-src"
RUNTIME_ROOT = "/opt/commonplace-compute/runtime"
ENTRY_PATH = "/usr/local/libexec/commonplace-compute/helper-entry"
AUTHORIZED_KEYS_PATH = "/etc/commonplace/compute-authorized-keys"
MANIFEST_PATH = "/etc/commonplace/compute-install-manifest.json"
SSHD_DROPIN_PATH = "/etc/ssh/sshd_config.d/60-commonplace-compute.conf"
SSHD_CONFIG_PATH = "/etc/ssh/sshd_config"
SSHD_RUNTIME_DIR = "/run/sshd"
LIB_ROOT = "/opt/commonplace-compute/lib"
SYSTEMD_DIR = "/etc/systemd/system"
SCOPE_UNIT = "commonplace-compute-helper"
SCRATCH_OPTIONS = "size=32M,nr_inodes=4096,nosuid,nodev"
STAGED_ID = 64998
_ED25519_PREFIX = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32)
_EXCLUDED_STDLIB = frozenset(
    {
        "test",
        "tests",
        "__pycache__",
        "idlelib",
        "tkinter",
        "turtledemo",
        "ensurepip",
        "lib2to3",
        "site-packages",
        "dist-packages",
        "lib-dynload",
    }
)
_SYSTEM_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"


class InstallError(Exception):
    """A refusal or failure with an operator-readable, secret-free message."""


def pin_approval_keys(keyset_path):
    """Seam for pinning the owner's approval key set; the format is card #431's."""
    raise NotImplementedError("awaiting #431 format")


def systemd_escape_path(path):
    """systemd-escape --path: '/' becomes '-', bytes outside [A-Za-z0-9:_.] become \\xNN."""
    text = str(path).strip("/")
    out = []
    for index, char in enumerate(text):
        if char == "/":
            out.append("-")
        elif char.isascii() and (char.isalnum() or char in ":_" or (char == "." and index)):
            out.append(char)
        else:
            out.extend("\\x%02x" % byte for byte in char.encode())
    return "".join(out)


SCRATCH_UNIT_NAME = systemd_escape_path(WORKSPACE_ROOT + "/scratch") + ".mount"
RUNTIME_UNIT_NAME = systemd_escape_path(RUNTIME_ROOT) + ".mount"


@dataclass(frozen=True)
class ControllerKey:
    line: str
    principal: str


def parse_controller_key(text):
    """One ssh-ed25519 public key line; the comment is dropped, options are refused."""
    lines = [line for line in str(text).splitlines() if line.strip()]
    if len(lines) != 1:
        raise InstallError("controller key must be a single line")
    parts = lines[0].split()
    message = "controller key must be one ssh-ed25519 public key"
    if len(parts) < 2 or parts[0] != "ssh-ed25519":
        raise InstallError(message)
    try:
        wire = base64.b64decode(parts[1], validate=True)
    except (ValueError, binascii.Error):
        raise InstallError(message) from None
    if len(wire) != len(_ED25519_PREFIX) + 32 or not wire.startswith(_ED25519_PREFIX):
        raise InstallError(message)
    return ControllerKey("ssh-ed25519 " + parts[1], hashlib.sha256(wire).hexdigest())


def read_controller_key_file(path):
    try:
        with open(path, "rb") as handle:
            raw = handle.read(4097)
    except OSError:
        raise InstallError("cannot read controller key file") from None
    if len(raw) > 4096:
        raise InstallError("controller key file is too large")
    return raw.decode("utf-8", "replace")


@dataclass(frozen=True)
class RuntimeSpec:
    """Host files that make up the self-contained runtime tree."""

    binary: Path
    libs: tuple  # (destination relative to the tree, host source path)
    stdlib: Path
    dynload: Path
    version: tuple  # (major, minor)


def parse_ldd(text, *, strict=False):
    paths = []
    for line in text.splitlines():
        line = line.strip()
        if "not found" in line:
            if strict:
                raise InstallError("a shared library was not found: " + line.split()[0])
            continue
        name, _, rest = line.partition("=>")
        candidate = (rest if _ else name).split()
        if candidate and candidate[0].startswith("/"):
            paths.append(candidate[0])
    return paths


def _host_run(argv, timeout=30):
    try:
        done = subprocess.run(
            argv,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            env={"PATH": _SYSTEM_PATH, "LANG": "C"},
        )
    except (OSError, subprocess.SubprocessError) as error:
        detail = getattr(error, "stderr", "") or ""
        raise InstallError(f"{argv[0]} failed: {detail.strip()[:200]}") from None
    return done.stdout


def discover_runtime(python="/usr/bin/python3"):
    """Find the interpreter, its shared libraries and stdlib on this host (read-only)."""
    binary = Path(os.path.realpath(python))
    paths = parse_ldd(_host_run(["ldd", str(binary)]), strict=True)
    libs = tuple((path.lstrip("/"), Path(os.path.realpath(path))) for path in paths)
    code = "import sys,sysconfig;print(*sys.version_info[:2]);print(sysconfig.get_path('stdlib'))"
    version, stdlib = _host_run([str(binary), "-I", "-c", code]).splitlines()[:2]
    major, minor = (int(part) for part in version.split())
    stdlib = Path(stdlib)
    return RuntimeSpec(binary, libs, stdlib, stdlib / "lib-dynload", (major, minor))


def _mkdir(path, mode=0o755):
    os.mkdir(path, 0o700)
    os.chmod(path, mode)


def _copy(source, destination, mode):
    with open(source, "rb") as src, open(destination, "xb") as dst:
        shutil.copyfileobj(src, dst)
    os.chmod(destination, mode)


def _zip_stdlib(stdlib, destination):
    """Deterministic: sorted names, fixed timestamps, no tests, bytecode or extensions."""
    with zipfile.ZipFile(destination, "x", zipfile.ZIP_DEFLATED) as archive:
        for directory, dirs, files in os.walk(stdlib):
            dirs[:] = sorted(
                d for d in dirs if d not in _EXCLUDED_STDLIB and not d.startswith("config-")
            )
            for name in sorted(files):
                if not name.endswith(".py"):
                    continue
                path = Path(directory, name)
                info = zipfile.ZipInfo(str(path.relative_to(stdlib)), (1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                archive.writestr(info, path.read_bytes())
    os.chmod(destination, 0o644)


def build_runtime_tree(destination, spec):
    """Regular files and directories only, no symlinks, nothing group/world writable."""
    destination = Path(destination)
    major, minor = spec.version
    _mkdir(destination)
    for name in ("usr/bin", "usr/lib", f"usr/lib/python{major}.{minor}"):
        parent = destination
        for part in name.split("/"):
            parent /= part
            if not parent.exists():
                _mkdir(parent)
    dynload = destination / f"usr/lib/python{major}.{minor}/lib-dynload"
    _mkdir(dynload)
    for name in ("job", "proc", "dev", "tmp"):
        _mkdir(destination / name)
    _copy(spec.binary, destination / "usr/bin/python3", 0o755)
    for relative, source in spec.libs:
        parent = destination
        for part in Path(relative).parts[:-1]:
            parent /= part
            if not parent.exists():
                _mkdir(parent)
        _copy(source, destination / relative, 0o755)
    if spec.dynload.is_dir():
        for entry in sorted(spec.dynload.iterdir()):
            if entry.is_file():
                _copy(entry, dynload / entry.name, 0o755)
    _zip_stdlib(spec.stdlib, destination / f"usr/lib/python{major}{minor}.zip")


def staged_inventory(root, *, ignore_bytecode=False):
    """runtime_inventory's digest for an unmounted tree (no root-owner/read-only checks).

    ignore_bytecode skips __pycache__ and *.pyc, which any later import by root may add to a
    tree of python sources; it is never used for the read-only runtime tree.
    """
    entries = []

    def visit(directory, prefix):
        for name in sorted(os.listdir(directory)):
            if ignore_bytecode and (name == "__pycache__" or name.endswith(".pyc")):
                continue
            path = Path(directory, name)
            info = os.lstat(path)
            if info.st_mode & 0o022:
                raise InstallError(f"runtime entry is group or world writable: {prefix}{name}")
            relative = prefix + name
            if stat.S_ISDIR(info.st_mode):
                entries.append((relative, stat.S_IMODE(info.st_mode), "directory"))
                visit(path, relative + "/")
            elif stat.S_ISREG(info.st_mode):
                raw = path.read_bytes()
                entries.append(
                    (relative, stat.S_IMODE(info.st_mode), hashlib.sha256(raw).hexdigest())
                )
            else:
                raise InstallError(f"runtime entry is not a regular file: {relative}")

    visit(Path(root), "")
    return hashlib.sha256(canonical(entries)).hexdigest()


def running_packages():
    """The commonplace and mempalace package directories this installer is running from."""
    import commonplace
    import mempalace

    return Path(commonplace.__file__).parent, Path(mempalace.__file__).parent


def copy_python_packages(destination, packages):
    """Copy only .py sources (no bytecode, data or symlinks); the helper needs no third party."""
    destination = Path(destination)
    _mkdir(destination)
    for package in map(Path, packages):
        for directory, dirs, files in os.walk(package):
            dirs[:] = sorted(
                d for d in dirs if d != "__pycache__" and not Path(directory, d).is_symlink()
            )
            for name in sorted(files):
                source = Path(directory, name)
                if not name.endswith(".py") or source.is_symlink():
                    continue
                target = destination / package.name / source.relative_to(package)
                parent = destination
                for part in target.parent.relative_to(destination).parts:
                    parent /= part
                    if not parent.exists():
                        _mkdir(parent)
                _copy(source, target, 0o644)


def choose_site_dir(lines):
    """The administrator-local dist-packages of the system python (never its own packages)."""
    local = [line for line in lines if line.startswith("/usr/local/")]
    for line in local:
        if line.endswith("dist-packages"):
            return line
    if local:
        return local[0]
    raise InstallError("no system site directory under /usr/local was found")


class Host:
    """Everything that touches the live operating system; the staged host only records."""

    live = False
    durable = False  # fsync every write; only a real host needs it

    def euid(self):
        return os.geteuid()

    def account_exists(self, name):
        raise NotImplementedError

    def ids(self, name):
        raise NotImplementedError

    def run(self, argv):
        raise NotImplementedError

    def chown(self, path, uid, gid):
        raise NotImplementedError

    def owner_of(self, path):
        raise NotImplementedError

    def mounted_inventory(self, mountpoint, source):
        raise NotImplementedError

    def sshd_service(self):
        return "ssh.service"

    def sshd_effective_config(self):
        raise NotImplementedError

    def python_site_dir(self):
        raise NotImplementedError

    def stop_account(self, name):
        raise NotImplementedError

    def preflight(self, options):
        return []


class StagedHost(Host):
    """For --root and tests: records the commands a live install would run, runs none."""

    def __init__(self, uid=STAGED_ID, gid=STAGED_ID):
        self.uid, self.gid = uid, gid
        self.commands = []
        self.linger = False

    def sshd_effective_config(self):
        return "permituserenvironment no\n"

    def python_site_dir(self):
        return "/usr/local/lib/python3.12/dist-packages"

    def stop_account(self, name):
        if self.linger:
            self.run(["loginctl", "disable-linger", name])
        self.run(["loginctl", "terminate-user", name])

    def account_exists(self, name):
        return False

    def ids(self, name):
        return self.uid, self.gid

    def run(self, argv):
        self.commands.append(tuple(argv))

    def chown(self, path, uid, gid):
        pass

    def owner_of(self, path):
        return self.uid, self.gid  # staged runs never change ownership

    def mounted_inventory(self, mountpoint, source):
        return staged_inventory(source)


def _has_processes(uid):
    for entry in os.scandir("/proc"):
        try:
            if entry.name.isdigit() and entry.stat().st_uid == uid:
                return True
        except OSError:
            continue  # the process ended while we looked
    return False


class LiveHost(Host):
    live = True
    durable = True

    def account_exists(self, name):
        try:
            pwd.getpwnam(name)
        except KeyError:
            return False
        return True

    def ids(self, name):
        entry = pwd.getpwnam(name)
        return entry.pw_uid, entry.pw_gid

    def run(self, argv):
        _host_run(list(argv), timeout=120)

    def chown(self, path, uid, gid):
        os.chown(path, uid, gid)

    def owner_of(self, path):
        info = os.stat(path, follow_symlinks=False)
        return info.st_uid, info.st_gid

    def mounted_inventory(self, mountpoint, source):
        return runtime_inventory(mountpoint)

    def sshd_effective_config(self):
        return _host_run(["sshd", "-T"], timeout=60)

    def python_site_dir(self):
        code = "import site;print('\\n'.join(site.getsitepackages()))"
        return choose_site_dir(_host_run(["/usr/bin/python3", "-I", "-c", code]).splitlines())

    def stop_account(self, name):
        """End the account's sessions and user manager so userdel does not find it in use."""
        try:
            uid = pwd.getpwnam(name).pw_uid
        except KeyError:
            return
        if Path("/var/lib/systemd/linger", name).exists():
            self.run(["loginctl", "disable-linger", name])
        try:
            self.run(["loginctl", "terminate-user", name])
        except InstallError:
            pass  # not logged in
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and _has_processes(uid):
            time.sleep(0.2)
        if _has_processes(uid):
            raise InstallError(f"processes of {name} are still running; nothing was removed")

    def sshd_service(self):
        for directory in ("/usr/lib/systemd/system", "/lib/systemd/system"):
            if Path(directory, "ssh.service").exists():
                return "ssh.service"
        return "sshd.service"

    def preflight(self, options):
        problems = []
        if platform.system() != "Linux":
            problems.append("Linux is required")
        try:
            info = os.stat("/usr/bin/bwrap", follow_symlinks=False)
            usable = stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o6022
        except OSError:
            usable = False
        if not usable:
            problems.append(
                "a root-owned, non-setuid /usr/bin/bwrap is required (install bubblewrap first)"
            )
        try:
            controllers = Path("/sys/fs/cgroup/cgroup.controllers").read_text().split()
        except OSError:
            controllers = []
        if not {"cpu", "memory", "pids"} <= set(controllers):
            problems.append("cgroup v2 with the cpu, memory and pids controllers is required")
        for tool in ("useradd", "userdel", "systemctl", "systemd-run", "sshd"):
            if not any(Path(d, tool).exists() for d in _SYSTEM_PATH.split(":")):
                problems.append(f"{tool} was not found")
        return problems


@dataclass(frozen=True)
class InstallOptions:
    root: Path = Path("/")
    workspace: str | None = None
    gateway: str | None = None
    node: str | None = None
    controller_key: ControllerKey | None = None
    account: str = DEFAULT_ACCOUNT
    python: str = "/usr/bin/python3"
    approval_keyset: str | None = None


@dataclass
class Op:
    kind: str
    target: str
    detail: str
    check: object
    apply: object


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def entry_script():
    return f"""#!/bin/sh
# Managed by commonplace.compute.target_installer; removed by its uninstall.
# Fixed sshd ForceCommand entry: no arguments and no caller input. It re-runs itself in
# a systemd scope with Delegate=yes, then gives the sandbox its delegated cgroup subtree
# (runner child holds this process, fixture child is the sandbox cgroup_root).
[ "$#" -eq 0 ] || exit 1
cgroup=$(sed -n 's/^0:://p' /proc/self/cgroup)
case "$cgroup" in
*/{SCOPE_UNIT}.scope)
    base=/sys/fs/cgroup$cgroup
    mkdir "$base/runner" || exit 1
    echo $$ > "$base/runner/cgroup.procs" || exit 1
    echo "+cpu +memory +pids" > "$base/cgroup.subtree_control" || exit 1
    mkdir -m 0700 "$base/fixture" || exit 1
    echo "+cpu +memory +pids" > "$base/fixture/cgroup.subtree_control" || exit 1
    exec {TARGET_HELPER_COMMAND}
    ;;
esac
exec /usr/bin/systemd-run --user --scope --quiet --collect --unit={SCOPE_UNIT} -p Delegate=yes -- "$0"
"""


# Keywords sshd_config(5) allows inside a Match block (OpenSSH 9.x). Global-only ones such as
# PermitUserEnvironment are rejected there ("not allowed within a Match block"); the installer
# checks those through `sshd -T` instead.
SSHD_MATCH_KEYWORDS = frozenset(
    """AcceptEnv AllowAgentForwarding AllowGroups AllowStreamLocalForwarding AllowTcpForwarding
    AllowUsers AuthenticationMethods AuthorizedKeysCommand AuthorizedKeysCommandUser
    AuthorizedKeysFile AuthorizedPrincipalsCommand AuthorizedPrincipalsCommandUser
    AuthorizedPrincipalsFile Banner CASignatureAlgorithms ChannelTimeout ChrootDirectory
    ClientAliveCountMax ClientAliveInterval DenyGroups DenyUsers DisableForwarding ExposeAuthInfo
    ForceCommand GatewayPorts GSSAPIAuthentication HostbasedAcceptedAlgorithms
    HostbasedAuthentication HostbasedUsesNameFromPacketOnly IgnoreRhosts Include IPQoS
    KbdInteractiveAuthentication KerberosAuthentication LogLevel MaxAuthTries MaxSessions
    PasswordAuthentication PermitEmptyPasswords PermitListen PermitOpen PermitRootLogin PermitTTY
    PermitTunnel PermitUserRC PubkeyAcceptedAlgorithms PubkeyAuthentication PubkeyAuthOptions
    RDomain RekeyLimit RevokedKeys SetEnv StreamLocalBindMask StreamLocalBindUnlink
    TrustedUserCAKeys UnusedConnectionTimeout X11DisplayOffset X11Forwarding X11UseLocalhost
    """.split()
)


def validate_match_block(text):
    inside = False
    for line in text.splitlines():
        words = line.split()
        if not words or words[0].startswith("#"):
            continue
        if words[0] == "Match":
            inside = words[1:] != ["all"]
        elif inside and words[0] not in SSHD_MATCH_KEYWORDS:
            raise InstallError(f"{words[0]} is not allowed within an sshd Match block")
    return text


def sshd_dropin(account):
    return validate_match_block(f"""# Managed by commonplace.compute.target_installer; removed by its uninstall.
Match User {account}
    AuthenticationMethods publickey
    PubkeyAuthentication yes
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    AuthorizedKeysFile {AUTHORIZED_KEYS_PATH}
    ExposeAuthInfo yes
    ForceCommand {ENTRY_PATH}
    PermitTTY no
    AllowTcpForwarding no
    AllowAgentForwarding no
    X11Forwarding no
    PermitTunnel no
    PermitUserRC no
Match all
""")


def _scratch_unit(uid, gid):
    return (
        "[Unit]\nDescription=Commonplace compute scratch (bounded tmpfs)\n\n"
        f"[Mount]\nWhat=tmpfs\nWhere={WORKSPACE_ROOT}/scratch\nType=tmpfs\n"
        f"Options=mode=0700,uid={uid},gid={gid},{SCRATCH_OPTIONS}\n\n"
        "[Install]\nWantedBy=multi-user.target\n"
    )


def _runtime_unit():
    return (
        "[Unit]\nDescription=Commonplace compute runtime (read-only bind)\n\n"
        f"[Mount]\nWhat={RUNTIME_SOURCE}\nWhere={RUNTIME_ROOT}\nType=none\nOptions=bind,ro\n\n"
        "[Install]\nWantedBy=multi-user.target\n"
    )


class Installer:
    def __init__(
        self, options, host=None, *, runtime_source=None, package_source=None, pin=None, out=print
    ):
        self.options = options
        self.host = StagedHost() if host is None else host
        self.runtime_source = runtime_source or (lambda: discover_runtime(options.python))
        self.package_source = package_source or running_packages
        self.pin = pin or pin_approval_keys
        self.out = out
        self.root = Path(options.root)
        self.manifest_path = self._phys(MANIFEST_PATH)
        self.items = []
        self._loaded = False
        self._site = None

    # -- paths and manifest -------------------------------------------------------
    def _phys(self, logical):
        if ".." in Path(logical).parts or not logical.startswith("/"):
            raise InstallError("unsafe path")
        return self.root / logical.lstrip("/")

    def _load(self):
        if self._loaded:
            return
        self._loaded = True
        try:
            raw = self.manifest_path.read_bytes()
        except FileNotFoundError:
            return
        except OSError:
            raise InstallError("cannot read the install manifest") from None
        try:
            document = json.loads(raw)
            if document["v"] != 1 or type(document["items"]) is not list:
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise InstallError(
                "the install manifest is not understood; refusing to touch anything"
            ) from None
        self.items = document["items"]

    def _flush(self):
        if not self.manifest_path.parent.is_dir():
            return  # the first directory item flushes once /etc/commonplace exists
        data = (json.dumps({"v": 1, "items": self.items}, indent=1, sort_keys=True) + "\n").encode()
        self._write(self.manifest_path, data, 0o600)

    def _item(self, kind, key):
        for item in self.items:
            if item["kind"] == kind and key in (item.get("path"), item.get("name")):
                return item
        return None

    def _write(self, path, data, mode):
        temporary = path.with_name(path.name + ".tmp-install")
        if temporary.is_symlink() or temporary.exists():
            temporary.unlink()
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
        try:
            os.fchmod(fd, mode)
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view) :]
            if self.host.durable:
                os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temporary, path)

    def _ids(self):
        return self.host.ids(self.options.account)

    # -- primitives: each returns whether it changed anything ----------------------
    def _dir_conflict(self, logical, shared):
        phys = self._phys(logical)
        if not shared and os.path.lexists(phys) and not self._item("dir", logical):
            return f"{logical} already exists and was not installed by this installer"
        return None

    def _dir(self, logical, mode, *, owner=False, shared=False):
        """Each directory is recorded before it is created and chowned; a recorded directory that
        is missing (an interrupted run) is simply created again. Only the directories that hold
        the manifest itself are recorded right after mkdir, because the manifest cannot be
        written before they exist."""
        changed, current = False, ""
        parts = [part for part in logical.split("/") if part]
        for index, part in enumerate(parts):
            current += "/" + part
            phys = self._phys(current)
            last = index == len(parts) - 1
            if phys.is_symlink():
                raise InstallError(f"{current} is a symlink")
            if phys.exists():
                if not phys.is_dir():
                    raise InstallError(f"{current} is not a directory")
                recorded = self._item("dir", current) is not None
                if last and recorded and stat.S_IMODE(phys.stat().st_mode) != mode:
                    os.chmod(phys, mode)
                    changed = True
            else:
                bootstrap = self.manifest_path.is_relative_to(phys)
                if self._item("dir", current) is None:
                    self.items.append({"kind": "dir", "path": current})
                if not bootstrap:
                    self._flush()
                _mkdir(phys, mode if last else 0o755)
                if bootstrap:
                    self._flush()
                changed = True
                recorded = True
            if last and owner and recorded and self.host.owner_of(phys) != self._ids():
                self.host.chown(phys, *self._ids())
                changed = True
        return changed

    def _file_conflict(self, logical, kind="file"):
        if not self._item(kind, logical) and os.path.lexists(self._phys(logical)):
            return f"{logical} already exists and was not installed by this installer"
        return None

    def _file(self, logical, data, mode, *, kind="file"):
        phys = self._phys(logical)
        sha = _sha256(data)
        item = self._item(kind, logical)
        changed = self._dir(str(Path(logical).parent), 0o755, shared=True)
        if item is None:
            if os.path.lexists(phys):
                raise InstallError(self._file_conflict(logical, kind))
            item = {"kind": kind, "path": logical, "sha256": sha, "mode": mode}
            self.items.append(item)
            self._flush()  # recorded before it exists: uninstall tolerates a missing file
        elif item["sha256"] != sha:
            raise InstallError(f"{logical} was installed with different settings; uninstall first")
        if not os.path.lexists(phys):
            self._write(phys, data, mode)
            return True, item
        if _sha256(phys.read_bytes()) != sha:
            raise InstallError(f"{logical} changed since install; uninstall first")
        if stat.S_IMODE(phys.stat().st_mode) != mode:
            os.chmod(phys, mode)
            changed = True
        return changed, item

    def _unit(self, name, text):
        changed, item = self._file(f"{SYSTEMD_DIR}/{name}", text.encode(), 0o644, kind="unit")
        item["name"] = name
        if not item.get("active"):
            self.host.run(["systemctl", "daemon-reload"])
            self.host.run(["systemctl", "enable", "--now", name])
            item["active"] = True
            self._flush()
            changed = True
        return changed

    # -- operations ---------------------------------------------------------------
    def _need(self):
        o = self.options
        for name in ("workspace", "gateway", "node"):
            if not getattr(o, name):
                raise InstallError(f"--{name} is required")
        if o.controller_key is None:
            raise InstallError(
                "a controller key is required (--controller-key or --controller-key-file)"
            )
        ScriptTargetConfig(
            enabled=True,
            workspace=o.workspace,
            gateway=o.gateway,
            node=o.node,
            key_principal=o.controller_key.principal,
            workspace_root=WORKSPACE_ROOT,
            scratch_root=WORKSPACE_ROOT + "/scratch",
            runtime_root=RUNTIME_ROOT,
            runtime_digest="0" * 64,
            cgroup_root=self._cgroup_root(0),
        )

    def _cgroup_root(self, uid):
        scope = f"{SCOPE_UNIT}.scope"
        return f"/sys/fs/cgroup/user.slice/user-{uid}.slice/user@{uid}.service/app.slice/{scope}/fixture"

    def _account_check(self):
        name = self.options.account
        if not self._item("account", name) and self.host.account_exists(name):
            return f"account {name} already exists and was not created by this installer"
        return None

    def _account(self):
        name = self.options.account
        item = self._item("account", name)
        if item is not None and item.get("created", True):
            return False
        if item is None:
            item = {"kind": "account", "name": name, "created": False}
            self.items.append(item)
            self._flush()  # recorded before useradd: a crash cannot leave an untracked account
        if not self.host.account_exists(name):
            self.host.run(
                [
                    "useradd",
                    "--system",
                    "--user-group",
                    "--home-dir",
                    WORKSPACE_ROOT,
                    "--no-create-home",
                    "--shell",
                    "/bin/sh",
                    "--comment",
                    "Commonplace compute target",
                    name,
                ]
            )
        item["created"] = True
        self._flush()
        return True

    def _tree_conflict(self, logical):
        if not self._item("tree", logical) and os.path.lexists(self._phys(logical)):
            return f"{logical} already exists and was not installed by this installer"
        return None

    def _tree(self, logical, build, *, ignore_bytecode=False):
        phys = self._phys(logical)
        item = self._item("tree", logical)
        if item and item.get("digest"):
            ignore = bool(item.get("ignore_bytecode"))
            if phys.is_dir() and staged_inventory(phys, ignore_bytecode=ignore) == item["digest"]:
                return False
            raise InstallError(f"{logical} changed since install; uninstall first")
        if item is None:
            item = {"kind": "tree", "path": logical, "digest": None}
            if ignore_bytecode:
                item["ignore_bytecode"] = True
            self.items.append(item)
            self._flush()
        elif phys.is_dir():
            shutil.rmtree(phys)  # an interrupted build we recorded ourselves
        build(phys)
        item["digest"] = staged_inventory(phys, ignore_bytecode=ignore_bytecode)
        self._flush()
        return True

    def _pth(self):
        logical = self._site_dir() + "/commonplace-compute.pth"
        recorded = self._item("file", logical)
        if recorded is not None and recorded.get("verified"):
            return self._file(logical, (LIB_ROOT + "\n").encode(), 0o644)[0]
        try:
            # The ForceCommand entry needs exactly this import to work with only the stdlib. Checked
            # before the .pth exists, so a failure never leaves a system-wide sys.path entry behind.
            # -B: as root this must not write bytecode into the placed tree.
            self.host.run(
                [
                    "/usr/bin/python3",
                    "-I",
                    "-B",
                    "-c",
                    f"import sys; sys.path.insert(0, {LIB_ROOT!r}); "
                    "import commonplace.compute.target_helper",
                ]
            )
        except InstallError:
            if recorded is not None:  # an interrupted earlier run left it behind
                phys = self._phys(logical)
                if os.path.lexists(phys) and _sha256(phys.read_bytes()) == recorded["sha256"]:
                    phys.unlink()
                self.items.remove(recorded)
                self._flush()
            raise
        item = self._file(logical, (LIB_ROOT + "\n").encode(), 0o644)[1]
        item["verified"] = True
        self._flush()
        return True

    def _site_dir(self):
        if self._site is None:
            self._site = self.host.python_site_dir()
        return self._site

    def _runtime_unit(self):
        changed = self._unit(RUNTIME_UNIT_NAME, _runtime_unit())
        if changed and self.host.live:
            digest = self._item("tree", RUNTIME_SOURCE)["digest"]
            mounted = self.host.mounted_inventory(
                self._phys(RUNTIME_ROOT), self._phys(RUNTIME_SOURCE)
            )
            if mounted != digest:
                raise InstallError("the mounted runtime does not match the tree that was built")
        return changed

    def _dropin(self):
        changed, item = self._file(
            SSHD_DROPIN_PATH, sshd_dropin(self.options.account).encode(), 0o644
        )
        if item.get("validated"):
            return changed
        try:
            self.host.run(["sshd", "-t"])
        except InstallError:
            self._phys(SSHD_DROPIN_PATH).unlink()
            self.items.remove(item)
            self._flush()
            raise
        self.host.run(["systemctl", "try-reload-or-restart", self.host.sshd_service()])
        item["validated"] = True
        self._flush()
        return True

    def _pin(self):
        keyset = self.options.approval_keyset
        if self._item("pin", keyset):
            return False
        self.pin(keyset)
        self.items.append({"kind": "pin", "path": keyset})
        self._flush()
        return True

    def _config_text(self):
        o = self.options
        digest = self._item("tree", RUNTIME_SOURCE)["digest"]
        config = ScriptTargetConfig(
            enabled=True,
            workspace=o.workspace,
            gateway=o.gateway,
            node=o.node,
            key_principal=o.controller_key.principal,
            workspace_root=WORKSPACE_ROOT,
            scratch_root=WORKSPACE_ROOT + "/scratch",
            runtime_root=RUNTIME_ROOT,
            runtime_digest=digest,
            cgroup_root=self._cgroup_root(self._ids()[0]),
        )
        return (json.dumps(asdict(config), indent=2, sort_keys=True) + "\n").encode()

    def _operations(self):
        o = self.options
        ops = [
            Op(
                "manifest",
                MANIFEST_PATH,
                "0600 root; records exactly what is added",
                None,
                lambda: self._dir("/etc/commonplace", 0o755, shared=True),
            ),
            Op(
                "dir",
                SSHD_RUNTIME_DIR,
                "0755 root; create and record if missing, before sshd validation",
                None,
                lambda: self._dir(SSHD_RUNTIME_DIR, 0o755, shared=True),
            ),
            Op(
                "sshd-effective-config",
                SSHD_CONFIG_PATH,
                "sshd -T; require global PermitUserEnvironment no before provisioning the account",
                None,
                self._check_sshd_effective_config,
            ),
        ]
        ops.append(
            Op(
                "account",
                o.account,
                f"system account, login shell /bin/sh, home {WORKSPACE_ROOT}, no password",
                self._account_check,
                self._account,
            )
        )
        for sub, mode in (
            ("", 0o700),
            ("/attempts", 0o700),
            ("/inputs", 0o700),
            ("/scratch", 0o700),
        ):
            path = WORKSPACE_ROOT + sub
            ops.append(
                Op(
                    "dir",
                    path,
                    f"0{mode:o} owner {o.account}",
                    lambda path=path: self._dir_conflict(path, False),
                    lambda path=path, mode=mode: self._dir(path, mode, owner=True),
                )
            )
        ops.append(
            Op(
                "dir",
                "/opt/commonplace-compute",
                "0755 root",
                lambda: self._dir_conflict("/opt/commonplace-compute", False),
                lambda: self._dir("/opt/commonplace-compute", 0o755),
            )
        )
        ops.append(
            Op(
                "dir",
                RUNTIME_ROOT,
                "0755 root (read-only bind mountpoint)",
                lambda: self._dir_conflict(RUNTIME_ROOT, False),
                lambda: self._dir(RUNTIME_ROOT, 0o755),
            )
        )
        ops.append(
            Op(
                "runtime-tree",
                RUNTIME_SOURCE,
                "root-owned copy of python3, its shared libraries, zipped stdlib and lib-dynload; "
                "digest = runtime_inventory",
                lambda: self._tree_conflict(RUNTIME_SOURCE),
                lambda: self._tree(
                    RUNTIME_SOURCE, lambda phys: build_runtime_tree(phys, self.runtime_source())
                ),
            )
        )
        ops.append(
            Op(
                "tmpfs-mount",
                f"{WORKSPACE_ROOT}/scratch",
                f"{SCRATCH_OPTIONS.replace(',', ' ')} via {SCRATCH_UNIT_NAME}",
                lambda: self._file_conflict(f"{SYSTEMD_DIR}/{SCRATCH_UNIT_NAME}", "unit"),
                lambda: self._unit(SCRATCH_UNIT_NAME, _scratch_unit(*self._ids())),
            )
        )
        ops.append(
            Op(
                "ro-bind-mount",
                RUNTIME_ROOT,
                f"{RUNTIME_SOURCE} read-only via {RUNTIME_UNIT_NAME}",
                lambda: self._file_conflict(f"{SYSTEMD_DIR}/{RUNTIME_UNIT_NAME}", "unit"),
                self._runtime_unit,
            )
        )
        ops.append(
            Op(
                "file",
                ENTRY_PATH,
                f"0755 root; fixed ForceCommand entry, runs the helper in a Delegate=yes scope "
                f"({TARGET_HELPER_COMMAND})",
                lambda: self._file_conflict(ENTRY_PATH),
                lambda: self._file(ENTRY_PATH, entry_script().encode(), 0o755)[0],
            )
        )
        ops.append(
            Op(
                "python-packages",
                LIB_ROOT,
                "root-owned copy of the commonplace and mempalace python sources the helper imports "
                "(stdlib only, no third party)",
                lambda: self._tree_conflict(LIB_ROOT),
                lambda: self._tree(
                    LIB_ROOT,
                    lambda phys: copy_python_packages(phys, self.package_source()),
                    ignore_bytecode=True,
                ),
            )
        )
        site = self._site_dir()
        ops.append(
            Op(
                "python-pth",
                f"{site}/commonplace-compute.pth",
                "0644 root; checks `/usr/bin/python3 -I -B -c import "
                f"commonplace.compute.target_helper` before adding {LIB_ROOT} for /usr/bin/python3",
                lambda: self._file_conflict(f"{site}/commonplace-compute.pth"),
                self._pth,
            )
        )
        principal = o.controller_key.principal if o.controller_key else "?"
        line = ("restrict " + o.controller_key.line + "\n").encode() if o.controller_key else b""
        ops.append(
            Op(
                "authorized-key",
                AUTHORIZED_KEYS_PATH,
                f"0644 root; restrict ssh-ed25519 key sha256:{principal}",
                lambda: self._file_conflict(AUTHORIZED_KEYS_PATH),
                lambda: self._file(AUTHORIZED_KEYS_PATH, line, 0o644)[0],
            )
        )
        ops.append(
            Op(
                "sshd-dropin",
                SSHD_DROPIN_PATH,
                f"0644 root; Match User {o.account}, ExposeAuthInfo yes, ForceCommand {ENTRY_PATH}; "
                "sshd -t then systemctl try-reload-or-restart (inactive service stays inactive)",
                lambda: self._file_conflict(SSHD_DROPIN_PATH),
                self._dropin,
            )
        )
        if o.approval_keyset:
            ops.append(
                Op(
                    "approval-keys",
                    o.approval_keyset,
                    "(seam: awaiting #431 format)",
                    None,
                    self._pin,
                )
            )
        ops.append(
            Op(
                "target-config",
                TARGET_CONFIG_PATH,
                "0644 root; ScriptTargetConfig written last, it is what activates the helper",
                lambda: self._file_conflict(TARGET_CONFIG_PATH),
                lambda: self._file(TARGET_CONFIG_PATH, self._config_text(), 0o644)[0],
            )
        )
        return ops

    def _sshd_effective_problems(self):
        """PermitUserEnvironment is global-only (sshd rejects it in Match), so read its value."""
        value = None
        for line in self.host.sshd_effective_config().splitlines():
            words = line.split()
            if len(words) >= 2 and words[0].lower() == "permituserenvironment":
                value = words[1].lower()
        if value != "no":
            return [
                "sshd's global PermitUserEnvironment must be no (it cannot be set per Match "
                f"block); effective value: {value or 'unknown'}"
            ]
        return []

    def _check_sshd_effective_config(self):
        problems = self._sshd_effective_problems()
        if problems:
            raise InstallError("\n".join(problems))
        return False

    # -- sshd preflight (file reads only) -----------------------------------------
    def _sshd_problems(self):
        main = self._phys(SSHD_CONFIG_PATH)
        if not main.is_file():
            return ["sshd is not installed (no /etc/ssh/sshd_config)"] if self.host.live else []
        problems = []
        text = main.read_text(errors="replace")
        if not re.search(r"^\s*Include\s+\S*sshd_config\.d/", text, re.M):
            problems.append("sshd_config does not Include /etc/ssh/sshd_config.d/*.conf")
        dropins = self._phys(SSHD_DROPIN_PATH).parent
        sources = [main] + (sorted(dropins.glob("*.conf")) if dropins.is_dir() else [])
        for source in sources:
            if source == self._phys(SSHD_DROPIN_PATH):
                continue
            for raw in source.read_text(errors="replace").splitlines():
                words = raw.split("#", 1)[0].split()
                if (
                    words[:1]
                    and words[0].lower() == "acceptenv"
                    and any(
                        fnmatch.fnmatchcase("SSH_USER_AUTH", pattern)
                        for pattern in words[1:]
                        if not pattern.startswith("-")
                    )
                ):
                    problems.append(
                        f"sshd accepts a client SSH_USER_AUTH (AcceptEnv in {source.name}); remove it"
                    )
        return problems

    # -- public commands ----------------------------------------------------------
    def plan(self):
        self._need()
        self.out(f"root: {self.root}")
        self.out(
            "plan runs one read-only python probe to find the site directory and changes nothing on disk"
        )
        self.out(
            "preflight (install only, read-only): euid 0, Linux, root-owned non-setuid /usr/bin/bwrap,"
        )
        self.out(
            "  cgroup v2 cpu/memory/pids, useradd/userdel/systemctl/systemd-run/sshd, sshd Include and"
        )
        self.out("  no AcceptEnv SSH_USER_AUTH,")
        self.out("  no pre-existing target path outside the manifest")
        for number, op in enumerate(self._operations(), 1):
            self.out(f"{number:2}. {op.kind} {op.target} {op.detail}")
        self.out(
            "missing parent directories are created and recorded; nothing is fetched or scanned"
        )
        return 0

    def install(self):
        self._need()
        if self.host.live and self.host.euid() != 0:
            raise InstallError("a real install must run as root (single elevation, e.g. sudo)")
        self._load()
        ops = self._operations()
        problems = self.host.preflight(self.options) + self._sshd_problems()
        problems += [message for op in ops if op.check and (message := op.check())]
        if problems:
            raise InstallError("\n".join(problems))
        changes = 0
        for op in ops:
            if op.apply():
                changes += 1
                self.out(f"done: {op.kind} {op.target}")
        if not changes:
            self.out("already installed; nothing to do")
        self.out(f"COMMONPLACE_COMPUTE_SANDBOX_FIXTURE={self._phys(TARGET_CONFIG_PATH)}")
        return 0

    def _workspace_data(self):
        found = []
        for sub in ("attempts", "inputs", "scratch"):
            directory = self._phys(f"{WORKSPACE_ROOT}/{sub}")
            if directory.is_dir() and any(directory.iterdir()):
                found.append(sub)
        return found

    def _discard(self, sub):
        directory = self._phys(f"{WORKSPACE_ROOT}/{sub}")
        if not directory.is_dir():
            return
        for entry in sorted(directory.iterdir()):
            if entry.is_dir() and not entry.is_symlink():
                fd = os.open(entry, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    _remove_owned_job(fd)
                finally:
                    os.close(fd)
                os.rmdir(entry)
            else:
                entry.unlink()

    def _remove(self, item, deferred, reloads):
        kind, path = item["kind"], item.get("path")
        if kind == "dir":
            phys = self._phys(path)
            if self.manifest_path.is_relative_to(phys):
                deferred.append(item)  # still holds the manifest; removed last
            elif phys.is_dir() and not any(phys.iterdir()):
                phys.rmdir()
            elif phys.exists():
                raise InstallError(f"{path} is not empty; kept and still recorded in the manifest")
        elif kind == "tree":
            phys = self._phys(path)
            if phys.is_dir():
                ignore = bool(item.get("ignore_bytecode"))
                if (
                    item.get("digest")
                    and staged_inventory(phys, ignore_bytecode=ignore) != item["digest"]
                ):
                    raise InstallError(f"{path} changed since install; left in place")
                shutil.rmtree(phys)  # includes any __pycache__ later imports added
        elif kind in ("file", "unit"):
            phys = self._phys(path)
            if kind == "unit" and item.get("active"):
                self.host.run(["systemctl", "disable", "--now", item["name"]])
                reloads.add("units")
            if os.path.lexists(phys):
                if _sha256(phys.read_bytes()) != item["sha256"]:
                    raise InstallError(f"{path} changed since install; left in place")
                phys.unlink()
            if path == SSHD_DROPIN_PATH:
                reloads.add("sshd")
        elif kind == "account":
            if item.get("created", True) or self.host.account_exists(item["name"]):
                self.host.run(["userdel", item["name"]])
        elif kind == "pin":
            self.out(f"note: the approval key pin for {path} is not reverted (format is #431's)")

    def uninstall(self, discard_workspace_data=False):
        self._load()
        if self.host.live and self.host.euid() != 0:
            raise InstallError("a real uninstall must run as root")
        if not self.items:
            self.out("not installed; nothing to do")
            return 0
        data = self._workspace_data()
        if data and not discard_workspace_data:
            raise InstallError(
                "workspace holds attempt evidence or inputs ("
                + ", ".join(data)
                + "); preserve it, "
                "confirm termination, then re-run with --discard-workspace-data"
            )
        for item in self.items:
            if item["kind"] == "account":
                # Sessions and the account's systemd user manager hold it in use. Nothing is
                # removed, workspace data included, until its processes are gone.
                self.host.stop_account(item["name"])
        for sub in data:
            self._discard(sub)
        left, deferred, errors = [], [], []
        reloads = set()
        # Keep sshd's runtime directory through the refresh: an active unit's
        # ExecReload may itself run sshd -t. Only then remove directories we created.
        runtime_dirs = []
        for item in reversed(self.items):
            if item["kind"] == "dir" and item["path"] in (SSHD_RUNTIME_DIR, "/run"):
                runtime_dirs.append(item)
                continue
            try:
                self._remove(item, deferred, reloads)
            except (InstallError, OSError) as error:
                errors.append(str(error))
                left.append(item)
        if "units" in reloads:
            self.host.run(["systemctl", "daemon-reload"])
        if "sshd" in reloads:
            self.host.run(["systemctl", "try-reload-or-restart", self.host.sshd_service()])
        for item in runtime_dirs:
            try:
                self._remove(item, deferred, reloads)
            except (InstallError, OSError) as error:
                errors.append(str(error))
                left.append(item)
        if errors:
            keep = {id(item) for item in left + deferred}
            self.items = [item for item in self.items if id(item) in keep]
            self._flush()
            raise InstallError("\n".join(errors))
        if self.manifest_path.exists():
            self.manifest_path.unlink()
        for item in deferred:
            phys = self._phys(item["path"])
            if phys.is_dir() and not any(phys.iterdir()):
                phys.rmdir()
        self.items = []
        self.out("uninstalled")
        return 0


def _parser():
    parser = argparse.ArgumentParser(
        prog="python -m commonplace.compute.target_installer",
        description="Provision, inspect or remove a Compute target (run install once, as root).",
    )
    parser.add_argument("command", choices=("plan", "install", "uninstall"))
    parser.add_argument("--root", default="/", help="stage under this prefix instead of the host")
    parser.add_argument("--workspace")
    parser.add_argument("--gateway")
    parser.add_argument("--node")
    key = parser.add_mutually_exclusive_group()
    key.add_argument("--controller-key", help="the controller's ssh-ed25519 public key line")
    key.add_argument("--controller-key-file", help="file holding that public key")
    parser.add_argument("--account", default=DEFAULT_ACCOUNT)
    parser.add_argument("--python", default="/usr/bin/python3")
    parser.add_argument(
        "--approval-keyset", help="path handed to pin_approval_keys (awaiting #431)"
    )
    parser.add_argument("--discard-workspace-data", action="store_true")
    return parser


def main(argv=None, *, host=None, runtime_source=None, package_source=None, pin=None):
    args = _parser().parse_args(argv)
    try:
        root = Path(args.root)
        if not root.is_dir():
            raise InstallError("--root must be an existing directory")
        root = Path(os.path.realpath(root))
        key = None
        if args.controller_key is not None:
            key = parse_controller_key(args.controller_key)
        elif args.controller_key_file is not None:
            key = parse_controller_key(read_controller_key_file(args.controller_key_file))
        options = InstallOptions(
            root=root,
            workspace=args.workspace,
            gateway=args.gateway,
            node=args.node,
            controller_key=key,
            account=args.account,
            python=args.python,
            approval_keyset=args.approval_keyset,
        )
        if host is None:
            host = LiveHost() if root == Path("/") else StagedHost()
        installer = Installer(
            options, host, runtime_source=runtime_source, package_source=package_source, pin=pin
        )
        if args.command == "plan":
            return installer.plan()
        if args.command == "install":
            return installer.install()
        return installer.uninstall(args.discard_workspace_data)
    except (InstallError, NotImplementedError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
