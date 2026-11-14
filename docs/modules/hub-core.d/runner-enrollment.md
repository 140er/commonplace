- **Owns:**

  - A narrow enrollment admission check before generic machine/worker token dispatch, and the two code-only bootstrap paths' anonymous transport admission.

- **Promises:**

  - Setup controls return 404 to non-owner or token callers, and to off workspaces except a signed-in owner's explicit settings write. Bootstrap rejects all session/bearer authority. Settings PUT requires the same JSON/same-origin protection as other owner writes. Unrelated HTTP paths, tool catalogs and event envelopes retain their contracts.

- **Tests:**

  - `tests/test_runner_enrollment.py`, the public route inventory, Space catalog fixtures and pinned off-state HTTP/audit/tool baselines cover this boundary.
