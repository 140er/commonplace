- **Promises:**

  - `setup_propose` (card #675) is a checked write, like `orch_config_propose`: an agent's proposal carries the rules version it read, and the freshness check runs before the proposal is stored. The owner's apply and reject are web-session routes, not tools, so the checked and exempt write inventories gain only this one name.
- **Tests:**

  - `tests/test_commonplace_rules_enforce.py` keeps the checked and exempt inventory equal to every gateway write tool, including `setup_propose`.
