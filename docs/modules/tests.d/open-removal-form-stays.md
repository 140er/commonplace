- **Tests:**

  - `tests/remove_assignment.mjs` (run by `tests/test_remove_assignment_ui.py`) draws the open card again while a Remove assignment form is open, in both looks: the same card version leaves the form and its typed reason in place, a new version draws the drawer, no form draws it as before, and Cancel returns to the drawer. `tests/test_remove_assignment.py` covers the form in a browser.
