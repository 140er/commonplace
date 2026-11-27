- **Promises:**

  - The Today email thread never queries the control database once it is stopped. Stopping it, which closing the database does first, ends a pass that is still settling without running it, and a pass in flight checks the stop under the database lock before each locked block of its own statements, and before it starts a Today-list or link read, so a late tick cannot start SQL after the connection closes. A read already under way finishes; closing the database waits at most one second for it. The lock is not held while the Today list is computed.
- **How it fails:** `tests/test_commonplace_today_email.py` fails if closing the database while the notifier settles runs a statement after the server closed, or if a stopped pass issues any statement.
