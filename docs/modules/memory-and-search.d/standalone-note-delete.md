- **Owns:** Standalone-note delete eligibility and POST /api/memory/<id>/delete.
- **Promises:** Only the current workspace owner in a browser session can delete marked new standalone notes. The route uses mempalace_delete_drawer and records ids, wing and room without note content.
- **How it fails:** Legacy, imported and mined drawers are refused; missing and repeated deletes return 404. Low storage and backup pause remain retryable.
- **Tests:** tests/test_memory_note_delete.py, tests/test_memory_note.py and
  tests/test_memory_note_delete_browser.py exercise the note deletion boundary.
