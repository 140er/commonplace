# Verified runtime discovery and the host producer

A runtime's presence is distinct from subscription sign-in, owner approval and
issuer verification. Every state remains setup-only. An unsupported or unpinned
issuer never becomes verified through a machine assertion.

The installer accepts a bounded native release up to 384 MiB, selected by exact
SHA-256 in the approved signed receipt. Smaller credential and response budgets
are unchanged. The maintainer verifies publisher provenance before offering a
release: signed publisher manifests where available, or the publisher's package
integrity/checksum metadata. A separately installed bundle can be located through
the normal command path or the standard private user command directory. Its
parents and executable must have trusted ownership and no group/other write bits.

Some embedded host runtimes need their real module filename even for help.
Read-only host discovery can use an open, rehashed, unchanged installed inode;
symlinks at the final open and unsafe modes are refused. Only version and help
are run there, with closed stdin and no inherited credentials. Provider probes,
provider login and the issuer producer retain sealed executable bytes. Host help
need only advertise the public headless entry point; a hidden bridge command is
not a reliable discovery signal. An optional `v` version prefix is accepted,
while the selected version must still match exactly.

Lifecycle state is initialized before service activation. A supervisor that reads
already-owned state does not contend for the settings worker's installer lock.
The separate lifecycle lock still serializes and persists every fresh sequence.
A slow runtime probe cannot extend the signed lease or delay revocation.

Provider homes are journaled before even a read-only status probe can create
private state. After confirmed stop, cleanup validates both owned trees before
removal. CLI helper symlinks are unlinked by their checked parent descriptor;
their targets are never visited or removed. Replaced roots, foreign ownership
and unsafe files still refuse cleanup. Normal private reads never accept links.

## Producer contract for the separate host follow-up

The adapter first verifies a fresh approved runtime and the private host grant,
including its signature, expiry, exact current scope and secret hash. A compatible
pinned release advertises `runner-status` in help. That command reads one JSON
object from stdin with `version: 1`, `purpose: "runner-host-status"`, a fresh
`nonce`, and the private `grant` envelope. No grant is put in arguments,
environment, logs, a card or chat. Requests and responses are limited to 16 KiB;
pipe reads/writes and process lifetime are bounded inside the existing lease.

The response contains exactly `receipt` and `signature`; its receipt binds the
same fresh nonce. The machine includes that envelope in its own signed lifecycle
status. The hub verifies it with the independently pinned issuer and the existing
scope, identity, expiry and watchdog checks. The adapter never generates an
issuer key, accepts a response-selected key or stores a previous signed status.
A host without the command reports verification unavailable; a malformed producer
or invalid grant stops renewal. Shutdown never invokes the producer.

The current host has no compatible issuer. The enrollment schema also supplies
no host-issuer pin that can be reused. The separate host change therefore remains
pending: implement exact project/topic/ceiling enforcement, revocation and a
maximum five-second watchdog at the host boundary; add the stdin producer above;
use the existing independently trusted enrollment issuer pin when available.
Until that pin source exists, stop rather than create a new trust root. Acceptance
requires live pinned signatures, wrong-pin/replay/stale-scope refusal, direct
out-of-scope host denial, and connected/offline stop proof. A discovery check or
synthetic signing test does not satisfy those live criteria.

For subscription sign-in, Codex uses its browser device flow. If the available
browser needs a person session, record authorization as unproved and do not wait.
Claude remains sign-in needed until the owner runs
`sh "$HOME/.commonplace-runner/sign-in" claude`, opens its printed link on any
device and types the page's one-time code into the machine. Commonplace never
sees this code; only the CLI probe and signed machine status resolve the card.
Do not call a revoked disposable proof installation ready for that login.
