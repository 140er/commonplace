- **Owns:**

  - `tests/test_model_modes.py` and `tests/test_model_modes_runs.py`, the model mode entries of the public surface inventory and the tool list fixture, and the model-mode schema initialization in the Coworker dispatch fixture.

- **Promises:**

  - The dispatch fixture initializes the real model-mode tables without adding mode settings or personal connections; its legacy shared-machine admissions exercise the same resolver as production. The matrix of modes, the owner-only setting, private machines, never-silent refusals and the migration of an older store are pinned. Connection IDs are deterministic. Both run tables store exactly the pinned account fields and no credentials. Queued chat rejects valid account switches and same-label connection replacements; unchanged eligible personal machines still lease, and schema upgrades preserve older queued rows while refusing their unknown accounts.

- **How it fails:**

  - Real missing-table and shared-cache table-lock cases fail if Needs me loses unrelated items, returns a server error or clears a pending connection during an outage. Account assertions fail if either the job or ledger contains credentials or the wrong account.

- **Tests:**

  - The two files themselves, including the identical missing/locked-store selection on the pinned base and fixed head.
