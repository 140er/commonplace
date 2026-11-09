### Thread-derived mutations and citations

- Within one workspace, a fact with any readable thread source is mutable.
  The read rule considers both flat provenance and source rows. The worker
  rechecks each authorized fact's wing and source set inside its transaction;
  rows changed or added after the probe do not acquire authorization.
- Every lookup and worker call uses the authenticated principal's workspace.
  A foreign workspace identifier resolves as missing. Internal mutation scopes
  are stripped from client arguments and hidden from tool schemas.
- Every principal, including an owner, needs read access to cited threads,
  private source ids, drawers and closets. Missing, unreadable or undecodable
  citations use the same not-found reply. Existing mutations on unreadable
  facts and rooms are no-ops; owners have no bypass.
- Provenance is decoded up to four percent-decoding rounds and checked for
  control/format characters after decoding, before whitespace is stripped.
  Recognized tokens anywhere in a provenance string must resolve readable;
  original strings are forwarded and stored unchanged. Plain strings with
  encoded controls are also refused, and partial hex tokens in filenames
  such as `thr_2024_notes.md` are recognized. Private source ids allow path
  separators, so the complete private token remains literal rather than
  guessing where a path-embedded id ends.
- Source deletes enumerate explicit ids and filter drawers, closets, AAAK
  rows and revisions by their own readable room. A roomless row needs a
  readable current drawer or, when its drawer is unresolved/absent, readable
  normalized source tokens. A resolved unreadable drawer or resolver error
  always keeps the row. Snapshots are rechecked before current drawers go;
  revision checks hold the revision lock. Unassociated legacy rows remain,
  even for an owner's MCP call. Unscoped local calls retain source-wide purge.
- Tunnel deletion rechecks endpoint snapshots, including drawer ids. Hallway
  deletion rechecks every room under its lock. Taxonomy and room-list results
  remove unreadable thread keys for every principal.
- Non-reader extraction leaves its cursor unchanged so a later reader can
  finish filing. A missing optional closet collection means no closet rows;
  other probe failures propagate. Parser/access caches expire with a response.
- No scope is persisted and no existing user text is rewritten. Rollback is
  a code revert; the per-request revocation window remains unchanged.

