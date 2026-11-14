### Plain-file citation proof and compatibility narrowing

- Under locked D9.1=A, token-free source_file citations match the union of the stored literal
  spelling and the complete percent-decoded, whitespace-trimmed identity against
  drawer metadata in the authenticated workspace. Every
  match needs current wing and room authority, including for owners; a final
  metadata snapshot revalidates that same union before publication. Chroma selects
  the complete set of matching row ids in one SQLite snapshot, deduplicates by
  row id and decodes metadata in batches of at most 500 without hydrating documents.
- An empty decoded identity never supplies proof: omit its citation before any
  association worker call, and reject an empty decoded selector in the worker.
- Zero matches, missing room proof, hidden matches, changed descriptors, expired
  deadlines and unavailable snapshots omit only the citation. Generic backends
  without a complete snapshot cannot supply proof. Basenames, prefixes, filesystem
  inspection and other workspaces never supply proof. Non-Chroma backends lose
  plain-file citations because their collection API cannot establish a complete
  stable match set. Drawer display still uses
  the basename after raw-path authorization.
- Original-source D1-readable fact text stays verbatim, external plain source_url
  compatibility is preserved, and O4 roomless/unproved source rows stay stored.
  No migration or deletion occurs. Provenance aliases and sequence bundles retain
  their existing behavior. Locked D9.2=A and its accepted global-quota effect on
  actually blocked readable work remain unchanged.

- D9.1=A accepts a presence side-channel: adding a hidden drawer that shares a
  readable plain path changes a previously retained citation to an omitted one
  (mixed means omit). This is an explicit exception to hidden-present/absent
  output equality, including literal-wing readers; it grants no read permission.
- Import-consented single, batch and listing drawer reads keep raw provenance
  through wing-policy filtering. The final response gate applies plain-file proof
  once before converting the path to its display basename, just as for readers
  without import consent.
- Gateway KG projection and the final response gate share the same call-local
  access object. Both gates still run; a new call creates fresh access, never
  shared across principals or workspaces. Other routes retain their existing
  gating, including the list_rooms/status respond bypass and backup-pause errors.
- Proof results are memoized by kind and concrete selector within one response;
  already scrubbed rows are not scrubbed twice. A revocation after a selector's
  first proof is not observed by later citations with that selector in the same
  response; this window is accepted under the response-local cache contract.
  A later request obtains fresh proof. The union rule applies only to token-free
  file citations; token-bearing values retain their existing token authorization
  without proving the plain remainder.

- Authenticated search proves the raw source_path before retaining its source_file
  display basename. These fields share one union proof, cannot disagree about the
  source, and disappear together if proof fails. The same gate covers media search;
  failed asset proof also removes its path, title and availability aliases. Local
  unscoped search keeps its existing display format and source filters.
- Only non-fact records constructed after exact import-range proof retain the
  documented source_path and nested metadata.source_file alias exemption. Ordinary
  search and media records receive no exemption. Blank decoded drawer and closet
  selectors are refused before worker calls; the worker also refuses a decoded
  file selector containing only whitespace.
