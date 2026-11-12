- **How it fails:**

  - A SQLite "database is locked" error on a gateway MCP call is logged with the tool name only (`call=<tool>`, no arguments or content) and answered with a retryable JSON-RPC error (`-32001`, `data.retryable`, `data.retry_after_s`) instead of the bare `-32603` Internal error; any other exception still maps to `-32603`.

  - `commonplace/db_busy.py` owns the busy test (`is_busy`) and `begin_immediate`, which retries only the lock acquisition of a short write transaction (3 attempts, instant refusals only), so a retry never applies a write twice. The control database, boards and orchestrator stores all connect with the same explicit 10 s busy timeout (`BUSY_TIMEOUT_S`) and open their write transactions through it.
