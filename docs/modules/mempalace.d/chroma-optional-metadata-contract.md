- **Promises:**

  - Metadata snapshots retain their lists, absent values and row order. Their readable type permits `None` metadata, including through the embedding wrapper. Existing native schema validation, dynamic caller-vector defaults and the exec-loaded palace lock remain unchanged.

  - Explicit writes to the embedding wrapper's `upsert_merges_metadata` capability now forward to its inner collection, including through nested wrappers. This extends assignment compatibility with the writable base interface; getter behavior and actual upsert merge rules stay the same. String queries become singleton lists at the wrapper boundary; invalid combined text/vector queries retain their backend refusal without embedding work.
