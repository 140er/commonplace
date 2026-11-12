- **Tests:**

  - `tests/test_eval_guards.py` allows the account purge's one dynamic `DELETE FROM {table} WHERE {clause}` (control-database tables of one account, from `commonplace/account/tables.py`) under its own path and text only; `test_a4_account_purge_allowance_is_narrow` pins that the guard still fires on the same statement elsewhere, on any other dynamic statement in that file and on thread-store statements.
