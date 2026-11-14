- **Owns:**

  - Reserved Todo cards offer Remove assignment through the existing versioned handoff route and required reason. Deck shows it only to workspace owners, uses an empty Reason field and white confirmation pill, and explains project pickup after removal under Nobody yet. Known authority, origin and concurrent-start refusals replace the form with Assignment not removed and one Close button; unknown refusals retain the server text. Classic keeps its existing presentation. Authority and audit rules are unchanged.

- **Tests:**

  - `tests/test_remove_assignment_ui.py` executes ownership visibility, confirmation, blank/filled reason, request and refusal states without a browser. `tests/test_remove_assignment.py` checks the real handoff audit, subsequent pickup, unchanged card/audit on refusals, optional desktop/phone browser behavior and Deck tokens.
