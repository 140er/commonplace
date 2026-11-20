- **Promises:**

  - Manual card starts preserve the exact `connection_missing` refusal even when writing the optional Needs me reminder fails. A failed reminder write does not claim an item was saved and does not queue work.

- **How it fails:**

  - The bookkeeping error policy matches chat refusals: catch only I/O, SQLite and timeout errors around `note_missing`, log safely, and retain the account decision. Other errors remain visible.

- **Tests:**

  - `tests/test_card_start_connection_bookkeeping.py` pins manual and configured refusal behavior and checks that successful personal starts still record the original account.
