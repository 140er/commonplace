- **Owns:**

  - The shared hub test client (`tests/_commonplace_helpers.py`) takes a per-call `timeout` (default 30 s) for tool calls that legitimately run long, such as the 500-drawer batch delete; `tests/test_commonplace_hub_helper.py` pins that a slow reply is cut off at a short limit and accepted at a longer one. Account-deletion before/after snapshots ignore the dispatcher cursors in `meta`, which advance on a background clock.
