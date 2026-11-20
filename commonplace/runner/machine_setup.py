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
import errno
import hashlib
import importlib.util
import io
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import venv
import zipfile
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
PROGRAM_PURPOSE = "runner-program-delivery"
PROGRAM_LIMIT = 32 * 1024 * 1024
PROGRAM_EXPANDED_LIMIT = 128 * 1024 * 1024
PROGRAM_ROOT = ".commonplace-runner-programs"


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
        or set(data) not in (ENVELOPE_KEYS, ENVELOPE_KEYS | {"program"})
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
    if "program" in data:
        program_pin(data["program"])
    return data


def program_pin(value):
    """Public digest pinned by the owner's command, never a response-selected URL or key."""
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "purpose", "sha256", "size", "python", "system", "machine"}
        or type(value["version"]) is not int
        or value["version"] != 1
        or value["purpose"] != PROGRAM_PURPOSE
        or not isinstance(value["sha256"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", value["sha256"])
        or type(value["size"]) is not int
        or not 0 < value["size"] <= PROGRAM_LIMIT
        or any(
            not isinstance(value[k], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value[k])
            for k in ("python", "system", "machine")
        )
    ):
        raise ValueError("invalid program pin")
    return value


def program_contents(raw, pin):
    """Verify the whole package before creating a prefix; never use archive extraction paths."""
    program_pin(pin)
    if len(raw) != pin["size"] or hashlib.sha256(raw).hexdigest() != pin["sha256"]:
        raise ValueError("program integrity check failed")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        names = [item.filename for item in entries]
        if (
            len(entries) > 10000
            or len(set(names)) != len(names)
            or sum(item.file_size for item in entries) > PROGRAM_EXPANDED_LIMIT
            or any(
                stat.S_IFMT(item.external_attr >> 16) not in (0, stat.S_IFREG) or item.is_dir()
                for item in entries
            )
            or "program.json" not in names
            or archive.getinfo("program.json").file_size > 2 * 1024 * 1024
        ):
            raise ValueError("invalid program archive")
        manifest = json.loads(archive.read("program.json"))
        if (
            not isinstance(manifest, dict)
            or set(manifest)
            != {"version", "purpose", "python", "system", "machine", "dependencies", "files"}
            or any(
                manifest[k] != pin[k] for k in ("version", "purpose", "python", "system", "machine")
            )
            or not isinstance(manifest["dependencies"], dict)
            or set(manifest["dependencies"]) != {"cryptography", "cffi", "pycparser"}
            or any(
                not isinstance(v, str) or not re.fullmatch(r"[0-9][A-Za-z0-9.+-]{0,79}", v)
                for v in manifest["dependencies"].values()
            )
            or not isinstance(manifest["files"], dict)
            or set(names) != {"program.json", *manifest["files"]}
            or "site/commonplace/runner/installer.py" not in manifest["files"]
        ):
            raise ValueError("invalid program manifest")
        files = {}
        for name, expected in manifest["files"].items():
            parts = name.split("/")
            if (
                not name.startswith("site/")
                or any(p in ("", ".", "..") for p in parts)
                or not re.fullmatch(r"[A-Za-z0-9_./+\-]+", name)
                or name.endswith(".pth")
                or parts[-1] in ("sitecustomize.py", "usercustomize.py")
                or not isinstance(expected, dict)
                or set(expected) != {"sha256", "size"}
                or type(expected["size"]) is not int
                or archive.getinfo(name).file_size != expected["size"]
            ):
                raise ValueError("unsafe program entry")
            data = archive.read(name)
            if hashlib.sha256(data).hexdigest() != expected["sha256"]:
                raise ValueError("program entry integrity check failed")
            files[name[5:]] = data
    return manifest, files


def _private(info, *, directory=False, executable=False):
    modes = (0o500, 0o700) if directory else (0o700,) if executable else (0o600,)
    if (
        (not stat.S_ISDIR(info.st_mode) if directory else not stat.S_ISREG(info.st_mode))
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) not in modes
        or (not directory and info.st_nlink != 1)
    ):
        raise ValueError("unsafe program prefix")


