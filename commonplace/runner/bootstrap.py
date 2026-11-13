"""Standalone launcher for a locally supplied signed runner zipapp.

Requires Python and cryptography, but no installed runner package. The release
channel supplies this launcher and a pinned Ed25519 public key through a trusted
path; neither an enrollment response nor the archive can select that trust key.
No download or package installation is performed by this slice.
"""

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


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
        args = parser.parse_args(argv)
        raw = verified_release(args.release, args.release_signature, args.release_key)
        if args.plan or not args.accept:
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
            command = [sys.executable, "-I", str(archive), "runner", "install"]
            for flag, value in vars(args).items():
                if isinstance(value, bool):
                    if value:
                        command.append("--" + flag.replace("_", "-"))
                else:
                    command.extend(("--" + flag.replace("_", "-"), value))
            if launch is None:
                result = subprocess.run(
                    command,
                    stdin=sys.stdin,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    env={"PATH": "/usr/bin:/bin", "HOME": args.home},
                )
                code = result.returncode
            else:
                code = launch(command)
        print("runner bootstrap: " + ("complete" if code == 0 else "refused"))
        return 0 if code == 0 else 1
    except Exception:
        print("runner bootstrap: release verification or installation refused", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
