- **Owns:**

  - Round 8 of the #615 review, screen side (`compute-overview.js`, `compute.css`): the paused or stalled-tab banner is a plain note and only a failed read is red; the banner's button reads "Refresh" like the page's button.
- **Promises:**

  - Red means a read failed.
- **How it fails:**

  - The banner and its button stay hidden while the data is fresh.
- **Tests:** `tests/compute_overview_ui.mjs` and `tests/test_compute_overview_browser.py` (the hourly-limit runner and the paused tab, Classic and Deck, desktop and phone).
