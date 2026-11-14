### Runner consent and provider sign-in, slice 5

- **M:** [Setup guide](../runner-browser-setup.md) implements signed machine
  status, sealed scoped host access and owner-only fingerprint confirmation.
  The owner performs these minimum steps, in order:
  1. In the browser or app, enable setup, review the selected scope and choose
     **Add runner** once.
  2. On the intended machine, run the generated command and accept once. Keep
     its printed fingerprint available.
  3. For Codex, compare every fingerprint group in **Needs me**, tick the
     comparison and choose **Check this machine**. On the next refresh,
     compare again and choose **Sign in to Codex**. Open the provider
     website on any device, sign in to the intended subscription account and
     enter the displayed short code there.
  4. For Claude, the **Sign in to Claude** card gives these instructions:
     1. Run the command below on the machine.
     2. Open the link it prints, on any device.
     3. Type the code from that page into the machine.

     Commonplace never sees this code.

     **Run on the machine:** `sh "$HOME/.commonplace-runner/sign-in" claude`.
     **Waiting for sign-in on the machine** resolves automatically after a fresh
     CLI status probe and signed machine report; there is no completion checkbox.
  5. In **Needs me**, compare the fingerprint, review Projects, Topics and Access
     limit, tick the comparison and choose **Approve access**. Delivery is
     automatic; never copy or type a host access secret.
  6. At **Confirm this machine**, compare every group again, tick the comparison
     and choose **Confirm matching machine**. Access approval and attestation
     may finish while provider sign-in is still needed; that provider cannot run.
  If any fingerprint differs, choose **This is not my machine**, then confirm
  refusal. This ends enrollment and revokes its credentials, status and grants.
- **M:** Setup titles describe each action: Check this machine, Sign in to Codex
  (or Claude), Approve machine access and Confirm this machine. Step counts include
  only that machine's steps; completed provider steps retain their positions.
  Phone cards have 16px inner padding inside the 16px page gutter.
- **M:** Browser requests last five minutes; device codes are hashed at rest and
  displayed once from volatile memory. Host access lasts ten minutes; renewal
  needs fresh browser consent. Refusal, Revoke and Disable runner setup invalidate
  authority together. Every owner decision is audited. Both tables are classified;
  export redacts verifiers and ciphertext. Credentials stay in private machine
  files. The terminal sign-in exception is deliberate and supported for Claude.
- **T:** Execution remains disabled in this slice. Real host issuer verification,
  connected stop, offline deadlines and live proofs belong to slice 6 in a
  separate change. No live provider login or live host authority is claimed here.
