- **Owns:**

  - `tests/compute_overview_ui.mjs` (wrapper `tests/test_compute_overview_ui.py`) and `tests/test_compute_overview_browser.py` cover the Compute overview screen: the production module and the production Compute controller against an in-memory server with fake timers, and real Chromium against a real gateway with a synthetic runner fleet, at 1280x800 and 390x844 in Classic and Deck, with viewport-sized screenshots when `COMPUTE_OVERVIEW_SHOTS` names a directory. `tests/compute_ui.mjs` and `tests/compute_workspaces_ui.mjs` load the new module for the production controller.
- **Promises:**

  - The browser cases fail instead of skipping when `CP_BROWSER_E2E=1` and Playwright is missing (they share the helpers and the skip rule of `tests/test_compute_workspaces_browser.py`). Locators are by role and name, never by position; touch targets are measured at 44 px and the status list at 16 px on the phone, and nothing may reach past the right edge (checked per element, because the Deck scroller would hide it).
- **How it fails:**

  - A status that says running or online on old data, a zero for an unreported number, a machine name or address in a member's view, a refresh that moves focus or reads when the page is hidden, or an overview wider than the phone fails these tests.
- **Tests:** The files above; run them with `tests/test_compute_ui.py`, `tests/test_compute_workspaces_ui.py` and, for the browser cases, `tests/test_compute_workspaces_browser.py`.
