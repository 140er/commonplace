- **Owns:**

  - Reserved Todo cards say who holds them (a Reserved for marker on the board card and in the card header, from the card's `reserved_for` display name) and offer Drop reservation through the existing versioned handoff route. The reason is optional; a blank reason is saved as the default note Reservation dropped. Deck shows the control only to workspace owners, uses an empty Reason (optional) field and white confirmation pill, and explains project pickup after the drop under Nobody yet. Known authority, origin and concurrent-start refusals replace the form with Reservation not dropped and one Close button; unknown refusals retain the server text. Classic keeps its existing presentation. Authority and audit rules are unchanged.

- **Tests:**

  - `tests/test_remove_assignment_ui.py` executes ownership visibility, confirmation, blank/filled reason, request and refusal states without a browser. `tests/test_remove_assignment.py` checks the real handoff audit, subsequent pickup, unchanged card/audit on refusals, optional desktop/phone browser behavior and Deck tokens.
