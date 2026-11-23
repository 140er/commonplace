- **Owns:**
  `activity.py`: stored aggregate consent, machine reports and enrolling-person-only snapshot reads. Source-local collection, interval accounting and the aggregate wire schema are separate modules. App behavior is documented in `docs/system/compute-activity-app.md`.
- **Promises:**
  Every enumeration, consent, recap and detail read rechecks the current person-owner session, workspace, machine registry binding, machine connection and grant. Reports require the enrolled machine and matching explicit consent. Account exports apply the same enrolling-owner restriction; account and workspace purge remove the corresponding rows. Stored detail reads contain only aggregate buckets; the app initially exposes retained summary lists without detail actions.
- **How it fails:**
  Expired or revoked connections, disabled machines, changed grants, foreign workspaces, other owners, members and external agent tokens cannot disclose retained snapshots. Unknown wire keys, replayed older reports and malformed queries fail without echoing values. Missing collection is unavailable data rather than zero work. No report grants execution, readiness, heartbeat or capacity authority.
- **Tests:**
  `tests/test_compute_activity_api.py`, `tests/test_compute_activity_authority.py`, `tests/test_compute_activity_accounts.py` and `tests/test_compute_activity_admission.py` exercise real protected HTTP and actual account lifecycle operations.
