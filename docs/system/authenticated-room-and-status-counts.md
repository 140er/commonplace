### Authenticated room and status counts

- For `status`, `list_rooms`, `list_wings` and `get_taxonomy`, backup pauses
  retain the gateway retry contract: JSON-RPC error -32603 with `retry_after_s`,
  and an internal ToolError explaining the backup pause. Pair acquisition and
  status diagnostics propagate that pause rather than returning zero counts.
  Consented `private_chats` import listings retain the same retry errors for
  `list_rooms` and `list_drawers`. Shared pair-projection search fan-out also
  propagates the pause. Other acquisition faults retain the zero-count empty
  response. Other drawer, hallway, tunnel, graph and KG projection pause behavior
  remains separate work.

- Authenticated `list_rooms` and `status` sum the complete authorized pair
  projection, including wing aliases and unknown loci. Wing policy and the
  current workspace thread reader both apply, including to actual owners.
  Malformed private mirror rooms need readable association proof.
- Metadata pages and final snapshot batches stay bounded to 500 under one
  request deadline. Changed descriptors are excluded. Incomplete acquisition
  returns zero counts/maps without partial global totals or raw failures.
- Authenticated status suppresses global `hnsw_capacity` and raw capacity
  probe messages. A disabled vector index reports `vector_disabled: true`
  with the fixed reason `Vector index unavailable; repair required.`, including
  after incomplete acquisition. Local operator status retains its raw diagnostics.
- Authenticated diagnostics omit `sqlite_integrity`, `library_versions` and
  `updates`, including embedded palace/database paths. Integrity failure still
  reports `sqlite_integrity_failed`, the fixed error and `partial`. Unexpected
  fields fail closed. Principal projections never enter raw caches.
- The worker status schema retains the private scope property required by
  internal argument whitelisting; authenticated discovery strips it and client
  scopes are discarded. Non-object local scopes receive an argument error.
  Invalid room-list wing types return the ordinary name-validation error;
  whitespace-only wing filters retain the existing all-rooms convention.
- Imported-history listing consent and source-range rules remain in force;
  these counts create no new read grant. The merged hallway, tunnel, graph and
  KG projections retain their own read contracts. Legacy provenance remains
  separate work. Stored rows stay intact.
