- **Owns:**
  `tests/activity_http.py`, the Activity API, authority, admission, account lifecycle, UI and browser tests, and the small nested-route additions to the workspace and public-surface inventories. These app tests consume the separate collector fixture in `tests/activity_core_fixtures.py`.
- **Promises:**
  Synthetic snapshots pass through the real protected gateway, enrolled machine identity and normal app sign-in. Assertions cover owner isolation, current connection/grant fences, malformed reports, retained pagination, two-owner account exports, account/workspace purge, global ranking, filter round trips, keyboard focus and phone semantics. Source replacement, acknowledged revocation and disabled enumeration are paired with pending reads and 503/network failures; active-consent offline controls preserve the retained snapshot and query. Existing public-surface assertions remain intact. Browser dependencies are required; an unavailable runtime fails instead of skipping.
- **How it fails:**
  Source text escaping an aggregate, wrong authorization, stale query identity, mixed pagination ranges, lost column semantics or phone overflow fails a focused assertion. This app slice has no detail action; detail-only tests are introduced with that feature. Collector budgets, interval semantics and complexity assertions stay with the collector tests.
- **Tests:**
  The named affected app tests, existing account and workspace wrappers, and the four required public-surface, evaluation and documentation guards. No full-suite fallback is required.
