- **Owns:**

  - `tests/conftest.py`: the autouse MCP reset also closes and clears the worker observer and resets its failure latch and loss counter after every test, so one test's stub-config request cannot silence observation for later tests.
  - `tests/test_worker_observation_isolation.py`: the regression pair (a test that latches the failure, then a test that asserts clean state).
