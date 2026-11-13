### Runner consent and provider sign-in, slice 5

- **M:** [Setup guide](../runner-browser-setup.md) implements signed machine
  status, sealed scoped host access and owner-only fingerprint confirmation.
  The owner performs these minimum steps, in order:
  1. In the browser or app, enable setup, review the selected scope and choose
     **Add runner** once.
  2. On the intended machine, run the generated command and accept once. Keep
     its printed fingerprint available.
  3. For Codex, compare every fingerprint group in **Needs me**, tick the
     comparison and choose **Confirm matching machine**. On the next refresh,
     compare again and choose **Show sign-in link and code**. Open the provider
     website on any device, sign in to the intended subscription account and
     enter the displayed short code there.
  4. For Claude, on that machine run `sh "$HOME/.commonplace-runner/sign-in" claude`.
     It runs the verified CLI's `claude auth login --claudeai` in its private directory.
     Open the link it prints on any device, then type the code the sign-in page
     shows into the machine when asked. The hub never sees this code. No browser
     completion checkbox is provided: a fresh CLI status probe and signed machine
     report resolve the waiting card automatically.
  5. In **Needs me**, compare the fingerprint, review Projects, Topics and Access
     limit, tick the comparison and choose **Approve access**. Delivery is
     automatic; never copy or type a host access secret.
  6. At **Confirm this machine**, compare every group again, tick the comparison
     and choose **Confirm matching machine**. Access approval and attestation
     may finish while provider sign-in is still needed; that provider cannot run.
  If any fingerprint differs, choose **This is not my machine**, then confirm
  refusal. This ends enrollment and revokes its credentials, status and grants.
- **M:** Browser requests last five minutes; device codes are hashed at rest and
  displayed once from volatile memory. Host access lasts ten minutes; renewal
  needs fresh browser consent. Refusal, Revoke and Disable runner setup invalidate
  authority together. Every owner decision is audited. Both tables are classified;
  export redacts verifiers and ciphertext. Credentials stay in private machine
  files. The terminal sign-in exception is deliberate and supported for Claude.
- **T:** Execution remains disabled in this slice. Real host issuer verification,
  connected stop, offline deadlines and live proofs belong to slice 6 in a
  separate change. No live provider login or live host authority is claimed here.
