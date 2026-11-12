- **Owns:**

  - Account export and deletion regressions (`tests/test_commonplace_account_` files, fixtures in `tests/_commonplace_account_helpers.py`) run a real gateway over temporary data: archive content and redaction, constant export memory, the deletion switch, grace period, blockers, an interrupted purge, and a before/after proof that other accounts' rows and files are unchanged.

  - `tests/test_eval_guards.py` allows the account purge's one dynamic `DELETE FROM {table} WHERE {clause}` (control-database tables of one account, from `commonplace/account/tables.py`) under its own path and text only; `test_a4_account_purge_allowance_is_narrow` pins that the guard still fires on the same statement elsewhere, on any other dynamic statement in that file and on thread-store statements.
