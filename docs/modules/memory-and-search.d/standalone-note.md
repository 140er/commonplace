- **Owns:** Standalone note validation and destination hints in commonplace/memory_tools.py
  and bounded optional room discovery in commonplace/memory_places.py.
- **Promises:** POST /api/memory remains the single note write route over memory_save and
  mempalace_add_drawer. Text is preserved verbatim, including Unicode and whitespace.
  GET /api/memory/rooms?compose=1 uses only active board wings and the default wing,
  with general as the room; opening never scans the palace. An explicit wing query
  loads richer rooms within 0.75 seconds, with a 30-second cache per workspace,
  connection and policy. Late answers are ignored, and at most two readers run per
  gateway. Existing Memory Map overview scans have a one-second deadline.
  The default is the policy write wing or shared in
  the active workspace. Save responses identify the actual stored wing and room.
  REST notes and the MCP memory_save tool share validation: nonempty text up to
  100,000 Unicode characters; NUL and unpaired surrogates are refused rather than
  removed. Tabs and line breaks remain valid. Accepted MCP text and replies are unchanged.
- **How it fails:** Empty and oversized text return plain 400 messages; invalid text is
  refused rather than rewritten. Scope and wing refusals remain 403. Storage and busy
  failures keep their retryable 507/503 responses and the existing free-space floor.
  Optional room timeouts or unavailable memory leave general usable; only successful,
  timely hints enter the cache. Every request checks the destination before cache use.
- **Tests:** tests/test_memory_note.py exercises real HTTP storage, retrieval, search,
  validation, destination defaults, scope checks and retryable failures.
