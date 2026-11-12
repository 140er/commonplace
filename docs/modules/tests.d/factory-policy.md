- **Tests:**

  - `tests/test_orch_factory.py` pins the `[factory]` merge policy of the orchestrator config (schema, contradictions, read path, merge guard, boards without a policy); the Merge policy list of the coordinator status block is pinned by two cases of `tests/orchstatus.mjs`.
