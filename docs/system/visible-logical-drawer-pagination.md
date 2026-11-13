### Visible logical drawer pagination

- Authenticated drawer listings enumerate metadata in bounded pages, authorize
  current physical identities in the caller's workspace, and revalidate them
  before logical grouping, date filtering, totals and slicing. Owners use the
  same reader. An unreadable chunk cannot authorize readable siblings or
  contribute to their preview, date or count. Wing aliases retain their meaning.
- SQLite final membership and page hydration share one snapshot. SDK fallback
  revalidates metadata and hydrates only visible page identities in batches of
  at most 500, checking their metadata again with the fetched documents.
  One request deadline bounds acquisition and final reads; no timing claim is made.
- Missing workspace context, incomplete enumeration, malformed pages and read
  faults return the normal empty pagination envelope with zero totals. No raw
  exception, partial global total or private descriptor is published. Raw caches
  contain no principal projection; local unscoped calls retain existing behavior.
- The final response gate retains the proven visible total when it only scrubs
  provenance metadata. If it removes a drawer after projection, the listing
  fails closed to the same empty envelope rather than returning a page gap.
- Literal-wing readers with imported-history read consent retain proved visible
  totals through both response gates, including provenance-only metadata scrubs.
  Existing wing and thread authority still controls membership.
- Page arguments use the worker's shared schema rules after policy checks:
  JSON null selects the default, invalid integers retain JSON-RPC invalid-params,
  and worker filter validation errors remain error results. Date validation stays
  in the worker; invalid filters never masquerade as empty successful pages.
  A metadata-free worker call with an empty allow-list validates first, preserving
  worker ordering without loading the worker server in the gateway process.
  Non-owner wing refusals retain their existing messages. Incomplete membership
  proof still returns the empty envelope rather than a worker failure message.
- Consented `private_chats` drawer listings use the same worker validation before
  reading imports, under one request deadline. Null defaults and integer/range
  errors match the worker route; invalid dates retain the worker error result.
  Import listing receives the validated limit and offset unchanged. Valid date
  windows still exclude imports without a proved date. Incomplete validation or
  import-listing faults yield the existing empty listing shape, with no raw error.
- The import branch of `list_rooms` also validates through `list_drawers`, so
  validation errors name `mempalace_list_drawers`. Its public schema declares only
  `wing`; legacy extra filters are still accepted through drawer validation,
  including the raw `room` filter passed to import listing. This is not parity
  with the worker's own `list_rooms` argument validation.
- Stored bytes, imported-history consent/range rules, provenance alias scrubbing
  and legacy source-file proof retain their separate contracts and residuals.
