- **Owns:**

  - The account of a config-driven start: `runner_bridge._account` calls `projects.model_modes.resolve_account` for the mode and account, swaps in the person's own machine in personal mode and pins it on the run.

- **Promises:**

  - A start with no requesting person belongs to the person who accepted the config version; the launch reply carries the mode and account kind, never the label of a person's own account.

- **How it fails:**

  - `connection_missing`, `machine_not_found` and `mode_unavailable` refuse the start before any job is written.

- **Tests:**

  - `tests/test_model_modes_runs.py`.
