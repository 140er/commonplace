- **Owns:**

  - Round 5 of the #615 review, screen side (`compute-overview.js`, `compute.css`): the screen's age override sets attention only for Offline (Slow to report and Unknown wait) and takes its drain sentence from the payload; the attention filter returns to all statuses when nothing needs attention; loading, a failed first read and an empty overview are plain headline text with no operable summary; the failed-read alert is one static node (the attempt count is plain text beside it, "Try again" is busy by attribute), a retry and a detail load say so once through the status line; overview body text is 14px on a coarse pointer.
- **Promises:**

  - One outage has one voice; the alert is written once.
- **How it fails:**

  - A failed first read shows the alert and "Try again" with no fold.
- **Tests:** `tests/compute_overview_ui.mjs` and `tests/test_compute_overview_browser.py`.
