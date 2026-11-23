- **Tests:**

  - `tests/test_chat_coworkers_runtime_redaction.py` contains 38 finite synthetic cases for transient credential redaction. The matrix covers local DM/group/board and explicitly configured hosted envelopes, credential corpora and exact noncredential controls. Additional cases cover pre-dispatch refusals, persona slicing, required/optional expansion budgets, selected-memory freshness, retained bot history and explicit task originals. Workers, stores and serialization stay real; only transport capture replaces the provider call and denies its connector.
