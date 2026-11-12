- **Owns:**

  - Account export and deletion regressions (`tests/test_commonplace_account_` files, fixtures in `tests/_commonplace_account_helpers.py`) run a real gateway over temporary data: archive content and redaction, constant export memory, the deletion switch, grace period, blockers, an interrupted purge, and a before/after proof that other accounts' rows and files are unchanged.
