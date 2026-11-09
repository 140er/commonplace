"""Concrete default-off Linux confinement for the separately installed helper.

Operator prerequisites (none are created here): a dedicated non-root service UID,
non-setuid /usr/bin/bwrap with userns support, an immutable read-only
minimal runtime filesystem, a delegated cgroup-v2 subtree with cpu/memory/pids
already enabled, and a private bounded tmpfs scratch mount. No host root, home,
environment, agent socket or network is exposed. Runtime inventory hashes bind
the exact provisioned regular-file tree into the approval. Unsupported hosts
fail closed; there is no shell/unsandboxed fallback.

BubblewrapSandbox(config).check(manifest) does not launch processes or create
files. run(request, script_path, job_path, cancelled=...) uses real cgroup limits,
no-new-privileges, namespaces and process-group/cgroup cleanup. Only the job
directory is a writable host bind; /dev,/proc,/tmp are private namespaces/tmpfs.
The caller must durably consume the attempt before run. Raw result bytes are
private transport data, never safe logs or public output.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import platform
import selectors
import signal
import stat
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .protocol import canonical, identifier
from .scripts import ScriptDenied, digest

_STARTED = b"COMPUTE_SCRIPT_STARTED_V1\n"
_BOOTSTRAP = (
    "import os,runpy,sys;os.write(1,b'COMPUTE_SCRIPT_STARTED_V1\\n');"
    "sys.argv=sys.argv[1:];runpy.run_path(sys.argv[0],run_name='__main__')"
)
_TMPFS_MAGIC = 0x01021994
_CGROUP2_MAGIC = 0x63677270


def private_directory(path, *, uid=None, private=True):
    """Open every component with openat/O_NOFOLLOW; caller owns returned fd."""
    uid = os.geteuid() if uid is None else uid
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise ScriptDenied("unsafe_workspace")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            if info.st_uid not in {0, uid} or (
                info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX
            ):
                raise ScriptDenied("unsafe_workspace")
        info = os.fstat(fd)
        if info.st_uid != uid or (private and stat.S_IMODE(info.st_mode) != 0o700):
            raise ScriptDenied("unsafe_workspace")
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_regular_at(directory, name, maximum, *, uid=None):
    """Anchored no-follow read, rejecting hardlinks and concurrent modification."""
    fd = os.dup(directory)
    file_fd = None
    try:
        parts = name.split("/")
        if any(x in {"", ".", ".."} for x in parts):
            raise ScriptDenied("invalid_path")
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        before = os.fstat(file_fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size > maximum
            or (uid is not None and before.st_uid != uid)
        ):
            raise ScriptDenied("unsafe_file")
        chunks = bytearray()
        while len(chunks) <= maximum:
            part = os.read(file_fd, min(65536, maximum + 1 - len(chunks)))
            if not part:
                break
            chunks.extend(part)
        after = os.fstat(file_fd)
        if len(chunks) > maximum or (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ScriptDenied("file_changed")
        return bytes(chunks)
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(fd)


def atomic_file(directory, name, data):
    """Private fsync+renameat2(RENAME_NOREPLACE); never overwrite any old data."""
    if type(name) is not str or name in {"", ".", ".."} or "/" in name or "\x00" in name:
        raise ScriptDenied("invalid_path")
    temporary = name + ".upload"
    fd = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=directory,
    )
    try:
        os.fchmod(fd, 0o600)
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view) :]
        os.fsync(fd)
    finally:
        os.close(fd)
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    if (
        rename is None
        or rename(
            ctypes.c_int(directory),
            ctypes.c_char_p(os.fsencode(temporary)),
            ctypes.c_int(directory),
            ctypes.c_char_p(os.fsencode(name)),
            ctypes.c_uint(1),
        )
        != 0
    ):
        raise ScriptDenied("atomic_upload_denied")
    os.fsync(directory)


def _filesystem_type(fd):
    # Linux statfs begins with signed long f_type. Reserve more than the ABI struct.
    data = ctypes.create_string_buffer(512)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.fstatfs(ctypes.c_int(fd), ctypes.byref(data)) != 0:
        raise ScriptDenied("sandbox_unavailable")
    return ctypes.c_long.from_buffer(data).value


def runtime_inventory(root):
    """Hash only an operator-provisioned read-only, root-owned runtime tree.

    No symlinks/devices, at most4096 entries/256MiB; directory/file modes are part
    of the identity. The operator computes this same digest when provisioning.
    Runtime trees must be self-contained; this function never follows mounts or
    symlinks into the host filesystem.
    """
    root = Path(root)
    fd = private_directory(root, uid=0, private=False)
    entries, total = [], 0
    try:
        if not os.fstatvfs(fd).f_flag & os.ST_RDONLY:
            raise ScriptDenied("sandbox_unavailable")
        device = os.fstat(fd).st_dev

        def visit(directory, prefix):
            nonlocal total
            names = []
            with os.scandir(directory) as children:
                for child in children:
                    names.append(child.name)
                    if len(names) + len(entries) > 4096:
                        raise ScriptDenied("sandbox_unavailable")
            for name in sorted(names):
                if len(entries) >= 4096:
                    raise ScriptDenied("sandbox_unavailable")
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                path = prefix + name
                if info.st_uid != 0 or info.st_mode & 0o022 or info.st_dev != device:
                    raise ScriptDenied("sandbox_unavailable")
                if stat.S_ISDIR(info.st_mode):
                    entries.append((path, stat.S_IMODE(info.st_mode), "directory"))
                    child = os.open(
                        name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
                    )
                    try:
                        if not os.fstatvfs(child).f_flag & os.ST_RDONLY:
                            raise ScriptDenied("sandbox_unavailable")
                        visit(child, path + "/")
                    finally:
                        os.close(child)
                elif stat.S_ISREG(info.st_mode):
                    total += info.st_size
                    if total > 268435456:
                        raise ScriptDenied("sandbox_unavailable")
                    raw = read_regular_at(directory, name, 33554432, uid=0)
                    entries.append(
                        (path, stat.S_IMODE(info.st_mode), hashlib.sha256(raw).hexdigest())
                    )
                else:
                    raise ScriptDenied("sandbox_unavailable")

        visit(fd, "")
    finally:
        os.close(fd)
    return hashlib.sha256(canonical(entries)).hexdigest()


@dataclass(frozen=True)
class ScriptTargetConfig:
    enabled: bool = False
    workspace: str = "disabled"
    gateway: str = "disabled"
    node: str = "disabled"
    key_principal: str = "disabled"
    workspace_root: str = ""
    scratch_root: str = ""
    runtime_root: str = ""
    runtime_digest: str = "0" * 64
    cgroup_root: str = ""
    controller_contract: str = "controller-ssh-permit-v1"

    def __post_init__(self):
        if type(self.enabled) is not bool:
            raise ScriptDenied("invalid_configuration")
        for name in ("workspace", "gateway", "node", "key_principal"):
            identifier(getattr(self, name))
        digest(self.runtime_digest)
        if self.controller_contract != "controller-ssh-permit-v1":
            raise ScriptDenied("invalid_configuration")
        if self.enabled:
            for name in ("workspace_root", "scratch_root", "runtime_root", "cgroup_root"):
                value = getattr(self, name)
                if type(value) is not str or not value.startswith("/") or ".." in Path(value).parts:
                    raise ScriptDenied("invalid_configuration")
            workspace = Path(self.workspace_root)
            if Path(self.scratch_root) != workspace / "scratch":
                raise ScriptDenied("invalid_configuration")
            if not Path(self.cgroup_root).is_relative_to("/sys/fs/cgroup"):
                raise ScriptDenied("invalid_configuration")
            runtime = Path(self.runtime_root)
            if runtime in (Path("/"), Path("/usr"), Path("/home"), Path("/etc")) or (
                runtime.is_relative_to(workspace) or workspace.is_relative_to(runtime)
            ):
                raise ScriptDenied("invalid_configuration")


@dataclass(frozen=True, repr=False)
class ScriptResult:
    state: str
    code: str
    exit_code: int | None = None
    cancel_requested: bool = False
    termination_confirmed: bool = False
    stdout: bytes = field(default=b"", repr=False)
    stderr: bytes = field(default=b"", repr=False)
    artifacts: tuple[tuple[str, bytes], ...] = field(default=(), repr=False)

    def __repr__(self):
        return "<ScriptResult redacted>"


class _Cgroup:
    def __init__(self, config, request):
        self.parent = private_directory(config.cgroup_root)
        self.name = "compute-" + hashlib.sha256(request.attempt.encode()).hexdigest()
        self.fd = None
        try:
            os.mkdir(self.name, 0o700, dir_fd=self.parent)
            self.fd = os.open(
                self.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.parent
            )
            limits = request.manifest.limits
            for name, value in (
                ("pids.max", limits.processes),
                ("memory.max", limits.memory_bytes),
                ("memory.swap.max", 0),
                ("memory.oom.group", 1),
                ("cpu.max", "100000 100000"),
                ("cgroup.max.descendants", 0),
            ):
                self.write(name, str(value))
                if self.read(name).strip() != str(value):
                    raise ScriptDenied("sandbox_unavailable")
            self.procs = os.open("cgroup.procs", os.O_WRONLY | os.O_NOFOLLOW, dir_fd=self.fd)
        except BaseException:
            if self.fd is not None:
                os.close(self.fd)
            os.close(self.parent)
            raise

    def write(self, name, value):
        fd = os.open(name, os.O_WRONLY | os.O_NOFOLLOW, dir_fd=self.fd)
        try:
            os.write(fd, value.encode("ascii"))
        finally:
            os.close(fd)

    def read(self, name):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.fd)
        try:
            return os.read(fd, 4096).decode("ascii")
        finally:
            os.close(fd)

    def cpu_usec(self):
        values = dict(line.split() for line in self.read("cpu.stat").splitlines())
        return int(values["usage_usec"])

    def empty(self):
        return "populated 0" in self.read("cgroup.events").splitlines()

    def close(self):
        os.close(self.procs)
        os.close(self.fd)
        try:
            if self.empty_after_close:
                os.rmdir(self.name, dir_fd=self.parent)
        finally:
            os.close(self.parent)


class BubblewrapSandbox:
    def __init__(self, config=None):
        self.config = ScriptTargetConfig() if config is None else config

    def check(self, manifest):
        """Mandatory verified resource prerequisites; no subprocess/file writes."""
        if not self.config.enabled or platform.system() != "Linux" or os.geteuid() == 0:
            raise ScriptDenied("sandbox_unavailable")
        try:
            self._check_host(manifest)
        except (OSError, ValueError, KeyError, AttributeError):
            raise ScriptDenied("sandbox_unavailable") from None

    def _check_host(self, manifest):
        if manifest.runtime_digest != self.config.runtime_digest:
            raise ScriptDenied("runtime_digest_mismatch")
        if int(Path("/proc/sys/user/max_user_namespaces").read_text()) <= 0:
            raise ScriptDenied("sandbox_unavailable")
        for name, denied in (
            ("/proc/sys/kernel/unprivileged_userns_clone", "0"),
            ("/proc/sys/kernel/apparmor_restrict_unprivileged_userns", "1"),
        ):
            path = Path(name)
            if path.exists() and path.read_text().strip() == denied:
                raise ScriptDenied("sandbox_unavailable")
        status = dict(
            line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines()
        )
        if int(status["CapEff"].strip(), 16) != 0:
            raise ScriptDenied("sandbox_unavailable")
        info = os.stat("/usr/bin/bwrap", follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o6022:
            raise ScriptDenied("sandbox_unavailable")
        for root in (self.config.workspace_root, self.config.scratch_root, self.config.cgroup_root):
            fd = private_directory(root)
            try:
                if root == self.config.scratch_root:
                    usage = os.fstatvfs(fd)
                    if (
                        _filesystem_type(fd) != _TMPFS_MAGIC
                        or usage.f_blocks * usage.f_frsize > 67108864
                        or usage.f_files > 4096
                        or usage.f_files <= 0
                    ):
                        raise ScriptDenied("sandbox_unavailable")
                if root == self.config.cgroup_root:
                    if _filesystem_type(fd) != _CGROUP2_MAGIC:
                        raise ScriptDenied("sandbox_unavailable")
                    for name in ("cgroup.controllers", "cgroup.subtree_control"):
                        text = read_regular_at(fd, name, 1024).decode("ascii")
                        if not {"cpu", "memory", "pids"} <= set(text.split()):
                            raise ScriptDenied("sandbox_unavailable")
                    # cgroup.kill is required, not an optimistic killpg substitute.
                    kill_fd = os.open("cgroup.kill", os.O_WRONLY | os.O_NOFOLLOW, dir_fd=fd)
                    os.close(kill_fd)
            finally:
                os.close(fd)
        if runtime_inventory(self.config.runtime_root) != self.config.runtime_digest:
            raise ScriptDenied("runtime_digest_mismatch")
        for directory in ("job", "proc", "dev", "tmp"):
            path = Path(self.config.runtime_root) / directory
            if not path.is_dir() or any(path.iterdir()):
                raise ScriptDenied("sandbox_unavailable")
        binary = Path(self.config.runtime_root) / "usr/bin/python3"
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise ScriptDenied("sandbox_unavailable")

    def argv(self, request, script_path, job_path):
        args = [
            "/usr/bin/bwrap",
            "--unshare-all",
            "--die-with-parent",
            "--new-session",
            "--disable-userns",
            "--assert-userns-disabled",
            "--cap-drop",
            "ALL",
            "--ro-bind",
            self.config.runtime_root,
            "/",
            "--bind",
            str(job_path),
            "/job",
            "--ro-bind",
            str(script_path),
            "/job/script.py",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--size",
            "8388608",
            "--tmpfs",
            "/tmp",
            "--clearenv",
        ]
        for item in request.manifest.input_paths:
            args += ["--ro-bind", str(Path(job_path) / item.path), "/job/" + item.path]
        args += [
            "--setenv",
            "PATH",
            "/usr/bin",
            "--setenv",
            "LANG",
            "C.UTF-8",
            "--setenv",
            "HOME",
            "/nonexistent",
            "--setenv",
            "TMPDIR",
            "/tmp",
            "--chdir",
            "/job",
            "--",
            "/usr/bin/python3",
            "-I",
            "-B",
            "-u",
            "-c",
            _BOOTSTRAP,
            "/job/script.py",
            *request.manifest.arguments,
        ]
        return args

    def run(self, request, script_path, job_path, *, cancelled=lambda: False):
        self.check(request.manifest)
        if time.time() >= request.expires_at:
            return ScriptResult("denied", "admission_expired", termination_confirmed=True)
        if cancelled():
            return ScriptResult(
                "cancelled",
                "cancelled_before_start",
                cancel_requested=True,
                termination_confirmed=True,
            )
        group, process = None, None
        try:
            group = _Cgroup(self.config, request)
            process = self._spawn(request, script_path, job_path, group)
            return self._collect(request, process, group, cancelled)
        except (OSError, ValueError, subprocess.SubprocessError):
            return ScriptResult("uncertain" if process else "denied", "sandbox_unavailable")
        finally:
            if group is not None:
                try:
                    group.write("cgroup.kill", "1")
                    if process is not None:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.wait(timeout=1)
                    group.empty_after_close = group.empty()
                    group.close()
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    # The durable target claim remains reserved on any ambiguity.
                    pass
            if process is not None:
                process.stdout.close()
                process.stderr.close()

    def _spawn(self, request, script_path, job_path, group):
        limits = request.manifest.limits

        def constrain():
            import resource

            os.write(group.procs, b"0")
            os.close(group.procs)
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_seconds, limits.cpu_seconds))
            resource.setrlimit(resource.RLIMIT_FSIZE, (limits.file_bytes, limits.file_bytes))
            resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
            # pids.max, not RLIMIT_NPROC, enforces the entire descendant count.
            libc = ctypes.CDLL(None, use_errno=True)
            if libc.prctl(38, 1, 0, 0, 0) != 0:
                os._exit(126)

        return subprocess.Popen(
            self.argv(request, script_path, job_path),
            shell=False,
            env={},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            pass_fds=(group.procs,),
            start_new_session=True,
            preexec_fn=constrain,
        )

    def _collect(self, request, process, group, cancelled):
        limits = request.manifest.limits
        output = {"stdout": bytearray(), "stderr": bytearray()}
        deadline = time.monotonic() + limits.wall_seconds
        reason, cancel, killed_at = "exited", False, None
        with selectors.DefaultSelector() as selector:
            for name in output:
                stream = getattr(process, name)
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, name)
            while selector.get_map() or process.poll() is None:
                now = time.monotonic()
                cancel = cancel or bool(cancelled())
                if killed_at is None:
                    if cancel:
                        reason = "cancel_requested"
                    elif now >= deadline or group.cpu_usec() >= limits.cpu_seconds * 1000000:
                        reason = "runtime_limit"
                    if reason != "exited":
                        group.write("cgroup.kill", "1")
                        killed_at = now
                if killed_at is not None and now - killed_at > 1:
                    return ScriptResult(
                        "uncertain", "termination_unconfirmed", cancel_requested=cancel
                    )
                for key, _ in selector.select(0.05):
                    data = os.read(key.fileobj.fileno(), 4096)
                    if not data:
                        selector.unregister(key.fileobj)
                        continue
                    available = limits.output_bytes + len(_STARTED) - sum(map(len, output.values()))
                    output[key.data].extend(data[: max(0, available)])
                    if len(data) > available and killed_at is None:
                        reason = "output_limit"
                        group.write("cgroup.kill", "1")
                        killed_at = time.monotonic()
                # A parent exit does not imply descendants have stopped.
                if process.poll() is not None and killed_at is None:
                    group.write("cgroup.kill", "1")
                    killed_at = time.monotonic()
            stdout = bytes(output["stdout"])
            started = stdout.startswith(_STARTED)
            if not started:
                return ScriptResult(
                    "denied",
                    "sandbox_unavailable",
                    cancel_requested=cancel,
                    termination_confirmed=group.empty(),
                )
            stdout = stdout[len(_STARTED) :]
            stderr = bytes(output["stderr"])
            if len(stdout) + len(stderr) > limits.output_bytes:
                stdout, stderr = b"", b""
                reason = "output_limit"
            return ScriptResult(
                "observed_exit" if group.empty() else "uncertain",
                reason,
                exit_code=process.returncode,
                cancel_requested=cancel,
                termination_confirmed=group.empty(),
                stdout=stdout,
                stderr=stderr,
            )
