- **Owns:**

  - Round 5 of the #615 review: a runner with some of its runs at once in use is Busy and with all of them in use is Full (`full` status), with one count in the chip, the sentence and the blocker, and a next step that names a finished run's card and says to move it out of Doing; the fact "Allowed to start card runs" follows the drain; the payload serves `drain_scope`; a workspace shown as Unknown is not restartable (`state_unknown`) but can be stopped.
- **Promises:**

  - The words never say "slot"; moving a finished card out of Doing frees the machine for the lease and the overview alike.
- **How it fails:**

  - A restart of an Unknown workspace is `409` / `state_unknown`; nothing changes.
- **Tests:** `tests/test_compute_overview.py` (busy and full, a finished run's next step and the card moved out of Doing against the real lease, allowed to start, `drain_scope`), `tests/test_compute_workspace_lifecycle.py` (restart of an Unknown workspace).
