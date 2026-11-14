- **Tests:**

  - `tests/test_cost_budgets.py` covers budgets: sums below 80% do nothing, one warning per window and scope, refusal of new starts at 100% on that scope only (and each scope kind), running work not killed, queued starts cancelled at lease, unknown-cost and other-currency runs not counted, window rollover, the two concurrent-start races, owner-only routes with audit and workspace isolation, validation, and the clean-workspace case with no rows and no new tool.
