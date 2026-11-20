- **Owns:**

  - Review round 3, screen side of Add a machine (`compute-pairing.js`, `compute.js`, `compute.css`): buttons that wrap at phone width, a way back to the list for a member, the overview told when a machine appears, is approved or is removed, and a failed check said once in words with the clock line saying only how old the shown state is.

- **Promises:**

  - On a phone, no label runs past the screen edge (buttons wrap, 44px tall); the overview reads again when the list of machines changes state, so it never says "Nothing here yet" beside a machine row; a member is never stranded in a detail.

- **How it fails:**

  - If the overview cannot be read the section is unaffected; the overview shows its own failure.

- **Tests:** `tests/compute_pairing_ui.mjs` (overview follows the machines, member way back, poll failure) and `tests/test_compute_pairing_browser.py` (every state, the two confirmations and the member states at 320, 390, 768 and 1280 with nothing past the edge).
