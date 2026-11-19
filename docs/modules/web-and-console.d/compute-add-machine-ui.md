- **Owns:**

  - The Add a machine section of the Compute screen (`compute-pairing.js`, additive hooks in `compute.js`, styles in `compute.css`; design in `docs/system/compute-enroll.md`): one shared module for Classic and the Deck Management tab. It turns on machine setup (owner), makes a one-time command for a runner machine, shows the machine as it connects, shows each setup step with its cause and next step when one fails, asks for the owner's single approval with the machine's name, fingerprint and grants in plain words, and removes or sets up again with the same values. Members get a read-only list.

- **Promises:**

  - Every sentence about a state, a failed step and a refusal comes from the server (`/api/compute/pairings/options` and each pairing); nothing says a machine runs Coworkers, and the page says once what this version does not do. The command is held in memory for the pairing just made, never stored or put in the address bar, and a copy that cannot be proven is reported as failed with the command selected.
  - The section checks on the machine only while its state can still change, never in a hidden tab, and says a state once when it changes (not on every check). A failed check keeps the last known state and says when it was true. A repaint never takes focus from the control in use. There is no sticky or fixed bar; on touch screens every control is 44px tall and inputs are 16px.

- **How it fails:**

  - A refusal names its field next to the control and in one summary that takes focus once; an answer that never came says nothing is confirmed and keeps the request id so a retry cannot make two setups. A stale approval or removal says why and reads the one pairing again.

- **Tests:** `tests/compute_pairing_ui.mjs` (run by `tests/test_compute_pairing_ui.py`) and `tests/test_compute_pairing_browser.py`.
