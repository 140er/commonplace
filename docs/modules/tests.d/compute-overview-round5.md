- **Owns:**

  - Round 5 of the #615 review: `tests/test_compute_overview.py` pins the busy and full words and counts, a finished run's next step and the real lease after the card leaves Doing, allowed-to-start and `drain_scope`; `tests/test_compute_workspace_lifecycle.py` pins the restart refusal; `tests/compute_overview_ui.mjs` pins attention only for Offline, the plain state, the alert written once and the attention filter reset; `tests/test_compute_overview_browser.py` has a build-runner with a finished run whose card is still Doing (Busy, with the next step) and the filter reset capture.
- **Promises:**

  - Each check fails on the code before it; no sleeps.
- **How it fails:**

  - Two words for one state, a rewritten alert node or an attention override for Slow fails these tests.
- **Tests:** The files above.
