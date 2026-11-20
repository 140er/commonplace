- **Owns:**
  `tests/activity_fixtures.py`, `tests/activity_http.py`, `tests/test_compute_activity_admission.py`, `tests/test_compute_activity_collector.py`, `tests/test_compute_activity_api.py`, `tests/test_compute_activity_ui.py`, `tests/activity_ui.mjs` and `tests/test_compute_activity_browser.py`; the existing workspace navigation harness also expects the new nested Activity link.
- **Promises:**
  Real fixture SQLite databases, read-only write refusals, indexed pagination, interval unions, cross-midnight and both daylight saving boundaries, native-child deduplication, cancellation, cumulative-context exclusion, PR deduplication, privacy canaries, actual protected HTTP and normal product sign-in are covered. Fixtures contain only synthetic source text. Browser evidence is captured after sign-in, without credentials, authentication screenshots or event exports.
- **How it fails:**
  Source text escaping an aggregate, unknown wire fields, falsely zero missing data, double-counted time/tokens, incorrect authorization, broken drilldown or phone overflow fails a focused assertion. A missing browser runtime is an unavailable check, never passing evidence.
- **Tests:**
  The named tests and the four required public-surface, evaluation and documentation guard files.
