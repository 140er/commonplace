- **Owns:**

  - Review minors of the cloud workspaces card (`compute-workspaces.js`, `compute.css`, `deck-shell.css`, `palette.js`): the stale-edit banner and reload sentences, the refused-discard row label and its announcement (the reason is the description of "Keep it"), one "not chosen yet" label for no machine, the "Image address" label and hint, a review line when the chosen machine is not verified, one `PRIVATE_IMAGE` sentence, `request_pending` wording, history that reads failure from the status, a Close in the detail header, `role="list"` on the list, one static alert node (`data-ws-problem`) for a failed load, a dimmed disabled machine select, the plain first-run line, the 11px Deck phone tab label with its 320px spacing, and the palette entry "Cloud workspaces".
- **Promises:**

  - A failed load is announced once, also after a failed Reload (the status line stays silent about it); a refused discard's reason is read when focus reaches "Keep it"; the Deck phone tab labels do not touch at 320px; a disabled field looks disabled.
  - One sentence says why a private image does not work wherever it comes up.
- **How it fails:**

  - A person who chooses an unverified machine is told in the review what that means; saving still works.
- **Tests:** `tests/compute_workspaces_ui.mjs` (case `minors-621`, via `tests/test_compute_workspaces_ui.py`) and `tests/test_compute_workspaces_browser.py` (states at 1280, 390, 320 and 768 touch in Classic and Deck, the disabled field, the failed load, the 320px tab labels).
