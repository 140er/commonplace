- **Owns:**

  - Review round 1, screen side of Add a machine (`compute-pairing.js`, `compute.css`, a carry hook in `compute.js`): numbered run steps above the command, the approval sentence and plain sheet words, Approve waiting for the machine's fingerprint, Set up again (cancel the old setup, then a prefilled form) as the one next step of a failed or stalled machine, "Not run" step states, confirm dialogs that open on Go back, read-only member intro, and a section that repaints only when what it shows changes and survives the screen's own rebuilds with its command, focus and selection.

- **Promises:**

  - A check that finds nothing new writes only the clock line. A rebuild of the Compute screen puts the section back as it was (and reselects the part of the command that was selected) when the state is unchanged. Enter on a confirm dialog's default answer never removes access. Nothing says the machine can run Coworkers before a provider is connected and an owner attests it.

- **How it fails:**

  - If cancelling the old setup fails (stale, refused), the form does not open and the reason is said; the old setup is untouched.

- **Tests:** `tests/compute_pairing_ui.mjs` (run by `tests/test_compute_pairing_ui.py`) and `tests/test_compute_pairing_browser.py` (a no-change poll keeps the command node, focus and selection; Enter twice removes nothing; Set up again over a failed machine).
