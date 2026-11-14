- **Tests:**

  - `tests/test_runner_grant_refusal.py` asserts that another workspace's owner gets one identical 404 from the runner wing grant route for a known wing, an unknown wing and a machine that does not exist, and that the machine's own owner still gets 400 for an unknown wing.
