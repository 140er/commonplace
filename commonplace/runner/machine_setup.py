"""One-command machine setup: check first, spend the one-time code last, report every step.

The hub serves this file's source inside a small shell launcher (``/machine-setup.sh``), so the
top level imports only the standard library: it must run on a machine that has nothing of
Commonplace installed. The runner installer (``commonplace.runner.installer``) is imported
only after the machine passed every check.

Order, and why:

1. Read the public envelope (argument) and the one-time code (standard input, never argv).
2. Check the machine: not root, a supported system, Python and ``cryptography``, the runner
   program, a running user service manager, free disk. A failure is reported to the app
   through the code-authenticated ``check`` route, which does not spend the code.
3. Ask the hub whether the code can still work (expired, used, cancelled, wrong workspace).
4. Keep user services running after logout (one administrator confirmation, only if it is off).
5. Run the existing installer, which spends the code and writes the private installation.
6. Report ``files``, ``service`` and ``check`` with the machine's own credential.

Nothing here prints, logs or stores the code, and nothing touches SSH.
"""

import base64
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

ENVELOPE_KEYS = {
    "v",
    "hub",
    "workspace_id",
    "pairing_id",
    "enrollment_id",
    "label",
    "manifest_digest",
    "kind",
}
CODE = re.compile(r"[A-Za-z0-9_-]{43}")
ID = re.compile(r"[A-Za-z0-9_-]{1,80}")
LABEL = re.compile(r"[a-z0-9][a-z0-9._-]{0,39}")
LOOPBACK = ("127.0.0.1", "localhost", "[::1]")
MIN_PYTHON = (3, 10)
MIN_FREE_BYTES = 256 * 1024 * 1024
# Lowest supported release of each system: the app says "Ubuntu 24.04 and Debian 12 or later".
SUPPORTED = {"ubuntu": (24, 4), "debian": (12, 0)}


class Unreachable(Exception):
    """The hub could not be reached or answered something unreadable."""


class Failure(Exception):
    """A check that failed: a cause the app knows, and what to tell the person here."""

    def __init__(self, cause, message, then):
        super().__init__(cause)
        self.cause, self.message, self.then = cause, message, then


