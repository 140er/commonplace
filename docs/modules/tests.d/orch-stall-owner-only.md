- **Tests:**

  - `tests/test_orch_stall_owner_only.py` covers the no-progress nudge ignoring an expired restricted-origin card and a pending-proposal card (not stalled, no wake, streak cleared), an ordinary expired claim still stalling, an owner-only card not hiding an ordinary one, and a board file without the two columns.
