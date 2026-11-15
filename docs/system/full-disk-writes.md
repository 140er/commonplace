### A write that cannot be saved on a full disk is refused, retryable and safe to repeat

- A full data disk used to show up as journal errors in the worker log while the same calls
  were still answered as saved, and as plain "refused" or "Internal error" answers when the
  store itself failed. Now a write that cannot be persisted is refused with one retryable
  answer (MCP `-32006`, REST 507, both with `retry_after_s`) and nothing is left half-written.
- Decision: below a free-space floor (a deployed worker starts with 256 MiB;
  `MEMPALACE_MIN_FREE_BYTES` overrides, and the bare library has none, so tests and the CLI on a
  small disk are not refused) writes are refused before they start; delete tools are exempt so forgetting still works; an unknown free-space
  reading never refuses a write. A journal write that fails for a full disk refuses the write,
  because an unjournaled write is one the audit trail cannot account for; any other journal
  failure stays non-fatal. A failed outcome line is only logged, since the write already landed.
- Decision: retry safety rests on content-addressed ids (a drawer written twice is one drawer)
  and the capture client's `op_id`; diary entries get a fresh id per call, so a diary write that
  was refused is the caller's to send again, and one that was answered is not.
