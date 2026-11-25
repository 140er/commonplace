- **Owns:**

  - Review round 2, screen side of Add a machine (`compute-pairing.js`, `compute.css`): the copy note written in place (outside the repaint signature), a once-only status line for the first failed check, numerals on the run steps, the command as a named region, a full-width "What it may do without asking" field, `abandoned` as a closed state, Set up again carrying requesters, and the attested/provider wording.

- **Promises:**

  - A failed copy that selected the command, and the checks after it, leave the command's node, focus, selection and status region alone. A failed check is announced once and a recovery clears it.

- **How it fails:**

  - If the status line cannot be written the page still shows the failure in its clock line.

- **Tests:** `tests/compute_pairing_ui.mjs` (copy in place, poll failure announced, requesters kept) and `tests/test_compute_pairing_browser.py` (selection through checks, numerals and region, tablet width, failed check said once).
