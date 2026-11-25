- **Owns:** Trusted standalone-note origin stamping and the existing singular delete handler.
- **Promises:** New browser notes carry hub_origin=standalone-note-v1. A standalone-note delete operation rechecks the marker and absence of source provenance on the resolved record before the existing WAL and removal path runs.
- **How it fails:** Existing records are never relabeled by duplicate saves. Physical chunk ids, unmarked drawers and source-backed drawers fail the restricted deletion check.
- **Tests:** tests/test_memory_note_delete.py, tests/test_memory_note.py and
  tests/test_memory_note_delete_browser.py exercise the note deletion boundary.
