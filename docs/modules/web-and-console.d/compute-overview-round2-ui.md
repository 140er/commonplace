- **Owns:**

  - Round 2 of the #615 review, screen side (`compute-overview.js`, `compute.css`, one line of the Deck map in `deck-compute.js`): the clock is the server's ("now" is the server time at the last read plus the time elapsed here, so a browser clock that is minutes or hours off cannot turn a healthy runner Offline or a fresh workspace Unknown) and a client-side Offline or Unknown names itself as a blocker instead of sitting beside "Nothing is blocking"; the refresh state (meta line, alert, status line) sits above the fold and the fold's summary names a failed refresh, so a folded phone overview never reads as current after a failed poll; the fold opens for a failed first read and starts closed above five items; the failed refresh is spoken once (the alert) and a failed detail load once; Pause automatic updates says that ages keep counting; the headline and body never repeat each other; the summary says how many workspaces wait for a machine; status filters follow the chosen kind; a runner is defined; the Deck map has no "0 of 0 machines ready".
- **Promises:**

  - A closed fold still shows that the data is old; nothing in the overview is spoken twice for one event; a skewed browser clock changes nothing on screen.
- **How it fails:**

  - A failed first read opens the fold with "Try again"; a failed detail load keeps what the list knew and says so once.
- **Tests:** `tests/compute_overview_ui.mjs` and `tests/test_compute_overview_browser.py` (folded phone with a failed refresh and a failed first read, a browser clock 1000 s ahead and behind, member and no-machine views, kind-scoped filters).