def _program_file(prefix, relative, *, executable=False):
    """Read through owned directory descriptors; refuse replaced parents, links and hard links."""
    fd = os.open(prefix, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        _private(os.fstat(fd), directory=True)
        parts = relative.split("/")
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
            _private(os.fstat(fd), directory=True)
        file = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            _private(os.fstat(file), executable=executable)
            with os.fdopen(file, "rb", closefd=False) as stream:
                return stream.read(PROGRAM_LIMIT + 1)
        finally:
            os.close(file)
    finally:
        os.close(fd)


def verify_program(prefix, pin, files):
    site = f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
    receipt = json.loads(_program_file(prefix, "program-install.json"))
    if receipt != {"pin": pin}:
        raise ValueError("program prefix belongs to a different release")
    # Python itself remains a trusted OS prerequisite. Refuse a replaced private copy or
    # a venv configuration that could import a different standard library/system site.
    base_executable = getattr(sys, "_base_executable", None)
    if not isinstance(base_executable, str):
        raise ValueError("base Python unavailable")
    if _program_file(prefix, "bin/python", executable=True) != Path(base_executable).read_bytes():
        raise ValueError("installed Python integrity check failed")
    rows = [
        line.split("=", 1) for line in _program_file(prefix, "pyvenv.cfg").decode().splitlines()
    ]
    if any(len(row) != 2 for row in rows):
        raise ValueError("invalid Python environment")
    config = {key.strip().lower(): value.strip() for key, value in rows}
    if (
        len(config) != len(rows)
        or config.get("include-system-site-packages") != "false"
        or config.get("home") != str(Path(base_executable).parent)
    ):
        raise ValueError("Python environment changed")
    for name, raw in files.items():
        if _program_file(prefix, site + "/" + name) != raw:
            raise ValueError("installed program integrity check failed")
    actual = set()
    site_path = Path(prefix) / site
    for directory, dirs, names in os.walk(site_path, followlinks=False):
        _private(os.lstat(directory), directory=True)
        for name in dirs:
            _private(os.lstat(Path(directory) / name), directory=True)
        for name in names:
            path = Path(directory) / name
            _private(os.lstat(path))
            actual.add(path.relative_to(site_path).as_posix())
    if actual != set(files):
        raise ValueError("unexpected installed program files")
    if set(os.listdir(Path(prefix) / "bin")) != {"python"}:
        raise ValueError("unexpected Python startup files")
    return Path(prefix) / "bin/python"


def install_program(home, raw, pin):
    """Publish one complete private venv, with no pip, network, system package or service changes."""
    manifest, files = program_contents(raw, pin)
    if (pin["python"], pin["system"], pin["machine"]) != (
        sys.implementation.cache_tag,
        platform.system(),
        platform.machine(),
    ):
        raise ValueError("program is built for a different Python or machine")
    home = Path(home).absolute()
    if home.resolve() != home or any(ord(c) < 32 for c in str(home)):
        raise ValueError("unsafe program home")
    home_fd = os.open(home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(home_fd)
        if info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise ValueError("unsafe program home")
        try:
            os.mkdir(PROGRAM_ROOT, 0o700, dir_fd=home_fd)
        except FileExistsError:
            pass
        root_fd = os.open(
            PROGRAM_ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=home_fd
        )
    finally:
        os.close(home_fd)
    root = home / PROGRAM_ROOT
    prefix = root / pin["sha256"]
    try:
        _private(os.fstat(root_fd), directory=True)
        import fcntl

        lock = os.open(
            pin["sha256"] + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=root_fd
        )
        try:
            _private(os.fstat(lock))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if prefix.exists() or prefix.is_symlink():
                if _program_file(prefix, "program.zip") != raw:
                    raise ValueError("cached program archive integrity check failed")
                return verify_program(prefix, pin, files)
            with tempfile.TemporaryDirectory(prefix=".install-", dir=root) as temporary:
                staged = Path(temporary) / "program"
                venv.EnvBuilder(with_pip=False, symlinks=False).create(staged)
                # Only the checked interpreter is used. Do not publish unused aliases or
                # activation scripts whose contents would otherwise escape verification.
                for entry in (staged / "bin").iterdir():
                    if entry.name != "python":
                        entry.unlink()
                site = (
                    staged
                    / f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
                )
                for name, data in files.items():
                    path = site / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open("xb") as stream:
                        stream.write(data)
                (staged / "program-install.json").write_text(json.dumps({"pin": pin}))
                (staged / "program.zip").write_bytes(raw)
                for path in staged.rglob("*"):
                    if path.is_symlink():
                        # venv may create only this standard library directory alias.
                        if path == staged / "lib64" and os.readlink(path) == "lib":
                            path.unlink()
                            continue
                        raise ValueError("unexpected program link")
                    path.chmod(0o700 if path.is_dir() or path.parent == staged / "bin" else 0o600)
                # Read-only library directories prevent untracked bytecode from
                # accumulating when the ordinary setup service imports this package.
                for directory in sorted((p for p in site.rglob("*") if p.is_dir()), reverse=True):
                    directory.chmod(0o500)
                site.chmod(0o500)
                staged.chmod(0o700)
                # Import the real installer and perform real cryptographic operations in the new interpreter.
                check = subprocess.run(
                    [
                        str(staged / "bin/python"),
                        "-I",
                        "-B",
                        "-c",
                        "from commonplace.runner.installer import Installer; "
                        "from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey; "
                        "k=Ed25519PrivateKey.generate(); k.public_key().verify(k.sign(b'program-check'),b'program-check')",
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env={"PATH": "/usr/bin:/bin", "HOME": str(home), "LANG": "C"},
                )
                if check.returncode:
                    raise ValueError("delivered runner cannot start on this machine")
                verify_program(staged, pin, files)
                os.rename(staged, prefix)
                os.fsync(root_fd)
            return prefix / "bin/python"
        finally:
            os.close(lock)
    finally:
        os.close(root_fd)


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
            if exc.code >= 500:
                raise Unreachable from None  # the site did not give an answer about this code
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

    def program(self, body, pin):
        request = urllib.request.Request(
            self.origin + "/api/runner-pairing/check",
            data=json.dumps(
                body | {"delivery": {"purpose": PROGRAM_PURPOSE, "sha256": pin["sha256"]}}
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self.opener.open(request, timeout=20) as response:
                if response.headers.get_content_type() != "application/octet-stream":
                    raise ValueError
                raw = response.read(pin["size"] + 1)
            if len(raw) != pin["size"] or hashlib.sha256(raw).hexdigest() != pin["sha256"]:
                raise ValueError
            return raw
        except Exception:
            raise Unreachable from None


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


def prerequisites(probe, *, program=True, service=True):
    """Raise ``Failure`` for the first thing that stops setup; return None when all pass."""
    if probe.uid() == 0:
        raise Failure(
            "run_as_root",
            "This command was run as root (or with sudo).",
            "Open a terminal as the normal account that should own the runner (do not type sudo), then open Cloud workspaces in the app and choose Set up again.",
        )
    system, ident, version = probe.system()
    if system != "Linux" or ident not in SUPPORTED or _version(version) < SUPPORTED[ident]:
        found = " ".join(part for part in (system, ident, version) if part) or "an unknown system"
        raise Failure(
            "unsupported_os",
            "This system is not supported (found " + found + ").",
            "Use Ubuntu 24.04 or later, or Debian 12 or later, then open Cloud workspaces in the app and choose Set up again.",
        )
    if tuple(probe.python()) < MIN_PYTHON:
        raise Failure(
            "missing_python",
            "Python 3.10 or later is required.",
            "Install it (sudo apt install python3), then open Cloud workspaces in the app and choose Set up again.",
        )
    if program and not probe.can_import("cryptography"):
        raise Failure(
            "missing_prerequisite",
            "The Python package 'cryptography' is missing.",
            "Install it (sudo apt install python3-cryptography), then open Cloud workspaces in the app and choose Set up again.",
        )
    if program and not probe.can_import("commonplace.runner.installer"):
        raise Failure(
            "missing_runner_program",
            "The runner program is not installed on this machine.",
            "This command does not install it yet. Ask the person who runs this site for the "
            "runner program, then open Cloud workspaces in the app and choose Set up again.",
        )
    if service and not probe.user_manager():
        raise Failure(
            "no_service_manager",
            "This machine has no login session that can run a background service.",
            "Log in on the machine with a normal session (not su or sudo), then open Cloud workspaces "
            "in the app and choose Set up again.",
        )
    if probe.free_bytes(probe.home()) < MIN_FREE_BYTES:
        raise Failure(
            "disk_full",
            "There is less than 256 MB free in your home folder.",
            "Free some space, then open Cloud workspaces in the app and choose Set up again.",
        )


AGAIN = "Open Cloud workspaces in the app and choose Set up again."
# The code is spent and this machine holds no credential, so the app cannot be told: it shows the setup as not
# finished after 15 minutes, and only then offers Set up again.
SPENT_AGAIN = (
    "The app cannot be told about this. After 15 minutes Cloud workspaces shows this setup as not finished;",
    "then open Cloud workspaces in the app and choose Set up again.",
)


def _phase(installer):
    """The phase an interrupted install reached, from its own journal (None before it has one)."""
    return (getattr(installer, "journal", None) or {}).get("phase")


def _deliver_program(encoded, envelope, probe, hub, base, ask, out, *, program_only):
    """Verify program delivery before continuing setup; return a finished status or None."""
    pin = envelope["program"]
    prefix = probe.home() / PROGRAM_ROOT / pin["sha256"]
    cached = prefix.exists() or prefix.is_symlink()
    running_here = not program_only and Path(sys.prefix) == prefix
    # No service, linger, enrollment or credential operation happens at this boundary.
    if program_only or not cached:
        answer = ask()
        if answer is None:
            return 1
        if not answer.get("usable"):
            out(str(answer.get("text", "The code cannot be used.")))
            out(str(answer.get("next", AGAIN)))
            return 1
    try:
        prerequisites(probe, program=False, service=False)
        conflict = _other_site(probe.home(), envelope)
        if conflict:
            for line in conflict:
                out(line)
            return 1
        if cached:
            # Reuse verifies the original pinned archive and every installed file.
            # Keeping the archive allows interrupted enrollment to resume without
            # downloading with a code that was already spent, including when the
            # original launcher starts outside this environment.
            raw = _program_file(prefix, "program.zip")
        else:
            out("Downloading and checking the pinned runner program ...")
            raw = hub.program(base, pin)
        python = install_program(probe.home(), raw, pin)
    except Failure as exc:
        ask({"step": "prerequisites", "state": "failed", "cause": exc.cause})
        out(exc.message)
        out(exc.then)
        return 1
    except (Unreachable, OSError, ValueError, zipfile.BadZipFile):
        ask({"step": "files", "state": "failed", "cause": "program_unavailable"})
        out(
            "The pinned runner program could not be delivered or verified. No setup service was changed."
        )
        out("Ask the site operator to check its program release, then choose Set up again.")
        return 1
    out("Runner program and dependencies installed and checked. Execution remains disabled.")
    if program_only:
        out(
            "Program delivery only: the enrollment code was not spent. No machine was bound or run."
        )
        return 0
    if not running_here:
        # Start the actual delivered program, with the code on its private input pipe.
        # The fixed environment cannot inherit a provider credential or another installation.
        result = subprocess.run(
            [str(python), "-I", "-B", "-m", "commonplace.runner.machine_setup", encoded],
            input=base["code"].encode(),
            env={"PATH": "/usr/bin:/bin", "HOME": str(probe.home()), "LANG": "C"},
        )
        return result.returncode

    return None


def main(argv=None, *, code=None, probe=None, hub=None, make_installer=None, out=print):
    argv = sys.argv[1:] if argv is None else argv
    program_only = len(argv) == 2 and argv[1] == "--program-only"
    try:
        envelope = parse_envelope(argv[0] if len(argv) == 1 or program_only else None)
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

    pin = envelope.get("program")
    if program_only and pin is None:
        out(
            "This command has no pinned runner program. Ask the site operator to configure a release."
        )
        return 1
    if pin:
        delivery_status = _deliver_program(
            argv[0], envelope, probe, hub, base, ask, out, program_only=program_only
        )
        if delivery_status is not None:
            return delivery_status

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
        if failure:
            out(failure.message)
            out(failure.then)
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
    conflict = _other_site(probe.home(), envelope)
    if conflict:
        # Before any question is asked: another site's machine is never touched, nor its credential sent here.
        for line in conflict:
            out(line)
        return 1
    if not probe.linger():
        out(
            "To keep the runner running after you log out, one administrator confirmation is needed."
        )
        if not probe.enable_linger():
            ask({"step": "prerequisites", "state": "failed", "cause": "missing_privilege"})
            out(
                "The administrator confirmation was not given, or the password was wrong, so nothing was installed."
            )
            out(
                "Open Cloud workspaces in the app, choose Set up again, run the new command and type your password "
                "carefully; or ask an administrator to run: sudo loginctl enable-linger "
                + probe.account()
            )
            return 1
    out("Continuing the setup that stopped earlier ..." if resume else "Installing the runner ...")
    return _install(envelope, code, probe, hub, make_installer, out, ask)


def _reason_lines(exc, lines, code):
    """What the window says about why it failed: the installer's last lines and one fixed reason.

    Never the code, a token or an exception's own text (it may quote a path or a value): a fixed installer
    refusal is printed as it is, an operating-system error as its system message, anything else by its kind.
    """
    from_installer = [
        line
        for line in lines[-3:]
        if code not in line
        and "token" not in line.lower()
        and not line.startswith("Machine fingerprint")
        and not line.startswith(
            "Continue in Needs me"
        )  # nothing is waiting there for a machine set up this way
    ]
    if exc is None:
        reason = None
    elif type(exc).__name__ in ("InstallError", "EnrollmentRefused"):
        reason = str(exc)
    elif isinstance(exc, OSError):
        reason = exc.strerror or "an operating-system error"
    else:
        reason = "an unexpected " + type(exc).__name__
    return from_installer + (["Reason: " + reason] if reason else [])


def _earlier_alive(home, envelope, hub):
    """Is the installation here still open on the site? True, False, or None when the site cannot say.

    No credential means it never connected: nothing healthy to protect. A closed or cancelled setup answers
    closed (or its credential no longer works); an open one answers ok. The probe writes nothing.
    """
    token = _token(home, envelope)
    if not token:
        return False
    try:
        answer = hub.status(token, {"step": "probe"})
    except Unreachable:
        return None
    return bool(isinstance(answer, dict) and answer.get("ok"))


def _manifest_label(home):
    try:
        journal = json.loads((Path(home) / ".commonplace-runner" / "manifest.json").read_text())
        return str(journal["intent"]["machine_label"])
    except (OSError, ValueError, KeyError, TypeError):
        return "of an earlier setup"


def _install(envelope, code, probe, hub, make_installer, out, ask):
    home = probe.home()
    lines = []

    def collect(line):
        lines.append(str(line))

    installer = None

    def build():
        if make_installer is not None:
            return make_installer(envelope, collect)
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
        return intent, inst.Installer(home, out=collect)

    try:
        intent, installer = build()
        if _other_installation(home, envelope):
            # Only what Set up again cancelled may be removed: ask the site about the earlier setup first. A healthy
            # approved runner of another pairing is never uninstalled behind its owner's back.
            alive = _earlier_alive(home, envelope, hub)
            if alive is None:
                out(
                    "This machine could not reach the site to check the earlier setup on this account."
                )
                out(
                    "Nothing was changed. Check the internet connection, then run the command again."
                )
                return 1
            if alive:
                out(f"This account already runs machine {_manifest_label(home)}.")
                out(
                    "Remove it under Cloud workspaces first, then run the command again. Nothing was changed."
                )
                return 1
            out("Removing the earlier unfinished setup on this machine ...")
            if installer.uninstall() != 0:
                raise RuntimeError("earlier setup could not be removed")
            intent, installer = (
                build()
            )  # a fresh one: the old object still remembers what it removed
        installer.install(intent, accepted=True, code_source=lambda: code)
    except Exception as exc:
        return _failed(exc, installer, home, hub, out, ask, lines, code, envelope)
    token = _token(home, envelope)
    try:
        state = installer.host.state()
        running = bool(state["enabled"] and state["active"])
    except Exception:
        running = False
    results = [
        ("files", True, None),
        ("service", running, None if running else "The background service is not running."),
    ]
    try:
        installer.verify()
        results.append(("check", True, None))
    except Exception as exc:
        why = _reason_lines(exc, [], code)
        results.append(("check", False, why[-1] if why else "The final check did not pass."))
    causes = {"files": "unknown", "service": "service_failed", "check": "check_failed"}
    # The fingerprint is on the screen before the app can show the last step as done, so the person can compare.
    printed = [line for line in lines if line.startswith("Machine fingerprint:")]
    for line in printed:
        out(line)
    untold = False
    for step, ok, _why in results:
        body = (
            {"step": step, "state": "ok"}
            if ok
            else {"step": step, "state": "failed", "cause": causes[step]}
        )
        if token and not _report(hub, token, body):
            untold = True
            out("Could not tell the app about the " + step + " step. It will show it as waiting.")
    if not all(ok for _, ok, _ in results):
        out("The machine was installed, but one check failed. The app shows which.")
        for _step, ok, why in results:
            if not ok and why:
                out(why)
        out(AGAIN)
        return 1
    if untold:
        out("This machine is set up, but the app could not be told, so it may not show it yet.")
        out("Check the internet connection, then run the command again to report it.")
        return 1
    out("Done on this machine. Go back to Cloud workspaces in the app: it shows this machine and")
    out(
        "asks you to approve it. Compare the fingerprint above with the one on that screen."
        if printed
        else "asks you to approve it. Its fingerprint was printed the first time this command ran."
    )
    return 0


def _failed(exc, installer, home, hub, out, ask, lines, code, envelope):
    """Say what happened and why, tell the app the step it reached, and give the one next step."""
    refusal_types: tuple[type[Exception], ...]
    try:
        from commonplace.runner.installer import EnrollmentRefused

        refusal_types = (EnrollmentRefused,)
    except ImportError:  # pragma: no cover - the program only gets here with the runner program
        refusal_types = ()
    phase, token = _phase(installer), _token(home, envelope)
    if isinstance(exc, refusal_types):
        # The site answered and said no: ask it why, in the app's own words, instead of calling it a lost connection.
        answer = ask()
        if answer is not None:
            out(str(answer.get("text") or "The site did not accept the code."))
            out(str(answer.get("next") or AGAIN))
        return 1
    why = _reason_lines(exc, lines, code)
    if token:
        # The machine's own credential exists: the step it reached is reported with it.
        step, cause = (
            ("service", "service_failed") if phase == "credentials" else ("files", "unknown")
        )
        _report(hub, token, {"step": step, "state": "failed", "cause": cause})
        out("Setup did not finish on this machine.")
        for line in why:
            out(line)
        out(AGAIN)
        return 1
    # Checks passed, so a lost connection at the exchange is the files step, not the pre-flight one.
    if "delivery unavailable" in str(exc):
        report = {"step": "files", "state": "failed", "cause": "cannot_reach_hub"}
    else:
        disk = getattr(exc, "errno", None) == errno.ENOSPC
        report = {"step": "files", "state": "failed", "cause": "disk_full" if disk else "unknown"}
    # Before the code is spent this is shown in the app; after it, the app refuses a report and nothing changes.
    answer = ask(report)
    spent = answer is not None and answer.get("reason") == "used"
    out("Setup did not finish on this machine.")
    if spent:
        out("The one-time code was used, but this machine could not save what it needs.")
    for line in why:
        out(line)
    if spent:
        for line in SPENT_AGAIN:
            out(line)
    else:
        out(AGAIN)
    return 1


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


def _other_installation(home, envelope):
    """A private installation here that belongs to a different enrollment than this command's."""
    path = Path(home) / ".commonplace-runner" / "manifest.json"
    if not path.exists():
        return False
    return not _resumable(home, envelope)


def _other_site(home, envelope):
    """The window's lines when an installation here belongs to another site (or cannot be read), else None."""
    if not (Path(home) / ".commonplace-runner" / "manifest.json").exists():
        return None
    other = _manifest_hub(home)
    if other == str(envelope["hub"]).rstrip("/"):
        return None
    if other is None:
        return [
            "This machine already has a runner installation that this setup cannot read.",
            "Nothing was changed. Remove it on this machine first, then run the command again.",
        ]
    return [
        f"This machine already has a runner set up for another site ({other}).",
        "Nothing was changed. Remove that machine under Cloud workspaces on that site, then run the command again.",
    ]


def _manifest_hub(home):
    """The site the installation here was made for, or None when there is no readable manifest."""
    try:
        journal = json.loads((Path(home) / ".commonplace-runner" / "manifest.json").read_text())
        return str(journal["intent"]["hub_url"]).rstrip("/")
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _token(home, envelope):
    """The machine's own credential, only for the site that issued it: never for another one."""
    if _manifest_hub(home) != str(envelope["hub"]).rstrip("/"):
        return None
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
