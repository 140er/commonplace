- **Owns:**

  - Review round 2, server side (`pairing.py`, `pairing_routes.py`, `runner/machine_setup.py`): `accept` requires all four steps `ok` and a setup that is not stalled; the close reason `abandoned` for a failed or stalled setup; the read-only `probe` step; audited check denials; member wording; the machine prints a fixed reason on every failure path, never uninstalls an open earlier machine, and reports a lost connection at the exchange as the `files` step.

- **Promises:**

  - The authority to approve is the server's: before the machine has finished, `accept` is `not_finished` (or `stalled`). Removing a failed setup is never worded as the owner's refusal. A probe changes nothing. The window's reason lines never carry the code, a token or an exception's own text.

- **How it fails:**

  - A site that cannot say whether an earlier setup is still open leaves it untouched and says so; a wrong-typed report field is a 400, not a server error.

- **Tests:** `tests/test_compute_pairing.py` and `tests/test_machine_setup.py` (approval rule, abandoned close, probe, audit, window output for A, healthy earlier machine survives for D, `cause.step == "files"` for E).
