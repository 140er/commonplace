- **Owns:**

  - Review round 4, screens (`compute-pairing.js`, `compute-overview.js`): the chip of a stalled setup, the copy fallback's wording, and the overview's state slot being rewritten when the first machine arrives.

- **Promises:**

  - A stalled setup never looks like a quiet wait; the overview never keeps "No machine is set up yet" beside the first machine row.

- **How it fails:**

  - If the overview cannot be read, the section is unaffected.

- **Tests:** `tests/compute_pairing_ui.mjs` (stalled chip) and `tests/test_compute_pairing_browser.py` (the first machine takes the first-run sentence away).
