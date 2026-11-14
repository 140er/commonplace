- **Tests:**

  - `tests/test_commonplace_release_loop.py` independently checks take and drop against the same-numbered card in another workspace, with specific authority refusals and both stores unchanged. Its member matrix covers every target status from todo and doing, handoff, exact refusal text, and unchanged holders when restating the status, through both service and HTTP routes.
  - `tests/test_commonplace_release_loop_browser.py` waits for the card panel. A real browser handoff succeeds with an unrelated request held open, proving that network idle is unnecessary.
