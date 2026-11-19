- **Owns:**

  - Round 1 of the #615 review, screen side: `tests/compute_overview_ui.mjs` (wrapper `tests/test_compute_overview_ui.py`) runs the production overview module and Compute controller against an in-memory server with fake timers and a slot-aware fake host (writes per slot are counted, so "not rebuilt" is a number); `tests/test_compute_overview_browser.py` runs real Chromium, Classic and Deck, 1280x800 and 390x844, owner and member, with `page.clock` for time and no sleeps. `tests/test_compute_overview.py` pins the shared vocabulary, including a comparison of the strings with `compute-workspaces.js`.
- **Promises:**

  - Each check waits on an event or a locator, never a fixed delay; time moves with the fake clock. A fixture's audit trail agrees with its report times (the audit is append-only, so the old report is made with an older store clock).
- **How it fails:**

  - A row that keeps saying Online or Running after a failed refresh, a rebuilt detail node, a closed status list, a "Nothing here yet" above existing records or a sentence that disagrees with the workspaces list fails these tests.
- **Tests:** The files above, with `tests/test_compute_ui.py`, `tests/test_compute_workspaces_ui.py`, `tests/test_commonplace_deck_compute.py` and the node-harness wrappers (all of them, because `deck-compute.js` changed).
