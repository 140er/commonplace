- **Owns:**

  - `tests/test_card_start_connection_bookkeeping.py`, the shared card-start reminder regression selection.

- **Promises:**

  - Both start paths run against genuinely dropped and shared-cache locked reminder tables, preserve the exact account refusal, and leave runner jobs and ledger rows absent. Healthy reminders deduplicate, valid personal accounts still start, unreadable account settings fail closed, and non-storage bookkeeping errors propagate.

- **Tests:**

  - The identical frozen test file is run against the pinned source and the committed fix; storage failure probes distinguish the intended regression from setup failures.
