- **Promises:**

  - The board's agent-status header and the Today project status render the server's `waiting_summary` verbatim as escaped text. The sentence wraps on narrow screens; clients never infer waiting counts from the visible column or a local capacity default.

- **Tests:**

  - `tests/board_waiting_summary.mjs`, wrapped by `tests/test_board_waiting_contract.py`, checks both status surfaces, escaping and an absent summary. Existing waiting-note rendering remains a fallback for older responses.
