### Worker observation test isolation

- The worker observer is process-global state in `mempalace.mcp_server`: the observer itself, a latch that records a failed construction, and a counter of lost records. A failed construction latches for the rest of the process, by design, so a broken sink is not retried per call.
- A test that serves a request with a stub config lacking `config_dir` makes construction fail and sets that latch. Without a reset every later observation test in the same process saw no records, and whether it happened depended on whether an earlier test had already created an observer.
- The autouse MCP reset in `tests/conftest.py` now closes and clears the observer and resets the latch and the loss counter after each test. Production behaviour is unchanged.
