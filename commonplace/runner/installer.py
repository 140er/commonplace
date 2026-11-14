"""Recoverable enrollment and a setup-only user service. No work admission here."""

import base64
import fcntl
import json
import os
import re
import secrets
import signal
import sys
import threading
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import enrollment_crypto
from .install_files import MANIFEST, STATE, Files, digest, encoded
from .install_host import UNIT, InstallError, LiveHost
from .receipt_cache import APPROVAL_KEY, CACHE

RUNTIME_CACHE = STATE + "/runtime-config.json"

UNIT_PATH = ".config/systemd/user/" + UNIT
KEY = STATE + "/machine-key.pem"
DELIVERY = STATE + "/delivery.json"
CONFIG = STATE + "/config.json"
LOCK = STATE + "/install.lock"
SIGN_IN = STATE + "/sign-in"
FILES = {
    STATE + "/lifecycle.json",
    STATE + "/lifecycle.lock",
    SIGN_IN,
    APPROVAL_KEY,
    CACHE,
    KEY,
    DELIVERY,
    CONFIG,
    LOCK,
    STATE + "/host-grant.json",
    STATE + "/setup-status.json",
    UNIT_PATH,
    STATE + "/machine.token",
    STATE + "/worker.token",
    STATE + "/runner.pyz",
}
DIRS = {STATE, ".config", ".config/systemd", ".config/systemd/user"}
EXCHANGE = "/api/runner-enrollment/exchange"
RECOVER = "/api/runner-enrollment/recover"


