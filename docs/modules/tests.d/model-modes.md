- **Owns:**

  - `tests/test_model_modes.py` and `tests/test_model_modes_runs.py`, and the model mode entries of the public surface inventory and the tool list fixture.

- **Promises:**

  - The matrix of modes, the owner-only setting, private machines, never-silent refusals and the migration of an older store are pinned. Connection IDs are deterministic. Both run tables store exactly the pinned account fields and no credentials.

- **How it fails:**

  - Real missing-table and shared-cache table-lock cases fail if Needs me loses unrelated items, returns a server error or clears a pending connection during an outage. Account assertions fail if either the job or ledger contains credentials or the wrong account.

- **Tests:**

  - The two files themselves, including the identical missing/locked-store selection on the pinned base and fixed head.