def parse_envelope(text):
    """The public values the app put in the command; refuses anything else."""
    if not isinstance(text, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,2048}", text):
        raise ValueError("envelope")
    try:
        data = json.loads(base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)))
    except Exception:
        raise ValueError("envelope") from None
    if (
        not isinstance(data, dict)
        or set(data) != ENVELOPE_KEYS
        or data["v"] != 1
        or data["kind"] != "runner"
        or not isinstance(data["label"], str)
        or not LABEL.fullmatch(data["label"])
        or not isinstance(data["manifest_digest"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", data["manifest_digest"])
        or any(
            not isinstance(data[key], str) or not ID.fullmatch(data[key])
            for key in ("workspace_id", "pairing_id", "enrollment_id")
        )
        or not isinstance(data["hub"], str)
        or not origin_ok(data["hub"])
    ):
        raise ValueError("envelope")
    return data


def origin_ok(origin):
    """HTTPS, or plain HTTP only to this very machine; no path, query, user or fragment."""
    parts = urlsplit(origin)
    if (
        parts.username
        or parts.password
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
        or not parts.hostname
    ):
        return False
    host = "[::1]" if parts.hostname == "::1" else parts.hostname
    return parts.scheme == "https" or (parts.scheme == "http" and host in LOOPBACK)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Hub:
    """The two routes this program calls. No redirects, no environment proxies."""

    def __init__(self, origin):
        if not origin_ok(origin):
            raise ValueError("origin")
        self.origin = origin.rstrip("/")
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def _post(self, route, body, token=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        request = urllib.request.Request(
            self.origin + route, data=json.dumps(body).encode(), headers=headers, method="POST"
        )
        try:
            with self.opener.open(request, timeout=20) as response:
                return json.loads(response.read(65537))
        except urllib.error.HTTPError as exc:
            try:
                return json.loads(exc.read(65537))
            except Exception:
                raise Unreachable from None
        except Exception:
            raise Unreachable from None

    def check(self, body):
        return self._post("/api/runner-pairing/check", body)

    def status(self, token, body):
        return self._post("/api/runner/pairing-status", body, token)


class LiveProbe:
    """What this machine looks like. Read-only except ``enable_linger``."""

    def uid(self):
        return os.getuid()

    def home(self):
        return Path.home()

    def account(self):
        import pwd

        return pwd.getpwuid(os.getuid()).pw_name

    def system(self):
        facts = {}
        try:
            for line in Path("/etc/os-release").read_text().splitlines():
                key, _, value = line.partition("=")
                facts[key] = value.strip().strip('"')
        except OSError:
            pass
        return platform.system(), facts.get("ID", ""), facts.get("VERSION_ID", "")

    def python(self):
        return sys.version_info[:2]

    def can_import(self, name):
        try:
            return importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            return False

    def user_manager(self):
        return self._ok(["/usr/bin/systemctl", "--user", "show", "--property=Version"])

    def linger(self):
        result = self._run(
            ["/usr/bin/loginctl", "show-user", self.account(), "--property=Linger", "--value"]
        )
        return result is not None and result.stdout.strip() == "yes"

    def enable_linger(self):
        """The one administrator confirmation. The password prompt is the system's own."""
        try:
            return (
                subprocess.run(
                    ["/usr/bin/sudo", "/usr/bin/loginctl", "enable-linger", self.account()],
                    timeout=120,
                ).returncode
                == 0
            )
        except (OSError, subprocess.SubprocessError):
            return False

    def free_bytes(self, path):
        return shutil.disk_usage(path).free

    def _run(self, argv):
        try:
            return subprocess.run(argv, capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            return None

    def _ok(self, argv):
        result = self._run(argv)
        return result is not None and result.returncode == 0


def _version(text):
    parts = re.findall(r"\d+", text or "")
    return tuple(int(p) for p in parts[:2]) + (0,) * (2 - len(parts[:2]))


def prerequisites(probe):
    """Raise ``Failure`` for the first thing that stops setup; return None when all pass."""
    if probe.uid() == 0:
        raise Failure(
            "run_as_root",
            "This command was run as root (or with sudo).",
            "Run it again as the normal account that should own the runner, without sudo.",
        )
    system, ident, version = probe.system()
    if system != "Linux" or ident not in SUPPORTED or _version(version) < SUPPORTED[ident]:
        found = " ".join(part for part in (system, ident, version) if part) or "an unknown system"
        raise Failure(
            "unsupported_os",
            "This system is not supported (found " + found + ").",
            "Use Ubuntu 24.04 or later, or Debian 12 or later.",
        )
    if tuple(probe.python()) < MIN_PYTHON:
        raise Failure(
            "missing_python",
            "Python 3.10 or later is required.",
            "Install it (sudo apt install python3), then run the command again.",
        )
    if not probe.can_import("cryptography"):
        raise Failure(
            "missing_prerequisite",
            "The Python package 'cryptography' is missing.",
            "Install it (sudo apt install python3-cryptography), then run the command again.",
        )
    if not probe.can_import("commonplace.runner.installer"):
        raise Failure(
            "missing_runner_program",
            "The runner program is not installed on this machine.",
            "This command does not install it yet. Install it first (see the runner guide), "
            "then run the command again.",
        )
    if not probe.user_manager():
        raise Failure(
            "no_service_manager",
            "There is no running user service manager.",
            "Log in on the machine with a normal session (not su or sudo), then run the "
            "command again.",
        )
    if probe.free_bytes(probe.home()) < MIN_FREE_BYTES:
        raise Failure(
            "disk_full",
            "There is less than 256 MB free in your home folder.",
            "Free some space, then run the command again.",
        )


def _phase_failure(installer):
    """Which step an interrupted install reached, from its own journal.

    The installer's phase reads ``credentials`` once every private file is written; a failure
    after that point can only be the user service (unit, linger, enable, start).
    """
    journal = getattr(installer, "journal", None) or {}
    if journal.get("phase") == "credentials":
        return "service", "service_failed"
    return "files", "unknown"


def main(argv=None, *, code=None, probe=None, hub=None, make_installer=None, out=print):
    argv = sys.argv[1:] if argv is None else argv
    try:
        envelope = parse_envelope(argv[0] if len(argv) == 1 else None)
    except ValueError:
        out("The command is not complete. Copy the whole command again from the app.")
        return 2
    code = sys.stdin.read(128).strip() if code is None else code
    if not CODE.fullmatch(code or ""):
        out("The one-time code did not arrive. Copy the whole command again from the app.")
        return 2
    probe = probe or LiveProbe()
    hub = hub or Hub(envelope["hub"])
    base = {k: envelope[k] for k in ("workspace_id", "pairing_id")} | {"code": code}

    def ask(report=None):
        try:
            return hub.check(base | ({"report": report} if report else {}))
        except Unreachable:
            out("This machine could not reach " + envelope["hub"] + ".")
            out("Check its internet connection, then run the command again.")
            return None

    out("Checking this machine ...")
    failure = None
    try:
        prerequisites(probe)
    except Failure as exc:
        failure = exc
    report = {"step": "prerequisites", "state": "ok"}
    if failure:
        report = {"step": "prerequisites", "state": "failed", "cause": failure.cause}
    answer = ask(report)
    if answer is None:
        return 1
    resume = answer.get("reason") == "used" and _resumable(probe.home(), envelope)
    if not answer.get("usable") and not resume:
        out(str(answer.get("text", "The code cannot be used.")))
        out(str(answer.get("next", "Copy the command again from the app.")))
        return 1
    if failure:
        out(failure.message)
        out(failure.then)
        out("The app shows this too. Nothing was changed on this machine.")
        return 1
    if not probe.linger():
        out(
            "To keep the runner running after you log out, one administrator confirmation is needed."
        )
        if not probe.enable_linger():
            ask({"step": "prerequisites", "state": "failed", "cause": "missing_privilege"})
            out("The confirmation was not given, so nothing was installed.")
            out(
                "Run the command again and confirm, or ask an administrator to run: "
                "sudo loginctl enable-linger " + probe.account()
            )
            return 1
    out(
        "Continuing the setup that stopped earlier ..."
        if resume
        else "Installing the runner (this spends the one-time code) ..."
    )
    return _install(envelope, code, probe, hub, make_installer, out, ask)


def _install(envelope, code, probe, hub, make_installer, out, ask):
    home = probe.home()
    lines = []

    def collect(line):
        lines.append(str(line))

    try:
        if make_installer is None:
            from types import SimpleNamespace

            from commonplace.runner import installer as inst

            intent = inst.intent_from(
                SimpleNamespace(
                    hub=envelope["hub"],
                    workspace=envelope["workspace_id"],
                    label=envelope["label"],
                    manifest_digest=envelope["manifest_digest"],
                    enrollment_id=envelope["enrollment_id"],
                )
            )
            installer = inst.Installer(home, out=collect)
        else:
            intent, installer = make_installer(envelope, collect)
        installer.install(intent, accepted=True, code_source=lambda: code)
    except Exception as exc:
        step, cause = _phase_failure(locals().get("installer"))
        token = _token(home)
        reported = False
        if token:
            reported = _report(hub, token, {"step": step, "state": "failed", "cause": cause})
        else:
            transport = "delivery unavailable" in str(exc)
            ask(
                {
                    "step": "prerequisites",
                    "state": "failed",
                    "cause": "cannot_reach_hub" if transport else "unknown",
                }
            )
            reported = True
        out("Setup did not finish on this machine.")
        out(
            "Run the same command again: it continues from where it stopped."
            if reported
            else "Run the same command again."
        )
        return 1
    token = _token(home)
    state = installer.host.state()
    results = [("files", True), ("service", bool(state["enabled"] and state["active"]))]
    try:
        installer.verify()
        results.append(("check", True))
    except Exception:
        results.append(("check", False))
    causes = {"files": "unknown", "service": "service_failed", "check": "check_failed"}
    for step, ok in results:
        body = (
            {"step": step, "state": "ok"}
            if ok
            else {"step": step, "state": "failed", "cause": causes[step]}
        )
        if token and not _report(hub, token, body):
            out("Could not tell the app about the " + step + " step. It will show it as waiting.")
    for line in lines:
        if line.startswith("Machine fingerprint:"):
            out(line)
    if not all(ok for _, ok in results):
        out("The machine was installed, but one check failed. The app shows which.")
        return 1
    out("Done on this machine. Go back to Compute in the app: it shows this machine and")
    out("asks you to approve it. Compare the fingerprint above with the one on that screen.")
    return 0


def _resumable(home, envelope):
    """A private installation for this very enrollment is already here, so a spent code is fine.

    The installer then continues from its saved identity and its own recovery proof; the code
    is not needed again. Any other state (no installation, another enrollment) is a used code.
    """
    try:
        journal = json.loads((Path(home) / ".commonplace-runner" / "manifest.json").read_text())
        intent = journal["intent"]
        return (
            intent["enrollment_id"] == envelope["enrollment_id"]
            and intent["workspace_id"] == envelope["workspace_id"]
            and intent["machine_label"] == envelope["label"]
            and intent["hub_url"] == envelope["hub"]
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _token(home):
    try:
        raw = (Path(home) / ".commonplace-runner" / "machine.token").read_text().strip()
    except OSError:
        return None
    return raw if raw and "\n" not in raw else None


def _report(hub, token, body):
    try:
        answer = hub.status(token, body)
    except Unreachable:
        return False
    return bool(isinstance(answer, dict) and answer.get("ok"))


if __name__ == "__main__":
    raise SystemExit(main())
