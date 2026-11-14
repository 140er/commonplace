"""Verify actual host attestations using an independently configured issuer pin.

Machine signatures and owner statements never substitute for this signature.
An absent issuer or unsupported host leaves execution disabled.
"""

import base64
from datetime import datetime, timezone

from . import install_receipts
from .receipt_cache import public_key


def verify(envelope, pins, scope, frozen, nonce, *, now=None):
    if not isinstance(envelope, dict) or set(envelope) != {"receipt", "signature"}:
        raise ValueError("host verification unavailable")
    claims = envelope["receipt"]
    if not isinstance(claims, dict) or set(claims) != {
        "version",
        "purpose",
        "issuer",
        "nonce",
        "scope_digest",
        "identity",
        "scope_enforced",
        "watchdog_seconds",
        "issued_at",
        "expires_at",
    }:
        raise ValueError("host verification unavailable")
    # Pins come only from the trusted hub configuration, never this envelope.
    pin = pins[claims["issuer"]]
    public_key(pin).verify(
        base64.b64decode(envelope["signature"], validate=True),
        install_receipts.canonical(claims),
    )
    identity = {
        k: scope[k]
        for k in (
            "workspace_id",
            "machine_id",
            "enrollment_id",
            "installation_id",
            "machine_key_digest",
            "provider_instance_id",
            "worker_connection_id",
            "grant_version",
            "binding_epoch",
            "settings_version",
        )
    }
    current = now or datetime.now(timezone.utc)
    issued, expires = (
        datetime.strptime(claims[k], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        for k in ("issued_at", "expires_at")
    )
    if (
        type(claims["version"]) is not int
        or claims["version"] != 1
        or claims["purpose"] != "runner-host-status"
        or claims["nonce"] != nonce
        or claims["scope_digest"] != frozen
        or claims["identity"] != identity
        or claims["scope_enforced"] is not True
        or type(claims["watchdog_seconds"]) is not int
        or not 0 < claims["watchdog_seconds"] <= 5
        or not issued <= current < expires
        or not 0 < (expires - issued).total_seconds() <= 5
    ):
        raise ValueError("host verification unavailable")
    return claims
