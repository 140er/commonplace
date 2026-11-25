- **Tests:**

  - `tests/test_commonplace_account_tables.py` reviews `runner_machines.grant_scope_sha256` and `runner_machines.binding_scope_sha256` as plain content digests of a reported grant scope (a SHA-256 over a validated record of ids, modes and project names, never a credential), so the credential-like column check passes without redacting them in the account export.
