- **Promises:**

  - The first page of a scoped overview probe (`thread_scope` with `probe`) reuses a clean `overview_too_large` verdict for a palace and collection for up to an hour instead of re-running the full wing/room GROUP BY on every probe. Only a clean tally is remembered: a too-large or failed result is never reused, later pages never scan, and a different palace or an expired verdict recounts. Unscoped status, list and taxonomy reads still use the write-keyed exact cache and are unchanged.

- **Tests:**

  - `tests/test_scoped_taxonomy_scan_budget.py`: five first-page probes with a write between each run one tally scan, the verdict expires on the injected clock, later pages and too-large results never reuse it, and another palace does not inherit it.
