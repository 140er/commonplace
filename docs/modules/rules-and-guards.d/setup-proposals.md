- **Promises:**

  - `setup_propose` (card #675) is a checked write, like `orch_config_propose`: an agent's proposal through the MCP tool carries the rules version it read, and the freshness check runs before the proposal is stored. The REST routes answer only the workspace owner and drop the web shell's rules version without checking it. The owner's apply and reject are web-session routes, not tools, so the checked and exempt write inventories gain only this one name.
- **Tests:**

  - `tests/test_commonplace_rules_enforce.py` keeps the checked and exempt inventory equal to every gateway write tool, including `setup_propose`.
