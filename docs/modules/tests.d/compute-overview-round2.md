- **Owns:**

  - Round 2 of the #615 review, screen side: `tests/compute_overview_ui.mjs` (the fold and a failed refresh above it, an alert written once, a detail error said once, kind-scoped filters, the waiting-for-a-machine sentence, and a browser clock skewed by +1000, -1000, +200, -200 and a day) and `tests/test_compute_overview_browser.py` (a folded phone with a stale and with a failed overview, a browser clock 1000 s ahead and behind through `page.clock`, the member no-machine view, an expired request asked 25 hours ago and a drain that follows the run it left alone, so the fixture's trail agrees with itself).
- **Promises:**

  - No sleeps: time moves with the fake clock or the store clock; each check waits on a locator. A skewed clock changes nothing on screen.
- **How it fails:**

  - A closed fold that hides a failed refresh, a healthy runner that turns Offline because the browser clock is ahead, or a fixture whose activity contradicts its own order fails these tests.
- **Tests:** The two files above, with the neighbouring Compute suites and the node-harness wrappers (`deck-compute.js` changed).
