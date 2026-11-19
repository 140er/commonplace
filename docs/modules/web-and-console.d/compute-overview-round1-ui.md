- **Owns:**

  - Round 1 of the #615 review, screen side (`compute-overview.js`, its mount in `compute.js`, `compute.css`, and one empty-state line of the Deck map in `deck-compute.js`): the clock ages every row in place ("Last seen" recomputed from the report time, a running workspace with no report for 600 s reads Unknown, a runner past its thresholds reads Slow or Offline), a failed refresh marks every row "Last known" and "As of", a refresh repaints rows only and never rebuilds an open detail (the page's own re-render carries the section's nodes across), Close sits in the panel header, the summary names what needs attention and a "N need attention" button sets the filter, "Showing N of M", blockers are named, one Refresh button (the page's), a Pause updates toggle, the overview folds to its summary sentence on a phone, the cloud workspace wording is the list's own ("Saved, not started", no machine is not an attention state), and the Deck map stops saying "Checking what is running" when there is no machine.
- **Promises:**

  - The screen never says running, online or busy beyond what the last read and the clock allow; the words and thresholds are the server's. A refresh moves no focus, closes no open status list and announces a failure once.
- **How it fails:**

  - A failed refresh keeps the rows and marks them old; a workspaces list that moved while the overview is behind says "The overview is catching up" instead of "Nothing here yet".
- **Tests:** `tests/compute_overview_ui.mjs` (clock staleness, in-place ticks, refresh in place, pause, workspace sync, fold) and `tests/test_compute_overview_browser.py` (node identity across a refresh and a page re-render, the clock with a refused read, the phone fold, no-machine rows and form, expired, slow and unverified states, the loading state, the Deck map).
