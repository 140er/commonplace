# Runner setup approvals

Slice 5 of [the installer plan](design/runner-box-installer.md) adds owner consent,
provider sign-in, private host access delivery and final fingerprint confirmation.
Setup is off by default. Execution remains disabled until slice 6 proves the
real host issuer, direct scope enforcement, supervised stop and offline deadlines.

## Exact owner steps

1. In the browser or app, enable runner setup, review the selected projects,
   topics, providers and access limit, and choose **Add runner** once.
2. On the intended machine, run the generated command and accept its installation
   scope once. Keep the printed fingerprint available for comparison.
3. For Codex, compare every fingerprint group in **Needs me**, tick the comparison
   and choose **Check this machine**. On the next refresh, compare again
   and choose **Sign in to Codex**. Open the provider website on any
   device, sign in to the intended subscription account and enter the displayed
   short code there. It is shown once; lost or expired presentations require a
   fresh request. The machine completes the device exchange itself.
4. For Claude, the **Sign in to Claude** card gives these instructions:
   1. Run the command below on the machine.
   2. Open the link it prints, on any device.
   3. Type the code from that page into the machine.

   Commonplace never sees this code.

   **Run on the machine:** `sh "$HOME/.commonplace-runner/sign-in" claude`.
   This invokes the verified CLI's `claude auth login --claudeai` in the private
   credential directory. **Waiting for sign-in on the machine** resolves after
   the next CLI status probe and signed machine report; there is no completion
   checkbox to tick.
5. In **Needs me**, compare the fingerprint, review **Projects**, **Topics** and
   **Access limit**, tick the comparison and choose **Approve access**. Delivery
   is automatic; no host access secret is copied, typed or displayed.
6. At **Confirm this machine**, compare every fingerprint group again, tick the
   comparison and choose **Confirm matching machine**. This records the owner's
   attestation. Host access approval and attestation can finish while a provider
   still needs sign-in. That provider remains unable to run.

At any comparison, a mismatch ends with **This is not my machine**, followed by
**Yes, refuse this machine**. Refusal ends the enrollment and revokes its machine
and worker credentials, binding, grants, CLI status and pending Needs me requests.
Cancel returns to comparison without a write. Every decision records the person.

The owner-approved terminal step for Claude is a supported exception to the
plan's original browser-only login recommendation. Nothing from that exchange is
entered into the hub. Only the machine's signed fixed status reaches it. The
command has no credential argument; no credential directory is copied, no
callback proxy is introduced and no alternate billing path is used.

The manual counts are one Add action and one installation accept, followed by
one browser consent and one provider website sign-in for missing Codex login,
one terminal command and one provider website/code exchange for missing Claude
login, one access approval and one final machine confirmation. Refreshes and
fingerprint comparisons carry no secrets. Sources: [device authentication](https://learn.chatgpt.com/docs/auth),
[terminal/browser authentication](https://code.claude.com/docs/en/authentication)
and [sign-in/status commands](https://code.claude.com/docs/en/cli-reference).

## Scope, authority and recovery

Only the enrollment's current person owner can approve, reveal a device code,
refuse or attest. Writes require JSON and same-origin protection. Machine, worker,
member and other connected credentials cannot decide. Requests freeze the complete
scope and expire after five minutes. Wrong workspace, machine, fingerprint,
settings or binding versions, replay and expired decisions are refused.

The hub mints a dormant host access contract bound to the machine key, workspace,
installation, provider instance, projects, topics, wings, requesters, versioned
binding and access limit. Real issuer compatibility remains a release blocker.
The hub stores only a verifier hash and machine-key ciphertext; the only persistent
plaintext copy is the machine's 0600 access file in its 0700 installation directory.
Durable storage precedes acknowledgement, which purges ciphertext. Lost responses
recover the same envelope, never another secret. Access lasts ten minutes and
renewal requires fresh consent, with no silent scope expansion.

Short device codes are hashed at rest and displayed once from bounded volatile
memory. Restart loses the presentation. Provider credentials never reach the hub.
The automatic device process has closed stdin, an isolated private home, bounded
output and a hard deadline. The deliberate terminal exchange inherits only the
owner's terminal streams and a restricted environment, with a five-minute timeout;
its output and input are never captured by setup or forwarded to the hub. A
subscription-only read-only probe runs afterwards in the same private directory.

Revoke, refusal and Disable runner setup invalidate credentials, status, decisions
and access verifiers atomically. Reads immediately refuse revoked authority. The
setup service clears cached access and stops its owned device login when it next
checks authority. Offline process termination is a slice 6 proof, not claimed
here. Re-enabling revives no approval. Account export redacts secret fields and
account deletion removes the dependent rows. The UI lives within the real Deck
shell, with a selectable fingerprint grouped exactly like installer output.

The screen titles are **Check this machine**, **Sign in to Codex** (or Claude),
**Approve machine access** and **Confirm this machine**. Step numbering includes
only that machine's steps: four with Codex, three with the Claude terminal path,
two without provider sign-in, or five with both. Completed provider steps retain
their position while access and confirmation finish. Phone cards keep a 16px
inner inset within the page gutter; the fingerprint well has its own 16px padding.
