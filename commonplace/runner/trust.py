"""Explicit, private deployment configuration for signing and issuer pins."""

import json
import os
import stat

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .receipt_cache import public_key


def private_file(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid not in (0, os.getuid())
            or info.st_mode & 0o077
            or info.st_size > 16384
        ):
            raise ValueError
        with os.fdopen(fd, "rb", closefd=False) as stream:
            return stream.read(16385)
    finally:
        os.close(fd)


def configure(gateway, path=None):
    path = path or os.environ.get("COMMONPLACE_RUNNER_TRUST_FILE")
    if not path:
        return
    try:
        config = json.loads(private_file(path))
        if not isinstance(config, dict) or set(config) != {
            "approval_private_key_file",
            "host_issuers",
        }:
            raise ValueError
        signer = serialization.load_pem_private_key(
            private_file(config["approval_private_key_file"]),
            password=None,
        )
        if not isinstance(signer, Ed25519PrivateKey) or not isinstance(
            config["host_issuers"], dict
        ):
            raise ValueError
        pins = {}
        for name, pem in config["host_issuers"].items():
            if not isinstance(name, str) or not 1 <= len(name) <= 80 or not isinstance(pem, str):
                raise ValueError
            raw = pem.encode("ascii")
            public_key(raw)
            pins[name] = raw
        gateway.runner_receipt_signer = signer
        gateway.runner_host_issuers = pins
    except Exception:
        raise ValueError("runner trust configuration unavailable") from None
