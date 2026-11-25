- **Owns:**

  - Joint round 5 (`overview.py`, `pairing.py`): `pairing.pending_machines` and the pending-approval reason of `can_take_new_work` give a machine added through the setup guide, connected and not approved, the same chip, sentence, Allowed and next step in the overview as in Add a machine ("Waiting for your approval" / "Not finished"), before "turned off"; `pairing.guided_not_running` names the machines that cannot run Coworkers in this version.

- **Promises:**

  - The overview never calls a machine that is waiting for approval or not finished "Disabled" or "never contacted"; it counts as needing attention and its next step points to Add a machine.

- **How it fails:**

  - A hub without the pairing tables has no pending machines; every row reads as before.

- **Tests:** `tests/test_compute_overview.py` (waiting, failed and stalled guided runners next to the matrix, with the hub's lease as the oracle).
