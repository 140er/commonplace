- **Tests:**

  - `tests/test_mcp_source_provenance.py` also keeps a tunnel label with an emoji joiner and a literal percent sign readable, and still hides an `mcp:` source ID split by a joiner; the session test doubles in `tests/test_orch_sessions.py` and `tests/test_runner_session_guards.py` return an empty `stderr` like a real subprocess result, since the runner now reads it.