class Transport:
    """TLS only, no redirects, cookies, bearer credentials or environment proxies."""

    def __init__(self, origin):
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise InstallError("an authenticated HTTPS origin is required")
        self.origin = origin.rstrip("/")
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def post(self, route, body):
        if route not in (EXCHANGE, RECOVER):
            raise InstallError("unsupported enrollment operation")
        request = urllib.request.Request(
            self.origin + route,
            data=encoded(body),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self.opener.open(request, timeout=30) as response:
                raw = response.read(32769)
                if len(raw) > 32768:
                    raise ValueError
                return json.loads(raw)
        except Exception:
            raise InstallError(
                "enrollment delivery unavailable; retry with the saved identity"
            ) from None

    def runtime_status(self, token, body):
        from .runtime_status import PATH

        if not token or any(c in token for c in "\r\n"):
            raise InstallError("invalid machine credential")
        request = urllib.request.Request(
            self.origin + PATH,
            data=encoded(body),
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self.opener.open(request, timeout=15) as response:
                result = json.loads(response.read(1025))
                if result != {"ok": True, "agent_ready": False}:
                    raise ValueError
                return result
        except Exception:
            raise InstallError("runtime status unavailable") from None

    def lifecycle(self, body):
        request = urllib.request.Request(
            self.origin + RECOVER,
            data=encoded(body),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self.opener.open(request, timeout=1) as response:
                raw = response.read(8193)
                if len(raw) > 8192:
                    raise ValueError
                return json.loads(raw)
        except Exception:
            raise InstallError("lifecycle unavailable") from None

    def setup(self, token, route, body=None, *, nonce=None):
        from .setup_approvals import MACHINE_PATHS

        if route not in MACHINE_PATHS or not token or any(c in token for c in "\r\n"):
            raise InstallError("invalid setup operation")
        if nonce is not None and not re.fullmatch(r"[0-9a-f]{32}", nonce):
            raise InstallError("invalid setup challenge")
        request = urllib.request.Request(
            self.origin + route + ("?nonce=" + nonce if nonce else ""),
            data=encoded(body) if body is not None else None,
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
            method="GET" if nonce else "POST",
        )
        try:
            with self.opener.open(request, timeout=15) as response:
                raw = response.read(65537)
                if len(raw) > 65536:
                    raise ValueError
                return json.loads(raw)
        except Exception:
            raise InstallError("browser setup unavailable") from None

    def receipt(self, token, nonce):
        from .install_receipts import PATH

        if not token or any(c in token for c in "\r\n"):
            raise InstallError("invalid machine credential")
        request = urllib.request.Request(
            self.origin + PATH + "?nonce=" + nonce,
            headers={"Authorization": "Bearer " + token},
        )
        try:
            with self.opener.open(request, timeout=15) as response:
                raw = response.read(65537)
                if len(raw) > 65536:
                    raise ValueError
                return json.loads(raw)
        except Exception:
            raise InstallError("approved binding unavailable; owner review required") from None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise InstallError("enrollment redirect refused")


def intent_from(args):
    intent = {
        "hub_url": args.hub,
        "workspace_id": args.workspace,
        "machine_label": args.label,
        "manifest_digest": args.manifest_digest,
        "enrollment_id": args.enrollment_id,
    }
    Transport(args.hub)  # Validate even when a test supplies a transport.
    if (
        not re.fullmatch(r"[0-9a-f]{64}", args.manifest_digest or "")
        or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,39}", args.label or "")
        or any(
            not isinstance(intent[k], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", intent[k])
            for k in ("workspace_id", "enrollment_id")
        )
    ):
        raise InstallError("invalid installation intent")
    return intent


def service_text(home, archive=False):
    # Quote systemd arguments, including its separate percent expansion.
    def quote(value):
        return (
            '"'
            + str(value)
            .replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("%", "%%")
            .replace("$", "$$")
            + '"'
        )

    command = [sys.executable, "-I"]
    if archive:
        command += [
            str(home / STATE / "runner.pyz"),
            "runner",
            "_setup-service",
            "--home",
            str(home),
        ]
    else:
        command += ["-m", "commonplace.runner.installer", "--serve", str(home)]
    from .supervisor import command as setup_command

    cleanup = setup_command(home, "_setup-stopped")
    return (
        "[Unit]\nDescription=Commonplace runner setup (execution disabled)\n\n"
        "[Service]\nType=simple\nExecStart=" + " ".join(map(quote, command)) + "\n"
        "ExecStopPost=" + " ".join(map(quote, cleanup)) + "\n"
        "UMask=0077\nNoNewPrivileges=yes\nRestart=no\nKillMode=control-group\n"
        "TimeoutStopSec=1\nSendSIGKILL=yes\n"
        "StandardOutput=null\nStandardError=null\n\n[Install]\nWantedBy=default.target\n"
    ).encode()


class Installer:
    def __init__(self, home, host=None, transport=None, *, out=print, runtime_host=None):
        self.fs = Files(home)
        self.host = host if host is not None else LiveHost()
        self.transport = transport
        self.out = out
        self.journal = None
        self.runtime_host = runtime_host

    def load(self):
        if self.fs.info(MANIFEST) is None:
            return False
        document = json.loads(self.fs.read(MANIFEST))
        if (
            not isinstance(document, dict)
            or document.get("version") != 1
            or document.get("phase")
            not in ("prepared", "exchanging", "delivered", "credentials", "installed", "removing")
            or not isinstance(document.get("items"), list)
        ):
            raise InstallError("installation manifest is invalid")
        from .runtime_install import owned

        seen = set()
        for item in document["items"]:
            path, kind = item["path"], item["kind"]
            if (
                path in seen
                or (kind == "file" and path not in FILES and not owned(path, kind))
                or (kind == "dir" and path not in DIRS and not owned(path, kind))
                or kind not in ("file", "dir")
            ):
                raise InstallError("installation manifest is invalid")
            seen.add(path)
        self.journal = document
        return True

    def flush(self):
        self.fs.write(MANIFEST, encoded(self.journal), replace=self.fs.info(MANIFEST) is not None)

    def record(self, item):
        self.journal["items"].append(item)
        self.flush()  # intent is durable before each owned artifact or side effect

    def file(self, path, data):
        item = next((i for i in self.journal["items"] if i["path"] == path), None)
        sha = digest(data)
        if item is None:
            if self.fs.info(path) is not None:
                raise InstallError("installation path is already occupied")
            self.record({"path": path, "kind": "file", "digest": sha})
        elif item["digest"] != sha:
            raise InstallError("installation settings changed; uninstall first")
        if self.fs.info(path) is None:
            self.fs.write(path, data)
        elif digest(self.fs.read(path)) != sha:
            raise InstallError("an owned installation file changed")

    def directory(self, path):
        if self.fs.info(path) is not None:
            self.fs._check(self.fs.info(path), directory=True)
            return
        if not any(i["path"] == path for i in self.journal["items"]):
            self.record({"path": path, "kind": "dir"})
        self.fs.mkdir(path)

    @contextmanager
    def locked(self):
        if self.fs.info(LOCK) is None:
            if not any(i["path"] == LOCK for i in self.journal["items"]):
                self.record({"path": LOCK, "kind": "file", "digest": digest(b"")})
            self.fs.write(LOCK, b"")
        with self.fs.parent(LOCK) as (parent, name):
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                self.fs._check(os.fstat(fd))
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise InstallError("another installation operation is running") from None
                yield
            finally:
                os.close(fd)

    def plan(self):
        for action in (
            "Preflight Linux user manager, service collisions and password-free linger permission.",
            "Accept once; read the one-time code from protected stdin, never command arguments.",
            "Create owned private directories (0700), lock and phase manifest (0600).",
            "Persist the installation identity and machine key (0600) before redeeming the code.",
            "Exchange once over HTTPS; on interruption recover the same encrypted delivery by signed key proof.",
            "Write separate machine and worker credential files and setup-only config (0600).",
            "Acknowledge durable delivery through signed key proof.",
            "Optionally verify and copy a signed runner archive (0600); keep the trusted release key pinned.",
            "Record original linger state; enable linger only if absent.",
            "Create the owned setup-only user unit (0600), reload, enable and start it.",
            "Verify private artifacts, linger, enabled unit and active setup-only service.",
            "On failure stop the owned unit before rollback; retain only recoverable enrollment state.",
            "Uninstall stops the owned unit, removes unchanged owned files and empty owned directories;",
            "restore linger only if enabled here and no other user service depends on it.",
        ):
            self.out(action)
        for action in (
            "Verify signed runtime choices; locate selected compatible components only.",
            "Download only approved version/digest pins; verify all before execution or publication.",
            "Write owned 0600 artifacts and runtime config cache in private 0700 directories.",
            "Derive provider/topic/project mappings; report sign-in needed for browser approval.",
            "Fetch signed browser decisions; complete supported device sign-in without terminal input.",
            "Receive the sealed scoped grant into a private 0600 file; compare the machine fingerprint in the browser.",
        ):
            self.out(action)
        return 0

    def _preflight_files(self):
        for path in (*sorted(DIRS), *sorted(FILES)):
            info = self.fs.info(path)
            if info is None:
                continue
            self.fs._check(info, directory=path in DIRS)
            if path in FILES and path != LOCK:
                if not self.journal or not any(i["path"] == path for i in self.journal["items"]):
                    raise InstallError("installation path is already occupied")
        if not self.journal and self.fs.info(STATE) is not None:
            if not self.fs.empty(STATE):
                raise InstallError("unmanaged installation directory is occupied")

    def _key(self):
        return serialization.load_pem_private_key(self.fs.read(KEY), password=None)

    def _identity(self):
        key = self._key()
        pem = (
            key.public_key()
            .public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            .decode()
        )
        return {
            "workspace_id": self.journal["intent"]["workspace_id"],
            "enrollment_id": self.journal["intent"]["enrollment_id"],
            "installation_id": self.journal["installation_id"],
            "public_key": pem,
        }

    def _proof(self, action):
        identity = self._identity()
        reply = self.transport.post(RECOVER, {**identity, "action": "challenge"})
        challenge = reply["challenge"]
        if not isinstance(challenge, str) or len(challenge) > 512:
            raise InstallError("invalid recovery challenge")
        signature = self._key().sign(
            (action + ":" + challenge).encode(),
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
            hashes.SHA256(),
        )
        return self.transport.post(
            RECOVER,
            {
                **identity,
                "action": action,
                "challenge": challenge,
                "signature": base64.b64encode(signature).decode(),
            },
        )

    def _credentials(self, envelope):
        key = self._key()
        context = json.loads(envelope["context"])
        _, _, fingerprint = enrollment_crypto.public_key(self._identity()["public_key"])
        expected = {k: v for k, v in self.journal["intent"].items() if k != "hub_url"}
        expected.update(
            version=1,
            installation_id=self.journal["installation_id"],
            machine_key_digest=fingerprint,
            machine_id=context["machine_id"],
        )
        if envelope["version"] != 1 or context != expected:
            raise InstallError("credential delivery does not match installation")
        secret = key.decrypt(
            base64.b64decode(envelope["wrapped_key"], validate=True),
            padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
        )
        credentials = json.loads(
            AESGCM(secret).decrypt(
                base64.b64decode(envelope["nonce"], validate=True),
                base64.b64decode(envelope["ciphertext"], validate=True),
                envelope["context"].encode(),
            )
        )
        if (
            credentials.get("machine_id") != context["machine_id"]
            or not re.fullmatch(r"mach_[0-9a-f]{16}", credentials["machine_id"])
            or any(
                not isinstance(credentials.get(k + "_token"), str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{16,512}", credentials[k + "_token"])
                for k in ("machine", "worker")
            )
            or credentials["machine_token"] == credentials["worker_token"]
        ):
            raise InstallError("invalid credential delivery")
        return credentials

    def install(self, intent, *, accepted=False, code_source=None, release=None, approval_key=None):
        if not accepted:
            return self.plan()
        if approval_key is not None:
            from .receipt_cache import public_key

            public_key(approval_key)
        self.host.preflight()
        self.load()
        self._preflight_files()
        if self.journal and self.journal["intent"] != intent:
            raise InstallError("installation intent changed; uninstall first")
        archive = None
        if release is not None:
            from .bootstrap import verified_release

            archive = verified_release(*release)
        if not self.journal:
            original = self.host.state()
            if original["enabled"] or original["active"]:
                raise InstallError("user service name is already occupied")
            if self.fs.info(STATE) is None:
                self.fs.mkdir(STATE)
            self.journal = {
                "version": 1,
                "intent": intent,
                "installation_id": "install_" + secrets.token_hex(16),
                "phase": "prepared",
                "original": original,
                "service_pending": False,
                "linger_pending": False,
                "items": [{"kind": "dir", "path": STATE}],
            }
            self.flush()
        with self.locked():
            self.load()
            self.file(LOCK, b"")
            if self.journal["phase"] == "installed":
                self.verify(check_service=False)
                state = self.host.state()
                if not state["linger"]:
                    self.journal["linger_pending"] = True
                    self.flush()
                    self.host.change("enable-linger")
                if not state["enabled"] or not state["active"]:
                    self.host.change("enable")
                self.verify()
                self.out("already installed; service verified")
                return 0
            if self.journal["phase"] == "removing":
                raise InstallError("uninstall is incomplete; retry uninstall")
            self.transport = self.transport or Transport(intent["hub_url"])
            try:
                if self.journal["phase"] == "prepared":
                    if self.fs.info(KEY) is None:
                        key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
                        raw = key.private_bytes(
                            serialization.Encoding.PEM,
                            serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption(),
                        )
                        # An interrupted key write can safely be regenerated before exchange.
                        self.journal["items"] = [
                            i for i in self.journal["items"] if i["path"] != KEY
                        ]
                        self.file(KEY, raw)
                    code = code_source() if code_source else ""
                    if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", code):
                        raise InstallError("a one-time code on protected stdin is required")
                    identity = self._identity()
                    self.journal["phase"] = "exchanging"
                    self.flush()
                    envelope = self.transport.post(
                        EXCHANGE,
                        {k: v for k, v in identity.items() if k != "enrollment_id"}
                        | {
                            "machine_label": intent["machine_label"],
                            "manifest_digest": intent["manifest_digest"],
                            "code": code,
                        },
                    )
                    code = None
                elif self.journal["phase"] == "exchanging" and self.fs.info(DELIVERY) is None:
                    envelope = self._proof("recover")
                else:
                    envelope = json.loads(self.fs.read(DELIVERY))
                credentials = self._credentials(envelope)
                self.file(DELIVERY, encoded(envelope))
                self.journal["phase"] = "delivered"
                self.flush()
                for kind in ("machine", "worker"):
                    self.file(STATE + "/" + kind + ".token", credentials[kind + "_token"].encode())
                config = {
                    "version": 1,
                    "mode": "setup-only",
                    "agent_ready": False,
                    "hub_url": intent["hub_url"],
                    "machine_id": credentials["machine_id"],
                    "worker_connection_id": credentials["worker_connection_id"],
                    "installation_id": self.journal["installation_id"],
                    "machine_token_file": "machine.token",
                    "worker_token_file": "worker.token",
                    "runtime_cache_file": "runtime-config.json",
                }
                self.file(CONFIG, encoded(config))
                if approval_key is not None:
                    self.file(APPROVAL_KEY, approval_key)
                    self.file(CACHE, encoded({}))
                self.journal["phase"] = "credentials"
                self.flush()
                try:
                    self._proof("ack")
                except Exception:
                    pass  # durable local delivery is complete; expiry also purges the relay
                if archive is not None:
                    self.file(STATE + "/runner.pyz", archive)
                for directory in (".config", ".config/systemd", ".config/systemd/user"):
                    self.directory(directory)
                self.file(
                    UNIT_PATH,
                    service_text(self.fs.home, self.fs.info(STATE + "/runner.pyz") is not None),
                )
                if not self.host.state()["linger"]:
                    self.journal["linger_pending"] = True
                    self.flush()
                    self.host.change("enable-linger")
                self.journal["service_pending"] = True
                self.flush()
                self.host.change("reload")
                self.host.change("enable")
                self.verify()
                self.journal["phase"] = "installed"
                self.flush()
            except BaseException:
                self._rollback_service()
                self._remove_unit()
                raise
        _, _, fingerprint = enrollment_crypto.public_key(self._identity()["public_key"])
        self.out("Machine fingerprint: " + " ".join(re.findall(".{8}", fingerprint)))
        self.out(
            "Continue in Needs me to approve sign-in, machine access and the matching fingerprint."
        )
        self.out("installed; setup-only; execution disabled")
        return 0

    def _remove_unit(self):
        item = next((i for i in self.journal["items"] if i["path"] == UNIT_PATH), None)
        if item is not None:
            if self.fs.info(UNIT_PATH) is not None:
                if digest(self.fs.read(UNIT_PATH)) != item["digest"]:
                    raise InstallError("owned unit changed; retained for recovery")
                self.fs.remove(UNIT_PATH)
            self.journal["items"].remove(item)
            self.flush()
            self.host.change("reload")

    def _rollback_service(self):
        if self.journal["service_pending"]:
            self.host.change("disable")  # stop before discarding anything
            if self.host.state()["active"] or self.host.state()["enabled"]:
                raise InstallError("service stop unconfirmed; private artifacts retained")
            self.journal["service_pending"] = False
            self.flush()
        if self.journal["linger_pending"] and not self.journal["original"]["linger"]:
            if not self.host.has_other_services():
                self.host.change("disable-linger")
                self.journal["linger_pending"] = False
                self.flush()

    def verify(self, *, check_service=True):
        for item in self.journal["items"]:
            info = self.fs.info(item["path"])
            if info is None:
                raise InstallError("installation artifact is missing")
            self.fs._check(info, directory=item["kind"] == "dir")
            if (
                item["kind"] == "file"
                and item["path"]
                not in (
                    CACHE,
                    RUNTIME_CACHE,
                    STATE + "/host-grant.json",
                    STATE + "/setup-status.json",
                    STATE + "/lifecycle.json",
                )
                and digest(self.fs.read(item["path"])) != item["digest"]
            ):
                raise InstallError("an owned installation file changed")
        config = json.loads(self.fs.read(CONFIG))
        if config.get("mode") != "setup-only" or config.get("agent_ready") is not False:
            raise InstallError("setup-only configuration is required")
        if check_service and not all(self.host.state().values()):
            raise InstallError("setup service verification failed")

    def status(self):
        if not self.load():
            self.out("not installed")
            return 0
        phase = self.journal["phase"]
        lifecycle = {}
        if self.fs.info(STATE + "/lifecycle.json") is not None:
            lifecycle = json.loads(self.fs.read(STATE + "/lifecycle.json"))
        if phase == "installed":
            self.verify(check_service=not lifecycle.get("stopped"))
        if lifecycle.get("stopped"):
            self.out("Runner service: Stopped; execution disabled")
        if self.fs.info(RUNTIME_CACHE) is not None:
            from .runtime_install import Runtime

            try:
                config = json.loads(self.fs.read(CONFIG))
                runtime = Runtime(self, config, self.transport).read()
                for name, fact in sorted(runtime["components"].items()):
                    self.out(name + ": " + fact["availability"] + "; " + fact["login"])
            except Exception:
                self.out("runtime cache unavailable; fresh approved settings required")
        if self.fs.info(STATE + "/setup-status.json") is not None:
            state = json.loads(self.fs.read(STATE + "/setup-status.json"))
            if state.get("fingerprint"):
                self.out(
                    "Machine fingerprint: " + " ".join(re.findall(".{8}", state["fingerprint"]))
                )
                for provider, outcome in state.get("providers", {}).items():
                    self.out(provider + ": " + outcome)
                self.out("Machine access: " + state.get("grant", "approval-needed"))
        self.out("installation phase: " + phase + "; execution disabled")
        return 0

    def sign_in(self, provider, *, supervised=False):
        from .runtime_install import HOME, Runtime

        if provider != "claude":
            raise InstallError("unsupported terminal sign-in")
        if not self.load() or self.journal["phase"] != "installed":
            raise InstallError("installation incomplete")
        if not supervised:
            from .supervisor import command

            return self.host.terminal(
                [*command(self.fs.home, "_setup-sign-in"), provider],
                self.journal["installation_id"],
                run=lambda: self.sign_in(provider, supervised=True),
            )
        if not self.host.owns_terminal(self.journal["installation_id"]):
            raise InstallError("supervised terminal sign-in is required")
        self.sync_receipts()  # Live authority and signed scope before the terminal step.
        with self.locked():
            self.load()
            config = json.loads(self.fs.read(CONFIG))
            runtime = Runtime(self, config, self.transport, self.runtime_host)
            scope = runtime._receipt()
            component = runtime.read()["components"][provider]
            raw = (
                self.fs.read(component["path"])
                if not os.path.isabs(component["path"])
                else runtime.host.locate(provider)["raw"]
            )
            fact = runtime.host.probe(
                provider,
                scope["runtime"]["components"][provider],
                raw,
                self.fs.home / HOME,
                sign_in=True,
            )
            if fact["availability"] != "present" or fact["login"] != "unverified":
                raise InstallError("subscription sign-in is still needed")
        self.sync_receipts()  # Only a new CLI probe and signed machine fact resolve the card.
        return 0

    def sync_receipts(self):
        from .receipt_cache import Cache

        if not self.load() or self.journal["phase"] != "installed":
            raise InstallError("installation is incomplete")
        with self.locked():
            self.load()
            self.verify(check_service=False)
            config = json.loads(self.fs.read(CONFIG))
            transport = self.transport or Transport(config["hub_url"])
            from .runtime_install import Runtime

            runtime = None
            try:
                approved = Cache(self.fs, self.journal, config, transport).sync()
                if "runtime" in approved:
                    runtime = Runtime(self, config, transport, self.runtime_host)
                    runtime.sync()
                    if hasattr(transport, "setup"):
                        import shlex

                        from .setup_client import Setup

                        command = (
                            [sys.executable, "-I", str(self.fs.home / STATE / "runner.pyz")]
                            if self.fs.info(STATE + "/runner.pyz") is not None
                            else [sys.executable, "-m", "commonplace"]
                        )
                        self.file(
                            SIGN_IN,
                            (
                                "#!/bin/sh\nexec " + shlex.join(command) + ' runner sign-in "$@"\n'
                            ).encode(),
                        )
                        Setup(self, runtime).sync()
                elif self.fs.info(RUNTIME_CACHE) is not None:
                    Runtime(self, config, transport).clear()
                return approved
            except Exception:
                from .setup_client import Setup

                login = getattr(self, "device_login", None)
                if login:
                    login.stop()
                if self.fs.info(STATE + "/host-grant.json") is not None:
                    Setup(self, runtime or Runtime(self, config, transport)).clear()
                if self.fs.info(RUNTIME_CACHE) is not None:
                    (runtime or Runtime(self, config, transport)).clear()
                raise

    def uninstall(self):
        if not self.load():
            self.out("not installed; no changes")
            return 0
        with self.locked():
            self.load()
            self.journal["phase"] = "removing"
            self.flush()
            self._rollback_service()
            # Validate every file before the first discard; changed files are retained.
            for item in self.journal["items"]:
                if item["kind"] == "file" and self.fs.info(item["path"]) is not None:
                    if (
                        item["path"]
                        not in (
                            CACHE,
                            RUNTIME_CACHE,
                            STATE + "/host-grant.json",
                            STATE + "/setup-status.json",
                            STATE + "/lifecycle.json",
                        )
                        and digest(self.fs.read(item["path"])) != item["digest"]
                    ):
                        raise InstallError("an owned installation file changed; uninstall refused")
            from .device_login import cleanup

            cleanup(self.fs, self.journal)
            for item in reversed(list(self.journal["items"])):
                path = item["path"]
                if path in (STATE, LOCK):
                    continue
                if self.fs.info(path) is not None:
                    if item["kind"] == "dir":
                        if not self.fs.empty(path):
                            continue  # preserve unrelated files and their containing directories
                        self.fs.remove(path, directory=True)
                    else:
                        self.fs.remove(path)
                self.journal["items"].remove(item)
                self.flush()
            self.host.change("reload")
            self.fs.remove(MANIFEST)
            self.fs.remove(LOCK)
            if self.fs.empty(STATE):
                self.fs.remove(STATE, directory=True)
        self.out("uninstalled; remote credentials remain subject to owner revocation")
        return 0


def sync_worker(home):
    """Sync approved settings when a trust pin exists; never lease or launch work."""
    fs = Files(home)
    config = json.loads(fs.read(CONFIG))
    if config.get("mode") != "setup-only" or config.get("agent_ready") is not False:
        raise InstallError("setup-only configuration is required")
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    if fs.info(APPROVAL_KEY) is None:
        stop.wait()
        return 0
    service = Installer(home, host=object())
    while not stop.is_set():
        try:
            service.sync_receipts()
        except Exception:
            pass  # setup remains incomplete; no raw error enters service output
        stop.wait(2)
    login = getattr(service, "device_login", None)
    if login:
        login.stop()
    return 0


def serve(home):
    from .supervisor import serve as supervised

    return supervised(home)


def run(args, *, host=None, transport=None, code_source=None):
    try:
        if args.action == "_setup-service":
            return serve(args.home)
        if args.action == "_setup-sync":
            return sync_worker(args.home)
        if args.action == "_setup-stopped":
            from .supervisor import stopped

            return stopped(args.home)
        if args.action == "_setup-sign-in":
            return Installer(args.home, host, transport).sign_in(args.provider, supervised=True)
        if args.action == "stop":
            from .supervisor import Channel, bounded, stopped

            installer = Installer(args.home, host, transport)
            try:
                channel = Channel(installer)
                bounded(lambda: channel.exchange("revoke"))
            except Exception:
                pass
            finally:
                installer.host.change("disable")
            return stopped(args.home, transport=transport, host=installer.host)
        installer = Installer(args.home, host, transport)
        if args.action == "status":
            return installer.status()
        if args.action == "sign-in":
            return installer.sign_in(args.provider)
        if args.action == "uninstall":
            return installer.uninstall()
        release = (args.release, args.release_signature, args.release_key)
        if any(release) and not all(release):
            raise InstallError("release archive, signature and trusted key are required together")
        return installer.install(
            intent_from(args),
            accepted=args.accept and not args.plan,
            code_source=code_source or (lambda: sys.stdin.read(128).strip()),
            release=release if all(release) else None,
            approval_key=(
                Path(args.approval_key).read_bytes()
                if args.approval_key and args.accept and not args.plan
                else None
            ),
        )
    except Exception:
        # Neither remote errors nor parsing/path/library errors may reflect a credential.
        print(
            "runner install: operation refused; inspect private state and retry or uninstall",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--serve":
        try:
            raise SystemExit(serve(sys.argv[2]))
        except Exception:
            raise SystemExit(1) from None
    raise SystemExit(1)
