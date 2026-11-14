- **Owns:**

  - The account classification of runner_lifecycle.

- **Promises:**

  - Lifecycle records belong to their enrollment and workspace and participate in export and deletion. They contain only fixed status, counters, timestamps and a scope digest.

- **How it fails:**

  - An unclassified table stops a complete export or deletion. Lifecycle data contains no credential, issuer private key or provider response.

- **Tests:**

  - tests/test_commonplace_account_tables.py and the populated lifecycle export/deletion witness.
