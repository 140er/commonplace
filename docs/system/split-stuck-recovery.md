### Split-stuck recovery authority

- Split authorization previously required a signed-in owner or a PAT matching
  the board's coordinator field. It rejected owner connections and OAuth board
  seats, and ignored a persisted seat binding when that field was empty.
- With workspace orchestration enabled, the live board seat and workspace owner
  may read the split inbox and mark a root `split` or `kept`. The owner's own
  top-level PAT or OAuth connection uses its existing owner authority, scope and
  board permissions. A worker credential, child connection, revoked connection
  or another board's seat cannot inherit that authority from its owning user.
- A persisted main-seat binding authorizes its own board when the coordinator
  field is empty. An explicit replacement takes precedence. No runner config,
  machine requester grant or held lease is required to finish the split.
- Use `card_split_done(task=<root>, state="split")` after all keyed children
  exist, or `state="kept"` for an intact root. The existing authenticated
  `POST /api/projects/<board>/orchestrator/split` accepts the same task and state.
  The owner can use their connection without opening the web interface; there
  is no dedicated split-recovery button in the current web interface.
- The marker clears only the split-stuck wait. It does not release proposals,
  review holds, dependency waits or other admission checks. Card history records
  the root, actor and state. The first marker wins; repeating either state from
  another authorized caller returns the stored result without a new event or
  version. Existing retry history remains intact.
- The workspace switch still defaults off. Tool discovery, refusal behavior and
  card projections with orchestration disabled are unchanged. The checked-write
  rules catalog already includes the marker and its REST route; the ordinary
  rules-freshness checks remain in effect. No route is added.
