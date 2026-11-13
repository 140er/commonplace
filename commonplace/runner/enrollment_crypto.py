"""Versioned machine-key envelopes and single-use recovery proof verification."""

import base64
import hashlib
import json
import secrets

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def public_key(pem):
    if not isinstance(pem, str) or len(pem) > 2048:
        raise ValueError("invalid machine key")
    key = serialization.load_pem_public_key(pem.encode("ascii"))
    if not isinstance(key, rsa.RSAPublicKey) or key.key_size not in (3072, 4096):
        raise ValueError("invalid machine key")
    if key.public_numbers().e != 65537:
        raise ValueError("invalid machine key")
    raw = key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    canonical = key.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    return key, canonical, hashlib.sha256(raw).hexdigest()


def seal(key, credentials, context):
    """Only the machine private key can open the issued credential envelope."""
    secret, nonce = AESGCM.generate_key(bit_length=256), secrets.token_bytes(12)
    ciphertext = AESGCM(secret).encrypt(nonce, json.dumps(credentials).encode(), context.encode())
    wrapped = key.encrypt(
        secret,
        padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )
    return {
        "version": 1,
        "context": context,
        "wrapped_key": base64.b64encode(wrapped).decode(),
        "nonce": base64.b64encode(nonce).decode(),
        "ciphertext": base64.b64encode(ciphertext).decode(),
    }


def verify(pem, challenge, signature):
    key, _, _ = public_key(pem)
    key.verify(
        base64.b64decode(signature, validate=True),
        challenge.encode(),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
        hashes.SHA256(),
    )
