### Hallway read eligibility and visible pagination

- Authenticated hallway reads require readable wing policy and every room. Every
  returned string, including embedded room tokens, ids, labels, entity/author and
  timestamp values, uses the same independent raw thread/private token scan and
  current read proof. Missing proof drops that row before visible counts/paging.
  Kept strings, emoji joiners and literal percent text remain byte-identical.
- One shared public field tuple is applied before count/sort/page and again by the
  final gate: id, wing, rooms, label, entity_a, entity_b, co_occurrence_count,
  created_at, created_by, strength, stability, last_activated and access_count.
  Existing dynamics fields remain present. Present string fields must be strings;
  rooms must be a list of nonempty strings. Strength, stability, access_count and
  co_occurrence_count must be finite int/float values (not bool/text); invalid
  rows are excluded, with absent legacy fields left absent. No other key returns.
- Metadata pages contain at most 500 descriptors in SQLite rowid or JSON insertion
  order. Request-local authorization yields concrete allow descriptors, revalidated
  in final read snapshots. Eligible rows retain stable strength ties. Ineligible
  rows drop before counting; only a failed final-gate recheck discards the entire
  page and its counts. Readable mirror rows avoid singular-room drawer scrubbing.
  Client discovery hides internal scope, mediation replaces client-supplied scope,
  and worker scope requires verified gateway identity. Local unscoped reads remain.
- Every scanned string is capped before scanning by the public shared
  provenance_refs.MAX_REFERENCE_CHARS. Duplicate tokens collapse. Overlapping
  raw scans cost O(C²) time/memory per capped string of at most C characters;
  the cap bounds a scan but does not promise it completes within a deadline.
  Deadline checkpoints precede each field scan and each token lookup, rather than
  waiting for an entire 500-row batch. Oversized/unproved values exclude the row.
- Missing context, incomplete acquisition, malformed protocol or deadline expiry
  discards all projected counts and returns the fixed empty pagination envelope.
  Reads do not recompute strings, clean up stored legacy rows (O4), or extend import
  consent. Non-canonical spellings outside the raw token grammar (percent escapes,
  fullwidth/NFKC forms) remain the accepted limitation; no returned string field is
  exempt from scanning. Other graph/provenance residuals below remain unchanged.
