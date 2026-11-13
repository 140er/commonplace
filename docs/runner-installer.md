# Private runner installation (slice 2)

`commonplace runner install` installs a **setup-only** user service. It does not
approve bindings, fetch acknowledgements, install a runtime or provider CLI,
authorize a login or grant, enable setup in the workspace, or run work. The
existing `runner check`, `run`, `chat` and `report` commands keep their defaults.

A signed-in workspace owner first enables setup and issues an enrollment through
the existing enrollment routes. Installation needs that enrollment's workspace,
label, id and approved manifest digest, plus the hub's HTTPS origin. The one-time
code enters on stdin, never as a flag, argument or environment variable. Use an
already protected input stream; do not put a literal code in shell history.

```sh
commonplace runner install --hub https://hub.example.test \
  --workspace WORKSPACE_ID --label example --enrollment-id ENROLLMENT_ID \
  --manifest-digest APPROVED_SHA256 --plan
```

Replace the identifiers and digest with the approved enrollment metadata. The
digest is 64 lowercase hexadecimal characters. Plan lists all actions without
creating files, contacting the hub or invoking the service manager. Omitting
`--accept` also produces only a plan. Repeat the command with `--accept`, without
`--plan`, and supply the code through protected stdin to accept installation once.
No password or further terminal authorization prompt is requested. Preflight
refuses unsupported systems, occupied paths or insufficient privilege before
reading or redeeming the code.

The intended unprivileged Linux account needs a running systemd user manager.
If linger is already enabled it is preserved. Otherwise a narrowly authorized,
password-free privilege channel must permit exactly `loginctl enable-linger`
and `loginctl disable-linger` for that account. Missing permission stops setup;
there is no interactive elevation fallback. Existing workloads are left alone.

## Private state and recovery

The installation lives in `~/.commonplace-runner` (0700). Its machine key,
separate machine/worker tokens, generated config, encrypted delivery and phase
manifest are 0600 from creation. The manifest contains ids, state and artifact
digests, never credentials or the code. New service directories are 0700 and the
user unit is 0600. Symlinks, foreign ownership, unsafe permissions, changed owned
files and collisions are refused. A process lock prevents concurrent changes.

Before exchange the installer durably saves an installation id and RSA-3072 key.
If the exchange response is lost, retry with the same metadata and private state:
it proves possession of that key to the recovery route and receives the same
encrypted delivery. It never redeems the code again. Individual durable file
writes resume through the manifest. Recovery has the existing ten-minute limit;
after expiry a missing delivery requires owner revocation and reissue. Keep the
private state until recovery or deliberate uninstall is complete.

The setup service neither reads token files nor contacts the hub. Its supervisor
only waits for termination; it cannot lease or launch work. Health verification
checks private artifacts, linger, and enabled/active service state. This does not
claim readiness for execution. Signed owner manifests/receipts and runtime
readiness remain prerequisites in later slices.

```sh
commonplace runner status
commonplace runner uninstall
```

`--home` selects the intended account's home explicitly when needed. Repeating a
successful installation with the same metadata changes no files, credentials or
service state. Uninstall first stops and disables the owned unit. A failed stop
retains all artifacts. It removes unchanged owned files and empty owned
directories, preserves unrelated or edited files, and undoes linger only when
this installation enabled it and no other enabled/active service depends on it.
Remote credentials still require owner revocation; this slice adds no machine-self
revocation endpoint. An incomplete uninstall can be retried.

## Verified bootstrap

When the runner package is absent, the release channel can supply the standalone
`commonplace/runner/bootstrap.py` launcher, a signed runner zipapp, its detached
Ed25519 signature and a separately trusted pinned release public key. Python and
`cryptography` are prerequisites; this slice downloads or installs no dependencies.
The key must arrive through the trusted release channel, independently of the
hub and archive. A key bundled by an untrusted sender is not a trust anchor.

Run that standalone launcher with the installation metadata above, `--home`,
`--release`, `--release-signature`, and `--release-key`. It verifies the archive
before invoking it with the same single accept and protected stdin. The installer
verifies it again before redemption, stores it privately and points the owned
service at that archive. Tampered or incorrectly signed archives are refused.
This supplies the bootstrap verification boundary, not a release-distribution
or package-publishing pipeline. Production signing and a disposable supported
system's logout/restart proof remain release requirements.

## Evidence

`tests/test_runner_install.py` uses a fresh home, a recording user manager and
the real enrollment handlers with in-memory request/response streams. No socket
or systemd process is used. It covers both original linger states, occupied
paths, secret scans, private creation, lost delivery, interrupted writes,
idempotence, rollback, stop-before-discard and archive verification.
