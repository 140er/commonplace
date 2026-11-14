"""User-manager operations, isolated from the enrollment and filesystem logic."""

import os
import platform
import pwd
import subprocess
from pathlib import Path

UNIT = "commonplace-runner-setup.service"


class InstallError(Exception):
    """A fixed, secret-free installation refusal."""


class FakeHost:
    """Recording user manager: never invokes a process or contacts a server."""

    def __init__(self, *, linger=False, permitted=True, occupied=False):
        self.linger = linger
        self.permitted = permitted
        self.occupied = occupied
        self.enabled = self.active = False
        self.other_services = False
        self.commands = []

    def preflight(self):
        if not self.permitted or self.occupied:
            raise InstallError("user service or noninteractive linger setup unavailable")

    def state(self):
        return {"linger": self.linger, "enabled": self.enabled, "active": self.active}

    def change(self, action):
        self.commands.append(action)
        if action == "enable-linger":
            self.linger = True
        elif action == "disable-linger":
            self.linger = False
        elif action == "enable":
            self.enabled = self.active = True
        elif action == "disable":
            self.enabled = self.active = False

    def has_other_services(self):
        return self.other_services

    def termination_confirmed(self):
        return not self.active


class LiveHost:
    """No shell, inherited credential environment, password or authorization prompt."""

    def __init__(self):
        self.account = pwd.getpwuid(os.getuid()).pw_name
        self.runtime = f"/run/user/{os.getuid()}"

    def _run(self, argv, *, check=True):
        try:
            result = subprocess.run(
                argv,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=30,
                env={
                    "PATH": "/usr/bin:/bin",
                    "LANG": "C",
                    "XDG_RUNTIME_DIR": self.runtime,
                    "DBUS_SESSION_BUS_ADDRESS": f"unix:path={self.runtime}/bus",
                },
            )
        except (OSError, subprocess.SubprocessError):
            raise InstallError("user service operation failed") from None
        if check and result.returncode:
            raise InstallError("user service operation failed")
        return result

    def _unit(self, action):
        return self._run(["/usr/bin/systemctl", "--user", action, UNIT], check=False)

    def termination_confirmed(self):
        main = self._run(
            [
                "/usr/bin/systemctl",
                "--user",
                "show",
                UNIT,
                "--property=MainPID",
                "--value",
            ]
        )
        return main.stdout.strip() == "0" and self._unit("is-active").returncode != 0

    def state(self):
        linger = self._run(
            ["/usr/bin/loginctl", "show-user", self.account, "--property=Linger", "--value"]
        )
        return {
            "linger": linger.stdout.strip() == "yes",
            "enabled": self._unit("is-enabled").returncode == 0,
            "active": self._unit("is-active").returncode == 0,
        }

    def preflight(self):
        if platform.system() != "Linux" or os.getuid() == 0:
            raise InstallError("an unprivileged Linux account is required")
        self._run(["/usr/bin/systemctl", "--user", "show", "--property=Version"])
        # A loaded unit outside our filesystem is a collision too.
        unit = self._run(
            ["/usr/bin/systemctl", "--user", "show", UNIT, "--property=FragmentPath", "--value"]
        )
        expected = Path.home() / ".config/systemd/user" / UNIT
        if unit.stdout.strip() and unit.stdout.strip() != str(expected):
            raise InstallError("user service name is already occupied")
        if not self.state()["linger"]:
            for action in ("enable-linger", "disable-linger"):
                argv = ["/usr/bin/loginctl", action, self.account]
                permission = self._run(["/usr/bin/sudo", "-n", "-l", "--", *argv])
                # Require the narrowly authorized, password-free command, not ALL.
                line = "NOPASSWD: " + " ".join(argv)
                if not any(s.strip().endswith(line) for s in permission.stdout.splitlines()):
                    raise InstallError("noninteractive linger permission is required")

    def change(self, action):
        if action.endswith("-linger"):
            self._run(["/usr/bin/sudo", "-n", "--", "/usr/bin/loginctl", action, self.account])
        elif action == "reload":
            self._run(["/usr/bin/systemctl", "--user", "daemon-reload"])
        else:
            self._run(["/usr/bin/systemctl", "--user", action, "--now", UNIT])

    def has_other_services(self):
        for args in (("list-units", "--state=active"), ("list-unit-files", "--state=enabled")):
            result = self._run(
                [
                    "/usr/bin/systemctl",
                    "--user",
                    *args,
                    "--type=service",
                    "--no-legend",
                    "--no-pager",
                ]
            )
            if any(line.split()[0] != UNIT for line in result.stdout.splitlines() if line.split()):
                return True
        return False
