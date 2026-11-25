- **Owns:** memory_delete_csrf proofs for note deletion.
- **Promises:** Proofs bind memory-delete-v1 to the browser session and exact drawer id; the route also requires an explicit same-origin Origin.
- **How it fails:** Missing, altered, cross-session and cross-drawer proofs are refused before deletion.
- **Tests:** tests/test_memory_note_delete.py, tests/test_memory_note.py and
  tests/test_memory_note_delete_browser.py exercise the note deletion boundary.
