### Scoped wing counts and search fan-out

- Authenticated `list_wings` and `get_taxonomy` enumerate complete metadata
  pages, authorize each pair with the existing workspace-bound thread reader,
  and sum only identities revalidated against current metadata. Owners use
  the same thread boundary. Readable imported rooms retain their existing
  presence/count behavior; message reads still require the consented range
  proof. No stored content or authorization policy is changed.
- The workspace store opens before deadline-bound pair authorization, so
  cold and warm requests retain the same readable rooms. Ordinary room names
  such as `thr_notes` use the existing thread-room predicate.
- Standalone mediators with a workspace-bound principal keep metadata
  projection even without gateway or handoff context: ordinary readable pairs
  remain visible, and unresolved thread pairs stay hidden. Principals without
  workspace context receive empty wing lists, taxonomy and search fan-out;
  no raw worker listing can bypass wing policy or thread authorization.
- SQLite identity seeks and the generic metadata fallback read at most 500
  records per page or final batch, without document columns. Missing, null
  and question-mark wing/room values normalize to `unknown`; bookkeeping
  stays excluded. Local CLI/stdio reads with no scope keep legacy behavior.
- Search fan-out uses that same visible wing projection before requested-wing
  selection, CLI compatibility filtering or the fan-out limit. Hidden-only
  wings cannot appear in `wings_searched` or change the denial count. Internal
  web and memory consumers inherit the scoped listing; connection validation
  rejects ordinary wings with only unreadable content.
- Worker scopes, identity descriptors and advancing cursors remain private.
  The gateway strips client-supplied scopes and removes them from discovery.
  Raw overview caches never hold a principal's projection; every response
  authorizes afresh, including after revocation or changed associations.
- Malformed pages, storage faults, partial acquisition and deadline exhaustion
  discard every accumulated page. Listings return exactly `{"wings": {}}` or
  `{"taxonomy": {}}`; fan-out returns the query with empty results and
  `wings_searched`, plus its normal facts attachment. No raw exception,
  partial count, hidden marker or cursor is published. One caller deadline
  bounds acquisition; this contract makes no timing or constant-time claim.
- Imported holder-room counts can include rows outside a consented range,
  as before this projection; content reads retain their separate range gate.
- Restricted structured drawer searches gate each ranked prefix before
  applying the requested result limit, for explicit wings and fan-out alike.
  A short readable page retries with doubled worker limits until filled,
  the worker returns a short prefix, or the 100-candidate bound is reached.
  At most eight worker searches share one existing request deadline; ranking
  still uses the same joint wing pool. Readable hits beyond that bound may
  remain unavailable. Every-wing principals keep their existing search path.
- Refill requires an attached gateway handoff context; standalone worker
  helpers retain their exact response shape and existing authorization gates.
  Pinned-wing searches retain the original forwarded worker response, including
  protocol errors and opaque replies, and refill only structured search hits.
  The first search and every refill share the same deadline and call bound.
- Restricted structured responses omit `total`, `total_before_filter` and
  `filtered_by_max_distance`; any `count` describes only returned readable
  hits. No hidden-candidate count or refill diagnostic is returned. Restricted
  CLI-compatible search acquires structured hits, so it uses the same refill
  before the final gate and rendering.
- Other overview, legacy
  provenance and owner search-index progress remain separate residuals.
