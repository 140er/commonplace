- **Owns:**
  `api.py` installs the independent Compute Activity report/read routes after the runner registry is initialized.
- **Promises:**
  This adds no runner launch, heartbeat, capacity, setup or readiness behavior. The existing authenticated machine identity can only report its own explicitly consented aggregate snapshot.
- **How it fails:**
  A worker, external token, revoked machine or missing current enrolling owner is refused. Activity report failures do not admit work or change runner state.
- **Tests:**
  `tests/test_compute_activity_api.py` exercises ordinary HTTP, machine/worker identities, coowners, foreign workspaces and revocation.
