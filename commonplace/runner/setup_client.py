"""Signed browser outcomes and private grant receipt; never admits agent work."""

import base64
import json
import secrets
from datetime import datetime, timezone

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import install_receipts
from .install_files import STATE, digest, encoded
from .install_host import InstallError
from .receipt_cache import CACHE

GRANT = STATE + "/host-grant.json"
STATUS = STATE + "/setup-status.json"


class Setup:
    def __init__(self, installer, runtime):
        self.install, self.runtime = installer, runtime
        self.fs = installer.fs
        self.transport = runtime.transport
        self.cache = runtime.cache
        self.token = self.fs.read(STATE + "/machine.token").decode()

    def call(self, path, body=None, nonce=None):
        return self.transport.setup(self.token, path, body, nonce=nonce)

    def _proof(self, row, action, **extra):
        body = {
            "request_id": row["id"],
            "scope_digest": row["scope_digest"],
            "action": action,
            **extra,
        }
        signature = self.install._key().sign(
            install_receipts.canonical(body),
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
            hashes.SHA256(),
        )
        return {**body, "signature": base64.b64encode(signature).decode()}

    def _write(self, path, value):
        raw = encoded(value)
        if self.fs.info(path) is None:
            self.install.file(path, raw)
            return
        item = next(i for i in self.install.journal["items"] if i["path"] == path)
        item["digest"] = digest(raw)
        self.install.flush()
        self.fs.write(path, raw, replace=True)

    def clear(self):
        for path in (GRANT, STATUS):
            if self.fs.info(path) is not None:
                self._write(path, {})
        login = getattr(self.install, "device_login", None)
        if login:
            login.stop()

    def sync(self):
        nonce = secrets.token_hex(16)
        try:
            envelope = self.call("/api/runner/setup-requests", nonce=nonce)
            receipt = envelope["receipt"]
            self.cache.key.verify(
                base64.b64decode(envelope["signature"], validate=True),
                install_receipts.canonical(receipt),
            )
            current = json.loads(self.fs.read(CACHE))["signed"]["receipt"]
            expected = {k: v for k, v in current.items() if k in receipt["scope"]}
            if (
                receipt["scope"] != expected
                or receipt["nonce"] != nonce
                or receipt["purpose"] != "runner-setup"
                or receipt["agent_ready"] is not False
            ):
                raise ValueError
            now = datetime.now(timezone.utc)
            issued, expiry = (_date(receipt[k]) for k in ("issued_at", "expires_at"))
            if not issued <= now < expiry or not 0 < (expiry - issued).total_seconds() <= 60:
                raise ValueError
            scope = receipt["scope"]
            frozen = digest(install_receipts.canonical(scope))
            if frozen != receipt["scope_digest"]:
                raise ValueError
            from .device_login import DeviceLogin

            if not hasattr(self.install, "device_login"):
                self.install.device_login = DeviceLogin()
            if not any(
                r["kind"] == "login" and r["provider"] == "codex" and r["state"] == "approved"
                for r in receipt["requests"]
            ):
                self.install.device_login.stop()
            status = {
                "agent_ready": False,
                "fingerprint": scope["machine_key_digest"],
                "providers": {},
                "grant": "approval-needed",
                "attestation": "approval-needed",
            }
            for row in receipt["requests"]:
                if (
                    row["scope_digest"] != frozen
                    or row["machine_id"] != scope["machine_id"]
                    or _date(row["expires_at"]) <= now
                ):
                    raise ValueError
                if row["kind"] == "login":
                    provider = row["provider"]
                    status["providers"][provider] = row["state"]
                    if provider == "claude":
                        fact = self.runtime.read()["components"][provider]
                        if fact["login"] == "unverified" and row["state"] != "succeeded":
                            self.call(
                                "/api/runner/setup-requests", self._proof(row, "login_succeeded")
                            )
                            status["providers"][provider] = "succeeded"
                        elif fact["login"] != "unverified" and row["state"] == "succeeded":
                            self.call(
                                "/api/runner/setup-requests", self._proof(row, "login_needed")
                            )
                            status["providers"][provider] = "pending"
                    if provider == "codex" and row["state"] == "approved":
                        self.install.device_login.advance(self, row, scope)
                elif row["kind"] == "grant":
                    if row["state"] == "approved":
                        self._grant(row, scope)
                        status["grant"] = "received"
                    elif row["state"] == "succeeded":
                        self._check_grant(row, scope)
                        status["grant"] = "received"
                elif row["kind"] == "attest":
                    status["attestation"] = row["state"]
            if status["grant"] != "received" and self.fs.info(GRANT) is not None:
                self._write(GRANT, {})
            self._write(STATUS, status)
            return status
        except Exception:
            self.clear()
            raise InstallError("browser setup incomplete; owner review required") from None

    def _check_grant(self, row, scope):
        value = json.loads(self.fs.read(GRANT))
        signed = value["signed"]
        self.cache.key.verify(
            base64.b64decode(signed["signature"], validate=True),
            install_receipts.canonical(signed["receipt"]),
        )
        r = signed["receipt"]
        if (
            r["scope"] != scope
            or r["request_id"] != row["id"]
            or r["scope_digest"] != row["scope_digest"]
            or r["purpose"] != "runner-host-grant"
            or r["agent_ready"] is not False
            or _date(r["expires_at"]) <= datetime.now(timezone.utc)
        ):
            raise ValueError
        if (
            not isinstance(value["secret"], str)
            or not 32 <= len(value["secret"]) <= 128
            or digest(value["secret"].encode()) != r["secret_hash"]
        ):
            raise ValueError

    def _grant(self, row, scope):
        # Durable receipt before ack; a dropped ack converges without issuing a secret.
        envelope = self.call("/api/runner/secret-delivery", self._proof(row, "fetch"))
        expected = install_receipts.canonical(
            {"request_id": row["id"], "scope_digest": row["scope_digest"]}
        ).decode()
        if envelope["version"] != 1 or envelope["context"] != expected:
            raise ValueError
        key = self.install._key().decrypt(
            base64.b64decode(envelope["wrapped_key"], validate=True),
            padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
        )
        value = json.loads(
            AESGCM(key).decrypt(
                base64.b64decode(envelope["nonce"], validate=True),
                base64.b64decode(envelope["ciphertext"], validate=True),
                expected.encode(),
            )
        )
        # Authenticate and match before storing any plaintext credential.
        signed = value["signed"]
        self.cache.key.verify(
            base64.b64decode(signed["signature"], validate=True),
            install_receipts.canonical(signed["receipt"]),
        )
        r = signed["receipt"]
        if (
            r["scope"] != scope
            or r["request_id"] != row["id"]
            or r["scope_digest"] != row["scope_digest"]
            or r["purpose"] != "runner-host-grant"
            or r["agent_ready"] is not False
            or _date(r["expires_at"]) <= datetime.now(timezone.utc)
        ):
            raise ValueError
        if (
            set(value) != {"secret", "signed"}
            or not isinstance(value["secret"], str)
            or not 32 <= len(value["secret"]) <= 128
            or digest(value["secret"].encode()) != r["secret_hash"]
        ):
            raise ValueError
        self._write(GRANT, value)
        self._check_grant(row, scope)
        self.call("/api/runner/secret-delivery", self._proof(row, "ack"))


def _date(value):
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
