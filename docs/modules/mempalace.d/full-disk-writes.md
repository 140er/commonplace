- **Owns:** `mempalace/storage_guard.py` owns the full-disk rules: the free-space floor, the
  recognition of a full disk, the retryable answer, and a log handler that survives a failing
  stream. `mempalace/wal.py` owns the one-line-one-write append of the write-ahead log.
- **Promises:** Below a free-space floor (`MEMPALACE_MIN_FREE_BYTES`; a deployed worker starts
  with 256 MiB, the bare library has no floor unless the variable is set, `0` turns it off)
  every write tool except the delete tools is refused before it starts, with JSON-RPC code `-32006` and `error.data` `{error_class: "StorageFull", retryable:
  true, retry_after_s}`; nothing is stored and not even a journal line is written, so the same
  call can be sent again. A write cut short by a full disk (errno ENOSPC or EDQUOT, SQLite
  "database or disk is full", or either wrapped by a library) is answered the same way, as a
  tool result with `success: false`, `error_class: "StorageFull"`, `retryable: true`,
  `retry_after_s` and MCP `isError`. The write-ahead log appends each line in one write and cuts
  a short write off again, so a full disk never leaves a torn line for the next entry to join.
  The worker's `mempalace_status` carries `data_free_bytes`, `min_free_bytes`, `disk_low`,
  `writes_refused` and `log_write_failures`; the gateway's scoped status drops them. The floor warning is logged at most once a minute with the free
  space. A daemon job refused for a full disk is parked and claimed again after
  `STORAGE_DEFER_BACKOFF_SECONDS` without spending an attempt.
- **How it fails:** A journal write that fails because the disk is full refuses the write
  before it changes anything; any other journal failure is still logged and never fails the
  call, and a failed outcome line (written after the write landed) is logged and never fails
  it. An unknown free-space reading never refuses a write. A log line that cannot be written is
  counted; the next write that works first reports how many were lost and the first one (the
  log handler keeps its own class; only its error path changes). A full disk while recording the
  prior drawer text keeps the message "could not record prior drawer text" and gains the retry
  fields.
- **Tests:** `tests/test_enospc_writes.py` injects ENOSPC on the journal path and in the store:
  a refused write stores nothing, the same write lands once after space returns, the floor
  refuses before the journal is touched, deletes pass the floor, status shows the free space,
  a short journal write leaves no torn line, logging keeps its first lost line, and a queued
  daemon write is parked, not dead-lettered.
