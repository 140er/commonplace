### Graph projection before traversal and ranking

- Authenticated graph reads first enumerate metadata in batches of at most 500,
  authorize each placement with wing policy and the live workspace reader, then
  revalidate concrete descriptors in fresh metadata snapshots. Empty allow means
  no placements; local unscoped graph calls keep their existing behavior.
- Nodes, plural wings/halls/dates, counts, edges, fuzzy suggestions, traversal and
  top-N ranking use only surviving rows. Grouped reads retain backend-specific
  ordering; collection paths retain enumeration order for tied readable candidates.
  Traversal and tunnel discovery retain the 50-result caps and stats the ten-node
  top list. Projected results never enter the raw graph or overview caches.
- Explicit tunnel totals reuse the authorized endpoint and drawer association set.
  Labels independently prove every recognized thread/private token before list,
  follow, preview hydration or counting. Kept labels (including emoji with a zero
  width joiner and ordinary percent text) and other stored fields remain unchanged.
  Label changes invalidate final tunnel descriptors; unrelated dynamics fields do
  not. Missing/unresolved optional drawers and duplicate tunnel ids fail closed as
  documented for explicit reads; computed imported drawers still require stored
  associations. Imported-history consent/range rules are not widened.
- Incomplete acquisition, malformed protocol replies, missing workspace context or
  deadline failure discard the entire graph response: zero stats, an empty tunnel
  list, or the ordinary not-found traversal envelope with empty suggestions. Odd
  client argument errors retain their JSON-RPC validation shape. No private diagnostics,
  redaction markers or partial global counts are returned. Literal-wing readers
  retain the existing all-wing-tool refusal; actual owners use the same live reader.
- Storage is never rewritten or purged. O4/O8 remain in force; external ids hidden
  only by wing policy retain their accepted limitation. The separate LOCKED
  D9.1 = A and D9.2 = A decisions, including actual-readable-work quota deferral,
  are unchanged by graph projection.
