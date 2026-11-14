- **Promises:**

  - Split authorization verifies live connection identity and board permissions, accepts the board's persisted main seat when its coordinator field is empty, and permits the owner's own top-level PAT or OAuth connection. Worker credentials, children and other boards' seats cannot inherit owner authority.

- **Tests:**

  - `tests/test_split_stuck_recovery.py` covers authorized recovery, refused callers, audit history, first-wins retries, claim resumption, independent holds and the disabled switch.
