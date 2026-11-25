- **Owns:**

  - Review round 1, server side (`pairing.py`, `pairing_routes.py`, `runner/machine_setup.py`, `runner/installer.py`): `derive` and the check route read an unused code past its lifetime as `expired` before any older report; the check route takes a `files` report while the code is live; a pairing that connected and did not finish all four steps within 15 minutes is `stalled`; the machine reports the step a failure reached and tells a refused exchange (`EnrollmentRefused`) from a lost connection; the command fetches its launcher first and is https unless it points at the machine itself.

- **Promises:**

  - A failed check, or a failed step, never outlives the code it was reported for; one next step for a failed machine (Set up again), never a command the page does not hold; a member reads the owner's decision in the third person. The machine says "continues" nowhere, prints its fingerprint before the last step is reported, and removes the leftovers of a cancelled earlier enrollment before installing the new one.

- **How it fails:**

  - A write that fails after the code was spent cannot be reported (no credential yet): the page stays at Machine connected, reads stalled after 15 minutes and offers Remove; the machine prints that the code was used and what to do.

- **Tests:** `tests/test_compute_pairing.py` and `tests/test_machine_setup.py` (all three positions of a failed write or refused exchange, with the production transport's error types).
