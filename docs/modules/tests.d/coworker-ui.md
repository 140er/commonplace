- **Owns:**

  - `tests/coworker_ui.mjs` (via `tests/test_coworker_ui.py`) and the `routing` case of `tests/compute_ui.mjs` cover the Coworkers and Compute pages, request fencing and navigation isolation with synthetic payloads; `tests/test_coworker_browser.py` and `tests/test_compute_browser.py` drive the real gateway UI in Chromium with synthetic blocked providers and no operator data; `tests/test_coworker_controls.py` and `tests/test_coworker_progress.py` cover canonical task controls and per-viewer progress.
