"""Pinned runtime producer adapter; it never creates an issuer or grants work."""

import fcntl
import json
import os
import selectors
import signal
import subprocess
import sys
import time

from . import host_authority
from .install_files import digest
from .install_host import InstallError
from .install_receipts import canonical
from .runtime_install import HOME
from .setup_client import GRANT


def invoke(command, fd, home, payload=b"", *, seconds=0.5):
    """Bound both pipes and time; a blocked writer cannot outlive the lease."""
    if len(payload) > 16384:
        raise InstallError("host request unavailable")
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        pass_fds=(fd,),
        cwd=home,
        umask=0o077,
        env={"PATH": "/usr/bin:/bin", "HOME": str(home), "LANG": "C", "NO_COLOR": "1"},
    )
    output = bytearray()
    deadline = time.monotonic() + seconds
    try:
        with selectors.DefaultSelector() as selector:
            os.set_blocking(process.stdout.fileno(), False)
            selector.register(process.stdout, selectors.EVENT_READ)
            if payload:
                os.set_blocking(process.stdin.fileno(), False)
                selector.register(process.stdin, selectors.EVENT_WRITE)
            else:
                process.stdin.close()
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError
                for key, _ in selector.select(remaining):
                    if key.fileobj is process.stdin:
                        count = os.write(process.stdin.fileno(), payload[:4096])
                        payload = payload[count:]
                        if not payload:
                            selector.unregister(process.stdin)
                            process.stdin.close()
                    else:
                        chunk = os.read(process.stdout.fileno(), 4096)
                        if not chunk:
                            selector.unregister(process.stdout)
                        output.extend(chunk)
                        if len(output) > 16384:
                            raise ValueError
        code = process.wait(timeout=max(0.001, deadline - time.monotonic()))
        return code, bytes(output)
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=0.25)
        process.stdout.close()
        process.stdin.close()


class HostStatus:
    def __init__(self, install):
        self.install = install
        self.unsupported = None

    def __call__(self, nonce):
        install = self.install
        if host_authority.load(install) is None:
            return None
        if install.fs.info(GRANT) is None:
            return None
        value = json.loads(install.fs.read(GRANT))
        if value == {}:
            return None
        try:
            runtime, scope, value = host_authority.context(install)
            component = runtime.read()["components"]["host"]
            release = scope["runtime"]["components"]["host"]
            if component["sha256"] == self.unsupported:
                return None
            raw = (
                install.fs.read(component["path"])
                if not os.path.isabs(component["path"])
                else runtime.host.locate("host")["raw"]
            )
            if digest(raw) != release["sha256"]:
                raise ValueError
            if release["format"] == "native" and not raw.startswith(b"\x7fELF"):
                raise ValueError
            fd = os.memfd_create("runner-host-status", os.MFD_ALLOW_SEALING | os.MFD_CLOEXEC)
            try:
                with os.fdopen(fd, "wb", closefd=False) as stream:
                    stream.write(raw)
                os.fchmod(fd, 0o700 if release["format"] == "native" else 0o600)
                fcntl.fcntl(fd, getattr(fcntl, "F_ADD_SEALS", 1033), 0x0F)
                path = f"/proc/self/fd/{fd}"
                command = [path] if release["format"] == "native" else [sys.executable, "-I", path]
                home = install.fs.home / HOME
                code, help_text = invoke([*command, "--help"], fd, home)
                if code:
                    raise ValueError
                if b"runner-status" not in help_text.split():
                    self.unsupported = release["sha256"]
                    return None
                code, output = invoke(
                    [*command, "runner-status"],
                    fd,
                    home,
                    canonical(
                        {
                            "version": 1,
                            "purpose": "runner-host-status",
                            "nonce": nonce,
                            "grant": value,
                        }
                    ),
                )
                if code:
                    raise ValueError
                envelope = json.loads(output)
                if (
                    set(envelope) != {"receipt", "signature"}
                    or envelope["receipt"]["nonce"] != nonce
                ):
                    raise ValueError
                host_authority.accept(install, envelope, nonce)
                # The hub also verifies under its independently configured pin.
                return envelope
            finally:
                os.close(fd)
        except Exception:
            raise InstallError("host status producer unavailable") from None
