- **Owns:**

  - Cost budgets: `cost_budgets` and `cost_budget_marks` in `runner.sqlite3`, the spend, warning and stop rules (`budgets.py`) and the owner-only routes `GET|PUT|DELETE /api/settings/budgets` (`budgets_api.py`); the stop is checked in `jobs.create_in` and when a queued start is leased, the marks are written by `ledger.report` and `jobs.result`; design in `docs/system/cost-budgets.md`.
- **Promises:**

  - With no budget set nothing changes. A new start is refused with `budget_exceeded` (naming scope, budget and window, no amounts) when a budget of its workspace, agent connection, project or card has spent its limit in the window, judged in the start's own write transaction; unknown-cost and other-currency runs never count; running work is flagged, never stopped; the 80% warning is one mark and one Needs me item per window and scope; only a signed-in owner reads or changes budgets (others get 404) and changes are audited.
