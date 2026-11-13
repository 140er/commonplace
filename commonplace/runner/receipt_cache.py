"""Verify a fresh signed binding before deriving compatibility acknowledgements.

The trust key comes from installation, never a hub response. A disk cache is
not authority: every sync uses a new challenge and clears it on any refusal.
"""

import base64
import hashlib
import json
import secrets
from datetime import datetime, timezone

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .install_files import STATE, encoded
from .install_host import InstallError
from .install_receipts import canonical

CACHE = STATE + "/approved-binding.json"
APPROVAL_KEY = STATE + "/approval-key.pem"
MODES = ("approval-required", "auto-accept-edits", "auto", "full-access")


def public_key(raw):
    key = serialization.load_pem_public_key(raw)
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("invalid approval key")
    return key


def verified(envelope, key, identity, *, nonce=None, now=None, fresh=True):
    if not isinstance(envelope, dict) or set(envelope) != {"receipt", "signature"}:
        raise ValueError("invalid receipt")
    receipt = envelope["receipt"]
    key.verify(base64.b64decode(envelope["signature"], validate=True), canonical(receipt))
    if not isinstance(receipt, dict) or any(receipt.get(k) != v for k, v in identity.items()):
        raise ValueError("wrong receipt identity")
    if nonce is not None and receipt.get("nonce") != nonce:
        raise ValueError("stale receipt challenge")
    if receipt.get("version") != 1 or receipt.get("agent_ready") is not False:
        raise ValueError("unsupported receipt")
    current = now or datetime.now(timezone.utc)
    issued = datetime.strptime(receipt["issued_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    expires = datetime.strptime(receipt["expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    if (fresh and not issued <= current < expires) or not 0 < (
        expires - issued
    ).total_seconds() <= 60:
        raise ValueError("expired receipt")
    for name in ("grant_version", "binding_epoch", "settings_version", "install_settings_version"):
        if type(receipt.get(name)) is not int or receipt[name] < 1:
            raise ValueError("invalid receipt version")
    if not receipt.get("provider_instance_id") or not receipt.get("worker_connection_id"):
        raise ValueError("binding missing")
    compatibility(receipt)  # Validate the supported compatibility contract too.
    return receipt


def compatibility(receipt):
    projects, topics = receipt["projects"], receipt["topics"]
    if not isinstance(projects, dict) or not projects or not isinstance(topics, list) or not topics:
        raise ValueError("empty approved scope")
    ceiling = receipt["max_runtime_mode"]
    if ceiling not in MODES or set(projects) != set(receipt["project_labels"]):
        raise ValueError("invalid approved scope")
    result = {}
    for label, project in projects.items():
        mode = project["runtime_ceiling"]
        if (
            project["checkout"] != "shared"
            or mode not in MODES
            or MODES.index(mode) > MODES.index(ceiling)
        ):
            raise ValueError("unsupported approved project")
        result[label] = {
            "project_id": project["project_id"],
            "shared_checkout_ack": label + ":shared-checkout",
            "runtime_ceiling": mode,
            "runtime_ack": label + ":" + mode,
        }
    config = {
        "projects": result,
        "topics": topics,
        "wings": receipt["wings"],
        "binding_acks": [label + ":" + topic for label in projects for topic in topics],
        "provider_instance_id": receipt["provider_instance_id"],
        "worker_connection_id": receipt["worker_connection_id"],
        "max_runtime_mode": ceiling,
        "grant_version": receipt["grant_version"],
        "binding_epoch": receipt["binding_epoch"],
        "settings_version": receipt["settings_version"],
        "agent_ready": False,
    }

    if "runtime" in receipt:
        from .runtime_manifest import validate

        config["runtime"] = validate(receipt["runtime"], topics)
    return config


class Cache:
    def __init__(self, fs, journal, config, transport):
        self.fs, self.transport = fs, transport
        self.key = public_key(fs.read(APPROVAL_KEY))
        private = serialization.load_pem_private_key(
            fs.read(STATE + "/machine-key.pem"), password=None
        )
        pem = private.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        self.identity = {
            "workspace_id": journal["intent"]["workspace_id"],
            "machine_id": config["machine_id"],
            "installation_id": config["installation_id"],
            "enrollment_id": journal["intent"]["enrollment_id"],
            "manifest_digest": journal["intent"]["manifest_digest"],
            "machine_key_digest": hashlib.sha256(pem).hexdigest(),
            "worker_connection_id": config["worker_connection_id"],
        }

    def read(self):
        stored = json.loads(self.fs.read(CACHE))
        receipt = verified(stored["signed"], self.key, self.identity)
        derived = compatibility(receipt)
        if stored["config"] != derived:
            raise ValueError("cached acknowledgements changed")
        return derived

    def sync(self):
        # Check the disk's consent strings, but never use them to widen a grant.
        # A damaged or expired cache must be replaced by a new signed response.
        previous = None
        try:
            stored = json.loads(self.fs.read(CACHE))
            previous = verified(stored["signed"], self.key, self.identity, fresh=False)
        except Exception:
            previous = None
        nonce = secrets.token_hex(16)
        try:
            token = self.fs.read(STATE + "/machine.token").decode()
            envelope = self.transport.receipt(token, nonce)
            receipt = verified(envelope, self.key, self.identity, nonce=nonce)
            if previous and any(
                receipt[k] < previous[k]
                for k in (
                    "grant_version",
                    "binding_epoch",
                    "settings_version",
                    "install_settings_version",
                )
            ):
                raise ValueError("receipt rollback")
            config = compatibility(receipt)
            # A fresh challenge was verified above; unchanged, still-valid disk
            # evidence need not be rewritten on every setup-service pass.
            volatile = {"nonce", "issued_at", "expires_at"}
            same = previous and {k: v for k, v in previous.items() if k not in volatile} == {
                k: v for k, v in receipt.items() if k not in volatile
            }
            try:
                self.read()
            except Exception:
                same = False
            if not same:
                self.fs.write(CACHE, encoded({"signed": envelope, "config": config}), replace=True)
            return config
        except Exception:
            self.fs.write(CACHE, encoded({}), replace=True)
            raise InstallError("approved binding unavailable; owner review required") from None
