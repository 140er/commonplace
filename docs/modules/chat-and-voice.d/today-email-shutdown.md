- **Promises:**

  - The Today email thread never queries the control database once it is stopped. Stopping it, which closing the database does first, ends a pass that is still settling without running it, and a pass in flight checks the stop under the database lock before each of its statements, so a late tick cannot run SQL after the connection closes. A busy tick never holds closing the database for more than one second.
- **How it fails:** `tests/test_commonplace_today_email.py` fails if closing the database while the notifier settles runs a statement after the server closed, or if a stopped pass issues any statement.
