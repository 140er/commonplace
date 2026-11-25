- **Tests:**

  - `tests/test_chroma_type_contract.py` exercises real metadata-less records, ordered snapshots and forwarding identity, native schema validation, dynamic flag defaults, the existing getter, the new transparent capability setter, actual upsert merges, query embedding counts and builtin combined-input refusals, whole-batch vector fallback, and the palace lock's runtime identity.

  - `tests/test_compact_metadata_type_contract.py` preserves the two existing private helpers' exact `None` refusals and exercises real Compact updates with explicit vectors, absent vectors and keyword-only records. It checks forwarded argument identity, embedding calls and retained rows; it does not assert that Compact produces absent metadata.
