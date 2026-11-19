- **Owns:**

  - Round 6 of the #615 review, server words: the runner fact "Allowed to start runs" is derived from the status the chip shows ("No: it was removed", "No: it is turned off", "No: it is not set up and approved yet", "No: it is draining" or "drained", "Yes, once it reports" for an offline or never-connected runner, otherwise "Yes"); a draining or drained runner holding a finished card names the card and the action; a finished card counts a runner as needing attention; the row and usage line use the same "N of M runs at once in use" count; "Drained." and "Draining." open their sentences; the payload serves the Offline and Slow next steps (`next_steps`) for the screen's age override; refusals for a workspace that cannot start say it "is not available in this version"; the fact label is "Runs at once".
- **Promises:**

  - What a runner is allowed to do never disagrees with its chip.
- **How it fails:**

  - Nothing new is refused; only words and one attention rule changed.
- **Tests:** `tests/test_compute_overview.py` (one case per status for "Allowed to start runs", attention for a finished card, the row count, drained with a finished card, `next_steps`) and `tests/test_runner_drain.py`.
