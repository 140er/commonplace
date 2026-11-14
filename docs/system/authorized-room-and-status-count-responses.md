### Authorized room and status count responses

- Only `mempalace_list_rooms` and `mempalace_status` replies produced by the
  gateway's `respond` path skip the final generic mirror/provenance gate.
  Their room/wing maps already come from complete request-local wing and
  thread authorization plus descriptor revalidation; imported room listings
  retain their existing consented-reader proof.
- This keeps visible labels such as `source_file` or `origin_id` as count-map
  keys and preserves room/wing sums. Hidden pairs are excluded before counts;
  incomplete proof still yields zero visible counts and maps.
- Tunnel replies, drawer records, every other tool and ordinary forwarded
  replies keep the generic gate. Provenance scrubbing and typed-association
  semantics are unchanged, including omission of malformed scalar citations.
- Broader semantic record/count-map collisions and search/duplicate vector
  diagnostics remain separate work.
