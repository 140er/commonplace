- **Owns:**

  - Round 6 of the #615 review, screen side (`compute-overview.js`, `compute.css`): a row the clock aged keeps the "Last known" mark and the "As of" sentence after a failed refresh (and on a paused screen) and carries the server's Offline or Slow next step; a failed retry is announced with the attempt count; the filters repaint when a load resets them even while the status list has focus; the fold does not close while focus is inside it; remaining overview text (intro, empty state, filter label, chips, eyebrow, member note) is 14px on a coarse pointer; a long action label in the overview wraps and fills the width at 699px and below.
- **Promises:**

  - After a failed refresh no row reads as current, whether the server or the clock made its status.
- **How it fails:**

  - A paused or failed screen marks only what is old; a row the clock did not change keeps its own words.
- **Tests:** `tests/compute_overview_ui.mjs` (`overrides-keep-the-mark`, `filters-repaint-when-load-resets-them`) and `tests/test_compute_overview_browser.py` (turned off, removed, offline and never-connected details; the clock-Offline row and detail; the 320 owner workspace detail).
