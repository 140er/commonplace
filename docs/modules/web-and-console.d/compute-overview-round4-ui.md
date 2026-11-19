- **Owns:**

  - Round 4 of the #615 review, screen side (`compute-overview.js`, `compute.js`, `compute-workspaces.js` for one alert role, `compute.css`, `deck-workspace.css`): the age override also takes a Slow runner past the red window to Offline; the list is rewritten only when a row, a status or a name changed (its markup carries no ages, which are patched in place), so a refresh no longer rebuilds or re-focuses a focused row; a desktop opens the overview whatever the count and a phone opens a short list and folds a long one; loading and a failed first read are plain text with no disclosure and closed, and the state sentence comes after the headline; an empty overview says "No machine is set up yet. You can still create a cloud workspace below; it waits for a machine."; the per-row "starting is not available" blocker is said once for the page; "Try again" is marked busy while it runs and announces the failure; the cloud workspaces list's failure is a polite status while the overview owns the alert; every text control is 16px where the pointer is coarse (the Advanced settings included), checkboxes keep their own size, and the Map and Management tabs are 44px for a tablet touch.
- **Promises:**

  - One outage has one alert; a refresh does not rebuild what the person is focused on.
- **How it fails:**

  - A failed first read is plain text with "Try again" outside any fold.
- **Tests:** `tests/compute_overview_ui.mjs` (rows patched in place, slow aged, fold rules, plain failed state) and `tests/test_compute_overview_browser.py` (fleet rows, paused, no match and a whole runner detail captured).
