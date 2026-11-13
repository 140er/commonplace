### Browser-only runner consent, slice 5

- **M:** [Browser setup](../runner-browser-setup.md) defines person-session owner
  decisions, signed machine login facts, sealed exact-scope access delivery and
  fingerprint-bound final attestation. The owner performs these minimum steps:
  enable setup and review the scope, choose Add runner once; run the generated
  command and accept once on the intended machine; compare its fingerprint in
  Needs me, allow each supported provider sign-in and complete its link/code on
  the provider website; compare and approve machine access; compare again and
  confirm the matching machine. No credential or returned login code is typed
  on the machine, and no access secret is copied or displayed.
- **M:** Requests last five minutes. Short provider codes are hashed at rest and
  shown once from volatile memory. Access files expire after ten minutes and
  renewal requires fresh browser consent. Private durable delivery precedes ack;
  revoke and the setup kill switch invalidate status, decisions and access.
  Account classification covers both new tables; export redacts secret fields.
- **T:** A provider whose remote OAuth fallback requires terminal code entry is
  unsupported and remains incomplete. Real host issuer compatibility and direct
  scope enforcement remain release blockers. Execution is disabled until slice 6
  proves supervised stop, offline deadlines and execution leases. No real login,
  live host authority or independent hardware attestation is claimed.
