- **Owns:**
  `activity.js`, `activity.css` and the small nested-route integrations in `routes.js` and `deck-workspace.js`: Compute > Activity with Recap, Time sinks, Trace and Live views.
- **Promises:**
  The four global destinations stay unchanged. The screen shows measured values or unavailable reasons, source coverage, local timezone, collection age and partial timing. Trace drilldown contains aggregated buckets only. Hidden, paused or departed views do not poll; requests do not overlap. Mobile controls have 44px targets and tables reflow at narrow widths.
- **How it fails:**
  Missing collection, denied access, failed refresh and stale snapshots remain different states. Refresh reads retained snapshots and never scans a source database. A permission or network failure does not become zero activity.
- **Tests:**
  `tests/activity_ui.mjs`, `tests/deck_workspace_pages.mjs`, `tests/test_compute_activity_browser.py` and the affected workspace navigation wrappers.
