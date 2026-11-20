- **Promises:**

  - Configured card starts preserve the original `connection_missing` code, status and actionable message when the optional Needs me reminder cannot be stored. Reminder storage failures never admit a start or create a runner job.

- **How it fails:**

  - Only I/O, SQLite and timeout errors from `note_missing` are logged with the existing safe failure logger; model-mode resolution, identity and admission refusals keep their existing behavior.

- **Tests:**

  - `tests/test_card_start_connection_bookkeeping.py` exercises real missing and shared-cache locked reminder tables, healthy reminders, valid personal starts and unreadable account settings.
