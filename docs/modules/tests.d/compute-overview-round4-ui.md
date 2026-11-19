- **Owns:**

  - Round 4 of the #615 review, screen side: `tests/compute_overview_ui.mjs` gains `rows-patched-in-place` and the slow, fold and plain-state rules; `tests/test_compute_overview_browser.py` captures the busy, draining and drained rows, the paused state, a no-match list and a whole runner detail, and builds its expired-request workspace 25 hours ago so its timeline reads in order; `tests/compute_workspaces_ui.mjs` counts one alert on the page for one outage.
- **Promises:**

  - No sleeps: a fake clock moves time and each check waits on a locator.
- **How it fails:**

  - A refresh that rewrites the list for an age, or two alerts for one outage, fails these tests.
- **Tests:** The three files above.
