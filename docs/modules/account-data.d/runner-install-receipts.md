- **Owns:**

  - Workspace classification of runner_install_manifests and runner_setup_requests, with enrollment parents for deletion ordering.

- **Promises:**

  - These tables contain approval snapshots and bounded versioned request metadata, never raw credentials. Account purge removes their workspace rows.

- **Tests:**

  - Receipt classification/purge assertions and account tables/export/deletion checks cover the records.
