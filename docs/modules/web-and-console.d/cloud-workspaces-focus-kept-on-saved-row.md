- **Owns:**

  - Focus after a save in the cloud workspaces section (`holdFocus` in `compute-workspaces.js`): the saved row's heading takes focus, and a later repaint of the whole Compute screen (the overview reads the list again once it changed) puts focus back on that same heading instead of dropping it to the page.
- **Promises:**

  - After "Confirm and save", keyboard and screen-reader users stay on the saved row's heading in the Classic and the Deck look, whatever repaints the screen next.
- **How it fails:**

  - A heading that `holdFocus` cannot name is not restored: focus falls to the page, and `test_a_background_repaint_keeps_focus_on_the_saved_row` and `test_owner_creates_edits_and_reads_status` fail with the heading "inactive".
- **Tests:** `tests/test_compute_workspaces_browser.py`.
