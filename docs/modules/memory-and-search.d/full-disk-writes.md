- **Promises:** Saving a memory or a fact on a full disk answers 507 with `retry_after_s`
  (`code: "storage_full"`) over REST and a retryable `no: ...` error over MCP, never a saved
  id; the same words sent again after space returns are stored once. A capture chunk that hits
  a full disk answers 507 `storage_full` with `retry_after_s`, which the capture client already
  retries with the same `op_id`.
- **How it fails:** While the disk is below the floor every memory write is refused before it
  starts and reads keep working.
