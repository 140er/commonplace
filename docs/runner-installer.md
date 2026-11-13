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

## Approved settings and acknowledgement cache (slice 3)

An installation can fetch an approved binding using its own machine credential at
`GET /api/runner/install-manifest?nonce=<fresh challenge>`. This is an action on
the existing authenticated machine prefix, never a public bootstrap route or an
MCP tool. Setup must be enabled for that workspace. Ordinary, worker and person
credentials cannot read this machine-self response.

The existing owner-only attestation request optionally includes `install_settings`:
its current `grant_version`, `binding_epoch`, `settings_version` and a `projects`
mapping. The existing owner registry view supplies the settings revision (zero
before the first approval); stale settings revisions conflict too. Every
project maps to its discovered `project_id`, `checkout: "shared"` and an explicit
`runtime_ceiling` no higher than the machine grant. The mapping must cover exactly
the granted projects, with distinct project ids. Attestation and the settings
snapshot commit together; stale versions roll both back. Only a current signed-in
person owner can approve this snapshot. This slice adds no final attestation UI.

The hub signs only the current owner-approved snapshot, binding the workspace,
enrollment digest, installation, machine-key fingerprint, worker, provider,
projects, topics, wings, requesters, ceilings and authority versions. Each response
also covers the request's random challenge and a lifetime of at most 60 seconds.
Missing signing support or audit persistence refuses delivery. Signing uses a
trusted provisioning seam; a request can neither supply a signing key nor choose
a grant. The separate approval-key custody and format remain prerequisites for
production availability, with no unsigned fallback.

A trusted launcher can supply the public approval pin using `--approval-key`.
It is copied into the owned private installation directory during acceptance;
it never comes from the hub response. The setup-only service synchronizes every
30 seconds when this pin exists. `runner check --config` also synchronizes an
installation's generated configuration. Without the pin the service stays inert.
Neither path leases work, installs a runtime, logs in a provider or enables work.

The private `approved-binding.json` contains the signed receipt and a derived
compatibility cache: `binding_acks`, each `shared_checkout_ack` and each
`runtime_ack`. Signature, identity, freshness and versions are checked before
writing it. Edited acknowledgement strings are rejected and replaced by a fresh
fetch. Fetch failure clears the compatibility cache; it cannot authorize offline
work. The generated installation configuration stays setup-only, and an owner
cannot enable an incomplete installation through an ordinary grant patch.

A changed wing, topic, grant or ceiling makes the old snapshot stale. On its next
sync the machine loses the old cache and a deduplicated settings reminder appears
in the owner's Needs me. The reminder opens existing runner settings and grants
no authority. A fresh owner attestation and settings approval lets the next sync
replace the cache without editing any machine file. Reminders and metadata stay
absent while setup is off; existing token tool catalogs remain unchanged.

## Selected runtime installation (slice 4)

The setup-only service and `runner check` can now locate or install an approved
headless host and selected subscription CLIs. The existing owner attestation
request accepts an optional `install_settings.runtime` object. Its `components`
map includes `host` and only the selected `claude`/`codex` entries. Each entry
requires an exact semantic `version`, `sha256`, HTTPS `url` without credentials,
query or fragment, and `format` (`native` or `python-zipapp`). `topic_models`
must map every approved topic to exactly one selected `provider` and `model`.
There is no local version selector and no latest-version fallback.

The hub signs these choices inside the existing machine-bound, challenged
receipt. The installer uses the separately pinned approval key, current authority
versions and expiry before consulting a runtime adapter. It hashes every selected
artifact before any artifact executes or is published. A signature change, local
pin edit, failed download or wrong digest leaves no newly installed artifact.
Release verification covers the signed manifest and its artifact digest together;
it does not trust a download-provided public key or a detached unsigned pin.

Only selected executable names are looked up. A matching existing artifact is
reused without modification; an incompatible installation is preserved while a
verified copy goes into the private runner prefix. Supported release shapes are
self-contained Linux native executables and Python zipapps. Unknown wrappers,
unsupported platforms, wrong versions or missing headless/status interfaces
remain incomplete. There is no package-manager invocation, privilege escalation,
global dependency installation or machine-setting change in this step.

Stored artifacts and generated configuration remain 0600, directories 0700.
Native capability probes execute verified bytes through sealed anonymous Linux
descriptors; no stored file gains execute bits. Probe stdin is closed, output is
captured and discarded, and the environment contains only fixed runtime values
and a dedicated private home. Checks inspect version, headless commands and
read-only authentication status. A successful status is recorded as unverified:
it cannot establish the intended account or scope. Existing credential
directories are neither copied nor used. No login flow starts and no host-grant
secret is received, stored or printed.

`runtime-config.json` is a derived runner configuration cache, not authority. It
contains the approved provider instance and worker ids, topic/model mapping,
project ids, deterministic private checkout directories, release digests and
bounded presence/login states. Checkout directories are empty reservations: this
slice does not clone a repository or register a host project. Each project keeps
its approved checkout choice; a local directory does not invent checkout consent.
The signed receipt remains the source of approval. Cache edits fail verification
and a fresh sync re-derives the mapping. Repeated unchanged syncs reuse files and
mappings; expired receipts are refreshed. Owned artifacts are journaled before
publication, and interrupted publication converges on retry. Uninstall confirms
that the setup service stopped, then removes only unchanged owned files and empty
owned directories. Unrelated installations and checkout files remain in place.

The enrolled machine may POST fixed component facts to
`/api/runner/install-status` only while setup is enabled and its approved binding,
settings revision and runtime digest remain current. The hub projects an owner-only
Needs me item, **Sign-in needed**, or a fixed missing/unsupported message. Reports
contain no paths, provider output, credentials or new grants. Old tokens gain no
route or tool visibility; disabling setup or changing the binding invalidates the
report. Installation records follow workspace account export and deletion.

The release and approval keys remain trusted provisioning inputs. Real release
packaging and provider/host interface compatibility need release-specific proof;
the offline fixtures demonstrate the adapter contract with synthetic releases.
Linux descriptor sealing is required; unavailable support fails closed. Existing
single-accept service/linger rules and five-/ten-minute enrollment/recovery
lifetimes are unchanged. Browser login, scoped host grants, final readiness,
authorization leases and live stop guarantees remain later slices. Execution
stays disabled throughout this slice.
