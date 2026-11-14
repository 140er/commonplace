"""Sealed install credentials: one authenticated-encryption blob per install.

The sealing key is derived per workspace from the hub's operator-provisioned wrapping key (the
file named by ``COMMONPLACE_WRAPPING_KEY_FILE``, outside the data tree), never stored in the
data directory, an agent environment or the control database. The install id is bound as
associated data, so a blob cannot be moved to another install. If no key can be served, sealing
is refused: credentials are never stored in the clear.
"""

import json
import os
from pathlib import Path

from mempalace.sqlite_crypto import EncryptionError, protected_key

KEY_FILE_ENV = "COMMONPLACE_WRAPPING_KEY_FILE"
VERSION = b"\x01"


class Unavailable(Exception):
    """No sealing key can be served. Fixed text; never a path or a key."""


def _info(workspace_id: str) -> bytes:
    return b"commonplace-chat-bridge-v1|" + workspace_id.encode()


def key_for(data_root, workspace_id: str) -> bytes:
    try:
        path = Path(os.environ[KEY_FILE_ENV])
        if path.resolve().is_relative_to(Path(data_root).resolve()):
            raise ValueError
        wrapping = protected_key(path)
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF

        return HKDF(
            algorithm=hashes.SHA256(), length=32, salt=None, info=_info(workspace_id)
        ).derive(wrapping)
    except (KeyError, OSError, ValueError, EncryptionError, ImportError):
        raise Unavailable() from None


def _aad(install_id: str) -> bytes:
    return json.dumps(["chat-bridge", 1, install_id], separators=(",", ":")).encode()


def seal(key: bytes, install_id: str, secrets: dict) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = os.urandom(12)
    raw = json.dumps(secrets, separators=(",", ":"), sort_keys=True).encode()
    return VERSION + nonce + AESGCM(key).encrypt(nonce, raw, _aad(install_id))


def open_blob(key: bytes, install_id: str, blob: bytes) -> dict:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        if blob[:1] != VERSION:
            raise ValueError
        raw = AESGCM(key).decrypt(blob[1:13], blob[13:], _aad(install_id))
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (InvalidTag, ValueError, TypeError):
        raise Unavailable() from None
