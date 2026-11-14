### Eval archives (slice 1)

- `commonplace eval` is an owner-only local CLI. It archives immutable runs of
  eligible stored threads (handoff transcripts and ACL imports in the `threads`
  wing) and reads them back as exact bytes. No REST, MCP, UI, hook, daemon,
  network, model, judge, score or replay is part of slice 1; later slices need
  their own plan.

- Live captures, grant imports, private chats and continuity threads refuse
  (`eval_source_gap: unsupported_in_slice1`). Owner role, eligibility, the read
  policy and the continuity probe are rechecked inside every archive commit and
  every list/show/read response; a hidden run keeps its quota. A lookup error
  refuses the response rather than hiding a run. The probe covers the
  continuity tables that record stored IDs (`sync_streams`, `sync_groups`,
  `sync_backfill`, `capture_ops`). `handoffs.stored_thread_id` also records
  stored IDs and is deliberately not probed: a handoff transcript is the
  eligible case, so its handoff record must not hide it.

- `eval preflight` is the one eval command dispatched without the backup fence
  and data lock (a deviation from slice-1 plan §6.3, accepted by the lead): it
  only stats paths and reads `workspace.json`, so it creates nothing, even on a
  fresh data directory.

- Eval uses local OS trust like every CLI command, never constructs
  `ControlDB`, and reads the control database `?mode=ro` behind a write-denying
  authorizer. Its principal has only `threads:read` and `memory:read`.

- Runs live in `<workspace>/evals/runs.sqlite3` (WAL, 0600 in a 0700 `evals/`,
  encrypted with the workspace). Purge by exact run ID is the only delete; keys
  are stored only as digests and stay retired for 30 days after purge. With
  plans enabled, the eval DB counts against plan storage, measured under the
  thread-store writer lock.
