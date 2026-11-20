- **Owns:**
  The two Compute Activity table rules and their enrolling-owner restriction in workspace account exports.
- **Promises:**
  Account and workspace purge remove the corresponding consent/snapshot rows. Even a coowner's workspace export includes only their own Activity rows; the enrolling owner can export their own aggregates. Other tables retain their existing export and deletion behavior.
- **How it fails:**
  Missing classification, another owner's aggregate in an export, or orphaned rows after account/workspace purge fails a focused lifecycle assertion.
- **Tests:**
  `tests/test_compute_activity_accounts.py` uses two real enrolled owners, real reports, account export streaming and actual account/workspace purge paths; existing account lifecycle tests also apply.
