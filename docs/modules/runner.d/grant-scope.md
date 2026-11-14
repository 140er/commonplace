- **Owns:**

  - The versioned per-machine grant scope record: shared validation and canonical bytes in `grant_scope.py`, the offline `runner check` producer, owner-submitted reports and the owner projection.

- **Promises:**

  - A grant scope report is always unverified and never creates a binding. Any changed canonical record resets the binding and increments the epoch once; an identical one does not. A confirmation after a report compares the stored digest and epoch.

  - Grant scope records hold no configured credential and no credential-shaped value, and appear only in the owner view; T3 access fields stay unknown.

- **How it fails:**

  - Invalid, duplicate-key, nonfinite or oversize scope bodies are refused without a write and close the connection; a stale confirmation digest or epoch returns 409. A full-config `runner check` with missing credentials or invalid settings exits 4 with `scope_invalid` and prints no record.

- **Tests:**

  - `tests/test_runner_scope.py` covers report storage, reset per field, confirmation compare-and-set and owner-only projection; `tests/test_runner_scope_local.py` covers the offline check record, provider selection and credential screening.
