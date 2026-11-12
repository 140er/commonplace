- **How it fails:**

  - The orchestrator store (lease heartbeats, claims, journal) connects with the shared `BUSY_TIMEOUT_S` (10 s) and opens write transactions through `db_busy.begin_immediate`, which retries an instant "database is locked" refusal up to 3 times; a lock that outlasts it reaches the caller as a retryable `-32001` error from the gateway instead of `-32603`.
