# Runner shutdown and host verification

The setup supervisor gives each installation a signed, four-second lease. A
separate process owns that lease, so a blocked capability probe, download or
provider sign-in cannot extend it. Replies must match a fresh nonce, monotonic
sequence, workspace, enrollment, installation and machine. Nothing in this
channel approves access or enables execution.

## Stop and revoke

In the runner screen, choose **Revoke**, then **Revoke machine**. Enrollment,
machine and worker credentials, binding, host grants, pending deliveries and
provider status lose authority in one transaction. The next supervisor pulse
receives **stop**. Loss of connectivity also stops setup within the local
five-second shutdown bound; it never waits for a slow setup operation.

The user unit kills its whole control group, including processes that created
their own session. Its post-stop helper checks that the main process is gone,
clears local credential files and approved caches, removes dedicated provider
credentials, and signs a termination acknowledgement. Unrelated processes and
services are outside that control group. The installer preserves existing linger.

The owner sees **Revoked; stop unconfirmed** until that acknowledgement arrives,
then **Stopped**. An offline machine is never called stopped merely because its
lease expired in the hub. Its unresolved stop remains in Needs me. The retained
installation key can report termination after token revocation; it cannot recover
credentials, approve anything or obtain another execution lease. Replays and
changed identities are refused. A hostile or unreachable operating system cannot
be synchronously killed by a hub; the state reports that limitation honestly.

On the machine, `commonplace runner stop` requests self-revocation and stops the
owned unit. Uninstall stops it before removing artifacts. An offline shutdown
keeps stop confirmation pending; repeat the stop command once connectivity is
restored to reconcile the acknowledgement. This command contains no credential.

## Explicit trust and real signatures

The maintainer explicitly supplies a private deployment trust file through
`COMMONPLACE_RUNNER_TRUST_FILE`. It contains exactly:

```json
{
  "approval_private_key_file": "<private signing-key file>",
  "host_issuers": {"<approved issuer>": "<PEM public key>"}
}
```

The configuration and signing-key file must be regular private files, owned by
the service account or root; symlinks and broad permissions are refused. The
signing key is Ed25519. Its public approval key must already be pinned by the
trusted installer. No key is generated or learned from a network response.

A host status uses a real Ed25519 signature under an independently configured
issuer key. It binds a fresh nonce, exact current scope digest, installation and
machine key, provider and worker binding, settings/grant versions, scope enforcement
and a watchdog of at most five seconds. Its lifetime is at most five seconds.
Wrong keys, unpinned issuers, stale versions and mismatched scope are refused;
missing evidence is **unavailable**, never verified. Owner attestation and a
machine's own signature are separate claims and cannot replace this signature.

This slice does not enable execution merely because that cryptographic verifier
accepts a status. A compatible real host issuer, direct scope enforcement and live
provider authorization must be proved before agent-ready admission is enabled.
An unsupported host stays setup-only; there is no fixture, self-signing, credential
copy or alternate billing fallback.

## Provider sign-in by the owner

After a fresh enrollment has the supported, verified runtime, Codex uses
**Check this machine**, then **Sign in to Codex** in Needs me. Open its provider
page on any device and enter the displayed device code there. Its signed machine
status resolves the card after the CLI completes the exchange.

For Claude, use the **Sign in to Claude** card:

1. Run `sh "$HOME/.commonplace-runner/sign-in" claude` on the machine.
2. Open the link it prints, on any device, and sign in to the intended account.
3. Type the one-time code from that page into the machine when asked.

Commonplace never sees this code. The card stays **Waiting for sign-in on the
machine** until the CLI status probe and signed report confirm the login. There
is no completion checkbox. A revoked proof installation must be enrolled again
before any sign-in; its old credentials cannot be revived.
