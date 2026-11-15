- **Promises:** A worker's `-32006` refusal, a `StorageFull` tool result, or a full-disk error
  raised inside the gateway reaches the caller as a retryable answer, never as `-32603`
  "Internal error" or a plain refusal: MCP gets error code `-32006` with `retryable` and
  `retry_after_s` in `data` (a local tool answers `isError` with the same sentence), and a REST
  route that fails the same way answers 507 with `retry_after_s` and a `Retry-After` header.
  The index job treats it as "memory said when to come back" and rests, without failing the
  thread.
- **How it fails:** A full disk that stops the gateway's own database mid-request answers 507
  `storage_full`; the request changed nothing that a retry cannot repeat.
