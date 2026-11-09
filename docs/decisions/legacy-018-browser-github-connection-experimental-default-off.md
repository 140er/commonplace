### Browser GitHub connection (experimental, default off)

- One GitHub worker owns a control-database lease shared by every serve on the same volume, including containers with private temporary directories or different mount paths. Claim and recovery occur in one short write transaction; no lifetime lock file is held in the data tree. The lease lasts 30 seconds and renews every 5 seconds. A second serve fails closed for GitHub writes and retries acquisition every 5 seconds after the owner closes or its lease expires. Restored backups may retain the lease until expiry. The Node lifecycle harness exercises connected rendering, stale reads, card switches and logout without a browser installation.

- Browser consent requires a person session, JSON, exact Origin and session-derived CSRF; agents and MCP receive no GitHub capability. Scope is exactly empty; inherited sign-in scopes require confirmation, every repeated scope header is cross-checked, and refresh material is discarded before encryption.

- Expired grants purge locally, increment generation and permit a new Connect without acknowledgement or a GitHub request. The expired view links to GitHub app access. Only ambiguous revocation requires acknowledgement.

- The ten-minute flow binds PKCE, browser, session, workspace, visible card and generation. Callbacks reopen the selected Needs item. Login and grants share subject ownership without provisioning a new login identity during connect.

- One person owns at most one grant. Another workspace reports the existing workspace explicitly and offers Disconnect; it does not expose Open PR or reuse that grant for work. The same person's browser consent may revoke the grant from either workspace.

- `COMMONPLACE_GITHUB_CONNECT` defaults off and prevents new start/callback/confirm work. Existing grants stay visible and browser-authenticated DELETE stays available while disabled; missing app credentials make revocation unconfirmed and purge locally.

- Confirmation expiry cleanup runs on the person's next Connect, Confirm or Disconnect write. GET status remains local-only and never revokes remotely. Failed cleanup marks revocation unconfirmed and settles exchange counters in finally blocks.

- Serve closes its control database on normal return and signal unwind, stopping registered workers and releasing only its own GitHub lease in a short transaction. HTTP server close also stops email admission before GitHub joins its heartbeat and releases its lease; database callbacks use the same order so GitHub cannot delay the email stop signal while waiting for SQLite. Repeated GitHub closes are serialized and perform no SQL after successful cleanup, including after SQLite closes. Never-owners retry claim and recovery; an expired former owner stays fenced because remote work may still be in flight.

- Generation, remote-work and exchange counters fence disconnect, confirmation and late callbacks until all cleanup settles. Lease ownership and expiry are checked in each state-changing transaction, including late remote completions. An expired owner cannot renew itself or alter a successor’s counters. A new owner recovers after lease expiry without replaying remote DELETE; orderly close releases only its own lease. No writable temporary directory or platform-specific file locking is required.

- Disconnect purges local grants and confirmations even if stored ciphertext is tampered or the wrapping key is unavailable. It retains the reconnect acknowledgement requirement for unconfirmed remote revocation and never retries unavailable credentials.

- AES-256-GCM encrypts tokens/verifiers before SQLite/WAL using per-user DEKs and user/provider/schema AAD. `COMMONPLACE_GITHUB_KEY_FILE` is a private, owner-held 32-byte key; code rejects it within the control database's parent directory. Operators must also exclude it from separately located workspace and backup trees, which this store does not inventory. Install the `commonplace` extra for cryptography.

- Back up the wrapping key separately. Rewrap DEKs offline with unchanged AAD/version while workers are stopped; replacing only the key file is unsupported. Retain the matching old key/database backup until a migrated copy is verified. Online rotation and migration tooling remain deferred.

- Needs disables loading controls, contains GitHub-only errors without hiding card actions, drops stale reads and clears rendered connection details on teardown. Ordinary decision details retain their existing teardown contract. PR links require HTTPS, exact github.com, a default port and a positive pull-request number; reports stay unverified and connection proves no merge rights.

- Full account-deletion integration, composite multi-worker HTTP acceptance and a live browser workspace-switch flow remain deferred. The GitHub deletion helper fences its own cleanup only, not other stores or the user record.

- Absent token expiry requires dated `COMMONPLACE_GITHUB_NONEXPIRING_EVIDENCE` in `YYYY-MM-DD|OAuth App|client-id|expiration disabled` form. Before enabling, record fresh-account/prior-login scope, header and expiry outcomes plus ingress/callback/backend no-secret logging canaries. Live GitHub OAuth behavior remains unproven. Merge, repository consent, refresh and reconciliation remain deferred.

