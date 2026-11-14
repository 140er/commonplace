"""Finite setup supervision, independent of slow probes and provider sign-in."""

import base64
import fcntl
import json
import os
import queue
import secrets
import signal
import subprocess
import sys
import threading
import time

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

from .install_files import STATE, digest, encoded
from .install_host import InstallError
from .install_receipts import canonical
from .receipt_cache import APPROVAL_KEY, public_key

CONTROL = STATE + "/lifecycle.json"
CONTROL_LOCK = STATE + "/lifecycle.lock"


def bounded(call, seconds=1):
    """A dribbling response cannot extend the supervisor's monotonic deadline."""
    result = queue.Queue(maxsize=1)

    def run():
        try:
            result.put((True, call()))
        except Exception:
            result.put((False, None))

    threading.Thread(target=run, daemon=True).start()
    try:
        ok, value = result.get(timeout=seconds)
    except queue.Empty:
        raise InstallError("lifecycle deadline expired") from None
    if not ok:
        raise InstallError("lifecycle unavailable")
    return value


def prepare(install):
    if not install.load():
        raise InstallError("installation missing")
    with install.locked():
        install.load()
        for path, raw in (
            (CONTROL, encoded({"sequence": 0, "stopped": False})),
            (CONTROL_LOCK, b""),
        ):
            if install.fs.info(path) is None:
                install.file(path, raw)
            elif not any(i["path"] == path for i in install.journal["items"]):
                raise InstallError("lifecycle path is unmanaged")


class Channel:
    def __init__(self, install):
        from .installer import CONFIG, Transport

        prepare(install)
        self.install, self.fs = install, install.fs
        self.config = json.loads(self.fs.read(CONFIG))
        self.transport = install.transport or Transport(self.config["hub_url"])
        self.key = public_key(self.fs.read(APPROVAL_KEY))

    def exchange(self, operation="pulse", host_status=None):
        with self.fs.parent(CONTROL_LOCK) as (parent, name):
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                self.fs._check(os.fstat(fd))
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                state = json.loads(self.fs.read(CONTROL))
                if type(state["sequence"]) is not int:
                    raise ValueError
                sequence = state["sequence"] + 1
                if type(sequence) is not int or not 0 < sequence < 2**53:
                    raise ValueError
                state["sequence"] = sequence
                self.fs.write(CONTROL, encoded(state), replace=True)
                body = {
                    "action": "lifecycle",
                    "purpose": "runner-lifecycle",
                    "operation": operation,
                    "workspace_id": self.install.journal["intent"]["workspace_id"],
                    "enrollment_id": self.install.journal["intent"]["enrollment_id"],
                    "installation_id": self.config["installation_id"],
                    "machine_id": self.config["machine_id"],
                    "sequence": sequence,
                    "nonce": secrets.token_hex(16),
                    "host_status": host_status,
                }
                signature = self.install._key().sign(
                    canonical(body),
                    padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
                    hashes.SHA256(),
                )
                envelope = self.transport.lifecycle(
                    {
                        **body,
                        "signature": base64.b64encode(signature).decode(),
                    }
                )
                if not isinstance(envelope, dict) or set(envelope) != {"receipt", "signature"}:
                    raise ValueError
                receipt = envelope["receipt"]
                self.key.verify(
                    base64.b64decode(envelope["signature"], validate=True), canonical(receipt)
                )
                expected = {
                    k: body[k]
                    for k in (
                        "purpose",
                        "nonce",
                        "sequence",
                        "workspace_id",
                        "enrollment_id",
                        "installation_id",
                        "machine_id",
                    )
                }
                if (
                    not isinstance(receipt, dict)
                    or set(receipt)
                    != set(expected)
                    | {
                        "version",
                        "command",
                        "lease_seconds",
                        "host_verification",
                        "agent_ready",
                    }
                    or any(receipt[k] != v for k, v in expected.items())
                    or receipt["version"] != 1
                    or receipt["agent_ready"] is not False
                    or receipt["command"] not in ("continue", "stop")
                    or receipt["host_verification"] not in ("verified", "unavailable")
                    or type(receipt["lease_seconds"]) is not int
                    or (receipt["command"] == "continue" and not 0 < receipt["lease_seconds"] <= 4)
                    or (receipt["command"] == "stop" and receipt["lease_seconds"] != 0)
                    or (operation != "pulse" and receipt["command"] != "stop")
                ):
                    raise ValueError
                return receipt
            finally:
                os.close(fd)


