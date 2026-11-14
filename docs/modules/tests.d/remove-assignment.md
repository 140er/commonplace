- **Tests:**

  - Reservation removal has a browser-free executable submit regression in `tests/test_remove_assignment_ui.py`, real route/audit and refusal coverage in `tests/test_remove_assignment.py`, and optional desktop/phone browser cases in the same file. `CP_BROWSER_E2E=1` requires browser evidence; `CP_BROWSER_E2E=0` skips only the browser cases.
