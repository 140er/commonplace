- **Owns:**
  `activity.py`, `activity_collector.py`, `activity_math.py` and `activity_schema.py`: source-local sparse read-only aggregation, exclusive interval accounting, a strict versioned aggregate contract, machine-authenticated reports and enrolling-person-only snapshot/trace reads. The privacy and counting decision is in `docs/system/compute-activity.md`.
- **Promises:**
  No prompt, title, path, command argument, file content or raw event crosses the report boundary. Missing metrics retain reasons and coverage. Collection is opt-in, bounded and local; reports never change execution, heartbeat, readiness or capacity. Current machine authority and person ownership are checked again for every read and report.
- **How it fails:**
  Unavailable source data raises a fixed redacted error; unconsented reports are refused. Changed grants, expired or revoked connections, disabled machines and other owners cannot disclose retained snapshots. Enumeration, consent, recap and trace revalidate current machine binding and enrolling-owner authority. Unknown wire keys, unbounded text and malformed queries are refused without value reflection. Every capped source reports its inspected-row budget and overflow; a missing retained trace does not imply zero work.
- **Tests:**
  `tests/test_compute_activity_collector.py`, `tests/test_compute_activity_regressions.py`, `tests/test_compute_activity_api.py`, `tests/test_compute_activity_ui.py` and `tests/test_compute_activity_browser.py`.
