- **Owns:**

  - The `model_connections` Needs me group: the caller's own missing connections.

- **Promises:**

  - The group is absent when empty, never lists another person's connection and is not part of the owner's items.

- **How it fails:**

  - A store that cannot be read leaves the group out rather than failing Needs me.

- **Tests:**

  - `tests/test_model_modes.py` and `tests/test_model_modes_runs.py`.
