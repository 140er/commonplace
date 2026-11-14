- **How it fails:**

  - `BoardStore.transaction` opens its write transaction through `db_busy.begin_immediate`: an instant "database is locked" refusal is retried up to 3 times before the error surfaces, and the busy timeout is the shared `BUSY_TIMEOUT_S` (10 s). The gateway answers a remaining lock error with a retryable `-32001`, not `-32603`.
