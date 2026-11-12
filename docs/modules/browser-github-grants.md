## browser-github-grants

- **Paths:** `commonplace/github/`
- **Owns:**

  - Explicit per-person GitHub browser grants, one-use connect flows, encrypted pending consent and direct bounded transport.
- **Promises:**

  - Never-owner workers retry claim and recovery on heartbeat ticks; former owners stay fenced. Serialized, idempotent shutdown deletes only the matching owner lease once, before server teardown returns or SQLite closes; standby and lost-lease logs explain readiness.

  - Disconnect purges grants and confirmations even when ciphertext or the wrapping key is unreadable, marking remote revocation unconfirmed without retrying unavailable credentials.

  - Browser sessions use exact Origin and session-derived CSRF; scope evidence and expiry fail closed; GitHub login and connect share subject ownership.
  - **Browser GitHub connection:** Default-off consent requests an empty scope, reads only the GitHub id/login and never creates login identities.
  - **Merge through the person's own connection:** A separate, explicit per-person opt-in ("Connect GitHub with repo access to merge") requests the `repo` scope only (merge-purpose flow, same subject, confirmation before the token replaces the stored one; lapsed or restart-orphaned candidates are revoked by the worker sweep, and the heartbeat retries release transactions that failed). `merge.py` merges a card's merge-ready github.com PR on the caller's own grant, resolving the PR server-side, refusing repositories the account cannot see, non-clean heads and any head other than the one the person confirmed, and pinning the head `sha`; branch protection stays with GitHub.
  - **Connection lifecycle proof:** Durable remote-work accounting holds the reconnect fence until revocation and older token cleanup both settle; only HTTPS or loopback development may start consent.
  - **Connection recovery:** Known expiry purges locally and reconnects without revocation acknowledgement; duplicate scope headers are all checked, and cleanup settles exchange counters even on failure. Own-person disconnect remains available across workspaces and while consent is disabled.

- **How it fails:**

  - Merge fails closed without the caller's own consented repo-scope grant (guidance: Connect GitHub with repo access to merge), a moved head or a PR that GitHub does not report clean; refusals are generic codes and never carry GitHub bodies or tokens.

  - Unknown expiry, scope conflict or missing keys refuse connection. Startup recovery requires an absent or expired shared-database lease; a competing serve cannot write GitHub state until its periodic retry claims after release/expiry; live OAuth behavior and production ingress logging remain unproven.
- **Tests:**

  - `tests/test_github_grants.py`, `tests/test_github_merge.py`, `tests/test_github_merge_units.py`, `tests/test_github_ui.py`, `tests/github_render.mjs`.

  - `tests/github_lifecycle.mjs` checks connected rendering, card switches and logout; a shared-volume lease fences recovery across private temporary directories without any lifetime lock file. Heartbeats renew the 30-second lease every 5 seconds, and expired owners fail closed on state writes.
