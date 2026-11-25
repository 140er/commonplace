- **Owns:**

  - `GET /api/compute/credentials` (`routes.py`, `control.py` `list_credentials`) and `credentials.py` `list_credential_references`: a read-only, owner-only list of the workspace's credential references for the management form. Each row carries only id, purpose, account, version, recorded state (pending, active, revoked, expired) and expiry; the response status is `ready`, `unprovisioned` (no custody file yet) or `unavailable` (unsafe or unreadable custody, with no detail).
- **Promises:**

  - The listing opens the custody file read-only with the same safe-open checks and selects only the plaintext reference columns of the caller's workspace. It never opens the wrapping key, the encrypted key or the public key, never creates custody, and never constructs the encrypted store. States are the recorded values, not authenticated; every use of a credential still rechecks them.
  - `docs/compute/RUNNING.md` documents the operator step that creates a pending credential; a test runs that exact block and finds its result in the list.
