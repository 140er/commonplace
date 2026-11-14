- **Promises:**

  - The accepted runner config pins one machine, so unrelated machines do not inflate the board's displayed runner capacity. Config and project limits accept 1 through 100 while machine grants and resource headroom still bound starts.

- **Tests:**

  - `tests/test_agent_limit_surfaces_388.py` confirms REST and MCP config proposals accept 100 and reject 101; `tests/test_runner_capacity_contract_388.py` proves the high configured cap does not bypass the machine ceiling.
