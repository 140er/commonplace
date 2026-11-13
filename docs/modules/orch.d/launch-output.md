- **Tests:**

  - `tests/test_orch_runner.py` keeps the existing launch payload and request digest stable while a legacy runner store gains the nullable launch-output column. Starts that have no captured failure keep that column NULL.
