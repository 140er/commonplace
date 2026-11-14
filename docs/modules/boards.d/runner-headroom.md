- **Promises:**

  - Configured board summaries show the project limit and the accepted config's runner capacity as “limit N, runner capacity M”. Board lists expose `runner_capacity` and `effective_start_limit`, the minimum of the two ceilings, while preserving existing waiting counts and read authority.

- **Tests:**

  - `tests/test_runner_capacity_contract_388.py` covers the board fields and summary at a project limit of 100; `tests/test_board_waiting_contract.py` protects existing waiting summaries and visibility.
