- **Owns:**

  - Reserved Todo cards offer Remove assignment in both looks. The confirmation asks for the existing required reason and posts a versioned handoff to `anyone`; the drawer explains board eligibility before and after removal. Active claims and unapproved proposals do not offer this action. Refusals stay in the drawer with the server's reason and next step; existing origin, membership and audit rules remain authoritative. Deck uses the drawer's surface and input tokens, with the danger token confined to the confirmation button.

- **Tests:**

  - `tests/test_remove_assignment_ui.py` executes the drawer, confirmation and submit path without a browser, covering the payload, blank reason and unchanged refusal messages. `tests/test_remove_assignment.py` checks the real handoff audit, subsequent pickup and unchanged card/audit on active-work, member, origin and blank-reason refusals. Optional browser checks exercise both looks and sizes, refusal messages and Deck tokens; evidence runs require the browser, while browser-free runs keep the request regression.
