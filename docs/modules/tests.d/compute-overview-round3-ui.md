- **Owns:**

  - Round 3 of the #615 review, screen side: `tests/compute_overview_ui.mjs` gains a monotonic clock next to the skewed wall clock (`clock-step`, `every-runner-status-ages`, `scope-and-state`) and `tests/test_compute_overview_browser.py` uses `page.clock.set_system_time` for a stepped wall clock, a removed open item and one outage with one alert.
- **Promises:**

  - No sleeps: the fake clock moves time and each check waits on a locator.
- **How it fails:**

  - A wall clock step that moves an age, a "Try again" with no feedback or a focus lost to the page fails these tests.
- **Tests:** The two files above, with the neighbouring Compute suites and the node-harness wrappers (only `compute.js` and `compute-overview.js` changed, but the wrappers load them).
