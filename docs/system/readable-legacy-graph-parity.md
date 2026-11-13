### Readable legacy graph parity

- Graph descriptors bind literal projected wing and room names, including legacy
  `?` values. Readable registry and sentinel rows remain graph inputs, matching
  the local graph paths; overview bookkeeping rules do not change.
- Graph argument names are checked against the worker's tool schema before any
  metadata probe. Unknown and missing arguments retain JSON-RPC `-32602` errors.
  Traversal retains the worker's null/default and integer-coercion conventions;
  booleans and non-coercible values retain its JSON-RPC error shape. Schema lookup
  and projection share one deadline. Worker/schema/projection faults still return
  the empty operation envelope, never a client-validation error.
- Ordering follows the reader that actually served the metadata page. Grouped
  Chroma uses room/wing/hall order. Exact and Compact readers use wing/room/hall
  with generated locus columns, or room/wing/hall on older read-only databases
  without them. Collection fallback keeps enumeration order. Sibling ties,
  suggestions and the 50-result cap follow that order; grouped statistics retain
  their separate room-name tie break.
- Exact and Compact projections read the local graph's SQL expressions and schema
  from one metadata-only snapshot. SQLite applies TEXT affinity on exact loci,
  including booleans and integers beyond int64; Compact and locus-less readers
  preserve native numeric values and their falsy-zero behavior before converting
  to text. Descriptors bind both projected values and stored SQLite sort values.
  Chroma preserves its grouped numeric text casts, ignores boolean locus columns,
  and reads dates from the string column. Collection fallback retains its existing
  metadata interpretation; overview bookkeeping rules remain separate.
- Null and missing tunnel labels are empty only for protected-token scanning.
  Returned/stored nulls, missing keys and unrelated fields remain unchanged; other
  non-string labels still fail closed. No palace data is rewritten.