def stop_group(process):
    if process is None:
        return
    # The process was created as its own session; do not target any other group.
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        if sig == signal.SIGTERM:
            time.sleep(0.1)
    process.wait(timeout=0.5)


def supervise(command, renew, *, stop=None, on_start=None):
    stop = stop or threading.Event()
    child = None
    try:
        while not stop.is_set():
            sent = time.monotonic()
            receipt = bounded(renew)
            if receipt["command"] != "continue":
                break
            deadline = sent + receipt["lease_seconds"]
            if time.monotonic() >= deadline:
                break
            if child is None:
                child = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                    umask=0o077,
                    env={
                        k: v
                        for k, v in os.environ.items()
                        if k in ("PATH", "LANG", "HOME", "PYTHONPATH")
                    },
                )
                if on_start:
                    on_start(child)
            if child.poll() is not None:
                break
            stop.wait(min(0.75, max(0, deadline - time.monotonic())))
    except Exception:
        pass  # A failed pin, malformed reply or unreachable hub always stops work.
    finally:
        stop_group(child)
    return 0


def command(home, action):
    from .installer import STATE

    if (home / STATE / "runner.pyz").is_file():
        return [
            sys.executable,
            "-I",
            str(home / STATE / "runner.pyz"),
            "runner",
            action,
            "--home",
            str(home),
        ]
    return [sys.executable, "-I", "-m", "commonplace.runner.supervisor", action, str(home)]


def serve(home):
    from .installer import Installer

    install = Installer(home, host=object())
    channel = Channel(install)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    return supervise(command(install.fs.home, "_setup-sync"), channel.exchange, stop=stop)


def stopped(home, *, transport=None, host=None):
    """ExecStopPost runs after systemd killed the main process and its cgroup.

    Retain the key solely to retry an offline stop acknowledgement. Neither a
    valid old key nor a stopped report grants a lease after revocation.
    """
    from .device_login import cleanup
    from .install_host import LiveHost
    from .installer import Installer

    host = host if host is not None else LiveHost()
    if not host.termination_confirmed():
        raise InstallError("service stop unconfirmed")
    install = Installer(home, host=host, transport=transport)
    prepare(install)
    with install.locked():
        install.load()
        for path in (
            STATE + "/machine.token",
            STATE + "/worker.token",
            STATE + "/host-grant.json",
            STATE + "/setup-status.json",
            STATE + "/runtime-config.json",
            STATE + "/approved-binding.json",
        ):
            item = next((i for i in install.journal["items"] if i["path"] == path), None)
            if item and install.fs.info(path) is not None:
                raw = b"" if path.endswith(".token") else encoded({})
                item["digest"] = digest(raw)
                install.flush()
                install.fs.write(path, raw, replace=True)
        cleanup(install.fs, install.journal)
        install.journal["items"] = [
            i for i in install.journal["items"] if install.fs.info(i["path"]) is not None
        ]
        install.flush()
        state = json.loads(install.fs.read(CONTROL))
        state["stopped"] = True
        install.fs.write(CONTROL, encoded(state), replace=True)
    try:
        channel = Channel(install)
        bounded(lambda: channel.exchange("revoke"), seconds=0.25)
    except Exception:
        return 1
    try:
        bounded(lambda: channel.exchange("stopped"), seconds=0.25)
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    try:
        if len(sys.argv) != 3:
            raise ValueError
        if sys.argv[1] == "_setup-stopped":
            raise SystemExit(stopped(sys.argv[2]))
        if sys.argv[1] == "_setup-sync":
            from .installer import sync_worker

            raise SystemExit(sync_worker(sys.argv[2]))
        raise ValueError
    except Exception:
        raise SystemExit(1) from None
