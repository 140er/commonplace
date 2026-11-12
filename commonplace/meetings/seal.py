"""Sealed provider credentials and meeting links: authenticated encryption, write-only.

The sealing key is derived per workspace from the same operator-held wrapping key file the
per-person grants use (``COMMONPLACE_MEETINGS_KEY_FILE``, else ``COMMONPLACE_GITHUB_KEY_FILE``),
which must sit outside the data directory and belong to the service user. The row id, the kind and
the workspace are bound as associated data, so a blob copied to another row or workspace does not
open. When the key cannot be served the write is refused; nothing is ever stored in the clear.
Errors carry a fixed code only.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from mempalace.sqlite_crypto import EncryptionError, protected_key

KEY_ENVS = ("COMMONPLACE_MEETINGS_KEY_FILE", "COMMONPLACE_GITHUB_KEY_FILE")
KINDS = ("secrets", "join_ref")


class SealError(Exception):
    """The key store cannot serve this workspace, or a blob does not open. Fixed text only."""

    code = "key_store_unavailable"


def _wrapping(data_dir) -> bytes:
    name = next((n for n in KEY_ENVS if os.environ.get(n)), None)
    if name is None:
        raise SealError
    try:
        path = Path(os.environ[name])
        if path.resolve().is_relative_to(Path(data_dir).resolve()):
            raise ValueError
        if path.stat().st_uid != os.getuid():
            raise ValueError
        return protected_key(path)
    except (OSError, ValueError, EncryptionError):
        raise SealError from None


def _key(data_dir, workspace_id) -> bytes:
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF

        return HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=b"commonplace-meetings/1/" + workspace_id.encode(),
        ).derive(_wrapping(data_dir))
    except ImportError:
        raise SealError from None


def _aad(workspace_id, kind, row_id) -> bytes:
    return json.dumps(
        ["commonplace-meetings", 1, workspace_id, kind, row_id], separators=(",", ":")
    ).encode()


def seal(data_dir, workspace_id, kind, row_id, value) -> bytes:
    """``value`` (JSON-serializable) as one ``nonce + ciphertext`` blob."""
    if kind not in KINDS:
        raise ValueError("unknown kind")
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = _key(data_dir, workspace_id)
    nonce = os.urandom(12)
    raw = json.dumps(value, separators=(",", ":")).encode()
    return nonce + AESGCM(key).encrypt(nonce, raw, _aad(workspace_id, kind, row_id))


def unseal(data_dir, workspace_id, kind, row_id, blob):
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = _key(data_dir, workspace_id)
    try:
        raw = AESGCM(key).decrypt(
            bytes(blob[:12]), bytes(blob[12:]), _aad(workspace_id, kind, row_id)
        )
        return json.loads(raw)
    except (InvalidTag, ValueError, TypeError):
        raise SealError from None
