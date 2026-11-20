- **Owns:**

  - `tests/test_compute_overview.py`, `tests/test_compute_workspace_lifecycle.py` and `tests/test_runner_drain.py` (helpers in `tests/compute_overview_helpers.py`: a synthetic runner fleet on the real compute fixture with a movable clock, and a fake lifecycle executor that drives `claim` and `report`) cover the Compute overview REST against the real stores: offline, never-connected, disabled and revoked runners, unknown and stale usage, spend with unknown costs, target machines, cloud workspace phases and failures, the member view and its leak checks, personal access tokens, the state machine and every refused move, idempotent repeats, stale tokens, effect previews and drain enforcement in the lease path, items beyond the list cap reached by id, request expiry (a request past 24 hours is never claimed), drain request ids and the same-transaction drain log, the not-verified blocker, and the route classification probes in `tests/test_commonplace_public_surface.py` (the overview routes authenticate first under the `/api/compute/` prefix and refuse every token, full or read-only).
  - Member fixture and exact-copy assertions retain the Admin parent in the hidden runner inventory message.
- **Promises:**

  - No test starts a container or reaches a machine; the lifecycle is exercised through the seam with a fake executor. A runner's last signal is set relative to the fixture clock, so "offline" is reached by moving the clock, not by sleeping.
- **How it fails:**

  - A status that says running or online on a stale report, a zero where usage is unknown, a machine name or address in a member's answer, a start request that moves a phase, or a drain that still leases fails these tests.
- **Tests:** The three files above; run them with the affected Compute and runner tests (`tests/test_compute_workspaces.py`, `tests/test_runner_capacity_contract_388.py`).
