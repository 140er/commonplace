"""Standalone launcher for a locally supplied signed runner zipapp.

Requires Python and cryptography, but no installed runner package. The release
channel supplies this launcher and a pinned Ed25519 public key through a trusted
path; neither an enrollment response nor the archive can select that trust key.
No download or package installation is performed by this slice.
"""

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


def parse_issuer_pin(value):
    if value is None:
        return None
    if (
        not isinstance(value, dict)
        or set(value) != {"issuer", "public_key"}
        or not isinstance(value["issuer"], str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,79}", value["issuer"])
        or not isinstance(value["public_key"], str)
    ):
        raise ValueError("invalid owner issuer pin")
    key = serialization.load_pem_public_key(value["public_key"].encode("ascii"))
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("invalid owner issuer pin")
    return {
        "issuer": value["issuer"],
        "public_key": key.public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode("ascii"),
    }


def read_issuer_pin(path):
    if path is None:
        return None
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid not in (0, os.getuid())
            or info.st_mode & 0o022
            or info.st_nlink != 1
            or info.st_size > 16384
        ):
            raise ValueError("invalid owner issuer pin")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(16385)
        if len(raw) > 16384:
            raise ValueError("invalid owner issuer pin")
        pin = parse_issuer_pin(json.loads(raw))
        if pin is None:
            raise ValueError("invalid owner issuer pin")
        return pin
    finally:
        os.close(fd)


def describe_issuer_pin(pin):
    if pin is None:
        return "Host issuer pin: not supplied; verification and execution stay disabled."
    key = serialization.load_pem_public_key(pin["public_key"].encode("ascii"))
    fingerprint = hashlib.sha256(
        key.public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    ).hexdigest()
    return (
        "Host issuer pin: "
        + pin["issuer"]
        + "; SHA-256 "
        + " ".join(re.findall(".{8}", fingerprint))
    )


def verified_release(archive, signature, trusted_key):
    raw = Path(archive).read_bytes()
    if not raw or len(raw) > 32 * 1024 * 1024:
        raise ValueError("invalid release size")
    key = serialization.load_pem_public_key(Path(trusted_key).read_bytes())
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("invalid release trust key")
    key.verify(Path(signature).read_bytes(), raw)
    return raw


def main(argv=None, *, launch=None):
    try:
        parser = argparse.ArgumentParser(description="Verify and install a signed runner archive")
        for flag in (
            "release",
            "release-signature",
            "release-key",
            "hub",
            "workspace",
            "label",
            "manifest-digest",
            "enrollment-id",
            "home",
        ):
            parser.add_argument("--" + flag, required=True)
        parser.add_argument("--accept", action="store_true")
        parser.add_argument("--plan", action="store_true")
        parser.add_argument("--approval-key")
        parser.add_argument("--host-issuer-pin")
        args = parser.parse_args(argv)
        raw = verified_release(args.release, args.release_signature, args.release_key)
        pin = read_issuer_pin(args.host_issuer_pin)
        if args.plan or not args.accept:
            print(describe_issuer_pin(pin))
            print(
                "Verify the pinned release; create a temporary private archive only after acceptance."
            )
            print("Preflight the user manager, owned paths and password-free linger permission.")
            print(
                "Read protected stdin; persist private key and identity; exchange once or recover by signed proof."
            )
            print(
                "Write 0600 tokens, config, phase manifest and archive in 0700 owned directories."
            )
            print(
                "Acknowledge delivery; write the 0600 setup-only unit; enable linger, reload and start; verify."
            )
            print(
                "On failure stop before rollback; uninstall removes only owned files and restores eligible linger."
            )
            return 0
        # TemporaryDirectory and exclusive creation are private from their first instant.
        with tempfile.TemporaryDirectory(prefix="runner-bootstrap-") as directory:
            archive = Path(directory) / "runner.pyz"
            fd = os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            if pin is not None:
                print(describe_issuer_pin(pin))
                snapshot = Path(directory) / "issuer-pin.json"
                fd = os.open(snapshot, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "w") as stream:
                    json.dump(pin, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                args.host_issuer_pin = str(snapshot)
            command = [sys.executable, "-I", str(archive), "runner", "install"]
            for flag, value in vars(args).items():
                if value is None:
                    continue
                if isinstance(value, bool):
                    if value:
                        command.append("--" + flag.replace("_", "-"))
                else:
                    command.extend(("--" + flag.replace("_", "-"), value))
            if launch is None:
                result = subprocess.run(
                    command,
                    stdin=sys.stdin,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    env={"PATH": "/usr/bin:/bin", "HOME": args.home},
                )
                code = result.returncode
                # Forward only the public comparison value, never raw installer output.
                if code == 0:
                    match = re.search(
                        rb"(?m)^Machine fingerprint: ([0-9a-f]{8}(?: [0-9a-f]{8}){7})$",
                        result.stdout,
                    )
                    if match:
                        print("Machine fingerprint: " + match[1].decode("ascii"))
            else:
                code = launch(command)
        print("runner bootstrap: " + ("complete" if code == 0 else "refused"))
        return 0 if code == 0 else 1
    except Exception:
        print("runner bootstrap: release verification or installation refused", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
