- **Promises:**

  - Automatic selection and card reads use `boards.why.waiting` for blocker reasons. Explicit claims report the same sentence. Blocker projections accept only same-board edges, including when old stored data contains an invalid edge.
  - Board reads expose `waiting_summary`, a compact sentence derived from the per-card reason counts and effective capacity. Archived cards do not contribute to waiting counts. MCP board lists include the sentence; card lists retain each card's reason. Disabled orchestration retains the existing output and selection behavior.

- **Tests:**

  - `tests/test_board_waiting_contract.py` covers capacity, named blockers, approval waits, matching counts, shared selection, read isolation and unchanged default claims.
