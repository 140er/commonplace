- **Owns:**

  - Cost events: `cost_events` in `runner.sqlite3` and the sums over them (`costs.py`), the `costs_summary` tool, `GET /api/costs` and the owner-set window `GET|PUT /api/settings/costs` (`costs_api.py`); design in `docs/system/cost-ledger.md`.

- **Promises:**

  - A cost event mirrors a run's totals and is appended on the report's own transaction; a started run without a known cost is counted and listed as unknown, never as zero, and money is summed per currency. Sums cover the runs of boards the caller can read; a person owner reads all; a machine credential reads nothing; only a signed-in owner sets the window.
