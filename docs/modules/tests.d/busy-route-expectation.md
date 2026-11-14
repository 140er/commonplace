- **Tests:** `tests/test_projects_split.py::test_busy_routes_bounded_and_unchanged` asserts status 409
  and code `busy` on every route, the plain "did not go through" sentence on the card routes (add,
  update) and the bare reason on the orchestrator routes (inbox, split); nothing is written.
