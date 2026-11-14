- **Tests:**

  - Reservation removal has a browser-free executable click/form/submit regression in `tests/test_remove_assignment_ui.py`, real route/audit and refusal coverage in `tests/test_remove_assignment.py`, and optional desktop/phone browser cases in that file. Deck checks include ownership visibility, empty and filled confirmation, Close-only refusals, unknown-error fallback and input tokens; Classic keeps its existing behavior. `CP_BROWSER_E2E=1` requires browser evidence; `CP_BROWSER_E2E=0` skips only browser cases.
