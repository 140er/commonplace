"""The subscription CLI owns its device exchange; no provider credential relay."""

import fcntl
import os
import re
import selectors
import signal
import subprocess
import sys
import threading
import time

from .install_files import digest
from .install_host import InstallError
from .runtime_install import HOME

URL = "https://auth.openai.com/codex/device"


class DeviceLogin:
    """One bounded, noninteractive process per approved login; no inherited secrets."""

    def __init__(self):
        self.timer = None
        self.process = None
        self.fd = None
        self.selector = None
        self.request = None
        self.deadline = 0
        self.buffer = b""
        self.presented = False

    def stop(self):
        if self.timer:
            self.timer.cancel()
            self.timer = None
        if self.process is not None:
            if self.process.poll() is None:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            self.process.wait(timeout=5)
            self.process.stdout.close()
        if self.selector:
            self.selector.close()
        if self.fd is not None:
            os.close(self.fd)
        self.process = self.fd = self.selector = self.request = None
        self.buffer = b""
        self.presented = False

    def advance(self, setup, row, scope):
        if self.request != row["id"]:
            self.stop()
            self._start(setup, row, scope)
        if time.monotonic() >= self.deadline:
            self.stop()
            raise InstallError("device sign-in expired")
        # Nonblocking reads: the setup loop continues to check current authority.
        while self.selector.select(timeout=0):
            chunk = os.read(self.process.stdout.fileno(), 4096)
            if not chunk:
                self.selector.unregister(self.process.stdout)
                break
            self.buffer += chunk
            if len(self.buffer) > 16384:
                self.stop()
                raise InstallError("unsupported device sign-in output")
        text = re.sub(rb"\x1b\[[0-9;]*m", b"", self.buffer).decode("utf-8", errors="replace")
        if not self.presented:
            codes = re.findall(r"(?m)^\s*([A-Z0-9]{4}-[A-Z0-9]{4})\s*$", text)
            if URL in text and len(codes) == 1:
                setup.call(
                    "/api/runner/setup-requests",
                    setup._proof(row, "device_code", user_code=codes[0]),
                )
                self.presented = True
                self.buffer = b""
        code = self.process.poll()
        if code is not None:
            if code or not self.presented:
                self.stop()
                raise InstallError("device sign-in unavailable")
            # Confirm durable private storage without returning its contents.
            if not setup.fs.read(HOME + "/.codex/auth.json"):
                self.stop()
                raise InstallError("device sign-in unavailable")
            setup.call("/api/runner/setup-requests", setup._proof(row, "login_succeeded"))
            self.stop()

    def _start(self, setup, row, scope):
        component = setup.runtime.read()["components"]["codex"]
        raw = (
            setup.fs.read(component["path"])
            if not os.path.isabs(component["path"])
            else setup.runtime.host.locate("codex")["raw"]
        )
        if digest(raw) != scope["runtime"]["components"]["codex"]["sha256"]:
            raise InstallError("device runtime changed")
        if component["format"] == "native" and not raw.startswith(b"\x7fELF"):
            raise InstallError("unsupported device runtime")
        fd = os.memfd_create("runner-login", os.MFD_ALLOW_SEALING | os.MFD_CLOEXEC)
        self.fd = fd
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(raw)
        os.fchmod(fd, 0o700 if component["format"] == "native" else 0o600)
        fcntl.fcntl(fd, getattr(fcntl, "F_ADD_SEALS", 1033), 0x0F)
        path = f"/proc/self/fd/{fd}"
        command = [path] if component["format"] == "native" else [sys.executable, "-I", path]
        directory = HOME + "/.codex"
        if setup.fs.info(directory) is not None and not any(
            i["path"] == directory for i in setup.install.journal["items"]
        ):
            raise InstallError("private provider home is occupied")
        setup.install.directory(directory)
        home = setup.fs.home / HOME
        self.process = subprocess.Popen(
            [
                *command,
                "-c",
                'forced_login_method="chatgpt"',
                "-c",
                'cli_auth_credentials_store="file"',
                "login",
                "--device-auth",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            pass_fds=(fd,),
            start_new_session=True,
            umask=0o077,
            cwd=home,
            env={
                "PATH": "/usr/bin:/bin",
                "LANG": "C",
                "HOME": str(home),
                "NO_COLOR": "1",
                "RUST_LOG": "off",
            },
        )
        os.set_blocking(self.process.stdout.fileno(), False)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        self.request = row["id"]
        self.deadline = time.monotonic() + 240
        process = self.process

        def expire():
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

        self.timer = threading.Timer(240, expire)
        self.timer.daemon = True
        self.timer.start()


def cleanup(fs, journal):
    """Remove only the dedicated owned provider directory after confirmed stop."""
    import stat

    entries = []

    def inspect(path):
        info = fs.info(path)
        directory = stat.S_ISDIR(info.st_mode)
        fs._check(info, directory=directory)
        if directory:
            for child in (fs.home / path).iterdir():
                inspect(str(child.relative_to(fs.home)))
        entries.append((path, directory))

    for root in (HOME + "/.codex", HOME + "/.claude"):
        if fs.info(root) is None:
            continue
        if not any(i["path"] == root and i["kind"] == "dir" for i in journal["items"]):
            raise InstallError("private provider directory is unmanaged")
        inspect(root)  # Validate all owned trees before removing anything.
    for path, directory in entries:
        fs.remove(path, directory=directory)
