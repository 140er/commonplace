### Thread-search ranking

- For a verified thread index, search finds the scoped BM25 cutoff using the FTS rank iterator, then includes every hit at or above that cutoff before ordering by BM25, thread ID and chunk index.

- The top rowids are materialized before fetching canonical chunk fields.

- Each ID batch keeps the same limit and the final merge uses the same ordering.

- Snapshot verification, external-change repair and the read-only logical query remain unchanged; a selected missing or mismatched canonical identity reruns the complete logical query.

- The synthetic thread-search benchmark reports p50/p95 and exports full result lists for revision comparisons.

- Timings are informational, never a gate.

