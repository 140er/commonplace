"""User-manager operations, isolated from the enrollment and filesystem logic."""

import os
import platform
import pwd
import re
import subprocess
from pathlib import Path

UNIT = "commonplace-runner-setup.service"


def sign_in_scope(installation_id):
    if not re.fullmatch(r"install_[0-9a-f]{32}", installation_id):
        raise InstallError("invalid installation identity")
    return "commonplace-runner-sign-in-" + installation_id + ".scope"


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
        self.in_terminal = False

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

    def termination_confirmed(self, installation_id=None):
        return not self.active and not self.in_terminal

    def owns_terminal(self, installation_id):
        sign_in_scope(installation_id)
        return self.in_terminal

    def terminal(self, command, installation_id, *, run):
        sign_in_scope(installation_id)
        if not self.active:
            raise InstallError("setup service is not active")
        self.commands.append("terminal-scope")
        self.in_terminal = True
        try:
            return run()
        finally:
            self.in_terminal = False


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

    def termination_confirmed(self, installation_id=None):
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

        if main.stdout.strip() != "0" or self._unit("is-active").returncode == 0:
            return False
        if installation_id is not None:
            state = self._run(
                [
                    "/usr/bin/systemctl",
                    "--user",
                    "show",
                    sign_in_scope(installation_id),
                    "--property=ActiveState",
                    "--value",
                ]
            )
            if state.stdout.strip() not in ("inactive", "failed"):
                return False
        return True

    def request_stop(self):
        self._run(
            [
                "/usr/bin/systemctl",
                "--user",
                "stop",
                "--no-block",
                UNIT,
            ]
        )

    def owns_terminal(self, installation_id):
        result = self._run(
            [
                "/usr/bin/systemctl",
                "--user",
                "show",
                sign_in_scope(installation_id),
                "--property=ControlGroup",
                "--value",
            ]
        )
        group = result.stdout.strip()
        return bool(
            group
            and any(
                line.split(":", 2)[-1] == group or line.split(":", 2)[-1].startswith(group + "/")
                for line in Path("/proc/self/cgroup").read_text().splitlines()
            )
        )

    def terminal(self, command, installation_id, *, run=None):
        if self._unit("is-active").returncode:
            raise InstallError("setup service is not active")
        try:
            result = subprocess.run(
                [
                    "/usr/bin/systemd-run",
                    "--user",
                    "--scope",
                    "--quiet",
                    "--collect",
                    "--unit=" + sign_in_scope(installation_id),
                    "--property=BindsTo=" + UNIT,
                    "--property=PartOf=" + UNIT,
                    "--property=Requisite=" + UNIT,
                    "--property=After=" + UNIT,
                    "--property=KillSignal=SIGKILL",
                    "--property=TimeoutStopSec=1",
                    "--",
                    *command,
                ],
                timeout=320,
                cwd=Path.home(),
                umask=0o077,
                env={
                    "PATH": "/usr/bin:/bin",
                    "LANG": "C",
                    "HOME": str(Path.home()),
                    "XDG_RUNTIME_DIR": self.runtime,
                    "DBUS_SESSION_BUS_ADDRESS": f"unix:path={self.runtime}/bus",
                },
            )
            if result.returncode:
                raise ValueError
            return 0
        except Exception:
            raise InstallError("terminal sign-in stopped or unavailable") from None
        finally:
            self._run(
                ["/usr/bin/systemctl", "--user", "stop", sign_in_scope(installation_id)],
                check=False,
            )

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
