- **Owns:**

  - The `model_connections` Needs me group: the caller's own missing connections.

- **Promises:**

  - The group is absent when empty and never lists another person's connection. A mode-store failure leaves unrelated approvals and setup items, including their counts, available.

- **How it fails:**

  - A missing or locked mode table leaves the group and its count out rather than failing Needs me. Both raw store errors and the resolver's unavailable error are handled; a temporary failure leaves pending connection items intact.

- **Tests:**

  - `tests/test_model_modes.py` exercises missing settings, missing needs and a real locked settings table through the HTTP route, including recovery. `tests/test_model_modes_runs.py` covers the run and permission boundaries.
