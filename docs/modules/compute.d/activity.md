- **Owns:**
  `activity.py`, `activity_collector.py`, `activity_math.py` and `activity_schema.py`: source-local sparse read-only aggregation, exclusive interval accounting, a strict versioned aggregate contract, machine-authenticated reports and enrolling-person-only snapshot/trace reads. The privacy and counting decision is in `docs/system/compute-activity.md`.
- **Promises:**
  No prompt, title, path, command argument, file content or raw event crosses the report boundary. Missing metrics retain reasons and coverage. Collection is opt-in, bounded and local; reports never change execution, heartbeat, readiness or capacity. Current machine authority and person ownership are checked again for every read and report.
- **How it fails:**
  Unavailable source data raises a fixed redacted error; unconsented reports are refused. Changed grants, revoked connections, disabled machines and other owners cannot disclose retained snapshots. Unknown wire keys, unbounded text and malformed queries are refused without value reflection. A capped scan remains explicitly partial.
- **Tests:**
  `tests/test_compute_activity_collector.py`, `tests/test_compute_activity_api.py`, `tests/test_compute_activity_ui.py` and `tests/test_compute_activity_browser.py`.
