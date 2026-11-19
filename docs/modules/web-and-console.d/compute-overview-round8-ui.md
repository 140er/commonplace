- **Owns:**

  - Round 7 of the #615 review, screen side (`compute-overview.js`): the browser no longer makes a status from its own clock. `viewOf` only marks a row ("Last known: <status>", "As of HH:MM:SS: ...", an age said as "40 seconds before HH:MM:SS") when the last refresh failed or nothing newer has arrived for three refresh intervals (at least 90 seconds). Removed with it: the Unknown, Slow and Offline overrides, the thresholds, drain scope and next steps in the payload, the recomputed "Last seen" and the age placeholder patched into rows. The stale banner has its own button ("Try again" after a failure, "Refresh now" otherwise); a failed refresh the person asked for is announced; an open detail says once that everything in it is as of the read; a paused overview ignores the screen's own reloads; a member's intro describes only cloud workspaces; the status filter names the server's status, not the mark.
- **Promises:**

  - A row never shows a status, attention state, blocker, next step or age that the server did not give for the last read.
- **How it fails:**

  - An old read marks every row as of its time and keeps the banner's button; it never changes a status.
- **Tests:** `tests/compute_overview_ui.mjs` and `tests/test_compute_overview_browser.py` (a failed read and a paused tab a quarter of an hour old, Classic and Deck, desktop and phone).
