- **Owns:**

  - Classification of `runner_install_settings`, `runner_enrollments`, `runner_secret_deliveries` and `runner_enrollment_attempts` as workspace control data; deliveries also name their enrollment parent for deletion order.

- **Promises:**

  - Account export redacts enrollment hashes, machine-key digests, public keys, encrypted credential envelopes and recovery challenge hashes. No plaintext enrollment code is stored. Workspace purge removes the four tables' scoped rows without touching another workspace.

- **Tests:**

  - Enrollment sentinel/export and account-purge tests supplement the account tables/export/deletion checks.
