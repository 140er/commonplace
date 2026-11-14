- **Owns:**

  - Workspace classification of runner_browser_requests and dependent runner_host_grants.

- **Promises:**

  - Export redacts proof and access hashes plus encrypted payloads; deletion removes access rows before their consent and enrollment parents. Provider credentials and plaintext short codes are not database fields.

- **Tests:**

  - tests/test_runner_setup_approvals.py exercises populated consent export and account deletion; the account table, export and deletion guards remain required.
