- **Tests:**

  - `tests/test_orch_recovery_view.py` pins the server rules the recovery view of Project settings relies on: a member reads the restore status but cannot acknowledge it, a wrong or cancelled acknowledgement leaves the restore pending and re-arms nothing, an acknowledged restore cannot be acknowledged again, the checkpoint carries the finding and step fields the page shows, and an owner cannot acknowledge a finding without the owning role's lease. The page cases are in `tests/orchstatus.mjs`.
