"""Offline-replaceable discovery, bounded downloads and noninteractive probes."""

import fcntl
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from .install_files import digest
from .install_host import InstallError
from .runtime_manifest import MAX_ARTIFACT

COMMANDS = {"host": "t3", "claude": "claude", "codex": "codex"}


class FakeRuntimeHost:
    """Synthetic bytes and facts; no process, network, package manager or login."""

    def __init__(self):
        self.existing = {}
        self.artifacts = {}
        self.facts = {}
        self.calls = []

    def locate(self, name):
        self.calls.append(("locate", name))
        return self.existing.get(name)

    def download(self, release):
        self.calls.append(("download", release["sha256"]))
        return self.artifacts[release["sha256"]]

    def probe(self, name, release, raw, home):
        assert digest(raw) == release["sha256"]
        self.calls.append(("probe", name))
        return self.facts.get(
            name,
            {
                "availability": "present",
                "login": "not-applicable" if name == "host" else "logged-out",
            },
        )


class LiveRuntimeHost:
    """Supported self-contained releases only; unknown wrappers are never run.

    Native artifacts stay 0600 on disk. Verified bytes execute through a sealed,
    anonymous Linux descriptor, rather than adding execute bits to stored files.
    Every probe has closed stdin and a private home, without inherited credentials.
    A successful login status is unverified until a later account/scope approval.
    """

    def locate(self, name):
        path = shutil.which(COMMANDS[name])
        if path is None:
            return None
        try:
            path = Path(path).resolve(strict=True)
            for parent in (path.parent, *path.parents):
                info = parent.stat()
                if info.st_uid not in (0, os.getuid()) or info.st_mode & 0o022:
                    return None
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                info = os.fstat(fd)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid not in (0, os.getuid())
                    or info.st_mode & 0o022
                    or info.st_size > MAX_ARTIFACT
                ):
                    return None
                with os.fdopen(fd, "rb", closefd=False) as stream:
                    raw = stream.read(MAX_ARTIFACT + 1)
            finally:
                os.close(fd)
            return {"path": str(path), "raw": raw}
        except OSError:
            return None

    def download(self, release):
        from .installer import _NoRedirect

        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        try:
            request = urllib.request.Request(
                release["url"], headers={"Accept": "application/octet-stream"}
            )
            deadline, raw = time.monotonic() + 30, bytearray()
            with opener.open(request, timeout=30) as response:
                while True:
                    if time.monotonic() >= deadline:
                        raise ValueError
                    chunk = response.read1(min(65536, MAX_ARTIFACT + 1 - len(raw)))
                    if not chunk:
                        break
                    raw.extend(chunk)
                    if len(raw) > MAX_ARTIFACT:
                        raise ValueError
            if not raw:
                raise ValueError
            return bytes(raw)
        except Exception:
            raise InstallError("runtime download unavailable") from None

    def probe(self, name, release, raw, home, *, sign_in=False):
        if digest(raw) != release["sha256"] or not raw or len(raw) > MAX_ARTIFACT:
            raise InstallError("runtime integrity verification failed")
        if release["format"] == "native" and not raw.startswith(b"\x7fELF"):
            return {"availability": "unsupported", "login": "unknown"}
        fd = os.memfd_create("runner-runtime", os.MFD_ALLOW_SEALING | os.MFD_CLOEXEC)
        try:
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(raw)
                stream.flush()
            os.fchmod(fd, 0o700 if release["format"] == "native" else 0o600)
            # Linux UAPI values: some supported Python builds omit these names.
            fcntl.fcntl(fd, getattr(fcntl, "F_ADD_SEALS", 1033), 0x0F)
            path = f"/proc/self/fd/{fd}"
            command = [path] if release["format"] == "native" else [sys.executable, "-I", path]

            def run(args):
                result = subprocess.run(
                    [*command, *args],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=10,
                    pass_fds=(fd,),
                    umask=0o077,
                    cwd=home,
                    env={
                        "PATH": "/usr/bin:/bin",
                        "LANG": "C",
                        "HOME": str(home),
                        "NO_COLOR": "1",
                        "CLAUDE_CONFIG_DIR": str(home / ".claude"),
                    },
                )
                if len(result.stdout) + len(result.stderr) > 65536:
                    raise ValueError
                return result.returncode, result.stdout.decode("utf-8", errors="strict")

            code, version = run(["--version"])
            if code or re.findall(
                r"(?<![\w.])\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?", version.strip()
            ) != [release["version"]]:
                return {"availability": "unsupported", "login": "unknown"}
            code, help_text = run(["--help"])
            required = {
                "host": ("acp-mcp-call", "serve"),
                "claude": ("--print", "auth"),
                "codex": ("exec", "login"),
            }[name]
            if code or not all(word in help_text.split() for word in required):
                return {"availability": "unsupported", "login": "unknown"}
            if name == "host":
                return {"availability": "present", "login": "not-applicable"}
            code, auth_help = run(["auth" if name == "claude" else "login", "--help"])
            if code or "status" not in auth_help.split():
                return {"availability": "unsupported", "login": "unknown"}
            if name == "claude":
                if sign_in:
                    # The owner owns this terminal exchange. No capture, relay,
                    # inherited credentials, browser launch or hub input.
                    subprocess.run(
                        [*command, "auth", "login", "--claudeai"],
                        check=True,
                        timeout=300,
                        pass_fds=(fd,),
                        umask=0o077,
                        cwd=home,
                        env={
                            "PATH": "/usr/bin:/bin",
                            "LANG": "C",
                            "HOME": str(home),
                            "CLAUDE_CONFIG_DIR": str(home / ".claude"),
                            "BROWSER": "/bin/false",
                        },
                    )
                code, status_text = run(["auth", "status", "--json"])
                status = json.loads(status_text)
                login = (
                    "unverified"
                    if code == 0
                    and status.get("loggedIn") is True
                    and status.get("authMethod") == "claude.ai"
                    else "logged-out"
                    if status.get("loggedIn") is False
                    else "unknown"
                )
            else:
                # The status subcommand is read-only. Its output is never retained.
                code, _ = run(["login", "status"])
                login = "unverified" if code == 0 else "logged-out" if code == 1 else "unknown"
            return {"availability": "present", "login": login}
        except Exception:
            raise InstallError("runtime capability probe unavailable") from None
        finally:
            os.close(fd)
