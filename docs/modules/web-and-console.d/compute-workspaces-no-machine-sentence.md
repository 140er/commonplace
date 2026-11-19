- **Owns:**

  - The one sentence for a cloud workspace when no machine is set up at all (`NO_MACHINES_SENTENCE` in `compute-workspaces.js`, mirrored by `overview.py` and compared by a test): "No machine is set up yet. Starting will need one, which is not available yet." It no longer mentions the Choose later option, which the Machine field already shows.
- **Promises:**

  - The workspaces list and the Compute overview describe a machine-less record in the same words.
- **How it fails:**

  - A change to one sentence without the other fails `tests/test_compute_overview.py` (the string comparison).
- **Tests:** `tests/test_compute_overview.py`, `tests/test_compute_workspaces_browser.py`.
