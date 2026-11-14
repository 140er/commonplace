- **Tests:**

  - `tests/test_split_stuck_recovery.py` reproduces missing seat bindings and refused owner connections, exercises signed-in owner recovery through the existing REST route, and checks unauthorized callers, scope limits, first-wins audit history and resumed claims. Split access controls use an actual worker credential rather than an owner connection as the unauthorized caller.
