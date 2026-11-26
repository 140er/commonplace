- **Owns:**

  - `tests/orchstatus.mjs` (run by `tests/test_orchstatus.py`) also covers Stop and Resume on the Coworker row (offered only to the owner, confirms, refusals, one request, no lease token drawn), the failed card starts with their reasons and the recent activity list; `tests/test_orch_lease_status.py` covers the owner-resume route (token never returned, `held` while a lease is live, owner-only refusals) and the checkpoint `history`.
