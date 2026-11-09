### Opt-in blockers, waiting reasons and split delivery

- Disabled orchestration retains legacy capacity refusals; shared waiting text is applied only while the switch is enabled.

- Split completion markers obey the same MCP and REST rules-freshness checks as other card decisions. Card schemas advertise blocker arguments only while the workspace switch is on; properties are derived per request without changing shared extension schemas. Split labels cannot be added to roots or removed from keyed children through label edits. REST marker references must be bounded positive integers. Only lock timeouts become busy responses, through the shared admission classifier; settings read errors retain their retryable status. Checkpoint inbox reads are best effort. A failed inbox snapshot after lease commit logs sanitized diagnostics without exception messages and retains the pinned launch instructions and asks the agent to read the inbox before acting. Board waiting summaries share one runner connection and count usage once for active slots and once for queued reservations. Card lists likewise share one read connection and one usage calculation per board. Capacity refusals retain the original error if a display waiting reason is unavailable. Release waiting applies only to todo/backlog cards. Existing runner rows migrate with a NULL launch role, which preserves their configured role and stored job fields.

- The workspace switch defaults off. Enabled boards add same-board blocker
  edges (maximum 16 per card, no self/cycles, DFS bounded to 200 nodes).
  Only Done unblocks. SQL selection skips unresolved blockers and the runner
  checks them before starting. Views use the pure `boards.why` predicate in
  blocker/person/release/cap order; explicit claims share its waiting text.
  Dependency waiting alone does not enter `needs_me`.
- `boards.sqlite3` adds `task_blockers` and `card_splits`. Keyed children use
  `split:<parent>:<key>` labels; key checks and inserts share the board write
  transaction. Inbox reads include open unsplit roots, excluding children and
  coordinator-created roots. Only the coordinator PAT or a signed-in owner
  reads or marks them. The first split/kept marker wins and appends card history.
- Configured todo triggers retain their prior behavior with the switch off.
  Enabled roots launch `main`; children never trigger split starts. Jobs pin
  the selected `launch_role` in an additive runner column, so later switch
  changes cannot change a queued job's launch instructions. Messages include
  inbox context; the agent creates missing stable keys and marks completion.
- Each runner sweep reconciles eligible roots older than 10 minutes with no
  active/started job. Retry keys are `split-retry:<card>:<n>`; gaps are 10, 30,
  90 minutes and n is bounded to 3. Attempts (including refusals) use the existing
  `orch_misses` log with negative event ids. Eligibility and attempt number are
  rechecked under `fenced_start` before inserting a job or miss. Three refused
  attempts show `not_released` / `split stuck` in waiting reasons and checkpoints.
  A completed marker stops reconciliation. The hub never creates children.
  Retry keys share the caller key space, scoped by card and requester. Reusing
  the same key and arguments returns the stored job; different arguments refuse
  with `request_conflict` rather than launching another job.
- Board reads inside runner transactions use the read-only connection path;
  no BoardService or BoardStore migration runs there. New route lock timeouts
  return `409 busy` without mutation. No external service is called.
- Without a runner, card delivery uses a subscribed `card.created` webhook
  plus the inbox at the next session. Without either, a new card waits for that
  session. Continuous orchestration, done/stuck routing and runner-job events
  remain separate work. Stable keys are the agent's responsibility; changing
  keys after a crash can create additional children.

- `runner.sqlite3`: runner jobs with pinned launch role; orchestrator configs, cursors, leases, steps, journal, retry attempts, restore flag

