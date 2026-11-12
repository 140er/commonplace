- **Owns:**

  - `tests/test_gateway_busy_retry.py`: a heartbeat succeeds when the orchestrator database lock is released inside the timeout, and gets a retryable error (never `-32603`, tool name logged, no secrets) when it is not; non-busy failures stay `-32603`; the control database busy timeout; `begin_immediate` retry bounds.
