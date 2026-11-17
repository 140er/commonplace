- **Owns:** Standalone note validation and destination hints in commonplace/memory_tools.py.
- **Promises:** POST /api/memory remains the single note write route over memory_save and
  mempalace_add_drawer. Text is preserved verbatim, including Unicode and whitespace.
  GET /api/memory/rooms?compose=1 adds readable, writable wings and existing rooms,
  including empty project wings. The default is the policy write wing or shared in
  the active workspace. Save responses identify the actual stored wing and room.
  REST notes and the MCP memory_save tool share validation: nonempty text up to
  100,000 Unicode characters; NUL and unpaired surrogates are refused rather than
  removed. Tabs and line breaks remain valid. Accepted MCP text and replies are unchanged.
- **How it fails:** Empty and oversized text return plain 400 messages; invalid text is
  refused rather than rewritten. Scope and wing refusals remain 403. Storage and busy
  failures keep their retryable 507/503 responses and the existing free-space floor.
- **Tests:** tests/test_memory_note.py exercises real HTTP storage, retrieval, search,
  validation, destination defaults, scope checks and retryable failures.
